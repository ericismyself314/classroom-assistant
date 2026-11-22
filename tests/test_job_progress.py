import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend import db, jobs, pipeline
from backend.ai import segment_analysis


def lesson():
    semester = db.create_semester('synthetic', '2026-09-01', '2027-01-31')
    course = db.create_course(semester, 'synthetic course', language_mode='zh')
    sid = db.get_or_create_session(course, '2026-11-13')
    return sid, db.get_course(course)


def test_progress_tracks_actual_phase_time_and_keeps_checkpoint(monkeypatch):
    sid, _ = lesson()
    jid = db.create_job(sid, 'transcribe')
    db.update_job(jid, progress_data=json.dumps({'checkpoint_epoch': 'original-run'}))
    now = ['2026-11-13T00:00:00+00:00']
    monkeypatch.setattr(db, 'now_iso', lambda: now[0])
    jobs.report(jid, 'transcribe', '识别第 1 段', completed=1, total=3)
    now[0] = '2026-11-13T00:02:00+00:00'
    jobs.report(jid, 'analyze', '整理第 1 块', completed=0, total=2)
    progress = jobs.progress(db.get_job(jid))
    assert progress['checkpoint_epoch'] == 'original-run'
    assert progress['phase_durations']['transcribe'] == 120
    assert progress['phase_started_at'] == now[0]
    assert progress['completed'] == 0 and progress['total'] == 2


def test_resume_never_treats_old_transcript_as_new_completed_transcription(tmp_path):
    sid, course = lesson()
    path = tmp_path / 'recording.wav'
    path.write_bytes(b'synthetic audio')
    db.update_session(sid, source_type='audio', audio_path=str(path), audio_hash='synthetic')
    db.replace_segments(sid, [{'text': 'old source', 'start_seconds': 0, 'end_seconds': 1}])
    jid = db.create_job(sid, 'transcribe', status='failed')
    session = db.get_session(sid)
    assert jobs.resume_details(session, db.get_job(jid), {})['mode'] == 'transcribe'
    signature = jobs.transcript_signature(session, course, {})
    db.update_job(jid, progress_data=json.dumps({'transcript_complete': True, 'transcript_signature': signature}))
    assert jobs.resume_details(session, db.get_job(jid), {})['mode'] == 'analyze'
    db.update_course(course['id'], language_mode='en')
    assert jobs.resume_details(session, db.get_job(jid), {})['mode'] == 'transcribe'


def test_legacy_failed_analysis_does_not_prove_old_audio_transcript_is_current(tmp_path):
    sid, _ = lesson()
    audio = tmp_path / 'new.wav'
    audio.write_bytes(b'new audio')
    db.update_session(sid, source_type='audio', audio_path=str(audio))
    db.replace_segments(sid, [{'text': 'old source', 'start_seconds': 0, 'end_seconds': 1}])
    jid = db.create_job(sid, 'analyze', status='failed')
    assert jobs.resume_details(db.get_session(sid), db.get_job(jid), {})['mode'] == 'transcribe'


