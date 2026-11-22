"""Recording drafts survive interruption and never replace existing audio."""
import io
import json
import os
import threading
import urllib.error
import urllib.request
import wave

import av
import pytest

from backend import api, db, recording_drafts


def request(url, method='GET', body=None, content_type='application/json'):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    req = urllib.request.Request(url, data=body, method=method, headers={'Content-Type': content_type})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def server():
    instance = api.make_server('127.0.0.1', 0)
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    return instance, f'http://127.0.0.1:{instance.server_address[1]}/api/recording-drafts'


def tone():
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x01' * 8000)
    return out.getvalue()


def course():
    semester = db.create_semester('Test', '2026-01-01', '2027-01-01')
    return db.create_course(semester, 'Test')


def test_draft_survives_restart_and_finalize_is_idempotent_with_existing_audio(tmp_path):
    cid = course()
    sid = db.get_or_create_session(cid, '2026-11-12')
    existing = tmp_path / 'old.wav'
    existing.write_bytes(tone())
    db.update_session(sid, audio_path=str(existing), audio_hash='old', audio_duration_seconds=1,
                      status='ready_to_process', source_type='audio')
    app, url = server()
    try:
        status, draft = request(url, 'POST', {'course_id': cid, 'session_id': sid,
            'session_date': '2026-11-12', 'title': 'Test', 'mime_type': 'audio/wav'})
        assert status == 200
        base = url + '/' + draft['id']
        audio = tone()
        midpoint = len(audio) // 2
        assert request(base + '/chunks/1', 'PUT', audio[midpoint:], 'application/octet-stream')[0] == 409
        assert request(base + '/chunks/0', 'PUT', audio[:midpoint], 'application/octet-stream')[0] == 200
        assert request(base + '/chunks/0', 'PUT', audio[:midpoint], 'application/octet-stream')[0] == 200
        assert request(base + '/chunks/0', 'PUT', b'different', 'application/octet-stream')[0] == 409
        assert request(base + '/chunks/1', 'PUT', audio[midpoint:], 'application/octet-stream')[0] == 200
    finally:
        app.shutdown(); app.server_close()
    # A restarted application has no in-memory leases from the prior process.
    recording_drafts._ACTIVE.clear()
    app, url = server()
    try:
        base = url + '/' + draft['id']
        status, listing = request(url)
        assert status == 200 and listing['drafts'][0]['chunk_count'] == 2
        status, result = request(base + '/finalize', 'POST', {'interrupted': True})
        assert status == 200 and result['session_id'] == sid and result['status'] == 'saved'
        assert request(base + '/finalize', 'POST', {'interrupted': True})[0] == 200
        saved = db.audio_files_for_session(sid)
        assert len(saved) == 2 and open(saved[0]['path'], 'rb').read() == tone()
        assert open(saved[1]['path'], 'rb').read() == audio
        assert db.get_session(sid)['status'] == 'ready_to_process'
        assert request(url)[1]['drafts'][0]['status'] == 'saved'
        assert request(url)[1]['drafts'][0]['chunk_count'] == 2
    finally:
        app.shutdown(); app.server_close()


def test_invalid_recovery_remains_available_and_unmodified():
    cid = course()
    app, url = server()
    try:
        status, draft = request(url, 'POST', {'course_id': cid, 'session_date': '2026-11-12',
            'title': 'Test', 'mime_type': 'audio/webm'})
        assert status == 200
        base = url + '/' + draft['id']
        assert request(base + '/chunks/0', 'PUT', b'not audio', 'application/octet-stream')[0] == 200
        assert request(base + '/finalize', 'POST', {'interrupted': False})[0] == 422
        status, listing = request(url)
        assert status == 200 and listing['drafts'][0]['status'] != 'saved'
        assert listing['drafts'][0]['chunk_count'] == 1 and listing['drafts'][0]['bytes'] == 9
        assert request(base + '/chunks/0', 'PUT', b'not audio', 'application/octet-stream')[0] == 200
    finally:
        app.shutdown(); app.server_close()


