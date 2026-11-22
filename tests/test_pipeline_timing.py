import logging

from backend import db, pipeline


def test_pipeline_records_stage_timings_without_transcript_or_path(tmp_path, monkeypatch, caplog):
    sem = db.create_semester('fall', '2026-09-01', '2027-01-31')
    course = db.create_course(sem, 'test course', language_mode='en')
    sid = db.get_or_create_session(course, '2026-09-09')
    job = db.create_job(sid, 'transcribe', status='running')
    recording = tmp_path / 'private-recording.wav'
    recording.write_bytes(b'fake')
    db.update_session(sid, audio_path=str(recording), source_type='audio')
    segment = {'text': 'private transcript words', 'original_text': 'private transcript words',
               'start_seconds': 0.0, 'end_seconds': 1.0, 'language': 'en',
               'confidence': None, 'is_uncertain': False}
    monkeypatch.setattr(pipeline, '_transcribe_audio_files', lambda *args: ([segment], 'en', 'cuda'))
    monkeypatch.setattr(pipeline, 'build_provider', lambda settings: type('Provider', (), {'model_label': lambda self: 'fake'})())
    monkeypatch.setattr(pipeline.segment_analysis, 'translate_for_display',
                        lambda provider, segments, meta, progress_cb, **kw: [
                            {**s, 'text': '课堂译文', 'display_text': '课堂译文'} for s in segments])
    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session',
                        lambda provider, segments, meta, progress_cb, **kw: ({'topics': [], 'notes': {}}, []))
    caplog.set_level(logging.INFO, logger='classroom_assistant.pipeline')

    result = pipeline.run_pipeline(sid, settings={'ai_provider': 'sidecar'}, job_id=job)

    assert result['status'] == 'completed'
    saved, = db.list_segments(sid)
    assert (saved['text'], saved['display_text']) == ('private transcript words', '课堂译文')
    records = [r.getMessage() for r in caplog.records if 'stage_timing' in r.getMessage()]
    assert all(any('stage=' + stage in row and 'seconds=' in row for row in records)
               for stage in ('transcribe', 'provider_start', 'translate', 'analyze'))
    assert all('private transcript words' not in row and 'private-recording.wav' not in row for row in records)


def test_failed_stage_is_timed_without_logging_error_contents(caplog):
    caplog.set_level(logging.INFO, logger='classroom_assistant.pipeline')
    try:
        with pipeline._timed_stage('test-session', 'analyze'):
            raise ValueError('private details')
    except ValueError:
        pass
    else:
        raise AssertionError('stage failure must propagate')
    messages = [r.getMessage() for r in caplog.records if 'stage_timing' in r.getMessage()]
    assert len(messages) == 1
    assert 'stage=analyze' in messages[0] and 'outcome=error' in messages[0]
    assert 'private details' not in messages[0]


def test_translation_api_only_receives_numbered_text_and_notes_use_existing_provider(monkeypatch):
    sem = db.create_semester('fall', '2026-09-01', '2027-01-31')
    course = db.create_course(sem, 'private course name', language_mode='en')
    sid = db.get_or_create_session(course, '2026-09-09')
    db.replace_segments(sid, [{'text': 'An example sentence for class.', 'start_seconds': 42.3,
                               'end_seconds': 48.7, 'is_uncertain': True}])
    db.update_session(sid, transcript_language='en')
    organizer = type('Organizer', (), {'model_label': lambda self: 'local'})()
    requests = []
    class Translator:
        name = 'translation_api'
        def model_label(self):
            return 'example'
        def chat(self, system, user, **kwargs):
            requests.append(user)
            return '{"translations":[{"index":0,"text":"课堂示例。"}]}'
    monkeypatch.setattr(pipeline, 'build_provider', lambda settings: organizer)
    monkeypatch.setattr(pipeline, 'build_translation_provider', lambda settings, existing: Translator())
    def notes(provider, segments, meta, **kwargs):
        assert provider is organizer
        assert segments[0]['text'] == '课堂示例。'
        return {'topics': [], 'notes': {}}, []
    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session', notes)
    result = pipeline.run_pipeline(sid, mode='analyze', settings={'translation_provider': 'api'},
                                   job_id=db.create_job(sid, 'analyze', status='running'))
    assert result['status'] == 'completed'
    prompt, = requests
    assert '[0] An example sentence for class.' in prompt
    assert all(private not in prompt for private in ('private course name', '42.3', '48.7', '2026-09-09', '听辨存疑'))
    assert db.list_segments(sid)[0]['display_text'] == '课堂示例。'


