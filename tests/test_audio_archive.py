"""Storage lifecycle with real encoded media and disposable classroom data."""
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
import json
import os

import av
import pytest

from backend import audio_archive as archive, backup, db, hashutil, recording_drafts as drafts, recording_library
from test_draft_lifecycle_backup import new_draft
from test_recording_library import running_app, request


def recording():
    d = new_draft()
    saved = drafts.finalize(d['id'])
    sid = saved['session_id']
    db.replace_segments(sid, [{'text': '课堂原话', 'display_text': '课堂原话', 'language': 'zh',
                               'start_seconds': .25, 'end_seconds': .8}])
    return sid, d['id'], Path(db.audio_files_for_session(sid)[0]['path'])


def completed(sid):
    db.save_analysis(sid, [], [], {'title': '课堂笔记', 'knowledge': []}, 'test')


def test_process_success_and_idempotent_batch_with_seek_and_recovery():
    sid, draft_id, old = recording()
    old_key = drafts.finalize(draft_id)['file']['file_key']
    assert archive.status()['eligible'] == 0 and not archive.status()['text_only']
    completed(sid)
    assert archive.status()['pending'] == 1
    assert archive.request_batch()['pending'] == 1
    assert archive.process_next()
    assert archive.status()['done'] == 1, archive.status()
    assert not old.exists()
    e = db.audio_files_for_session(sid)[0]
    assert e['archived'] and e['duration'] == 1
    f = Path(e['path'])
    assert e['hash'] == hashutil.sha256_file(str(f)) and e['bytes'] < 16000
    assert db.list_segments(sid)[0]['start_seconds'] == .25
    assert drafts.finalize(draft_id)['file']['file_key'] == old_key
    assert os.path.samefile(f, drafts._chunks(draft_id)[0])
    assert recording_library.library()['drafts'] == []
    with running_app() as url:
        import urllib.request
        url += f'/sessions/{sid}/audio?v=' + e['hash']
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Range':'bytes=10-39'})) as response:
            assert response.status == 206 and response.read() == f.read_bytes()[10:40]
    assert archive.request_batch()['pending'] == 0
    f.unlink()
    drafts.finalize(draft_id)
    assert hashutil.sha256_file(str(f)) == e['hash'] and db.get_session(sid)['status'] == 'completed'


def test_failed_conversion_preserves_original_and_retries(monkeypatch):
    sid, draft_id, original = recording()
    completed(sid)
    payload = original.read_bytes()
    encode = archive._encode
    def broken(source, target, duration):
        target.write_bytes(b'incomplete')
        raise OSError('out of disk space')
    monkeypatch.setattr(archive, '_encode', broken)
    archive.process_next()
    assert archive.status()['failed'] == 1 and original.read_bytes() == payload
    assert Path(db.audio_files_for_session(sid)[0]['path']) == original
    assert not list(original.parent.rglob('*.tmp'))
    monkeypatch.setattr(archive, '_encode', encode)
    archive.request_batch()
    assert archive.process_next() and archive.status()['failed'] == 0


def test_retention_legacy_grace_new_completion_off_and_reenable():
    old, old_draft, old_file = recording()
    completed(old)
    new, _, new_file = recording()
    completed(new)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now.isoformat()
    with db.write_txn() as c:
        c.execute("UPDATE class_sessions SET audio_completed_at=NULL WHERE id=?", (old,))
        c.execute("UPDATE class_sessions SET audio_completed_at=? WHERE id=?", ((now+timedelta(days=3)).isoformat(),new))
    assert archive.set_retention(True)['text_only']
    db.set_setting(archive._RETENTION,start)
    archive._expire(now+timedelta(days=7,seconds=-1))
    assert old_file.is_file()
    archive.set_retention(False)
    archive._expire(now+timedelta(days=60))
    assert old_file.is_file()
    archive.set_retention(True)
    db.set_setting(archive._RETENTION,start)
    job = db.create_job(old,'analyze',status='analyzing')
    archive._expire(now+timedelta(days=7))
    assert old_file.is_file()
    db.update_job(job,status='completed')
    archive._expire(now+timedelta(days=7))
    assert not old_file.exists() and db.get_session(old)['audio_expired_at']
    assert new_file.is_file() and db.list_segments(old) and db.get_session(old)['notes_data']
    assert not drafts._chunks(old_draft)
    with pytest.raises(drafts.DraftError):
        drafts.finalize(old_draft)
    archive._expire(now+timedelta(days=10))
    assert not new_file.exists()