@pytest.fixture
def clock(monkeypatch):
    ticks = [0]
    epoch = datetime(2026, 11, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(jobs, 'time', SimpleNamespace(monotonic=lambda: ticks[0]))
    monkeypatch.setattr(db, 'now_iso', lambda: (epoch + timedelta(seconds=ticks[0])).isoformat())
    return ticks


def test_first_local_run_estimates_after_real_work_and_never_reports_100_until_saved(clock):
    sid, _ = lesson()
    jid = db.create_job(sid, 'transcribe', status='transcribing')
    jobs.start_progress(db.get_job(jid), 'transcribe', {'ai_provider': 'sidecar'}, 100)
    observed = []

    def snapshot():
        state = jobs.progress(db.get_job(jid))
        observed.append(state['overall_percent'])
        return state

    jobs.report(jid, 'file_check')
    assert snapshot()['eta_seconds'] is None
    audio = jobs.counter(jid, sid, 'transcribe', 'audio_seconds')
    audio('读取录音', 0, 100)
    assert snapshot()['rate_per_second'] is None
    clock[0] = 10
    audio('语音识别', 40, 100)
    mid = snapshot()
    assert mid['rate_per_second'] == 4 and mid['phase_eta_seconds'] == 15
    assert mid['eta_seconds'] > 15 and mid['eta_basis'] == 'reference'
    clock[0] = 25
    audio('语音识别', 100, 100)
    snapshot()
    jobs.report(jid, translation_required=False, source_chars=42)
    assert 'translate' not in snapshot()['plan']
    for phase in ['load_text', 'analyze', 'synthesize', 'verify', 'save']:
        jobs.report(jid, phase)
        snapshot()
    assert observed == sorted(observed) and max(observed) == 99
    assert db.get_job(jid)['status'] != 'completed'
    jobs.report(jid, 'completed')
    assert db.get_job(jid)['status'] == 'completed' and snapshot()['overall_percent'] == 100


def test_api_progress_on_first_analysis_only_run_never_uses_local_reference_to_time_an_api(clock):
    sid, _ = lesson()
    jid = db.create_job(sid, 'analyze', status='analyzing')
    jobs.start_progress(db.get_job(jid), 'analyze', {'translation_provider': 'api'})
    updates = jobs.counter(jid, sid, 'translate', 'segments')
    updates('翻译', 0, 12)
    assert jobs.progress(db.get_job(jid))['eta_seconds'] is None
    clock[0] = 6
    updates('翻译', 3, 12)
    state = jobs.progress(db.get_job(jid))
    assert 'transcribe' not in state['plan'] and state['overall_percent'] < 25
    assert state['phase_eta_seconds'] == 18 and state['eta_seconds'] is None
    assert state['rate_per_second'] == 0.5 and state['translation_provider'] == 'api'


def test_history_uses_same_translation_service_and_scales_to_current_text(clock):
    sid, _ = lesson()
    settings = {'translation_provider': 'api', 'translation_base_url': 'https://one.example/v1',
                'translation_model': 'model-a'}
    old = db.create_job(sid, 'analyze', status='analyzing')
    jobs.start_progress(db.get_job(old), 'analyze', settings)
    jobs.report(old, 'translate', completed=0, total=4, source_chars=20)
    clock[0] = 20
    jobs.report(old, 'analyze')
    clock[0] = 30
    jobs.report(old, 'synthesize')
    clock[0] = 35
    jobs.report(old, 'completed')
    malformed = db.create_job(sid, 'analyze', status='completed')
    db.update_job(malformed, progress_data='{"engines":"obsolete","phase_durations":[]}')

    def from_history(patch):
        jid = db.create_job(sid, 'analyze')
        jobs.start_progress(db.get_job(jid), 'analyze', {**settings, **patch})
        jobs.report(jid, 'translate', completed=0, total=8, source_chars=40)
        return jobs.progress(db.get_job(jid))

    same = from_history({})
    assert same['phase_eta_seconds'] == 40 and same['eta_seconds'] == 65 and same['eta_basis'] == 'history'
    for patch in [{'translation_model': 'model-b'}, {'translation_base_url': 'https://two.example/v1'},
                  {'translation_provider': 'local'}]:
        changed = from_history(patch)
        assert 'translate' not in changed['history']
        assert changed['phase_eta_seconds'] is None and changed['eta_seconds'] is None


def test_quick_cache_hits_do_not_invent_speed_or_write_per_segment_and_cancel_freezes_counts(clock, monkeypatch):
    sid, _ = lesson()
    jid = db.create_job(sid, 'analyze', status='analyzing')
    jobs.report(jid, checkpoint_epoch='previous-attempt')
    jobs.start_progress(db.get_job(jid), 'analyze', {})
    report = jobs.report
    writes = []

    def capture(*args, **kwargs):
        writes.append(kwargs.get('completed'))
        return report(*args, **kwargs)

    monkeypatch.setattr(jobs, 'report', capture)
    updates = jobs.counter(jid, sid, 'translate', 'segments')
    for done in range(803):
        updates('读取已处理片段', done, 1000)
    state = jobs.progress(db.get_job(jid))
    assert len(writes) < 15 and 800 <= state['completed'] <= 802
    assert state['resumed'] and state['rate_per_second'] is None and state['eta_seconds'] is None
    clock[0] = 6
    updates('翻译', 805, 1000)
    state = jobs.progress(db.get_job(jid))
    assert state['rate_per_second'] == 0.5
    jobs.request_cancel(sid)
    try:
        with pytest.raises(InterruptedError):
            updates('翻译', 806, 1000)
        jobs.report(jid, 'cancelled')
    finally:
        jobs.clear_cancel(sid)
    stopped = jobs.progress(db.get_job(jid))
    assert stopped['stopped_phase'] == 'translate' and stopped['completed'] == 805
    assert stopped['overall_percent'] == state['overall_percent'] < 100
    assert stopped['eta_seconds'] is None and db.get_job(jid)['finished_at']


def test_translation_counts_partial_replies_retries_and_source_fallback_once_each(monkeypatch):
    segments = [{'text': 'English sample'} for _ in range(5)]
    def chunk(_provider, rows, _offset, **_kwargs):
        return {0: '甲', 2: '乙'} if len(rows) == 5 else {1: '丙'}
    monkeypatch.setattr(segment_analysis, '_translate_chunk', chunk)
    monkeypatch.setattr(segment_analysis, '_translate_one', lambda _p, _s, i, **_: '丁' if i == 1 else None)
    counts = []
    display = segment_analysis.translate_for_display(
        object(), segments, {'transcript_language': 'en'}, progress_cb=lambda _, f: counts.append(round(5 * f)))
    assert counts == [0, 2, 3, 4, 5, 5]
    assert len(display) == 5 and sum(bool(s['display_text']) for s in display) == 4


def test_multiple_unknown_audio_durations_keep_coverage_and_trailing_silence_on_retry(tmp_path, monkeypatch):
    sid, course = lesson()
    files = []
    for i in range(2):
        path = tmp_path / f'synthetic-{i}.wav'
        path.write_bytes(b'sample')
        files.append({'path': str(path), 'name': path.name})
    db.update_session(sid, audio_path=files[0]['path'], audio_manifest=json.dumps(files), source_type='audio')
    job = db.create_job(sid, 'transcribe', status='transcribing')
    jobs.start_progress(db.get_job(job), 'transcribe', {})
    monkeypatch.setattr(pipeline.hashutil, 'audio_duration_seconds', lambda _: None)
    reports = []
    def transcribe(path, progress_cb, on_retry, **kwargs):
        if path == files[0]['path']:
            progress_cb(6)
            on_retry('gpu', '')
            reports.append(jobs.progress(db.get_job(job)))
        progress_cb(10)
        return [{'text': 'synthetic', 'start_seconds': 0, 'end_seconds': 2}], 'en', 'cpu'
    monkeypatch.setattr(pipeline.whisper_worker, 'transcribe', transcribe)
    segments, _, _ = pipeline._transcribe_audio_files(db.get_session(sid), course, {}, 'test', 'en', job)
    assert segments[1]['start_seconds'] == 10 and reports[0]['completed'] == 0
    state = jobs.progress(db.get_job(job))
    assert state['total'] is None and state['completed'] == 20 and state['phase_eta_seconds'] is None
    assert '2/2' in db.get_job(job)['stage']


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_pipeline_reports_api_batches_before_requests_and_honors_stop_before_save(outcome, clock, monkeypatch):
    from backend.ai.llm_provider import TranslationAPIError

    sid, _ = lesson()
    db.update_session(sid, source_type='text', transcript_language='en')
    db.replace_segments(sid, [{'text': ('synthetic ' * 126) + str(i)} for i in range(3)])
    jid = db.create_job(sid, 'analyze', status='analyzing')
    local = SimpleNamespace(model_label=lambda: 'synthetic local')
    monkeypatch.setattr(pipeline, 'build_provider', lambda _: local)
    samples = []

    class Translation:
        name = 'translation_api'

        def chat(self, *_args, **_kwargs):
            samples.append(jobs.progress(db.get_job(jid)))
            clock[0] += 4
            if len(samples) == 2 and outcome == 'failed':
                raise TranslationAPIError('合成服务故障')
            count = 2 if len(samples) == 1 else 1
            return json.dumps({'translations': [{'index': i, 'text': f'合成翻译{i}'} for i in range(count)]})

    monkeypatch.setattr(pipeline, 'build_translation_provider', lambda *_: Translation())

    def analyze(provider, segments, _meta, progress_cb, **_kwargs):
        assert provider is local and len(segments) == 3
        assert jobs.progress(db.get_job(jid))['phase'] == 'analyze'
        progress_cb('整理第 1/1 块', 0)
        clock[0] += 3
        progress_cb('合并笔记与课程要求', 0.95)
        merging = jobs.progress(db.get_job(jid))
        assert merging['phase'] == 'synthesize' and merging['completed'] == 0 and merging['total'] == 1
        progress_cb('analyzing', 1)
        return {'topics': [], 'notes': {}}, []

    monkeypatch.setattr(segment_analysis, 'analyze_session', analyze)
    if outcome == 'cancelled':
        def stop_during_verify(*args):
            jobs.request_cancel(sid)
            return {'confirmed': [], 'pending': [], 'rejected': []}
        monkeypatch.setattr(pipeline.verification, 'apply_verification', stop_during_verify)

    result = pipeline.run_pipeline(sid, mode='analyze', settings={'translation_provider': 'api'}, job_id=jid)
    assert result['status'] == outcome and db.get_job(jid)['status'] == outcome
    assert [(s['phase'], s['completed'], s['total']) for s in samples] == [('translate', 0, 3), ('translate', 2, 3)]
    assert samples[1]['translation_provider'] == 'api' and samples[1]['overall_percent'] > samples[0]['overall_percent']
    finish = jobs.progress(db.get_job(jid))
    assert finish['eta_seconds'] is None
    if outcome == 'completed':
        assert finish['overall_percent'] == 100
    else:
        assert finish['overall_percent'] < 100 and db.get_session(sid)['notes_data'] is None
        assert finish['stopped_phase'] == ('translate' if outcome == 'failed' else 'verify')
