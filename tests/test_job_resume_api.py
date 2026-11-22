from backend import api, db, jobs, pipeline


def test_resume_keeps_epoch_and_uses_only_its_valid_completed_transcript(tmp_path, monkeypatch):
    class IdleThread:
        def __init__(self, **kwargs): pass
        def start(self): pass

    monkeypatch.setattr(api.threading, 'Thread', IdleThread)
    semester = db.create_semester('synthetic', '2026-09-01', '2027-01-31')
    cid = db.create_course(semester, 'synthetic', language_mode='zh')
    sid = db.get_or_create_session(cid, '2026-11-13')
    audio = tmp_path / 'recording.wav'
    audio.write_bytes(b'synthetic')
    db.update_session(sid, source_type='audio', audio_path=str(audio))
    first, error = api.start_pipeline(sid, 'transcribe')
    assert error is None
    db.update_job(first, status='failed')
    db.replace_segments(sid, [{'text': 'old source', 'start_seconds': 0, 'end_seconds': 1}])
    retry, error = api.start_pipeline(sid, 'resume')
    assert error is None
    assert db.get_job(retry)['job_type'] == 'transcribe'
    assert jobs.progress(db.get_job(retry))['checkpoint_epoch'] == first
    jobs.report(retry, transcript_complete=True,
                transcript_signature=jobs.transcript_signature(db.get_session(sid), db.get_course(cid), db.get_all_settings()))
    db.update_job(retry, status='failed')
    analysis, error = api.start_pipeline(sid, 'resume')
    assert error is None
    assert db.get_job(analysis)['job_type'] == 'analyze'
    assert jobs.progress(db.get_job(analysis))['checkpoint_epoch'] == first


def test_resume_does_not_silently_change_recognition_language():
    job, error = api.start_pipeline('unused', 'resume', language='en')
    assert job is None and '重新转写' in error


def test_resume_retains_explicit_language_and_honors_changed_course_language(tmp_path, monkeypatch):
    calls = []

    class IdleThread:
        def __init__(self, **kwargs): calls.append(kwargs['args'])
        def start(self): pass

    monkeypatch.setattr(api.threading, 'Thread', IdleThread)
    semester = db.create_semester('synthetic', '2026-09-01', '2027-01-31')
    cid = db.create_course(semester, 'synthetic', language_mode='auto')
    sid = db.get_or_create_session(cid, '2026-11-13')
    audio = tmp_path / 'recording.wav'
    audio.write_bytes(b'synthetic')
    db.update_session(sid, source_type='audio', audio_path=str(audio))
    first, _ = api.start_pipeline(sid, 'transcribe', language='en')
    db.update_job(first, status='failed')
    retry, _ = api.start_pipeline(sid, 'resume')
    assert calls[-1][3] == 'en'
    assert jobs.progress(db.get_job(retry))['effective_language'] == 'en'
    db.update_job(retry, status='failed')
    db.update_course(cid, language_mode='zh')
    api.start_pipeline(sid, 'resume')
    assert calls[-1][3] == 'zh'


def test_pipeline_resume_reuses_asr_epoch_but_explicit_retranscribe_is_fresh(tmp_path, monkeypatch):
    class IdleThread:
        def __init__(self, **kwargs): pass
        def start(self): pass

    monkeypatch.setattr(api.threading, 'Thread', IdleThread)
    semester = db.create_semester('synthetic', '2026-09-01', '2027-01-31')
    cid = db.create_course(semester, 'synthetic', language_mode='zh')
    sid = db.get_or_create_session(cid, '2026-11-13')
    audio = tmp_path / 'recording.wav'
    audio.write_bytes(b'synthetic')
    db.update_session(sid, source_type='audio', audio_path=str(audio))
    namespaces = []

    def transcribe(*args):
        namespaces.append(args[-1])
        if len(namespaces) == 1:
            raise InterruptedError('synthetic interruption')
        return [{'text': 'synthetic source', 'start_seconds': 0, 'end_seconds': 1}], 'zh', 'cpu'

    monkeypatch.setattr(pipeline, '_transcribe_audio_files', transcribe)
    monkeypatch.setattr(pipeline, 'build_provider', lambda settings: type('Provider', (), {'model_label': lambda self: 'fake'})())
    monkeypatch.setattr(pipeline.segment_analysis, 'translate_for_display', lambda p, s, m, **kw: s)
    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session', lambda *a, **kw: ({'topics': [], 'notes': {}}, []))
    first, _ = api.start_pipeline(sid, 'transcribe')
    assert pipeline.run_pipeline(sid, mode='transcribe', job_id=first)['status'] == 'cancelled'
    retry, _ = api.start_pipeline(sid, 'resume')
    assert pipeline.run_pipeline(sid, mode='transcribe', job_id=retry)['status'] == 'completed'
    redo, _ = api.start_pipeline(sid, 'retranscribe')
    assert pipeline.run_pipeline(sid, mode='retranscribe', job_id=redo)['status'] == 'completed'
    assert namespaces[0] == namespaces[1]
    assert namespaces[2] != namespaces[1]
