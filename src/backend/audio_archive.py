"""Local, restartable storage maintenance for completed classroom recordings."""
from __future__ import annotations

import json
import math
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, hashutil, recording_drafts, recording_library
from .config import get_config
from .logutil import get_logger

LOG = get_logger("audio_archive")
_WAKE = threading.Event()
_STOP = threading.Event()
_MAINTENANCE = threading.Lock()
_thread = None
_RETENTION = "audio_text_only_since"

# Earlier Chinese transcripts may predate display_text. An untranslated English
# segment or an unfinished analysis must keep its source even under retention.
_HAS_NOTES = """
    s.status='completed' AND s.audio_path IS NOT NULL AND COALESCE(s.source_type,'audio')='audio'
    AND (length(s.notes_data)>2 OR EXISTS(SELECT 1 FROM session_topics n WHERE n.session_id=s.id))
    AND EXISTS(SELECT 1 FROM transcript_segments t WHERE t.session_id=s.id AND trim(t.text)!='')
"""
_TRANSLATED = """NOT EXISTS(SELECT 1 FROM transcript_segments t WHERE t.session_id=s.id
        AND trim(t.text)!='' AND trim(COALESCE(t.display_text,''))=''
        AND COALESCE(NULLIF(t.language,''),s.transcript_language,'') NOT LIKE 'zh%')
"""
_ELIGIBLE = f"{_HAS_NOTES} AND {_TRANSLATED}"
_QUEUED = f"{_HAS_NOTES} AND (t.manual=1 OR {_TRANSLATED})"


def analysis_saved(sid):
    """Called inside the transaction that saves the new notes and their index."""
    conn = db.get_conn()
    changed = conn.execute(f"UPDATE class_sessions AS s SET audio_completed_at=? WHERE s.id=? AND {_ELIGIBLE}",
                           (db.now_iso(), sid)).rowcount
    if changed:
        conn.execute("""INSERT INTO audio_archive_tasks(session_id) VALUES (?)
                        ON CONFLICT(session_id) DO UPDATE SET state='pending',manual=0,error=NULL""", (sid,))


def status():
    conn = db.read_conn()
    counts = {row["state"]: row["n"] for row in conn.execute(f"""SELECT t.state,COUNT(*) n
        FROM audio_archive_tasks t JOIN class_sessions s ON s.id=t.session_id WHERE {_QUEUED} GROUP BY t.state""")}
    row = conn.execute("""SELECT COALESCE(SUM(saved_bytes),0) AS saved FROM audio_archive_tasks""").fetchone()
    candidates = conn.execute(f"""SELECT count(*) FROM class_sessions s LEFT JOIN audio_archive_tasks t
        ON t.session_id=s.id WHERE {_HAS_NOTES} AND (t.state IS NULL OR t.state='failed'
        OR (t.state IN ('pending','working') AND NOT (t.manual=1 OR {_TRANSLATED})))""").fetchone()[0]
    error = conn.execute("SELECT error FROM audio_archive_tasks WHERE state='failed' ORDER BY rowid DESC LIMIT 1").fetchone()
    return {"text_only": bool(db.get_setting(_RETENTION)), "eligible": candidates,
            "pending": counts.get("pending", 0), "working": counts.get("working", 0),
            "done": counts.get("done", 0), "unchanged": counts.get("unchanged", 0),
            "failed": counts.get("failed", 0), "saved_bytes": row["saved"], "error": error[0] if error else None}


def request_batch():
    with db.write_txn() as conn:
        conn.execute(f"""INSERT INTO audio_archive_tasks(session_id,manual)
            SELECT s.id,1 FROM class_sessions s WHERE {_HAS_NOTES}
            ON CONFLICT(session_id) DO UPDATE SET manual=1,
            state=CASE WHEN state='failed' THEN 'pending' ELSE state END,error=NULL
            WHERE state IN ('pending','working','failed')""")
    _WAKE.set()
    return status()


def set_retention(enabled):
    if not isinstance(enabled, bool):
        raise ValueError("请选择是否在 7 天后只留文字。")
    with db.write_txn():
        if bool(db.get_setting(_RETENTION)) != enabled:
            db.set_setting(_RETENTION, db.now_iso() if enabled else "")
    _WAKE.set()
    return status()


