"""A failed restore must put the current database and recordings back."""
import io
import os
from pathlib import Path
import wave

import pytest

from backend import backup, config, db


def test_audio_copy_failure_after_database_replacement_rolls_back_current_data(tmp_path, monkeypatch):
    semester = db.create_semester('Synthetic semester', '2026-01-01', '2027-01-01')
    original_course = db.create_course(semester, 'Archived course A')
    archived_session = db.get_or_create_session(original_course, '2026-11-12')
    archived_folder = config.get_config().dirs.audio_dir_for(semester, original_course, archived_session)
    archived_audio = Path(archived_folder) / 'archived.wav'
    archived_audio.write_bytes(b'archived audio')
    db.update_session(archived_session, source_type='audio', audio_path=str(archived_audio))
    archive = backup.create_backup(include_audio=True)

    current_course = db.create_course(semester, 'Current course B added after backup')
    current_session = db.get_or_create_session(current_course, '2026-11-13')
    folder = config.get_config().dirs.audio_dir_for(semester, current_course, current_session)
    audio = Path(folder) / 'current.wav'
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x02\x01' * 8000)
    payload = stream.getvalue()
    audio.write_bytes(payload)
    db.update_session(current_session, source_type='audio', audio_path=str(audio))

    copytree = backup.shutil.copytree
    copy2 = backup.shutil.copy2
    failed = []
    audio_root = os.path.abspath(config.get_config().dirs.audio)

    def fail_live_audio_write_once(destination):
        destination_path = os.path.abspath(os.fspath(destination))
        if not failed and os.path.commonpath([audio_root, destination_path]) == audio_root:
            failed.append(True)
            raise PermissionError('synthetic restored audio write failure')

    def checked_copytree(source, destination, *args, **kwargs):
        fail_live_audio_write_once(destination)
        return copytree(source, destination, *args, **kwargs)

    def checked_copy2(source, destination, *args, **kwargs):
        fail_live_audio_write_once(destination)
        return copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(backup.shutil, 'copytree', checked_copytree)
    monkeypatch.setattr(backup.shutil, 'copy2', checked_copy2)
    with pytest.raises(Exception):
        backup.restore_backup(archive)
    assert failed, 'Failure must happen while installing restored audio, after the database copy'
    assert db.get_course(current_course) is not None
    assert db.get_session(current_session)['audio_path'] == str(audio)
    assert audio.read_bytes() == payload
    assert archived_audio.read_bytes() == b'archived audio'