def test_saved_database_entry_can_be_retried_after_receipt_write_fails(monkeypatch):
    cid = course()
    app, url = server()
    try:
        _, draft = request(url, 'POST', {'course_id': cid, 'session_date': '2026-11-12',
            'title': 'Test', 'mime_type': 'audio/wav'})
        base = url + '/' + draft['id']
        assert request(base + '/chunks/0', 'PUT', tone(), 'application/octet-stream')[0] == 200
        original = recording_drafts._save
        def fail_receipt(value):
            if value['status'] == 'saved':
                raise OSError('simulated receipt failure')
            original(value)
        monkeypatch.setattr(recording_drafts, '_save', fail_receipt)
        assert request(base + '/finalize', 'POST', {'interrupted': False})[0] == 500
        monkeypatch.setattr(recording_drafts, '_save', original)
        status, result = request(base + '/finalize', 'POST', {'interrupted': False})
        assert status == 200
        assert len(db.audio_files_for_session(result['session_id'])) == 1
        assert open(db.audio_files_for_session(result['session_id'])[0]['path'], 'rb').read() == tone()
    finally:
        app.shutdown(); app.server_close()


def test_new_draft_creates_new_class_even_when_same_course_and_date_exists():
    cid = course()
    old_sid = db.get_or_create_session(cid, '2026-11-12')
    app, url = server()
    try:
        _, draft = request(url, 'POST', {'course_id': cid, 'session_date': '2026-11-12',
            'title': 'New class', 'mime_type': 'audio/wav'})
        base = url + '/' + draft['id']
        assert request(base + '/chunks/0', 'PUT', tone(), 'application/octet-stream')[0] == 200
        status, result = request(base + '/finalize', 'POST', {'interrupted': False})
        assert status == 200 and result['session_id'] != old_sid
        assert not db.audio_files_for_session(old_sid)
        assert len(db.audio_files_for_session(result['session_id'])) == 1
    finally:
        app.shutdown(); app.server_close()


def test_saved_receipt_without_database_record_remains_recoverable():
    cid = course()
    app, url = server()
    try:
        _, draft = request(url, 'POST', {'course_id': cid, 'session_date': '2026-11-12',
            'title': 'Test', 'mime_type': 'audio/wav'})
        base = url + '/' + draft['id']
        assert request(base + '/chunks/0', 'PUT', tone(), 'application/octet-stream')[0] == 200
        _, result = request(base + '/finalize', 'POST', {'interrupted': False})
        db.delete_session(result['session_id'])
        visible = request(url)[1]['drafts'][0]
        assert visible['status'] != 'saved'
        status, recovered = request(base + '/finalize', 'POST', {'interrupted': False})
        assert status == 200 and recovered['session_id'] == result['session_id']
        assert len(db.audio_files_for_session(result['session_id'])) == 1
    finally:
        app.shutdown(); app.server_close()


def test_many_chunk_writes_do_not_rescan_history_and_rebuild_after_restart(monkeypatch):
    cid = course()
    draft = recording_drafts.create({'course_id': cid, 'session_date': '2026-11-12', 'mime_type': 'audio/webm'})
    draft_dir = os.path.join(recording_drafts._root(), draft['id'])
    # Three hours at one fragment per second; make synthetic files without audio or user data.
    for index in range(10800):
        with open(os.path.join(draft_dir, f'{index:010d}.chunk'), 'wb') as f:
            f.write(b'a')
    getattr(recording_drafts, '_INDEX', {}).clear()  # Simulate restart and discover fragments from disk.
    listdir = os.listdir
    scans = []
    def count_scans(path):
        if os.path.normcase(str(path)) == os.path.normcase(draft_dir):
            scans.append(path)
        return listdir(path)
    monkeypatch.setattr(recording_drafts.os, 'listdir', count_scans)
    for index in range(10800, 10820):
        result = recording_drafts.put_chunk(draft['id'], index, b'b')
    assert result['chunk_count'] == 10820 and result['bytes'] == 10820
    assert len(scans) <= 2
    assert recording_drafts.put_chunk(draft['id'], 10800, b'b')['chunk_count'] == 10820
    os.remove(os.path.join(draft_dir, '0000000100.chunk'))
    with pytest.raises(recording_drafts.DraftError):
        recording_drafts.finalize(draft['id'])


