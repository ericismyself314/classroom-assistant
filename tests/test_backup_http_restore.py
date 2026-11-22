"""Restore through the threaded API while the startup SQLite connection survives."""
import io
import json
import threading
import urllib.error
import urllib.request
import wave
import zipfile

from backend import api, db


def test_threaded_http_restore_keeps_live_connections_and_recovers_unfinished_draft():
    startup_connection = db.get_conn()
    startup_connection.execute('SELECT 1').fetchone()
    server = api.make_server('127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}/api'

    def request(path, method='GET', body=None):
        content_type = 'application/octet-stream' if isinstance(body, bytes) else 'application/json'
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode('utf-8')
        message = urllib.request.Request(base + path, data=body, method=method,
                                         headers={'Content-Type': content_type})
        try:
            with urllib.request.urlopen(message, timeout=15) as response:
                data = response.read()
                return json.loads(data) if 'json' in response.headers.get('Content-Type', '') else data
        except urllib.error.HTTPError as error:
            raise AssertionError(f'{path}: HTTP {error.code}: {error.read().decode("utf-8", errors="replace")}') from error

    try:
        semester = request('/semesters', 'POST', {'name': 'Synthetic semester',
            'start_date': '2026-01-01', 'end_date': '2027-01-01'})['id']
        course = request('/courses', 'POST', {'name': 'Synthetic course', 'semester_id': semester})['id']
        draft = request('/recording-drafts', 'POST', {'course_id': course,
            'session_date': '2026-11-13', 'mime_type': 'audio/wav'})
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\x01' * 8000)
        recording = stream.getvalue()
        path = '/recording-drafts/' + draft['id']
        midpoint = len(recording) // 2
        request(path + '/chunks/0', 'PUT', recording[:midpoint])
        request(path + '/chunks/1', 'PUT', recording[midpoint:])
        archive = request('/backup?download=1')
        with zipfile.ZipFile(io.BytesIO(archive)) as snapshot:
            assert f"recording-drafts/{draft['id']}/0000000000.chunk" in snapshot.namelist()
        request(path + '/finalize', 'POST', {'interrupted': False})
        restored = request('/restore', 'POST', archive)
        assert restored['snapshot']
        rows = request('/recording-drafts')['drafts']
        pending = next(row for row in rows if row['id'] == draft['id'])
        assert pending['status'] == 'recording' and pending['chunk_count'] == 2 and not pending['active']
        recovered = request(path + '/finalize', 'POST', {'interrupted': True})
        assert request('/sessions/' + recovered['session_id'] + '/audio') == recording
        assert startup_connection.execute('SELECT COUNT(*) FROM courses WHERE id=?', (course,)).fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
