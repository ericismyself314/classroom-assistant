"""课堂处理流水线（协调转写与分析）。

流程：录音导入 -> 检查文件 -> 本地语音识别 -> 生成带时间戳转写 -> 本地文字模型分析 ->
保存课堂概览 -> 保存重要事项 -> 建立搜索索引 -> 完成。

约束：
- 同一时间默认只处理一条长录音（由 API 层用 active_jobs 强制）。
- 支持取消（jobs.is_cancelled）、失败后保留错误原因、应用重启后由 mark_interrupted_jobs 标记中断任务。
- 转写结果分批已在 whisper_worker 内顺序产生，这里整段写入数据库（课堂录音通常可控；如需超长可改流式）。
"""
from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager

from . import audio_archive, db, jobs, hashutil
from .config import get_config
from .logutil import get_logger
from .ai import llm_provider, manifest as aimanifest, segment_analysis, whisper_worker
from .ai import verification
from .ai.provider import build_provider
from .ai.translation_provider import build_translation_provider
from .ai.inference_cache import InferenceCache

LOG = get_logger("pipeline")


@contextmanager
def _timed_stage(session_id, stage):
    start = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        LOG.info("stage_timing session=%s stage=%s seconds=%.2f outcome=%s",
                 session_id, stage, time.monotonic() - start, outcome)


def _resolve_whisper_size(tier_or_size, settings: dict) -> str:
    # 旧数据库可能仍保存 fast/balanced/accurate；升级后无条件使用唯一的 Qwen ASR。
    return aimanifest.WHISPER_TIERS[aimanifest.DEFAULT_WHISPER_TIER]["model_size"]


def _transcribe_audio_files(session, course, settings, whisper_size, language, job_id, checkpoint_namespace=None):
    """Transcribe all recordings in order and shift later timestamps forward."""
    files = db.audio_files_for_session(session)
    if not files:
        raise FileNotFoundError("找不到原始录音文件，可能已被移动或删除")
    for entry in files:
        if not entry.get("path") or not os.path.exists(entry["path"]):
            raise FileNotFoundError(f"找不到录音文件：{entry.get('name') or entry.get('path') or '未知文件'}")

    durations = []
    for entry in files:
        duration = hashutil.audio_duration_seconds(entry["path"])
        if duration is None and entry.get("duration") is not None:
            try:
                duration = float(entry["duration"])
            except (TypeError, ValueError):
                duration = None
        durations.append(duration if duration and math.isfinite(duration) and duration > 0 else None)
    total_duration = sum(durations) if all(d is not None for d in durations) else None
    if total_duration is not None:
        db.update_session(session["id"], audio_duration_seconds=total_duration)
        session["audio_duration_seconds"] = total_duration

    device_pref = (settings.get("whisper_device") or "auto").lower()
    requested_language = language or course.get("language_mode", "auto")
    all_segments = []
    detected_languages = []
    offset = 0.0
    used_device = "cpu"
    report_audio = jobs.counter(job_id, session["id"], 'transcribe', 'audio_seconds')
    for index, entry in enumerate(files):
        if jobs.is_cancelled(session["id"]):
            raise InterruptedError("已取消")
        file_duration = durations[index]
        part = f"第 {index + 1}/{len(files)} 段录音"
        report_audio(f"正在读取{part}并加载语音模型", offset, total_duration, force=True,
                     audio_seconds=total_duration)
        last_second = 0.0
        stage = f"语音识别中 · {part}"

        def on_audio(seconds):
            nonlocal last_second
            if math.isfinite(seconds):
                last_second = max(last_second, min(seconds, file_duration) if file_duration else seconds)
                report_audio(stage, offset + last_second, total_duration)

        def on_retry(_reason, _message):
            nonlocal last_second, stage
            last_second = 0.0
            stage = f"语音识别中 · 已切换 CPU 重试{part}"
            report_audio(stage, offset, total_duration, reset_rate=True, force=True, asr_cpu_retry=True)

        segments, detected, used_device = whisper_worker.transcribe(
            entry["path"], size=whisper_size, language=requested_language,
            device_pref=device_pref,
            vocabulary=course.get("vocabulary", ""),
            checkpoint_dir=os.path.join(get_config().dirs.transcripts, session['id'], 'asr-checkpoints'),
            checkpoint_namespace=checkpoint_namespace,
            progress_cb=on_audio, on_retry=on_retry,
        )
        detected_languages.append(detected)
        for segment in segments:
            shifted = dict(segment)
            if shifted.get("start_seconds") is not None:
                shifted["start_seconds"] = round(float(shifted["start_seconds"]) + offset, 2)
            if shifted.get("end_seconds") is not None:
                shifted["end_seconds"] = round(float(shifted["end_seconds"]) + offset, 2)
            all_segments.append(shifted)
        max_end = max((float(s.get("end_seconds") or 0) for s in segments), default=0.0)
        offset += file_duration if file_duration is not None else max(max_end, last_second)
        report_audio(stage, offset, total_duration, force=True)

    if not all_segments:
        raise ValueError("这些录音中没有识别到清晰的人声。请先播放录音确认有声音，再尝试重新转写。")
    if requested_language == "mix" or len(set(detected_languages)) > 1:
        detected_language = "mix"
    else:
        detected_language = detected_languages[0] if detected_languages else None
    return all_segments, detected_language, used_device