def test_saved_receipt_with_missing_audio_file_can_restore_it():
    cid = course()
    draft = recording_drafts.create({'course_id': cid, 'session_date': '2026-11-12', 'mime_type': 'audio/wav'})
    recording_drafts.put_chunk(draft['id'], 0, tone())
    result = recording_drafts.finalize(draft['id'])
    path = db.audio_files_for_session(result['session_id'])[0]['path']
    os.remove(path)
    assert recording_drafts.list_drafts()['drafts'][0]['status'] != 'saved'
    restored = recording_drafts.finalize(draft['id'])
    assert restored['status'] == 'saved'
    assert open(path, 'rb').read() == tone()
    assert len(db.audio_files_for_session(result['session_id'])) == 1


def test_finalize_does_not_append_when_job_starts_after_initial_check(monkeypatch, tmp_path):
    cid = course()
    sid = db.get_or_create_session(cid, '2026-11-12')
    old = tmp_path / 'old.wav'
    old.write_bytes(tone())
    db.update_session(sid, audio_path=str(old), audio_hash='old', status='ready_to_process', source_type='audio')
    draft = recording_drafts.create({'course_id': cid, 'session_id': sid,
        'session_date': '2026-11-12', 'mime_type': 'audio/wav'})
    recording_drafts.put_chunk(draft['id'], 0, tone())
    checked, continue_finalize = threading.Event(), threading.Event()
    original = db.active_jobs
    def delayed_check():
        result = original()
        if threading.current_thread().name == 'finalize-race' and not checked.is_set():
            checked.set()
            assert continue_finalize.wait(5)
        return result
    monkeypatch.setattr(db, 'active_jobs', delayed_check)
    errors = []
    def worker():
        try:
            recording_drafts.finalize(draft['id'])
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker, name='finalize-race')
    thread.start()
    try:
        assert checked.wait(5)
        with db.write_txn():
            db.create_job(sid, 'transcribe', status='pending')
            db.update_session(sid, status='file_check')
    finally:
        continue_finalize.set()
        thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], recording_drafts.DraftError)
    assert db.get_session(sid)['status'] == 'file_check'
    assert len(db.audio_files_for_session(sid)) == 1
    assert old.read_bytes() == tone()
    assert recording_drafts.list_drafts()['drafts'][0]['status'] == 'recording'


def test_interrupted_webm_can_recover_decodable_prefix_and_keep_fragments():
    buffer = io.BytesIO()
    with av.open(buffer, 'w', format='webm') as container:
        stream = container.add_stream('libopus', rate=48000)
        frame = av.AudioFrame(format='s16', layout='mono', samples=48000)
        frame.sample_rate = 48000
        frame.planes[0].update(b'\x01\x01' * 48000)
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    truncated = buffer.getvalue()[:-100]
    cid = course()
    app, url = server()
    try:
        _, draft = request(url, 'POST', {'course_id': cid, 'session_date': '2026-11-12',
            'title': 'Test', 'mime_type': 'audio/webm'})
        base = url + '/' + draft['id']
        split = len(truncated) // 2
        assert request(base + '/chunks/0', 'PUT', truncated[:split], 'application/octet-stream')[0] == 200
        assert request(base + '/chunks/1', 'PUT', truncated[split:], 'application/octet-stream')[0] == 200
        assert request(base + '/finalize', 'POST', {'interrupted': True})[0] == 409
        recording_drafts._ACTIVE.clear()  # Simulate a new application process.
        status, result = request(base + '/finalize', 'POST', {'interrupted': True})
        assert status == 200 and result['interrupted'] is True
        saved = db.audio_files_for_session(result['session_id'])[0]['path']
        assert open(saved, 'rb').read() == truncated
        with av.open(saved) as recovered:
            assert sum(1 for _ in recovered.decode(audio=0)) > 0
        assert result['chunk_count'] == 2
    finally:
        app.shutdown(); app.server_close()
