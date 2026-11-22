"""End-to-end recording discovery uses only disposable, local application data."""
from contextlib import contextmanager
from datetime import datetime
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
import wave

import pytest

from backend import api, config, db, recording_drafts as drafts


def wave_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x01' * 8000)
    return stream.getvalue()


@contextmanager
def running_app():
    server = api.make_server('127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/api'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        drafts.reset_runtime_state()
        db.close_all_connections()


def request(url, method='GET', data=None, headers=None):
    headers = dict(headers or {})
    if isinstance(data, dict):
        headers['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read()
        if response.headers.get_content_type() == 'application/json':
            payload = json.loads(payload)
        return response.status, payload


def new_course():
    semester = db.create_semester('示例学期', '2026-09-01', '2027-01-01')
    return db.create_course(semester, '金融学示例', code='FIN443.DEMO')


def new_draft(cid, sid=None):
    return drafts.create({'course_id': cid, 'session_id': sid, 'session_date': '2026-11-17',
                          'title': '下午的示例课堂', 'mime_type': 'audio/wav'})['id']


def test_stopped_microphone_has_a_verified_receipt_and_survives_a_full_reopen():
    cid = new_course()
    with running_app() as url:
        identifier = new_draft(cid)
        drafts.put_chunk(identifier, 0, wave_bytes())
        status, receipt = request(f'{url}/recording-drafts/{identifier}/finalize', 'POST', {})
        assert status == 200
        assert 'file' in receipt, 'finalization must identify the exact saved recording'
        assert receipt['file']['duration'] == pytest.approx(1)
        assert receipt['file']['index'] == 0
    with running_app() as url:
        status, library = request(url + '/recordings')
        assert status == 200
        row, = library['sessions']
        assert row['course_name'] == '金融学示例' and row['course_code'] == 'FIN443.DEMO'
        assert row['session_date'] == '2026-11-17' and row['status'] == 'ready_to_process'
        assert row['session_id'] == receipt['session_id'] and not library['drafts']
        file, = row['files']
        assert file['availability'] == 'available' and file['file_key'] == receipt['file']['file_key']
        assert 'path' not in file
        audio = f"{url}/recordings/{row['session_id']}/files/0?key={file['file_key']}"
        assert request(audio) == (200, wave_bytes())


def test_older_and_multi_file_classes_remain_findable_without_recovering_deleted_audio():
    cid = new_course()
    sid = db.get_or_create_session(cid, '2026-09-01')
    directory = Path(config.get_config().dirs.audio_dir_for(db.get_course(cid)['semester_id'], cid, sid))
    legacy = directory / 'original.wav'
    legacy.write_bytes(wave_bytes())
    db.update_session(sid, audio_path=str(legacy), audio_duration_seconds=1,
                      source_type='audio', status='ready_to_process')
    with running_app() as url:
        assert request(url + '/recordings')[0] == 200
        first = request(url + '/recordings')[1]['sessions'][0]['files'][0]
        assert first['name'] == 'original.wav' and first['bytes'] == len(wave_bytes())
        identifier = new_draft(cid, sid)
        drafts.put_chunk(identifier, 0, wave_bytes())
        drafts.finalize(identifier)
        files = request(url + '/recordings')[1]['sessions'][0]['files']
        assert [f['index'] for f in files] == [0, 1]
        assert request(f'{url}/sessions/{sid}/audio/1', 'DELETE')[0] == 200
        db.archive_course(cid, True)
    with running_app() as url:
        library = request(url + '/recordings')[1]
        row, = library['sessions']
        assert row['files'] == [first] and not library['drafts']
        assert request(f'{url}/recording-drafts/{identifier}/finalize', 'POST', {'interrupted': True})[0] == 410


def test_crash_only_offers_written_fragments_and_recovery_is_idempotent():
    cid = new_course()
    with running_app() as url:
        identifier = new_draft(cid)
        empty = new_draft(cid)
        audio = wave_bytes()
        for i, chunk in enumerate([audio[:400], audio[400:]]):
            drafts.put_chunk(identifier, i, chunk)
        (Path(drafts._dir(identifier)) / '0000000002.chunk.tmp').write_bytes(b'partial write')
    with running_app() as url:
        status, library = request(url + '/recordings')
        assert status == 200
        assert not library['sessions']
        draft, = library['drafts']
        assert draft['id'] == identifier and draft['id'] != empty and draft['chunk_count'] == 2
        assert draft['bytes'] == len(audio) and draft['partial_write']
        action = f'{url}/recording-drafts/{identifier}/finalize'
        status, saved = request(action, 'POST', {'interrupted': True})
        assert status == 200 and saved['interrupted']
        assert request(action, 'POST', {'interrupted': True})[1]['file'] == saved['file']
    with running_app() as url:
        library = request(url + '/recordings')[1]
        assert not library['drafts']
        row, = library['sessions']
        file, = row['files']
        assert file['interrupted']
        assert request(f"{url}/recordings/{row['session_id']}/files/0") == (200, audio)


def test_pending_receipt_with_committed_database_file_does_not_duplicate_in_library():
    cid = new_course()
    identifier = new_draft(cid)
    metadata = drafts._read(identifier)
    drafts.put_chunk(identifier, 0, wave_bytes())
    drafts.finalize(identifier)
    # Simulate a shutdown after the database transaction and before the receipt update.
    drafts._save(metadata)
    drafts.reset_runtime_state()
    with running_app() as url:
        status, library = request(url + '/recordings')
        assert status == 200
        assert len(library['sessions']) == 1 and not library['drafts']


def test_capture_names_use_local_clock_and_legacy_names_remain_searchable():
    cid = new_course()
    captured = '2026-11-17T06:04:50+00:00'
    local = datetime.fromisoformat(captured).astimezone().strftime('%Y-%m-%d %H时%M分%S秒')
    identifier = new_draft(cid)
    meta = drafts._read(identifier)
    drafts._save(dict(meta, created_at=captured))
    drafts.put_chunk(identifier, 0, wave_bytes())
    saved = drafts.finalize(identifier)
    assert saved['file']['name'] == '课堂录音 ' + local + '.wav'
    assert saved['file']['recorded_at'] == captured
    legacy_name = '课堂录音-2026-11-17T06-04-50+00-00.wav'
    # A pre-upgrade archive can have its audio manifest without any microphone receipt.
    directory = Path(config.get_config().dirs.audio)
    legacy = directory / 'legacy.wav'
    legacy.write_bytes(wave_bytes())
    sid = db.get_or_create_session(cid, '2026-10-04')
    db.update_session(sid, audio_path=str(legacy), audio_manifest=json.dumps([{'path': str(legacy), 'name': legacy_name}]))
    with running_app() as url:
        library = request(url + '/recordings')[1]
        previous = next(row for row in library['sessions'] if row['session_id'] == sid)['files'][0]
        assert previous['recorded_at'] == captured and previous['name'] == legacy_name


def test_missing_external_and_stale_entries_cannot_reveal_or_play_another_file(tmp_path, monkeypatch):
    cid = new_course()
    sid = db.get_or_create_session(cid, '2026-09-01')
    directory = Path(config.get_config().dirs.audio_dir_for(db.get_course(cid)['semester_id'], cid, sid))
    a, b, missing = [directory / name for name in ('a.wav', 'b.wav', 'lost.wav')]
    external = tmp_path / 'outside.wav'
    for path in (a, b, external):
        path.write_bytes(wave_bytes())
    entries = [{'path': str(path), 'name': path.name, 'duration': 1} for path in (a, b, missing, external)]
    db.update_session(sid, audio_path=str(a), audio_manifest=json.dumps(entries), status='ready_to_process')
    with running_app() as url:
        status, library = request(url + '/recordings')
        assert status == 200
        files = library['sessions'][0]['files']
        assert [f['availability'] for f in files] == ['available', 'available', 'missing', 'external']
        from backend import recording_library
        opened = []
        monkeypatch.setattr(recording_library, '_select_in_folder', opened.append)
        def reveal(index, body=None, headers=None):
            return request(f'{url}/sessions/{sid}/audio/{index}/reveal', 'POST', body or {}, headers)[0]
        assert reveal(1, {'file_key': files[1]['file_key']}) == 200
        assert opened == [str(b)]
        assert reveal(0, {'path': str(external)}) == 400
        assert reveal(0, headers={'Origin': 'https://unrelated.example'}) == 403
        assert reveal(2) == 404 and reveal(3) == 403 and reveal(12) == 404
        assert request(f'{url}/recordings/{sid}/files/3')[0] == 403
        db.update_session(sid, audio_manifest=json.dumps(entries[1:]), audio_path=str(b))
        assert reveal(0, {'file_key': files[0]['file_key']}) == 409
        assert opened == [str(b)]


def test_unreadable_interrupted_audio_can_still_be_located_and_is_never_reported_saved(monkeypatch):
    cid = new_course()
    identifier = new_draft(cid)
    drafts.put_chunk(identifier, 0, b'written but undecodable')
    drafts.reset_runtime_state()
    with running_app() as url:
        from backend import recording_library
        opened = []
        monkeypatch.setattr(recording_library, '_select_in_folder', opened.append)
        base = f'{url}/recording-drafts/{identifier}'
        assert request(base + '/finalize', 'POST', {'interrupted': True})[0] == 422
        status, result = request(base + '/reveal', 'POST', {})
        assert status == 200 and result['ok']
        assert Path(opened[0]).parent == Path(drafts._dir(identifier))
        assert request(base + '/reveal', 'POST', {'path': str(Path.cwd())})[0] == 400
        visible = request(url + '/recordings')[1]
        assert not visible['sessions'] and visible['drafts'][0]['id'] == identifier