def test_partial_translation_failed_and_external_audio_preserved(tmp_path):
    sid, _, src = recording()
    db.replace_segments(sid,[{'text':'an incomplete translation','language':'en'}])
    completed(sid)
    assert not archive.status()['pending']
    archive.set_retention(True)
    db.set_setting(archive._RETENTION,'2010-01-01T00:00:00+00:00')
    archive._expire(datetime.now(timezone.utc))
    assert src.is_file()
    db.replace_segments(sid,[{'text':'a complete translation','display_text':'翻译完成','language':'en'}])
    outside=tmp_path/'user-original.wav'
    outside.write_bytes(src.read_bytes())
    db.update_session(sid,audio_path=str(outside),audio_manifest=None)
    assert archive.request_batch()['pending'] == 1
    archive.process_next()
    archive._expire(datetime.now(timezone.utc))
    assert outside.is_file() and db.get_session(sid)['audio_path'] == str(outside)
    db.update_session(sid,status='failed',audio_path=str(src))
    archive._expire(datetime.now(timezone.utc))
    assert src.is_file()


def test_multifile_order_and_small_file_preserved():
    sid, _, first = recording()
    course=db.get_session(sid)['course_id']
    other=new_draft(sid,course)
    drafts.finalize(other['id'])
    original=db.audio_files_for_session(sid)
    completed(sid)
    archive.process_next()
    entries=db.audio_files_for_session(sid)
    assert len(entries)==2 and [e['duration'] for e in entries]==[1,1]
    assert [Path(e['path']).stem for e in entries]==[Path(e['path']).stem for e in original]
    assert db.get_session(sid)['audio_duration_seconds']==2
    assert all(e['archived'] for e in entries)
    # Already small audio is retained byte-for-byte under the same name.
    raw = entries[0]
    path = Path(raw['path'])
    size = path.stat().st_size
    assert size < raw['duration']*4000
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(archive,'_encode',lambda *args:pytest.fail('small recording re-encoded'))
        archive._compress_file(sid, {**raw,'archived':False})
    assert path.stat().st_size==size


def test_timestamp_gaps_survive_archiving(tmp_path):
    # Two tone bursts separated by a one-second timestamp gap in a real container.
    import struct, math
    source=tmp_path/'source.mkv';target=tmp_path/'out.tmp'
    with av.open(str(source),'w') as out:
        st=out.add_stream('pcm_s16le',rate=48000);st.layout='mono'
        for i in range(100):
            frame=av.AudioFrame(format='s16',layout='mono',samples=960)
            frame.sample_rate=48000;frame.time_base=Fraction(1,48000)
            frame.pts=i*960+(48000 if i>=50 else 0)
            frame.planes[0].update(struct.pack('<960h',*(int(12000*math.sin(2*math.pi*400*n/48000)) for n in range(960))))
            for packet in st.encode(frame):out.mux(packet)
        for packet in st.encode(None):out.mux(packet)
    archive._encode(source,target,3)
    peaks=[]
    with av.open(str(target)) as inp:
        for frame in inp.decode(audio=0):
            if frame.time is not None and any(abs(frame.time-t)<.015 for t in (.5,1.5,2.5)):
                peaks.append((round(frame.time,1),max(abs(v) for v in frame.to_ndarray().flat)))
    assert any(t==1.5 and v<.05 for t,v in peaks)
    assert all(any(a==t and v>.1 for a,v in peaks) for t in (.5,2.5))


def test_batch_can_compress_old_transcripts_with_missing_translations_but_retention_keeps_audio():
    sid, _, _ = recording()
    db.replace_segments(sid, [{'text': 'Older English transcript', 'language': 'en'}])
    completed(sid)
    assert archive.status()['pending'] == 0
    with running_app() as url:
        assert request(url+'/audio-storage')[1]['eligible'] == 1
        status, data = request(url+'/audio-storage/compress', method='POST', data={})
        assert status == 200 and data['pending'] == 1
        assert request(url+'/audio-storage', method='PUT', data={'text_only':'false'})[0] == 400
        assert request(url+'/audio-storage', method='PUT', data={'text_only':True})[1]['text_only']
    assert archive.process_next()
    e = db.audio_files_for_session(sid)[0]
    assert e['archived']
    archive._expire(datetime.now(timezone.utc)+timedelta(days=15))
    assert Path(e['path']).is_file() and db.list_segments(sid)


def test_cleanup_after_locked_original_and_interrupted_work(monkeypatch):
    sid, _, old = recording()
    completed(sid)
    unlink = Path.unlink
    def locked(path, *args, **kw):
        if path == old:
            raise PermissionError('open player')
        return unlink(path,*args,**kw)
    monkeypatch.setattr(Path,'unlink',locked)
    archive.process_next()
    f = Path(db.audio_files_for_session(sid)[0]['path'])
    assert f.is_file() and old.is_file()
    assert db.get_conn().execute('SELECT COUNT(*) FROM audio_cleanup').fetchone()[0] == 1
    monkeypatch.setattr(Path,'unlink',unlink)
    with db.write_txn() as c:
        c.execute("UPDATE audio_archive_tasks SET state='working' WHERE session_id=?",(sid,))
    db.close_all_connections()
    archive._collect()
    assert not old.exists() and archive.process_next() and not archive.status()['working']
    assert f.is_file()
