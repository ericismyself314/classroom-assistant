"""Online backup keeps live WAL connections usable and bounds busy retries."""
import os
import sqlite3
import threading

import pytest

from backend import backup, config, db


def test_online_snapshot_contains_committed_wal_and_leaves_live_connection_open(tmp_path):
    semester = db.create_semester('WAL semester', '2026-01-01', '2027-01-01')
    course = db.create_course(semester, 'Committed in WAL')
    live = db.get_conn()
    assert os.path.exists(config.get_config().dirs.db_path() + '-wal')
    destination = tmp_path / 'snapshot.db'
    backup._sqlite_backup(config.get_config().dirs.db_path(), str(destination))
    with sqlite3.connect(destination) as snapshot:
        assert snapshot.execute('SELECT name FROM courses WHERE id=?', (course,)).fetchone()[0] == 'Committed in WAL'
    assert live.execute('SELECT name FROM courses WHERE id=?', (course,)).fetchone()[0] == 'Committed in WAL'


def test_online_backup_busy_timeout_is_atomic_and_closes_its_connections(tmp_path):
    semester = db.create_semester('Busy semester', '2026-01-01', '2027-01-01')
    db.create_course(semester, 'Before snapshot')
    live_path = config.get_config().dirs.db_path()
    snapshot_path = tmp_path / 'snapshot.db'
    backup._sqlite_backup(live_path, str(snapshot_path))
    newer = db.create_course(semester, 'Keep current data on timeout')
    blocker = sqlite3.connect(live_path, check_same_thread=False, isolation_level=None)
    blocker.execute('BEGIN IMMEDIATE')

    def release_if_broken_timeout():
        try:
            blocker.rollback()
        except sqlite3.Error:
            pass

    watchdog = threading.Timer(3, release_if_broken_timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        with pytest.raises(TimeoutError, match='超时'):
            backup._sqlite_backup(str(snapshot_path), live_path, timeout=0.01)
    finally:
        watchdog.cancel()
        blocker.rollback()
        blocker.close()
    assert db.get_course(newer)['name'] == 'Keep current data on timeout'
    # Windows refuses this rename if the helper leaked its read-only source connection.
    moved = tmp_path / 'moved-snapshot.db'
    os.rename(snapshot_path, moved)
    assert moved.is_file()
    assert db.create_course(semester, 'Writes still work')


def test_invalid_source_is_rejected_before_changing_live_database(tmp_path):
    semester = db.create_semester('Invalid source semester', '2026-01-01', '2027-01-01')
    course = db.create_course(semester, 'Keep this course')
    unrelated = tmp_path / 'unrelated.db'
    with sqlite3.connect(unrelated) as source:
        source.execute('CREATE TABLE unrelated(value TEXT)')
    with pytest.raises(ValueError, match='课堂数据库'):
        backup._sqlite_backup(str(unrelated), config.get_config().dirs.db_path())
    assert db.get_course(course)['name'] == 'Keep this course'