def _root():
    return Path(get_config().dirs.audio).resolve()


def _owned(path):
    root = _root()
    p = Path(path)
    if not p.is_absolute() or not p.is_relative_to(root):
        raise ValueError("录音位于课堂助手目录之外。")
    resolved = p.resolve()
    if resolved == root or not resolved.is_relative_to(root) or resolved != p:
        raise ValueError("录音路径已改变。")
    return resolved


def _check_stop():
    if _STOP.is_set():
        raise InterruptedError("应用正在关闭")


def _update_durations(sid, entry, duration):
    # Older multi-part manifests omitted per-file durations. The player needs
    # them to map a classroom timestamp into the correct part, even if it was already small.
    if entry.get("duration") == duration:
        return
    with db.write_txn():
        files = db.audio_files_for_session(sid)
        if entry not in files or not _eligible(sid):
            return
        files[entry["index"]]["duration"] = duration
        lengths = [f.get("duration") for f in files]
        fields = {"audio_duration_seconds": sum(lengths)} if all(lengths) else {}
        db.update_session(sid, audio_manifest=json.dumps(files, ensure_ascii=False), **fields)


def _encode(source, target, duration):
    """Write a seekable WebM. Follow the capture clock, including gaps between packets."""
    import av

    with av.open(str(source)) as inp, av.open(str(target), "w", format="webm") as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        stream.bit_rate = 32000
        stream.options = {"application": "voip"}
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000, frame_size=960,
                                      options={"async": "1", "first_pts": "0"})
        def encode_frames(frames):
            for frame in frames:
                for packet in stream.encode(frame):
                    out.mux(packet)
        for frame in inp.decode(audio=0):
            _check_stop()
            encode_frames(resampler.resample(frame))
        encode_frames(resampler.resample(None))
        for packet in stream.encode(None):
            out.mux(packet)
    length = hashutil.audio_duration_seconds(str(target))
    if length is None or abs(length - duration) > 0.15:
        raise ValueError("压缩前后时长不一致，已保留原音。")
    with av.open(str(target)) as check:
        audio = check.streams.audio[0]
        if audio.codec_context.name != "opus" or len(audio.codec_context.layout.channels) != 1:
            raise ValueError("语音格式校验失败。")
        for second in (0, duration * .5, max(0, duration - .2)):
            check.seek(int(second / audio.time_base), stream=audio, backward=True)
            for frame in check.decode(audio):
                if frame.time is not None and frame.time + frame.samples / frame.sample_rate >= second - .025:
                    if frame.time > second + .10:
                        raise ValueError("回听时间校验失败。")
                    break
            else:
                raise ValueError("压缩版无法定位到原录音时间。")


def _eligible(sid):
    return db.read_conn().execute(f"""SELECT 1 FROM class_sessions s JOIN audio_archive_tasks t
        ON t.session_id=s.id WHERE s.id=? AND {_QUEUED}""", (sid,)).fetchone()


