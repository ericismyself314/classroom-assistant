"""SQLite 数据库：连接管理、迁移、Schema、FTS5 全文索引与仓储函数。

设计要点：
- 使用 WAL 模式、开启外键与合理 busy_timeout，提升并发与崩溃安全性。
- 每个线程持有一个独立的数据库连接（thread-local），避免跨线程共享同一连接。
- 写操作通过全局写锁串行化，避免 "database is locked"。
- 提供迁移表 schema_migrations；后续结构变更通过新增迁移版本实现，不假设结构永远不变。
- 全文搜索使用 FTS5，覆盖 TranscriptSegment.text / SessionTopic.title / SessionTopic.summary /
  ImportantItem.title / ImportantItem.details，并保存用于筛选的未索引列（session_id/course_id/
  session_date/content_type）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import paths
from .config import get_config
from .logutil import get_logger

LOG = get_logger("db")

_local = threading.local()
_WRITE_LOCK = threading.RLock()
_SCHEMA_VERSION = 6


# ----------------------------------------------------------------------------
# 迁移定义（每条为 (version, sql)）
# ----------------------------------------------------------------------------
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS semesters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Hong_Kong',
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS courses (
            id TEXT PRIMARY KEY,
            semester_id TEXT NOT NULL,
            name TEXT NOT NULL,
            code TEXT,
            teacher_name TEXT,
            location TEXT,
            language_mode TEXT NOT NULL DEFAULT 'auto',
            color TEXT NOT NULL DEFAULT '#4CAF50',
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (semester_id) REFERENCES semesters(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS timetable_slots (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            location TEXT,
            recurrence_start TEXT,
            recurrence_end TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS class_sessions (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            week_number INTEGER,
            title TEXT,
            location TEXT,
            status TEXT NOT NULL DEFAULT 'waiting_upload',
            source_type TEXT,
            audio_path TEXT,
            audio_hash TEXT,
            audio_duration_seconds REAL,
            transcript_language TEXT,
            transcription_model TEXT,
            analysis_model TEXT,
            processing_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS transcript_segments (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            -- 文字导入没有音频时间戳，必须允许 NULL；录音转写仍会写入数值。
            start_seconds REAL,
            end_seconds REAL,
            text TEXT NOT NULL,
            original_text TEXT,
            language TEXT,
            confidence REAL,
            is_uncertain INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS session_topics (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            start_seconds REAL,
            end_seconds REAL,
            evidence_segment_ids TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS important_items (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            details TEXT,
            raw_date_text TEXT,
            normalized_date TEXT,
            action_required INTEGER NOT NULL DEFAULT 0,
            confidence TEXT NOT NULL DEFAULT 'explicit',
            evidence_data TEXT,
            is_confirmed INTEGER NOT NULL DEFAULT 0,
            is_completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS processing_jobs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT,
            progress_data TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_courses_semester ON courses(semester_id);
        CREATE INDEX IF NOT EXISTS idx_slots_course ON timetable_slots(course_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_course ON class_sessions(course_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_date ON class_sessions(session_date);
        CREATE INDEX IF NOT EXISTS idx_segments_session ON transcript_segments(session_id);
        CREATE INDEX IF NOT EXISTS idx_topics_session ON session_topics(session_id);
        CREATE INDEX IF NOT EXISTS idx_items_session ON important_items(session_id);
        CREATE INDEX IF NOT EXISTS idx_items_course ON important_items(course_id);
        CREATE INDEX IF NOT EXISTS idx_items_category ON important_items(category);
        CREATE INDEX IF NOT EXISTS idx_jobs_session ON processing_jobs(session_id);
        """,
    ),
    (
        2,
        """
        ALTER TABLE important_items ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'pending';
        ALTER TABLE important_items ADD COLUMN context_type TEXT;
        ALTER TABLE important_items ADD COLUMN rejection_reason TEXT;
        ALTER TABLE important_items ADD COLUMN evidence_matched INTEGER NOT NULL DEFAULT 1;
        CREATE INDEX IF NOT EXISTS idx_items_verify ON important_items(verification_status);
        """,
    ),
    (
        3,
        """
        -- 版本 1/2 的老数据库把时间戳设为 NOT NULL；重建表以兼容无时间戳文字导入。
        DROP TABLE IF EXISTS transcript_segments_new;
        CREATE TABLE transcript_segments_new (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            start_seconds REAL,
            end_seconds REAL,
            text TEXT NOT NULL,
            original_text TEXT,
            language TEXT,
            confidence REAL,
            is_uncertain INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
        );
        INSERT INTO transcript_segments_new
            (id, session_id, sequence_number, start_seconds, end_seconds, text,
             original_text, language, confidence, is_uncertain, created_at, updated_at)
        SELECT id, session_id, sequence_number, start_seconds, end_seconds, text,
               original_text, language, confidence, is_uncertain, created_at, updated_at
        FROM transcript_segments;
        DROP TABLE transcript_segments;
        ALTER TABLE transcript_segments_new RENAME TO transcript_segments;
        CREATE INDEX IF NOT EXISTS idx_segments_session ON transcript_segments(session_id);
        """,
    ),
    (4, """
        ALTER TABLE courses ADD COLUMN vocabulary TEXT NOT NULL DEFAULT '';
        ALTER TABLE class_sessions ADD COLUMN notes_data TEXT;
        CREATE TABLE session_revisions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES class_sessions(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_revisions_session ON session_revisions(session_id);
    """),
    (5, """
        -- 保留 ASR 英文原文，同时保存给用户看的简体中文展示稿。
        ALTER TABLE transcript_segments ADD COLUMN display_text TEXT;
    """),
    (6, """
        -- 一节课可以由多个连续录音分段组成。audio_path 继续保留为首段，
        -- audio_manifest 保存完整的有序文件清单，兼容旧数据库和旧接口。
        ALTER TABLE class_sessions ADD COLUMN audio_manifest TEXT;
    """),
]


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_search USING fts5(
    content,
    row_type UNINDEXED,
    ref_id UNINDEXED,
    session_id UNINDEXED,
    course_id UNINDEXED,
    session_date UNINDEXED,
    content_type UNINDEXED,
    tokenize='trigram'
);
"""


# ----------------------------------------------------------------------------
# 连接管理
# ----------------------------------------------------------------------------
def _new_connection() -> sqlite3.Connection:
    cfg = get_config()
    conn = sqlite3.connect(cfg.dirs.db_path(), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_connection()
        _local.conn = conn
    return conn


def close_all_connections() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def checkpoint(mode: str = "TRUNCATE") -> None:
    """把 WAL 中未落盘的数据刷回主库文件，并清理 -wal/-shm 临时文件。

    备份前调用可保证只依赖主库 .db 文件即可拿到全部已提交数据；
    恢复前调用可释放被内存映射占用的 -shm 文件，避免复制冲突。
    """
    try:
        with write_txn() as c:
            c.execute(f"PRAGMA wal_checkpoint({mode})")
    except Exception as e:  # noqa: BLE001
        LOG.warning("WAL checkpoint 失败: %s", e)


@contextmanager
def write_txn():
    """写事务：获取全局写锁，提交或回滚。"""
    with _WRITE_LOCK:
        conn = get_conn()
        nested = conn.in_transaction
        savepoint = gen_id("transaction") if nested else None
        try:
            conn.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN")
            yield conn
            conn.execute(f"RELEASE SAVEPOINT {savepoint}" if nested else "COMMIT")
        except Exception:
            if conn.in_transaction:
                if nested:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    conn.execute("ROLLBACK")
            raise


def read_conn() -> sqlite3.Connection:
    return get_conn()


# ----------------------------------------------------------------------------
# 初始化 / 迁移
# ----------------------------------------------------------------------------
def init_db() -> None:
    cfg = get_config()
    os.makedirs(os.path.dirname(cfg.dirs.db_path()), exist_ok=True)
    conn = get_conn()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
    )
    cur = conn.execute("SELECT version FROM schema_migrations")
    applied = {r["version"] for r in cur.fetchall()}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        with write_txn() as c:
            # executescript commits an existing transaction first. Execute complete
            # statements individually so a failed migration cannot half-apply.
            statement = ""
            for line in sql.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ""
            if version == 1:
                c.execute(FTS_DDL)
            c.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, now_iso()),
            )
        LOG.info("已应用数据库迁移 version=%s", version)
    # 确保 FTS 表存在（以防迁移中被跳过）
    conn.execute(FTS_DDL)
    LOG.info("数据库初始化完成: %s", cfg.dirs.db_path())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"


# ----------------------------------------------------------------------------
# 学期
# ----------------------------------------------------------------------------
def create_semester(name, start_date, end_date, timezone="Asia/Hong_Kong", is_active=0):
    sid = gen_id("sem")
    with write_txn() as c:
        c.execute(
            "INSERT INTO semesters(id,name,start_date,end_date,timezone,is_active,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, name, start_date, end_date, timezone, int(bool(is_active)), now_iso(), now_iso()),
        )
    return sid


def list_semesters() -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM semesters ORDER BY start_date DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_semester(sid: str) -> dict | None:
    r = read_conn().execute("SELECT * FROM semesters WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def set_active_semester(sid: str) -> None:
    with write_txn() as c:
        c.execute("UPDATE semesters SET is_active=0")
        c.execute("UPDATE semesters SET is_active=1, updated_at=? WHERE id=?", (now_iso(), sid))


def get_active_semester() -> dict | None:
    r = read_conn().execute("SELECT * FROM semesters WHERE is_active=1").fetchone()
    if r:
        return dict(r)
    r = read_conn().execute("SELECT * FROM semesters ORDER BY start_date DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def update_semester(sid, **fields) -> None:
    allowed = {"name", "start_date", "end_date", "timezone", "is_active"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with write_txn() as c:
        c.execute(f"UPDATE semesters SET {cols} WHERE id=?", tuple(fields.values()) + (sid,))


# ----------------------------------------------------------------------------
# 课程
# ----------------------------------------------------------------------------
def create_course(semester_id, name, code=None, teacher_name=None, location=None,
                  language_mode="auto", color="#4CAF50", is_archived=0):
    cid = gen_id("course")
    with write_txn() as c:
        c.execute(
            "INSERT INTO courses(id,semester_id,name,code,teacher_name,location,language_mode,color,is_archived,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, semester_id, name, code, teacher_name, location, language_mode, color,
             int(bool(is_archived)), now_iso(), now_iso()),
        )
    return cid


def list_courses(active_only=True, semester_id=None) -> list[dict]:
    sql = "SELECT * FROM courses"
    conds = []
    params: list = []
    if active_only:
        conds.append("is_archived=0")
    if semester_id:
        conds.append("semester_id=?")
        params.append(semester_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY name"
    rows = read_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_course(cid: str) -> dict | None:
    r = read_conn().execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def update_course(cid, **fields) -> None:
    allowed = {"semester_id", "name", "code", "teacher_name", "location",
               "language_mode", "vocabulary", "color", "is_archived"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with write_txn() as c:
        c.execute(f"UPDATE courses SET {cols} WHERE id=?", tuple(fields.values()) + (cid,))


def archive_course(cid, archived: bool) -> None:
    with write_txn() as c:
        c.execute("UPDATE courses SET is_archived=?, updated_at=? WHERE id=?",
                  (int(bool(archived)), now_iso(), cid))


# ----------------------------------------------------------------------------
# 课表槽位
# ----------------------------------------------------------------------------
def create_slot(course_id, weekday, start_time, end_time, location=None,
                recurrence_start=None, recurrence_end=None) -> str:
    sid = gen_id("slot")
    with write_txn() as c:
        c.execute(
            "INSERT INTO timetable_slots(id,course_id,weekday,start_time,end_time,location,recurrence_start,recurrence_end,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, course_id, int(weekday), start_time, end_time, location,
             recurrence_start, recurrence_end, now_iso(), now_iso()),
        )
    return sid


def list_slots(course_id: str) -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM timetable_slots WHERE course_id=? ORDER BY weekday, start_time", (course_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def replace_slots(course_id: str, slots: list[dict]) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM timetable_slots WHERE course_id=?", (course_id,))
        for s in slots:
            c.execute(
                "INSERT INTO timetable_slots(id,course_id,weekday,start_time,end_time,location,recurrence_start,recurrence_end,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (gen_id("slot"), course_id, int(s["weekday"]), s["start_time"], s["end_time"],
                 s.get("location"), s.get("recurrence_start"), s.get("recurrence_end"),
                 now_iso(), now_iso()),
            )


# ----------------------------------------------------------------------------
# 课堂记录
# ----------------------------------------------------------------------------
def get_or_create_session(course_id, session_date, **fields) -> str:
    conn = read_conn()
    r = conn.execute(
        "SELECT id FROM class_sessions WHERE course_id=? AND session_date=?",
        (course_id, session_date),
    ).fetchone()
    if r:
        return r["id"]
    sid = gen_id("sess")
    with write_txn() as c:
        c.execute(
            "INSERT INTO class_sessions(id,course_id,session_date,start_time,end_time,week_number,title,location,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, course_id, session_date, fields.get("start_time"), fields.get("end_time"),
             fields.get("week_number"), fields.get("title"), fields.get("location"),
             fields.get("status", "waiting_upload"), now_iso(), now_iso()),
        )
    return sid


def get_session(sid: str) -> dict | None:
    r = read_conn().execute("SELECT * FROM class_sessions WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def audio_files_for_session(session_or_id) -> list[dict]:
    """Return ordered audio files for a session, including legacy single-file rows.

    Paths are only used by the local backend. Callers that return this data to the
    frontend should expose metadata, not the absolute paths.
    """
    session = get_session(session_or_id) if isinstance(session_or_id, str) else session_or_id
    if not session:
        return []
    raw = session.get("audio_manifest")
    if raw:
        try:
            entries = __import__("json").loads(raw)
        except (TypeError, ValueError):
            entries = []
        if isinstance(entries, list):
            files = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or not entry.get("path"):
                    continue
                files.append({
                    "index": index,
                    "path": entry["path"],
                    "name": entry.get("name") or os.path.basename(entry["path"]),
                    "bytes": entry.get("bytes"),
                    "duration": entry.get("duration"),
                    "hash": entry.get("hash"),
                })
            if files:
                return files
    if session.get("audio_path"):
        return [{
            "index": 0,
            "path": session["audio_path"],
            "name": os.path.basename(session["audio_path"]),
            "bytes": None,
            "duration": session.get("audio_duration_seconds"),
            "hash": session.get("audio_hash"),
        }]
    return []


def list_sessions(course_id: str, include_empty=True) -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM class_sessions WHERE course_id=? ORDER BY session_date DESC", (course_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_session(sid, **fields) -> None:
    allowed = {"session_date", "start_time", "end_time", "week_number", "title", "location",
               "status", "source_type", "audio_path", "audio_hash", "audio_duration_seconds",
               "audio_manifest", "transcript_language", "transcription_model", "analysis_model",
               "processing_error", "notes_data"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with write_txn() as c:
        c.execute(f"UPDATE class_sessions SET {cols} WHERE id=?", tuple(fields.values()) + (sid,))


def delete_session(sid: str) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM class_sessions WHERE id=?", (sid,))


def snapshot_session(sid: str, reason: str) -> None:
    """Keep a local, exportable revision before replacing transcript or analysis."""
    import json
    with write_txn() as c:
        segments = list_segments(sid)
        if not segments:
            return
        payload = {"session": get_session(sid), "segments": segments,
                   "topics": list_topics(sid),
                   "items": [dict(r) for r in c.execute(
                       "SELECT * FROM important_items WHERE session_id=?", (sid,))]}
        c.execute("INSERT INTO session_revisions VALUES (?,?,?,?,?)",
                  (gen_id("rev"), sid, reason, json.dumps(payload, ensure_ascii=False), now_iso()))


def list_revisions(sid: str) -> list[dict]:
    return [dict(r) for r in read_conn().execute(
        "SELECT id,reason,created_at FROM session_revisions WHERE session_id=? ORDER BY created_at DESC", (sid,))]


def save_analysis(sid, topics, items, notes, model_label):
    import json
    with write_txn():
        replace_topics(sid, topics)
        replace_items(sid, items)
        update_session(sid, analysis_model=model_label, notes_data=json.dumps(notes, ensure_ascii=False),
                       status="completed", processing_error=None)
        index_session(sid)


# ----------------------------------------------------------------------------
# 转写片段
# ----------------------------------------------------------------------------
def replace_segments(session_id, segments: list[dict]) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM transcript_segments WHERE session_id=?", (session_id,))
        for i, seg in enumerate(segments):
            c.execute(
                "INSERT INTO transcript_segments(id,session_id,sequence_number,start_seconds,end_seconds,text,display_text,original_text,language,confidence,is_uncertain,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gen_id("seg"), session_id, i, seg.get("start_seconds"), seg.get("end_seconds"),
                 seg["text"], seg.get("display_text"), seg.get("original_text"), seg.get("language"),
                 seg.get("confidence"), int(bool(seg.get("is_uncertain", False))),
                 now_iso(), now_iso()),
            )


def list_segments(session_id: str) -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM transcript_segments WHERE session_id=? ORDER BY sequence_number", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_segment_text(seg_id: str, text: str) -> None:
    with write_txn() as c:
        c.execute("UPDATE transcript_segments SET text=?, updated_at=? WHERE id=?",
                  (text, now_iso(), seg_id))


def update_segment_display_texts(session_id: str, segments: list[dict]) -> None:
    """Persist translated display text by source segment id without changing ASR text."""
    with write_txn() as c:
        for seg in segments:
            if seg.get("id"):
                c.execute("UPDATE transcript_segments SET display_text=?, updated_at=? WHERE id=? AND session_id=?",
                          (seg.get("display_text") or None, now_iso(), seg["id"], session_id))


# ----------------------------------------------------------------------------
# 主题
# ----------------------------------------------------------------------------
def replace_topics(session_id, topics: list[dict]) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM session_topics WHERE session_id=?", (session_id,))
        for i, t in enumerate(topics):
            c.execute(
                "INSERT INTO session_topics(id,session_id,sequence_number,title,summary,start_seconds,end_seconds,evidence_segment_ids,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (gen_id("topic"), session_id, i, t["title"], t.get("summary"),
                 t.get("start_seconds"), t.get("end_seconds"),
                 _json_or_none(t.get("evidence_segment_ids")), now_iso(), now_iso()),
            )


def list_topics(session_id: str) -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM session_topics WHERE session_id=? ORDER BY sequence_number", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# 重要事项
# ----------------------------------------------------------------------------
def replace_items(session_id, items: list[dict]) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM important_items WHERE session_id=?", (session_id,))
        for it in items:
            c.execute(
                "INSERT INTO important_items(id,session_id,course_id,category,title,details,raw_date_text,normalized_date,action_required,confidence,evidence_data,verification_status,context_type,rejection_reason,evidence_matched,is_confirmed,is_completed,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gen_id("item"), session_id, it["course_id"], it["category"], it["title"],
                 it.get("details"), it.get("raw_date_text"), it.get("normalized_date"),
                 int(bool(it.get("action_required", False))), it.get("confidence", "explicit"),
                 _json_or_none(it.get("evidence_data") or it.get("evidence")),
                 it.get("verification_status", "pending"), it.get("context_type"),
                 it.get("rejection_reason"), int(bool(it.get("evidence_matched", True))),
                 int(bool(it.get("is_confirmed", False))),
                 int(bool(it.get("is_completed", False))), now_iso(), now_iso()),
            )


def list_items(filters: dict | None = None) -> list[dict]:
    # 联表 class_sessions 取出 session_date，供前端“课程 · 日期”展示（不删功能）
    sql = (
        "SELECT i.*, cs.session_date AS session_date "
        "FROM important_items i "
        "LEFT JOIN class_sessions cs ON cs.id = i.session_id"
    )
    conds = []
    params: list = []
    f = filters or {}
    if f.get("course_id"):
        conds.append("i.course_id=?")
        params.append(f["course_id"])
    if f.get("category"):
        conds.append("i.category=?")
        params.append(f["category"])
    if f.get("confidence"):
        conds.append("i.confidence=?")
        params.append(f["confidence"])
    if f.get("verification_status"):
        conds.append("i.verification_status=?")
        params.append(f["verification_status"])
    elif f.get("include_rejected") != "1":
        # 默认隐藏“已拒绝”的 AI 候选项（仍保留调试记录）
        conds.append("i.verification_status != 'rejected'")
    if f.get("handled") == "unhandled":
        conds.append("(i.is_completed=0 AND i.is_confirmed=0)")
    elif f.get("handled") == "handled":
        conds.append("(i.is_completed=1 OR i.is_confirmed=1)")
    if f.get("date_from"):
        conds.append("i.normalized_date>=?")
        params.append(f["date_from"])
    if f.get("date_to"):
        conds.append("i.normalized_date<=?")
        params.append(f["date_to"])
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY i.normalized_date, i.created_at DESC"
    rows = read_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_item(item_id, **fields) -> None:
    allowed = {"category", "title", "details", "raw_date_text", "normalized_date",
               "action_required", "confidence", "evidence_data", "is_confirmed", "is_completed"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with write_txn() as c:
        c.execute(f"UPDATE important_items SET {cols} WHERE id=?", tuple(fields.values()) + (item_id,))


def delete_item(item_id: str) -> None:
    with write_txn() as c:
        c.execute("DELETE FROM important_items WHERE id=?", (item_id,))


# ----------------------------------------------------------------------------
# 处理任务
# ----------------------------------------------------------------------------
def create_job(session_id, job_type, status="pending", stage=None) -> str:
    jid = gen_id("job")
    with write_txn() as c:
        c.execute(
            "INSERT INTO processing_jobs(id,session_id,job_type,status,stage,progress_data,attempt_count,error_message,started_at,finished_at,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, session_id, job_type, status, stage, None, 0, None, None, None, now_iso(), now_iso()),
        )
    return jid


def get_job(jid: str) -> dict | None:
    r = read_conn().execute("SELECT * FROM processing_jobs WHERE id=?", (jid,)).fetchone()
    return dict(r) if r else None


def update_job(jid, **fields) -> None:
    allowed = {"status", "stage", "progress_data", "attempt_count", "error_message",
               "started_at", "finished_at"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    with write_txn() as c:
        c.execute(f"UPDATE processing_jobs SET {cols} WHERE id=?", tuple(fields.values()) + (jid,))


def active_jobs() -> list[dict]:
    rows = read_conn().execute(
        "SELECT * FROM processing_jobs WHERE status IN ('pending','file_check','waiting_model','transcribing','analyzing','indexing')"
        " ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_interrupted_jobs() -> None:
    """应用重启后，把仍在进行中的任务标记为失败，等待用户重试。"""
    with write_txn() as c:
        c.execute(
            "UPDATE processing_jobs SET status='failed', error_message='应用重启时任务被中断', finished_at=?, updated_at=?"
            " WHERE status IN ('running','pending','file_check','waiting_model','transcribing','analyzing','indexing')",
            (now_iso(), now_iso()),
        )
        # The UI follows session.status, so reconcile both records, including
        # sessions left stale by a previous app version that only updated jobs.
        c.execute("UPDATE class_sessions SET status='failed', processing_error='应用重启时任务被中断，请重试', updated_at=? "
                  "WHERE status IN ('file_check','transcribing','analyzing','indexing','cancelling')", (now_iso(),))


# ----------------------------------------------------------------------------
# 设置
# ----------------------------------------------------------------------------
def get_setting(key: str, default=None):
    r = read_conn().execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return default
    return r["value"]


def set_setting(key: str, value) -> None:
    with write_txn() as c:
        c.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value if isinstance(value, str) else _json_or_none(value) if isinstance(value, (dict, list)) else str(value), now_iso()),
        )


def get_all_settings() -> dict:
    rows = read_conn().execute("SELECT key,value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ----------------------------------------------------------------------------
# FTS5 全文搜索
# ----------------------------------------------------------------------------
def rebuild_fts() -> None:
    conn = get_conn()
    with write_txn() as c:
        c.execute("DROP TABLE IF EXISTS fts_search")
        c.execute(FTS_DDL)
        # 转写片段
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT COALESCE(s.display_text,s.text),'segment',s.id,s.session_id,cs.course_id,cs.session_date,'transcript' "
            "FROM transcript_segments s JOIN class_sessions cs ON cs.id=s.session_id"
        )
        # 主题
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT COALESCE(t.title,'')||' '||COALESCE(t.summary,''),'topic',t.id,t.session_id,cs.course_id,cs.session_date,'topic' "
            "FROM session_topics t JOIN class_sessions cs ON cs.id=t.session_id"
        )
        # 重要事项（不含已拒绝的 AI 候选项，避免编造内容进入搜索结果）
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT COALESCE(i.title,'')||' '||COALESCE(i.details,''),'item',i.id,i.session_id,i.course_id,cs.session_date,'important' "
            "FROM important_items i JOIN class_sessions cs ON cs.id=i.session_id "
            "WHERE i.verification_status != 'rejected'"
        )


def index_session(session_id: str) -> None:
    """增量更新某个 session 的 FTS（重建该 session 相关行）。"""
    conn = get_conn()
    with write_txn() as c:
        c.execute(
            "DELETE FROM fts_search WHERE session_id=?", (session_id,)
        )
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT s.text,'segment',s.id,s.session_id,cs.course_id,cs.session_date,'transcript' "
            "FROM transcript_segments s JOIN class_sessions cs ON cs.id=s.session_id WHERE s.session_id=?",
            (session_id,),
        )
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT COALESCE(t.title,'')||' '||COALESCE(t.summary,''),'topic',t.id,t.session_id,cs.course_id,cs.session_date,'topic' "
            "FROM session_topics t JOIN class_sessions cs ON cs.id=t.session_id WHERE t.session_id=?",
            (session_id,),
        )
        c.execute(
            "INSERT INTO fts_search(content,row_type,ref_id,session_id,course_id,session_date,content_type) "
            "SELECT COALESCE(i.title,'')||' '||COALESCE(i.details,''),'item',i.id,i.session_id,i.course_id,cs.session_date,'important' "
            "FROM important_items i JOIN class_sessions cs ON cs.id=i.session_id WHERE i.session_id=? "
            "AND i.verification_status != 'rejected'",
            (session_id,),
        )


def fts_search(query: str, filters: dict | None = None) -> list[dict]:
    """FTS5 关键词搜索，返回命中行及上下文。

    使用 trigram 分词器，支持中文子串/关键词搜索。查询不足 3 个字符或 MATCH 失败时，
    回退到 LIKE 扫描（仍覆盖同样的来源与筛选）。
    筛选：course_id / content_type / session_date_from / session_date_to。
    """
    q = (query or "").strip()
    if len(q) >= 3:
        try:
            return _fts_match(q, filters or {})
        except Exception as e:  # noqa: BLE001
            LOG.warning("FTS MATCH 失败，回退 LIKE: %s", e)
            return _like_search(q, filters or {})
    return _like_search(q, filters or {})


def _fts_match(q: str, f: dict) -> list[dict]:
    where = ["fts_search MATCH ?"]
    params: list = [q]
    if f.get("course_id"):
        where.append("fts_search.course_id=?")
        params.append(f["course_id"])
    if f.get("content_type"):
        where.append("fts_search.content_type=?")
        params.append(f["content_type"])
    if f.get("session_date_from"):
        where.append("fts_search.session_date>=?")
        params.append(f["session_date_from"])
    if f.get("session_date_to"):
        where.append("fts_search.session_date<=?")
        params.append(f["session_date_to"])
    sql = (
        "SELECT fts_search.row_type, fts_search.ref_id, fts_search.session_id, fts_search.course_id, "
        "fts_search.session_date, fts_search.content_type, fts_search.content AS snippet_text, "
        "rank FROM fts_search WHERE " + " AND ".join(where) +
        " ORDER BY rank LIMIT 200"
    )
    rows = read_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _like_search(q: str, f: dict) -> list[dict]:
    """短查询 / MATCH 失败时的回退：在三个来源上做 LIKE 扫描。"""
    out: list[dict] = []
    like = f"%{q}%"
    conds = []
    params: list = [like]
    if f.get("course_id"):
        conds.append("cs.course_id=?")
        params.append(f["course_id"])
    if f.get("session_date_from"):
        conds.append("cs.session_date>=?")
        params.append(f["session_date_from"])
    if f.get("session_date_to"):
        conds.append("cs.session_date<=?")
        params.append(f["session_date_to"])
    extra = (" AND " + " AND ".join(conds)) if conds else ""

    rows = read_conn().execute(
        "SELECT 'segment' row_type, s.id ref_id, s.session_id, cs.course_id, cs.session_date, 'transcript' content_type, s.text snippet_text "
        "FROM transcript_segments s JOIN class_sessions cs ON cs.id=s.session_id WHERE s.text LIKE ?" + extra,
        params,
    ).fetchall()
    out += [dict(r) for r in rows]

    rows = read_conn().execute(
        "SELECT 'topic' row_type, t.id ref_id, t.session_id, cs.course_id, cs.session_date, 'topic' content_type, "
        "COALESCE(t.title,'')||' '||COALESCE(t.summary,'') snippet_text "
        "FROM session_topics t JOIN class_sessions cs ON cs.id=t.session_id WHERE (t.title LIKE ? OR t.summary LIKE ?)" + extra,
        [like, like] + params[1:],
    ).fetchall()
    out += [dict(r) for r in rows]

    rows = read_conn().execute(
        "SELECT 'item' row_type, i.id ref_id, i.session_id, i.course_id, cs.session_date, 'important' content_type, "
        "COALESCE(i.title,'')||' '||COALESCE(i.details,'') snippet_text "
        "FROM important_items i JOIN class_sessions cs ON cs.id=i.session_id WHERE (i.title LIKE ? OR i.details LIKE ?)" + extra,
        [like, like] + params[1:],
    ).fetchall()
    out += [dict(r) for r in rows]
    return out[:200]


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def _json_or_none(obj):
    if obj is None:
        return None
    import json

    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False)


def _row_to_dict(r):
    return dict(r) if r else None
