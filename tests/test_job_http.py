import json
import threading
import urllib.request

from backend import api, db, jobs


def test_http_resume_skips_asr_validation_for_verified_transcript_and_lists_job(tmp_path, monkeypatch):
    semester = db.create_semester('synthetic', '2026-09-01', '2027-01-31')
    cid = db.create_course(semester, 'synthetic course', language_mode='zh')
    sid = db.get_or_create_session(cid, '2026-11-13')
    audio = tmp_path / 'recording.wav'
    audio.write_bytes(b'synthetic')
    db.update_session(sid, source_type='audio', audio_path=str(audio), status='failed')
    db.replace_segments(sid, [{'text': 'synthetic source', 'start_seconds': 0, 'end_seconds': 1}])
    failed = db.create_job(sid, 'transcribe', status='failed')
    jobs.report(failed, transcript_complete=True, checkpoint_epoch='original-run',
                transcript_signature=jobs.transcript_signature(db.get_session(sid), db.get_course(cid), db.get_all_settings()))
    monkeypatch.setattr(api, 'detect_status', lambda settings: {})
    monkeypatch.setattr(api, 'provider_is_ready', lambda *args: True)
    monkeypatch.setattr(api, '_run_pipeline_thread', lambda *args: None)

    def no_asr_validation(*args):
        raise AssertionError('Resuming a verified transcript must not require ASR')

    monkeypatch.setattr(api.whisper_worker, 'validate_model_installation', no_asr_validation)
    server = api.make_server('127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        request = urllib.request.Request(base + f'/api/sessions/{sid}/process',
            data=json.dumps({'mode': 'resume'}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            job_id = json.load(response)['job_id']
        with urllib.request.urlopen(base + '/api/jobs', timeout=5) as response:
            current = json.load(response)['jobs'][0]
        assert current['id'] == job_id and current['job_type'] == 'analyze'
        assert current['progress']['checkpoint_epoch'] == 'original-run'
        assert current['session_title'] == 'synthetic course'
        with urllib.request.urlopen(base + f'/api/sessions/{sid}/job', timeout=5) as response:
            assert json.load(response)['job']['id'] == job_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