def test_pipeline_passes_same_session_cache_to_translation_and_analysis(monkeypatch):
    sem = db.create_semester('cache semester', '2026-09-01', '2027-01-31')
    course = db.create_course(sem, 'cache course', language_mode='zh')
    sid = db.get_or_create_session(course, '2026-09-09')
    job = db.create_job(sid, 'analyze', status='running')
    db.replace_segments(sid, [{'text': '合成课堂内容', 'start_seconds': 0, 'end_seconds': 1}])
    provider = type('Provider', (), {'model_label': lambda self: 'fake'})()
    monkeypatch.setattr(pipeline, 'build_provider', lambda settings: provider)
    caches = []

    def translate(provider, segments, meta, progress_cb=None, cache=None):
        caches.append(cache)
        return segments

    def analyze(provider, segments, meta, progress_cb=None, cache=None):
        caches.append(cache)
        return {'topics': [], 'notes': {}}, []

    monkeypatch.setattr(pipeline.segment_analysis, 'translate_for_display', translate)
    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session', analyze)
    result = pipeline.run_pipeline(sid, mode='analyze', settings={'ai_provider': 'sidecar'}, job_id=job)

    assert result['status'] == 'completed'
    assert len(caches) == 2 and caches[0] is not None
    assert caches[0] is caches[1]


def test_pipeline_releases_asr_before_loading_text_model(monkeypatch):
    sem = db.create_semester('handoff semester', '2026-09-01', '2027-01-31')
    course = db.create_course(sem, 'handoff course', language_mode='zh')
    sid = db.get_or_create_session(course, '2026-09-09')
    job = db.create_job(sid, 'analyze', status='running')
    db.replace_segments(sid, [{'text': '合成课堂内容', 'start_seconds': 0, 'end_seconds': 1}])
    events = []
    monkeypatch.setattr(pipeline.whisper_worker, 'release_model', lambda: events.append('release_asr'), raising=False)

    def provider(settings):
        events.append('load_text')
        return type('Provider', (), {'model_label': lambda self: 'fake'})()

    monkeypatch.setattr(pipeline, 'build_provider', provider)
    monkeypatch.setattr(pipeline.segment_analysis, 'translate_for_display', lambda p, s, m, **kw: s)
    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session', lambda *args, **kw: ({'topics': [], 'notes': {}}, []))
    pipeline.run_pipeline(sid, mode='analyze', settings={'ai_provider': 'sidecar'}, job_id=job)

    assert events == ['release_asr', 'load_text']


def test_completed_analysis_starts_fresh_but_interrupted_retry_reuses_cache(monkeypatch):
    sem = db.create_semester('retry semester', '2026-09-01', '2027-01-31')
    course = db.create_course(sem, 'retry course', language_mode='zh')
    sid = db.get_or_create_session(course, '2026-09-09')
    db.replace_segments(sid, [{'text': '合成课堂内容', 'start_seconds': 0, 'end_seconds': 1}])
    monkeypatch.setattr(pipeline, 'build_provider', lambda settings: type('Provider', (), {'model_label': lambda self: 'fake'})())
    monkeypatch.setattr(pipeline.segment_analysis, 'translate_for_display', lambda p, s, m, **kw: s)
    namespaces = []
    interrupt = [False]

    def analyze(*args, cache=None, **kwargs):
        namespaces.append(cache.namespace)
        if interrupt[0]:
            raise InterruptedError('synthetic interruption')
        return {'topics': [], 'notes': {}}, []

    monkeypatch.setattr(pipeline.segment_analysis, 'analyze_session', analyze)
    first = db.create_job(sid, 'analyze', status='running')
    assert pipeline.run_pipeline(sid, mode='analyze', settings={}, job_id=first)['status'] == 'completed'
    interrupt[0] = True
    second = db.create_job(sid, 'analyze', status='running')
    assert pipeline.run_pipeline(sid, mode='analyze', settings={}, job_id=second)['status'] == 'cancelled'
    interrupt[0] = False
    third = db.create_job(sid, 'analyze', status='running')
    assert pipeline.run_pipeline(sid, mode='analyze', settings={}, job_id=third)['status'] == 'completed'
    assert namespaces[0] != namespaces[1]
    assert namespaces[1] == namespaces[2]
