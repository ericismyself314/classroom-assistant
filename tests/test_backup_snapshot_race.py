"""A backup must represent one state even if the live recording is later deleted."""
import io
import json
import os
import sqlite3
import wave
import zipfile

from backend import backup, config, db, recording_drafts as drafts


def test_delete_during_zip_compression_cannot_remove_audio_from_the_snapshot(tmp_path, monkeypatch):
    semester = db.create_semester('Synthetic semester', '2026-01-01', '2027-01-01')
    course = db.create_course(semester, 'Synthetic course')
    draft = drafts.create({'course_id': course, 'session_date': '2026-11-13', 'mime_type': 'audio/wav'})
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x01\x01' * 8000)
    payload = stream.getvalue()
    drafts.put_chunk(draft['id'], 0, payload)
    saved = drafts.finalize(draft['id'])
    sid = saved['session_id']
    path = db.audio_files_for_session(sid)[0]['path']
    relative = os.path.relpath(path, config.get_config().dirs.data_dir).replace('\\', '/')
    write = zipfile.ZipFile.write
    deleted = []

    def delete_live_audio_after_database_is_archived(archive, filename, arcname=None, *args, **kwargs):
        result = write(archive, filename, arcname, *args, **kwargs)
        if str(arcname).replace('\\', '/') == 'database/classroom-assistant.db' and not deleted:
            deleted.append(True)
            with drafts.deleting_audio({sid: [path]}):
                os.remove(path)
                db.update_session(sid, audio_path=None, audio_manifest=None, audio_hash=None)
        return result

    monkeypatch.setattr(zipfile.ZipFile, 'write', delete_live_audio_after_database_is_archived)
    target = backup.create_backup(include_audio=True)
    assert deleted and not os.path.exists(path)
    with zipfile.ZipFile(target) as archive:
        database = tmp_path / 'archived.sqlite'
        database.write_bytes(archive.read('database/classroom-assistant.db'))
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT audio_path FROM class_sessions WHERE id=?', (sid,)).fetchone()[0] == path
        assert archive.read(relative) == payload
        metadata = json.loads(archive.read(f"recording-drafts/{draft['id']}/draft.json"))
        assert metadata['status'] == 'saved'
