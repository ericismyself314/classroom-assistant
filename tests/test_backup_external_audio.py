"""Deleting restored or legacy lessons must never delete external source audio."""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend import api, config, db


@pytest.mark.parametrize('route', ['sessions', 'courses'])
@pytest.mark.parametrize('legacy_external', [False, True])
def test_deleting_lesson_or_course_keeps_external_original(tmp_path, route, legacy_external):
    semester = db.create_semester('Synthetic semester', '2026-01-01', '2027-01-01')
    course = db.create_course(semester, 'Synthetic course')
    session = db.get_or_create_session(course, '2026-11-13')
    original = tmp_path / 'external-original.wav'
    original.write_bytes(b'external source must remain unchanged')
    directory = config.get_config().dirs.audio_dir_for(semester, course, session)
    managed = Path(directory) / 'internal-copy.wav'
    managed.write_bytes(b'internal copy may be deleted')
    entries = [{'path': str(managed)}]
    if not legacy_external:
        entries.append({'path': str(original)})
    db.update_session(session, source_type='audio', status='ready_to_process',
                      audio_path=str(original), audio_manifest=json.dumps(entries))
    target = session if route == 'sessions' else course
    server = api.make_server('127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f'http://127.0.0.1:{server.server_address[1]}/api/{route}/{target}', method='DELETE')
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        assert status in {200, 409}
        assert original.read_bytes() == b'external source must remain unchanged'
        if status == 200:
            assert not managed.exists()
    finally:
        server.shutdown()
        server.server_close()
