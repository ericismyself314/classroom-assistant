"""Draft cleanup and deletion preserve audio while respecting explicit deletion."""
import io
import json
import os
import threading
import urllib.error
import urllib.request
import wave
import zipfile

import pytest

from backend import api, backup, config, db, recording_drafts as drafts


def audio():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x01' * 8000)
    return stream.getvalue()


def new_draft(session_id=None, course_id=None):
    if course_id is None:
        semester = db.create_semester('Synthetic semester', '2026-01-01', '2027-01-01')
        course_id = db.create_course(semester, 'Synthetic course')
    draft = drafts.create({'course_id': course_id, 'session_id': session_id,
                           'session_date': '2026-11-13', 'mime_type': 'audio/wav'})
    payload = audio()
    drafts.put_chunk(draft['id'], 0, payload[:100])
    drafts.put_chunk(draft['id'], 1, payload[100:])
    return draft


def delete_request(path, method='DELETE', payload=None, headers=None):
    server = api.make_server('127.0.0.1', 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/api{path}',
                                         method=method, data=payload, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    finally:
        server.shutdown()
        server.server_close()


def test_other_session_processing_does_not_prevent_new_recording_save():
    old = new_draft()
    saved = drafts.finalize(old['id'])
    db.create_job(saved['session_id'], 'analyze', status='analyzing')
    fresh = new_draft()
    result = drafts.finalize(fresh['id'])
    assert result['status'] == 'saved'
    assert result['session_id'] != saved['session_id']
    assert db.audio_files_for_session(result['session_id'])


def test_successful_recording_compacts_to_same_storage_recovery_link():
    draft = new_draft()
    result = drafts.finalize(draft['id'])
    original = db.audio_files_for_session(result['session_id'])[0]['path']
    folder = drafts._dir(draft['id'])
    assert not any(name.endswith('.chunk') for name in os.listdir(folder))
    recovery = drafts._chunks(draft['id'])
    assert len(recovery) == 1 and os.path.samefile(original, recovery[0])
    os.remove(original)  # Accidental loss, not an intentional API deletion.
    drafts.finalize(draft['id'])
    assert open(original, 'rb').read() == audio()


def test_failed_hardlink_keeps_all_original_fragments(monkeypatch):
    draft = new_draft()
    monkeypatch.setattr(drafts.os, 'link', lambda *args: (_ for _ in ()).throw(OSError('unavailable')))
    saved = drafts.finalize(draft['id'])
    assert saved['status'] == 'saved'
    assert len([name for name in os.listdir(drafts._dir(draft['id'])) if name.endswith('.chunk')]) == 2


def test_intentional_audio_deletion_never_reappears_as_recoverable():
    draft = new_draft()
    saved = drafts.finalize(draft['id'])
    status, result = delete_request(f"/sessions/{saved['session_id']}/audio")
    assert status == 200 and result['audio_removed']
    entry = next(row for row in drafts.list_drafts()['drafts'] if row['id'] == draft['id'])
    assert entry['status'] == 'saved' and entry['deleted'] is True
    assert drafts._chunks(draft['id']) == []
    with pytest.raises(drafts.DraftError) as error:
        drafts.finalize(draft['id'])
    assert error.value.status == 410


def test_delete_failure_keeps_recovery_and_restores_receipt(monkeypatch):
    draft = new_draft()
    saved = drafts.finalize(draft['id'])
    path = db.audio_files_for_session(saved['session_id'])[0]['path']
    remove = os.remove

    def fail_target(target):
        if os.path.normcase(os.fspath(target)) == os.path.normcase(path):
            raise PermissionError('synthetic file lock')
        return remove(target)

    monkeypatch.setattr(api.os, 'remove', fail_target)
    status, _ = delete_request(f"/sessions/{saved['session_id']}/audio")
    assert status == 500
    assert drafts._read(draft['id'])['status'] == 'saved'
    assert open(path, 'rb').read() == audio()
    assert drafts._chunks(draft['id'])


def test_deleting_one_staged_recording_preserves_other_draft():
    first = new_draft()
    saved = drafts.finalize(first['id'])
    cid = db.get_session(saved['session_id'])['course_id']
    second = new_draft(saved['session_id'], cid)
    drafts.finalize(second['id'])
    status, _ = delete_request(f"/sessions/{saved['session_id']}/audio/0")
    assert status == 200
    listing = {row['id']: row for row in drafts.list_drafts()['drafts']}
    assert listing[first['id']]['deleted'] is True
    assert not listing[second['id']].get('deleted')
    assert drafts._chunks(second['id'])
    assert len(db.audio_files_for_session(saved['session_id'])) == 1


def test_full_backup_contains_unfinished_recording_and_audio_switch_excludes_it():
    draft = new_draft()
    full = backup.create_backup(include_audio=True)
    with zipfile.ZipFile(full) as archive:
        names = archive.namelist()
        assert f"recording-drafts/{draft['id']}/draft.json" in names
        assert f"recording-drafts/{draft['id']}/0000000000.chunk" in names
    without_audio = backup.create_backup(include_audio=False)
    with zipfile.ZipFile(without_audio) as archive:
        assert not any(name.startswith(('recording-drafts/', 'audio/')) for name in archive.namelist())


def test_restore_preserves_pre_restore_drafts_and_resets_runtime_indexes(tmp_path):
    draft = new_draft()
    archive = backup.create_backup(include_audio=True)
    copied_archive = tmp_path / 'draft-backup.zip'
    copied_archive.write_bytes(open(archive, 'rb').read())
    part = os.path.join(drafts._dir(draft['id']), '0000000000.chunk')
    with open(part, 'wb') as stream:
        stream.write(b'newer content before restore')
    extra = os.path.join(drafts._dir(draft['id']), '0000000002.chunk')
    with open(extra, 'wb') as stream:
        stream.write(b'extra newer recording fragment')
    drafts._INDEX[drafts._dir(draft['id'])] = (999, 999)
    drafts._ACTIVE.clear()
    result = backup.restore_backup(str(copied_archive))
    before = os.path.join(result['snapshot'], 'recording-drafts', draft['id'], '0000000000.chunk')
    assert open(before, 'rb').read() == b'newer content before restore'
    assert open(part, 'rb').read() == audio()[:100]
    assert not os.path.exists(extra)
    assert os.path.exists(os.path.join(result['snapshot'], 'recording-drafts', draft['id'], '0000000002.chunk'))
    restored = next(row for row in drafts.list_drafts()['drafts'] if row['id'] == draft['id'])
    assert restored['chunk_count'] == 2 and restored['bytes'] == len(audio())
    assert restored['active'] is False


def test_restoring_to_another_data_directory_rebases_only_internal_audio(tmp_path):
    saved_draft = new_draft()
    saved = drafts.finalize(saved_draft['id'])
    original_path = db.audio_files_for_session(saved['session_id'])[0]['path']
    pending = new_draft()
    external = tmp_path / 'external-original.wav'
    external.write_bytes(audio())
    external_sid = db.get_or_create_session(saved_draft['course_id'], '2026-11-14')
    db.update_session(external_sid, audio_path=str(external), source_type='audio')
    archive = backup.create_backup(include_audio=True)
    copied = tmp_path / 'portable-backup.zip'
    copied.write_bytes(open(archive, 'rb').read())
    drafts.reset_runtime_state()
    db.close_all_connections()
    destination = tmp_path / 'restored-on-another-machine'
    config.get_config().set_data_dir(str(destination))
    db.init_db()
    backup.restore_backup(str(copied))
    restored_path = db.audio_files_for_session(saved['session_id'])[0]['path']
    assert os.path.commonpath([str(destination), restored_path]) == str(destination)
    assert restored_path != original_path and open(restored_path, 'rb').read() == audio()
    assert open(original_path, 'rb').read() == audio(), 'Source directory must not be modified'
    assert db.get_session(external_sid)['audio_path'] == str(external)
    assert external.read_bytes() == audio()
    unfinished = next(row for row in drafts.list_drafts()['drafts'] if row['id'] == pending['id'])
    assert unfinished['status'] == 'recording' and unfinished['chunk_count'] == 2
    recovered = drafts.finalize(pending['id'])
    assert open(db.audio_files_for_session(recovered['session_id'])[0]['path'], 'rb').read() == audio()


def test_restore_aborts_when_pre_restore_snapshot_fails(monkeypatch, tmp_path):
    draft = new_draft()
    archive = backup.create_backup(include_audio=True)
    drafts.reset_runtime_state()
    copytree = backup.shutil.copytree

    def fail_snapshot(source, destination, **kwargs):
        if 'pre-restore-' in os.fspath(destination):
            raise PermissionError('synthetic unavailable backup storage')
        return copytree(source, destination, **kwargs)

    monkeypatch.setattr(backup.shutil, 'copytree', fail_snapshot)
    with pytest.raises(RuntimeError, match='快照'):
        backup.restore_backup(archive)
    assert open(drafts._chunks(draft['id'])[0], 'rb').read() == audio()[:100]


def test_uncommitted_delete_intent_retains_recovery_after_restart():
    draft = new_draft()
    saved = drafts.finalize(draft['id'])
    path = db.audio_files_for_session(saved['session_id'])[0]['path']
    metadata = drafts._read(draft['id'])
    metadata.update(status='deleting', delete_previous_status='saved',
                    delete_session_id=saved['session_id'], delete_paths=[path])
    drafts._save(metadata)
    os.remove(path)  # Crash before the DB transaction commits.
    visible = next(row for row in drafts.list_drafts()['drafts'] if row['id'] == draft['id'])
    assert visible['status'] == 'recording' and visible['chunk_count'] > 0
    drafts.finalize(draft['id'])
    assert open(path, 'rb').read() == audio()


def test_committed_delete_intent_does_not_resurrect_after_restart():
    draft = new_draft()
    saved = drafts.finalize(draft['id'])
    path = db.audio_files_for_session(saved['session_id'])[0]['path']
    metadata = drafts._read(draft['id'])
    metadata.update(status='deleting', delete_previous_status='saved',
                    delete_session_id=saved['session_id'], delete_paths=[path])
    drafts._save(metadata)
    os.remove(path)
    db.update_session(saved['session_id'], audio_path=None, audio_manifest=None)
    visible = next(row for row in drafts.list_drafts()['drafts'] if row['id'] == draft['id'])
    assert visible['status'] == 'saved' and visible['deleted'] is True
    assert drafts._chunks(draft['id']) == []


def test_deleting_saved_audio_keeps_current_unfinished_recording():
    old = new_draft()
    saved = drafts.finalize(old['id'])
    unfinished = new_draft(saved['session_id'], old['course_id'])
    assert delete_request(f"/sessions/{saved['session_id']}/audio")[0] == 200
    assert drafts._read(unfinished['id'])['status'] == 'recording'
    assert len(drafts._chunks(unfinished['id'])) == 2


def test_deferred_upload_saves_other_class_without_starting_model(monkeypatch):
    old = new_draft()
    saved = drafts.finalize(old['id'])
    db.create_job(saved['session_id'], 'analyze', status='analyzing')
    fresh = new_draft()
    target = db.get_or_create_session(fresh['course_id'], '2026-11-15')
    monkeypatch.setattr(api, 'start_pipeline', lambda *args: (_ for _ in ()).throw(AssertionError('must not process')))
    status, result = delete_request(f'/sessions/{target}/audio', 'POST', audio(),
                                   {'X-Defer-Processing': '1', 'X-Filename': 'synthetic.wav'})
    assert status == 200 and result['ready_to_process'] is True
    assert db.get_session(target)['status'] == 'ready_to_process'
    assert len(db.active_jobs()) == 1


@pytest.mark.parametrize('same_class,defer', [(True, True), (False, False)])
def test_active_job_rejects_same_class_or_immediate_upload_before_reading(monkeypatch, same_class, defer):
    old = new_draft()
    saved = drafts.finalize(old['id'])
    db.create_job(saved['session_id'], 'analyze', status='analyzing')
    target = saved['session_id'] if same_class else db.get_or_create_session(old['course_id'], '2026-11-16')
    monkeypatch.setattr(api, '_stream_to_file', lambda *args: (_ for _ in ()).throw(AssertionError('must reject early')))
    headers = {'X-Filename': 'synthetic.wav'}
    if defer:
        headers['X-Defer-Processing'] = '1'
    assert delete_request(f'/sessions/{target}/audio', 'POST', audio(), headers)[0] == 409


def test_deferred_upload_rechecks_target_job_inside_manifest_transaction(monkeypatch):
    draft = new_draft()
    sid = db.get_or_create_session(draft['course_id'], '2026-11-17')
    original = api._stream_to_file

    def start_job_during_upload(handler, destination):
        result = original(handler, destination)
        db.create_job(sid, 'transcribe', status='pending')
        db.update_session(sid, status='file_check')
        return result

    monkeypatch.setattr(api, '_stream_to_file', start_job_during_upload)
    status, _ = delete_request(f'/sessions/{sid}/audio', 'POST', audio(),
                              {'X-Defer-Processing': '1', 'X-Filename': 'synthetic.wav'})
    assert status == 409
    assert not db.audio_files_for_session(sid)
    assert db.get_session(sid)['status'] == 'file_check'


def test_listing_maintains_at_most_one_legacy_saved_draft(monkeypatch):
    compact = drafts._compact_saved
    monkeypatch.setattr(drafts, '_compact_saved', lambda draft: None)
    originals = [new_draft(), new_draft()]
    for draft in originals:
        drafts.finalize(draft['id'])
    drafts._ACTIVE.clear()
    calls = []

    def record_compaction(draft):
        calls.append(draft['id'])
        compact(draft)

    monkeypatch.setattr(drafts, '_compact_saved', record_compaction)
    drafts.list_drafts()
    assert len(calls) == 1
    assert sum(bool(drafts._read(draft['id']).get('compacted')) for draft in originals) == 1
    drafts.list_drafts()
    assert len(calls) == 2
    assert all(drafts._read(draft['id']).get('compacted') for draft in originals)


def test_restore_rejects_live_recording_but_backup_can_capture_its_saved_prefix():
    draft = new_draft()
    archive = backup.create_backup(include_audio=True)
    with pytest.raises(drafts.DraftError) as error:
        backup.restore_backup(archive)
    assert error.value.status == 409
    assert drafts._read(draft['id'])['status'] == 'recording'
    assert len(drafts._chunks(draft['id'])) == 2
