"""处理任务状态与取消控制。

单一活动任务约束（默认 max_concurrency=1）在 pipeline 层强制；这里只提供状态中文标签与
取消标志位（用于让长时间运行的转写/分析循环及时退出）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime

from .logutil import get_logger

LOG = get_logger("jobs")

# 处理状态（与需求文档一致）
STATUS_LABELS = {
    "waiting_upload": "等待上传",
    "file_check": "文件检查",
    "waiting_model": "等待模型",
    "transcribing": "正在转写",
    "analyzing": "正在分析",
    "indexing": "正在建立搜索索引",
    "completed": "已完成",
    "failed": "处理失败",
    "cancelled": "已取消",
}

JOB_TYPE_LABELS = {
    "transcribe": "转写并分析",
    "analyze": "仅分析（使用已有转写）",
    "retranscribe": "重新转写",
    "reanalyze": "仅重新分析",
}

# 取消标志：被用户在 UI 点击「取消」后置位
_CANCEL: set[str] = set()


def request_cancel(session_id: str) -> None:
    _CANCEL.add(session_id)
    LOG.info("用户请求取消 session=%s", session_id)


def is_cancelled(session_id: str) -> bool:
    return session_id in _CANCEL


def clear_cancel(session_id: str) -> None:
    _CANCEL.discard(session_id)


def reset_cancel() -> None:
    _CANCEL.clear()


def progress(job: dict | None) -> dict:
    try:
        value = json.loads((job or {}).get('progress_data') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


# Relative work shares from a timed 130-minute local recording. Small, atomic
# steps have one share. These are weights for an estimate, never a time promise.
_WEIGHTS = {'file_check': 1, 'transcribe': 2276, 'load_text': 1, 'translate': 580,
            'analyze': 272, 'synthesize': 48, 'verify': 1, 'save': 1}
_TIMED = ('transcribe', 'translate', 'analyze', 'synthesize')
_TERMINAL = {'completed', 'failed', 'cancelled'}


def _positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _seconds_between(start, end):
    try:
        return max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return 0


def start_progress(job, mode, settings, audio_seconds=None):
    """Load a few comparable completed timings once, without probing any model."""
    if not job:
        return
    from . import db
    def engine(*keys):
        values = [settings.get(key) for key in keys]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:16]

    analysis = engine('ai_provider', 'llm_model_id', 'ollama_model', 'openai_model', 'openai_base_url')
    api_translation = settings.get('translation_provider') == 'api'
    engines = {'transcribe': engine('whisper_device'), 'analyze': analysis, 'synthesize': analysis,
               'translate': engine('translation_provider', 'translation_base_url', 'translation_model')
               if api_translation else analysis}
    past = {}
    rows = db.read_conn().execute(
        "SELECT progress_data FROM processing_jobs WHERE status='completed' ORDER BY rowid DESC LIMIT 24"
    )
    for row in rows:
        old = progress(dict(row))
        if old.get('resumed') or not isinstance(old.get('engines'), dict) or not isinstance(old.get('phase_durations'), dict):
            continue
        for name in _TIMED:
            if name in past or old['engines'].get(name) != engines[name]:
                continue
            if name == 'transcribe' and old.get('asr_cpu_retry'):
                continue
            seconds = old['phase_durations'].get(name)
            if _positive(seconds) and seconds >= 2:
                past[name] = {'seconds': seconds, 'audio': old.get('audio_seconds'),
                              'chars': old.get('source_chars')}
        if len(past) == len(_TIMED):
            break
    plan = list(_WEIGHTS)
    if mode not in {'transcribe', 'retranscribe'}:
        plan = plan[2:]
    local = ['transcribe']
    if settings.get('ai_provider', 'sidecar') in {'auto', 'sidecar'}:
        local.extend(['analyze', 'synthesize'])
        if not api_translation:
            local.append('translate')
    epoch = progress(job).get('checkpoint_epoch')
    report(job['id'], plan=plan, engines=engines, history=past, local_phases=local,
           translation_provider='api' if api_translation else 'local',
           audio_seconds=audio_seconds, resumed=bool(epoch and epoch != job['id']))


def _project(data):
    """Keep the whole-job bar monotone and estimate only timed, comparable work."""
    phase, plan = data.get('phase'), data.get('plan') or []
    if phase not in plan:
        return
    index = plan.index(phase)
    done, total = data.get('completed'), data.get('total')
    fraction = min(1, max(0, done / total)) if _positive(total) and isinstance(done, (int, float)) else 0
    before = sum(_WEIGHTS[name] for name in plan[:index])
    percent = 100 * (before + _WEIGHTS[phase] * fraction) / sum(_WEIGHTS[name] for name in plan)
    data['overall_percent'] = min(99, max(data.get('overall_percent') or 0, int(percent)))
    data['eta_seconds'] = data['phase_eta_seconds'] = data['eta_basis'] = None
    remaining = [name for name in plan[index:] if name in _TIMED]
    if not remaining:
        return
    rate = data.get('rate_per_second')
    live = _positive(rate) and _positive(total)
    # A cached prefix may return minutes of audio instantly. Never extrapolate
    # those jumps to other stages, or use a remote translation to time a local model.
    local = [name for name in data.get('local_phases') or []
             if name != 'transcribe' or not data.get('asr_cpu_retry')]
    scale = None
    if not data.get('resumed'):
        if live and phase in local:
            scale = total / rate / _WEIGHTS[phase]
        else:
            for earlier in reversed(plan[:index]):
                seconds = (data.get('phase_durations') or {}).get(earlier)
                if earlier in _TIMED and earlier in local and _positive(seconds) and seconds >= 3:
                    scale = seconds / _WEIGHTS[earlier]
                    break
    seconds_left = []
    bases = set()
    for name in remaining:
        estimate = None
        if name == phase and live:
            estimate = max(0, total - done) / rate
            bases.add('live')
        else:
            sample = (data.get('history') or {}).get(name) or {}
            if name == 'transcribe' and data.get('asr_cpu_retry'):
                sample = {}
            if _positive(sample.get('seconds')):
                # One synthesis request per lesson. Other stages scale to actual
                # input size, preferring text once the transcript is available.
                if name == 'synthesize':
                    estimate = sample['seconds']
                else:
                    for key, current in [('chars', data.get('source_chars')), ('audio', data.get('audio_seconds'))]:
                        if key == 'chars' and name == 'transcribe':
                            continue
                        if _positive(current) and _positive(sample.get(key)):
                            estimate = sample['seconds'] * current / sample[key]
                            break
            if estimate is not None:
                bases.add('history')
            elif name in local and scale is not None:
                estimate = scale * _WEIGHTS[name]
                bases.add('reference')
            if estimate is not None and name == phase:
                estimate *= 1 - fraction
        if name == phase:
            data['phase_eta_seconds'] = estimate
        seconds_left.append(estimate)
    if all(s is not None for s in seconds_left):
        data['eta_seconds'] = sum(seconds_left)
        data['eta_basis'] = 'reference' if 'reference' in bases else 'history' if 'history' in bases else 'live'


def report(job_id, phase=None, label=None, completed=None, total=None, *, error_message=None, **metadata):
    """Persist real phase counters, timings, and a projection at work boundaries."""
    from . import db
    job = db.get_job(job_id) if job_id else None
    if not job:
        return
    data = progress(job)
    now = db.now_iso()
    previous = data.get('phase')
    if phase and phase != previous:
        if previous and data.get('phase_started_at'):
            seconds = _seconds_between(data['phase_started_at'], now)
            durations = data.setdefault('phase_durations', {})
            durations[previous] = durations.get(previous, 0) + seconds
        if phase in {'failed', 'cancelled'}:
            data['stopped_phase'] = previous
        else:
            data.update(completed=None, total=None, unit=None)
        data.update(rate_per_second=None, sample_interval=None)
        data.update(phase=phase, phase_started_at=now)
    if completed is not None or total is not None:
        data.update(completed=completed, total=total)
    data.update(metadata)
    data['observed_at'] = now
    if data.get('translation_required') is False and 'translate' in (data.get('plan') or []):
        data['plan'].remove('translate')
    if phase in _TERMINAL:
        data.update(eta_seconds=None, phase_eta_seconds=None, eta_basis=None)
        if phase == 'completed':
            data['overall_percent'] = 100
    elif data.get('plan'):
        _project(data)
    fields = {'progress_data': json.dumps(data, ensure_ascii=False)}
    if phase in _TERMINAL:
        fields.update(status=phase, finished_at=now)
        if error_message is not None:
            fields['error_message'] = error_message
    if label is not None:
        fields['stage'] = label
    db.update_job(job_id, **fields)


def counter(job_id, session_id, phase, unit):
    """Sample throughput on completed work and coalesce fast cache/callback bursts."""
    last_count = last_event = last_write = written_count = None
    written_label = None
    measured_units = measured_seconds = 0.0
    rate = interval = None

    def update(label, completed, total, *, force=False, reset_rate=False, **metadata):
        nonlocal last_count, last_event, last_write, written_count, written_label
        nonlocal measured_units, measured_seconds, rate, interval
        if is_cancelled(session_id):
            raise InterruptedError('已取消')
        now = time.monotonic()
        if reset_rate or last_count is None or completed < last_count:
            last_count, last_event = completed, now
            measured_units = measured_seconds = 0.0
            rate = interval = None
        if completed > last_count:
            delta = now - last_event
            # Immediate checkpoints are real coverage but provide no speed sample.
            if delta >= 0.05:
                keep = min(1.0, 30 / measured_seconds) if measured_seconds else 1.0
                measured_units = measured_units * keep + completed - last_count
                measured_seconds = measured_seconds * keep + delta
                if measured_seconds >= 1:
                    rate, interval = measured_units / measured_seconds, delta
            last_count, last_event = completed, now
        if completed == written_count and written_label == label and not reset_rate:
            return
        final = _positive(total) and completed >= total
        milestone = _positive(total) and written_count is not None and completed - written_count >= total / 10
        if (force or last_write is None or completed == written_count or written_count == 0
                or final or milestone or now - last_write >= 2):
            report(job_id, phase, label, completed=completed, total=total, unit=unit,
                   rate_per_second=rate, sample_interval=interval, **metadata)
            last_write, written_count, written_label = now, completed, label
    return update


def transcript_signature(session, course, settings, language=None):
    from . import db
    files = []
    for entry in db.audio_files_for_session(session):
        path = entry.get('path') or ''
        try:
            stat = os.stat(path)
            stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamp = None
        files.append((path, entry.get('hash'), stamp))
    payload = [session.get('audio_hash'), files, course.get('language_mode'), language or course.get('language_mode', 'auto'), course.get('vocabulary'),
               settings.get('whisper_device', 'auto')]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def resume_details(session, job, settings=None):
    from . import db
    if not session or not job or job.get('status') not in {'failed', 'cancelled'}:
        return None
    settings = db.get_all_settings() if settings is None else settings
    data = progress(job)
    segments = db.read_conn().execute(
        'SELECT 1 FROM transcript_segments WHERE session_id=? LIMIT 1', (session['id'],)
    ).fetchone() is not None
    course = db.get_course(session['course_id']) or {}
    language = (data.get('effective_language') if data.get('course_language_mode') == course.get('language_mode')
                else course.get('language_mode')) or course.get('language_mode', 'auto')
    ready = (data.get('transcript_complete') and
             data.get('transcript_signature') == transcript_signature(session, course, settings, language))
    mode = 'analyze' if segments and (session.get('source_type') == 'text' or ready) else 'transcribe'
    if mode == 'transcribe' and not session.get('audio_path'):
        return None
    return {'mode': mode, 'label': '继续整理' if mode == 'analyze' else '继续识别',
            'language': language, 'checkpoint_epoch': data.get('checkpoint_epoch') or job['id']}