def _signature(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _enqueue_cleanup(conn, path, sid, replacement=None):
    info = path.stat()
    conn.execute("""INSERT OR REPLACE INTO audio_cleanup(path,size,modified_ns,session_id,replacement)
                    VALUES(?,?,?,?,?)""", (str(path.relative_to(_root())), info.st_size, info.st_mtime_ns,
                    sid, str(replacement.relative_to(_root())) if replacement else None))


def _still_referenced(path):
    rows = db.read_conn().execute("SELECT audio_path,audio_hash,audio_duration_seconds,audio_manifest "
                                  "FROM class_sessions WHERE audio_path IS NOT NULL OR audio_manifest IS NOT NULL")
    return any(Path(e["path"]) == path for row in rows for e in db.audio_files_for_session(dict(row)))


def _collect():
    # A locked Windows playback handle or a crash after COMMIT leaves a retryable receipt.
    rows = db.read_conn().execute("SELECT * FROM audio_cleanup").fetchall()
    for row in rows:
        try:
            with recording_drafts._LOCK, db.write_txn() as conn:
                path = _owned(_root() / row["path"])
                if _still_referenced(path):
                    continue
                if row["replacement"]:
                    replacement = _owned(_root() / row["replacement"])
                    entries = db.audio_files_for_session(row["session_id"])
                    entry = next((e for e in entries if Path(e["path"]) == replacement), None)
                    if entry:
                        if (recording_library.file_info(entry)["availability"] != "available"
                                or hashutil.sha256_file(str(replacement)) != entry["hash"]):
                            continue
                        recording_drafts.archive_recovery(row["session_id"], entry)
                if path.is_file():
                    st = path.stat()
                    if (st.st_size, st.st_mtime_ns) == (row["size"], row["modified_ns"]):
                        path.unlink()
                conn.execute("DELETE FROM audio_cleanup WHERE path=?", (row["path"],))
        except (OSError, ValueError, recording_drafts.DraftError):
            LOG.warning("录音空间整理将在下次重试", exc_info=True)


def _compress_file(sid, entry):
    if entry.get("archived"):
        return
    info = recording_library.file_info(entry)
    if info["availability"] != "available":
        raise ValueError("原录音暂时无法读取或不在应用内。")
    source = _owned(entry["path"])
    original_stat = source.stat()
    duration = hashutil.audio_duration_seconds(str(source))
    if not duration or not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法确认原录音时长。")
    # A recording already below the target bitrate cannot yield a worthwhile saving.
    if original_stat.st_size <= duration * 4000:
        _update_durations(sid, entry, duration)
        return
    import av
    with av.open(str(source)) as media:
        if (len(media.streams.audio) != 1 or any(not (v.disposition & av.stream.Disposition.attached_pic)
                                                for v in media.streams.video)):
            _update_durations(sid, entry, duration)
            return
    if entry.get("hash") and hashutil.sha256_file(str(source)) != entry["hash"]:
        raise ValueError("原录音与保存时的校验值不一致。")
    directory = _owned(source.parent / "voice-archive")
    directory.mkdir(exist_ok=True)
    name = source.stem if recording_drafts.draft_file_id(source.name) else source.name
    final = _owned(directory / (name + ".webm"))
    temp = _owned(final.with_name(final.name + ".tmp"))
    try:
        _encode(source, temp, duration)
        sz = temp.stat().st_size
        if sz >= original_stat.st_size:
            _update_durations(sid, entry, duration)
            return
        with temp.open("rb+") as f:
            os.fsync(f.fileno())
        updated = {**entry, "path": str(final), "name": str(Path(entry["name"]).with_suffix(".webm").name),
                   "duration": duration, "bytes": sz, "hash": hashutil.sha256_file(str(temp)),
                   "archived": True, "file_key": info["file_key"]}
        from .api import _audio_collection_hash
        with recording_drafts._LOCK, db.write_txn() as conn:
            _check_stop()
            files = db.audio_files_for_session(sid)
            if (not _eligible(sid) or any(j["session_id"] == sid for j in db.active_jobs())
                    or entry not in files or _signature(source.stat()) != _signature(original_stat)):
                raise ValueError("课堂录音已变化，请稍后重试。")
            if _still_referenced(final):
                raise ValueError("压缩文件名已在使用。")
            _enqueue_cleanup(conn, source, sid, final)
            os.replace(temp, final)
            files[entry["index"]] = updated
            durations = [item.get("duration") for item in files]
            fields = {"audio_duration_seconds": sum(durations)} if all(durations) else {}
            db.update_session(sid, audio_path=files[0]["path"], audio_manifest=json.dumps(files, ensure_ascii=False),
                              audio_hash=_audio_collection_hash(files), **fields)
            conn.execute("UPDATE audio_archive_tasks SET saved_bytes=saved_bytes+? WHERE session_id=?",
                         (original_stat.st_size - sz, sid))
        _collect()
    finally:
        temp.unlink(missing_ok=True)


def _expire(now):
    since = db.get_setting(_RETENTION)
    if not since:
        return
    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    try:
        enabled_at = datetime.fromisoformat(since)
        if enabled_at.tzinfo is None or enabled_at > now - timedelta(days=7):
            return
    except (ValueError, TypeError):
        return
    due = f"""{_ELIGIBLE} AND COALESCE(s.audio_completed_at, ?) <= ?
        AND NOT EXISTS(SELECT 1 FROM audio_archive_tasks a WHERE a.session_id=s.id AND a.state='failed')"""
    sessions = db.read_conn().execute(f"SELECT s.id FROM class_sessions s WHERE {due}", (since, cutoff)).fetchall()
    for row in sessions:
        sid = row["id"]
        entries = db.audio_files_for_session(sid)
        try:
            paths = [_owned(e["path"]) for e in entries]
            if not paths:
                continue
            with recording_drafts.deleting_audio({sid: [str(p) for p in paths]}):
                conn = db.get_conn()
                if (recording_drafts.has_active_recordings() or db.get_setting(_RETENTION) != since
                    or entries != db.audio_files_for_session(sid)
                    or not conn.execute(f"SELECT 1 FROM class_sessions s WHERE s.id=? AND {due}",
                                        (sid, since, cutoff)).fetchone()):
                    raise recording_drafts.DraftError("录音保留设置或课堂状态已改变", 409)
                for p in paths:
                    if p.is_file():
                        _enqueue_cleanup(conn, p, sid)
                db.update_session(sid, audio_path=None, audio_manifest=None, audio_hash=None,
                                  audio_duration_seconds=None, audio_expired_at=db.now_iso())
                conn.execute("DELETE FROM audio_archive_tasks WHERE session_id=?", (sid,))
        except (ValueError, OSError, recording_drafts.DraftError):
            LOG.info("稍后重新检查课堂录音保留期 session=%s", sid)
    _collect()


def process_next():
    with _MAINTENANCE:
        with db.write_txn() as conn:
            row = conn.execute(f"""SELECT t.session_id FROM audio_archive_tasks t JOIN class_sessions s
                ON s.id=t.session_id WHERE t.state IN ('working','pending') AND {_QUEUED}
                AND NOT EXISTS(SELECT 1 FROM processing_jobs j WHERE j.session_id=s.id AND
                    j.status NOT IN ('completed','failed','cancelled')) ORDER BY t.rowid LIMIT 1""").fetchone()
            if not row:
                return False
            sid = row[0]
            conn.execute("UPDATE audio_archive_tasks SET state='working' WHERE session_id=?", (sid,))
        try:
            for entry in db.audio_files_for_session(sid):
                _compress_file(sid, entry)
            with db.write_txn() as conn:
                conn.execute("""UPDATE audio_archive_tasks SET state=CASE WHEN saved_bytes>0 THEN 'done'
                             ELSE 'unchanged' END,error=NULL WHERE session_id=?""", (sid,))
        except InterruptedError:
            raise
        except Exception as error:
            LOG.warning("课堂录音压缩未完成 session=%s", sid, exc_info=True)
            with db.write_txn() as conn:
                message = str(error) if isinstance(error, ValueError) and type(error) is ValueError else "压缩未完成，原录音已保留。"
                conn.execute("UPDATE audio_archive_tasks SET state='failed',error=? WHERE session_id=?", (message, sid))
        return True


@contextmanager
def during_restore():
    if not _MAINTENANCE.acquire(blocking=False):
        raise recording_drafts.DraftError("正在整理录音空间，请稍后恢复备份。", 409)
    try:
        yield
    finally:
        _MAINTENANCE.release()
        _WAKE.set()


def _run():
    try:
        while not _STOP.is_set():
            _WAKE.clear()
            try:
                with _MAINTENANCE:
                    _collect()
                    _expire(datetime.now(timezone.utc))
                while not _STOP.is_set() and process_next():
                    pass
            except InterruptedError:
                break
            except Exception:
                LOG.exception("录音空间后台整理稍后重试")
            _WAKE.wait(600)
    finally:
        db.close_all_connections()


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _STOP.clear()
    _thread = threading.Thread(target=_run, name="classroom-audio-storage", daemon=True)
    _thread.start()


def wake():
    _WAKE.set()


def stop():
    _STOP.set()
    _WAKE.set()
    if _thread:
        _thread.join(timeout=5)