def run_pipeline(session_id, mode="transcribe", whisper_tier=None, language=None,
                 settings=None, job_id=None) -> dict:
    session = db.get_session(session_id)
    if not session:
        raise ValueError("课堂记录不存在")
    course = db.get_course(session["course_id"])
    if not course:
        raise ValueError("课程不存在")
    settings = settings or db.get_all_settings()
    whisper_size = _resolve_whisper_size(whisper_tier, settings)
    llm_model_id = settings.get("llm_model_id") or aimanifest.default_llm_model()["id"]
    llm_provider.set_cancel_check(lambda: jobs.is_cancelled(session_id))
    job = db.get_job(job_id) if job_id else None
    language = language or jobs.progress(job).get('effective_language') or course.get('language_mode', 'auto')
    jobs.report(job_id, effective_language=language, course_language_mode=course.get('language_mode'))
    if job and not job.get('started_at'):
        db.update_job(job_id, started_at=db.now_iso())
    run_epoch = jobs.progress(job).get('checkpoint_epoch')
    previous_success = db.read_conn().execute(
        "SELECT id FROM processing_jobs WHERE session_id=? AND status='completed' "
        "ORDER BY finished_at DESC, rowid DESC LIMIT 1", (session_id,),
    ).fetchone()
    namespace = {'run_epoch': run_epoch} if run_epoch else {
        'after_completed_job': previous_success['id'] if previous_success else None}

    try:
        jobs.start_progress(job, mode, settings, session.get('audio_duration_seconds'))
        db.snapshot_session(session_id, "重新转写前" if mode in ("transcribe", "retranscribe") else "重新分析前")
        if mode in ("transcribe", "retranscribe"):
            db.update_session(session_id, status="file_check", processing_error=None)
            db.update_job(job_id, status="file_check", stage="检查音频文件")
            jobs.report(job_id, 'file_check', '检查音频文件', transcript_complete=False)
            db.update_session(session_id, status="transcribing")
            db.update_job(job_id, status="transcribing",
                          stage="正在加载本地语音模型并读取录音，请稍候")
            jobs.report(job_id, 'transcribe', '读取录音并加载本地语音模型')
            with _timed_stage(session_id, "transcribe"):
                segments, lang, device = _transcribe_audio_files(
                    session, course, settings, whisper_size, language, job_id, namespace)
            db.replace_segments(session_id, segments)
            segments = db.list_segments(session_id)
            db.update_session(session_id, transcript_language=lang,
                              transcription_model=f"Qwen3-ASR-0.6B/{device}", status="analyzing")
            # Do not display notes/evidence from the previous transcript as current.
            db.replace_topics(session_id, [])
            db.replace_items(session_id, [])
            db.update_session(session_id, notes_data=None)
            db.index_session(session_id)
        else:
            segments = db.list_segments(session_id)
            if not segments:
                raise ValueError("没有可分析的转写内容（请先转写或导入文字）")
            if session.get("source_type") == "audio" and session.get("transcript_language") not in {"zh", "en", "mix", None}:
                raise ValueError("旧转写的语言不符合中英文课堂，请先选择正确授课语言并「重新转写」。仅重新分析不能修复识别错误。")

        meta = {
            "session_date": session["session_date"],
            "course_name": course["name"],
            "language_mode": course.get("language_mode", "auto"),
            "transcript_language": db.get_session(session_id).get("transcript_language"),
        }
        needs_translation = segment_analysis.needs_chinese_display(segments, meta)
        jobs.report(job_id, transcript_complete=True, translation_required=needs_translation,
                    source_chars=sum(len(s.get('text') or '') for s in segments),
                    transcript_signature=jobs.transcript_signature(session, course, settings, language))

        db.update_session(session_id, status="analyzing", processing_error=None)
        db.update_job(job_id, status="analyzing", stage="课堂分析中")
        jobs.report(job_id, 'load_text', '加载本地文字模型')
        with _timed_stage(session_id, "release_asr"):
            whisper_worker.release_model()
        with _timed_stage(session_id, "provider_start"):
            provider = build_provider(settings)
            if isinstance(provider, llm_provider.LlamaCppSidecarLLM):
                provider.start()
        cache = InferenceCache(
            os.path.join(get_config().dirs.transcripts, session_id, "inference-cache"),
            namespace={"settings": settings, "generation": {"max_tokens": 2048}, **namespace},
        )

        total_segments = len(segments)
        report_translation = jobs.counter(job_id, session_id, 'translate', 'segments')
        report_analysis = jobs.counter(job_id, session_id, 'analyze', 'segments')
        report_synthesis = jobs.counter(job_id, session_id, 'synthesize', 'operation')

        def translation_progress(label, fraction):
            report_translation(label, round(fraction * total_segments), total_segments)

        def analysis_progress(label, fraction):
            if label.startswith('合并'):
                report_analysis('分段整理完成', total_segments, total_segments)
                report_synthesis(label, 0, 1)
            elif label == 'analyzing':
                report_synthesis('全课笔记已合并', 1, 1)
            else:
                report_analysis(label, round(fraction * total_segments), total_segments)
        translation_provider = provider
        try:
            with _timed_stage(session_id, "translate"):
                if needs_translation:
                    translation_progress('准备中文展示稿', 0)
                    translation_provider = build_translation_provider(settings, provider)
                    if translation_provider is not provider and isinstance(translation_provider, llm_provider.LlamaCppSidecarLLM):
                        translation_provider.start()
                display_segments = segment_analysis.translate_for_display(
                    translation_provider, segments, meta, cache=cache,
                    progress_cb=translation_progress,
                )
            db.update_segment_display_texts(session_id, display_segments)
            with _timed_stage(session_id, "analyze"):
                report_analysis('整理课堂内容', 0, total_segments)
                overview, items = segment_analysis.analyze_session(
                    provider, display_segments, meta, cache=cache,
                    progress_cb=analysis_progress,
                )
        finally:
            if translation_provider is not provider and isinstance(translation_provider, llm_provider.LlamaCppSidecarLLM):
                translation_provider.stop()
            if isinstance(provider, llm_provider.LlamaCppSidecarLLM):
                provider.stop()

        db.update_session(session_id, status="indexing")
        if jobs.is_cancelled(session_id):
            raise InterruptedError('已取消')
        db.update_job(job_id, status="indexing", stage="保存结果并建立搜索索引")
        jobs.report(job_id, 'verify', '核查重要事项与来源')

        # ---- 第五阶段：5 层证据核查关卡（模型输出之后的独立验证）----
        # 即使模型自信地返回一条通知，也要逐条独立核查：结构 / 原文存在性 / 上下文 / 语言信号 / 展示规则。
        # confirmed + pending 进入正式列表；rejected 仍入库（便于调试）但默认隐藏。
        audio_duration = session.get("audio_duration_seconds")
        vres = verification.apply_verification(
            items, display_segments, {"audio_duration_seconds": audio_duration}
        )
        LOG.info(
            "证据核查 session=%s confirmed=%d pending=%d rejected=%d",
            session_id, len(vres["confirmed"]), len(vres["pending"]), len(vres["rejected"]),
        )

        items_db = [
            {**it, "session_id": session_id, "course_id": session["course_id"]}
            for it in (vres["confirmed"] + vres["pending"] + vres["rejected"])
        ]
        # The visible non-knowledge section uses the same verified items as the
        # task list.  Keep the Chinese teacher wording/details and real source
        # evidence together so the reader can jump back to the audio.
        notes = dict(overview["notes"] or {})
        notes["non_knowledge"] = [
            {k: it.get(k) for k in (
                "category", "title", "details", "raw_date_text", "normalized_date",
                "action_required", "confidence", "verification_status", "evidence",
            )}
            for it in (vres["confirmed"] + vres["pending"])
        ]
        if jobs.is_cancelled(session_id):
            raise InterruptedError('已取消')
        if notes.get("title") and (not session.get("title") or session.get("title") == course.get("name")):
            db.update_session(session_id, title=notes["title"])
        jobs.report(job_id, 'save', '保存结果并建立搜索索引')
        db.save_analysis(session_id, overview["topics"], items_db, notes, provider.model_label())
        jobs.report(job_id, 'completed', '处理完成', topics=len(overview['topics']), items=len(items))
        jobs.clear_cancel(session_id)
        audio_archive.wake()
        LOG.info("处理完成 session=%s topics=%d items=%d", session_id, len(overview["topics"]), len(items))
        return {"status": "completed", "topics": len(overview["topics"]), "items": len(items)}

    except InterruptedError:
        jobs.report(job_id, 'cancelled', '已取消')
        db.update_session(session_id, status="cancelled", processing_error="用户已取消")
        jobs.clear_cancel(session_id)
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        LOG.exception("处理失败 session=%s", session_id)
        jobs.report(job_id, 'failed', '处理失败', error_message=msg)
        db.update_session(session_id, status="failed", processing_error=msg)
        jobs.clear_cancel(session_id)
        return {"status": "failed", "error": msg}
