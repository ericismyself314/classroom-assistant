"""数据备份与恢复（ZIP 形式）。

安全要点：
- 备份仅包含已知子目录（database / transcripts / audio / recording-drafts / 设置导出），不遍历任意路径。
- 恢复前自动创建当前数据快照（以防恢复出错可回退）。
- 恢复时对每一个 ZIP 成员做路径穿越检查（防 Zip Slip），任何越界成员直接丢弃并告警。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from . import db, paths, recording_drafts
from .logutil import get_logger

LOG = get_logger("backup")

_BACKUP_INCLUDES = ["database", "transcripts", "audio", "recording-drafts"]

# SQLite WAL 模式的临时文件，不应进备份、也不应在恢复时覆盖
_TRANSIENT_DB_SUFFIXES = ("-shm", "-wal", "-journal")


def _is_transient(name: str) -> bool:
    base = os.path.basename(name)
    return base.endswith(_TRANSIENT_DB_SUFFIXES)


def _snapshot_file(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def _replace_file(source, destination):
    # Restoring must not mutate an inode held by a concurrently compressed backup snapshot.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(destination), prefix='.restore-', delete=False) as stream:
            temporary = stream.name
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _sqlite_backup(source_path, destination_path, timeout=30):
    """Copy a committed SQLite snapshot without replacing files held by other threads."""
    if os.path.normcase(os.path.realpath(source_path)) == os.path.normcase(os.path.realpath(destination_path)):
        raise ValueError('数据库快照源和目标不能相同')
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    source = target = None
    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise TimeoutError('本地数据库快照等待超时，请稍后重试')

    try:
        source = sqlite3.connect(Path(source_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        source.execute('PRAGMA busy_timeout=1000')
        if source.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('备份数据库完整性检查失败')
        if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='class_sessions'").fetchone() is None:
            raise ValueError('备份中没有课堂数据库')
        target = sqlite3.connect(destination_path, timeout=1, isolation_level=None)
        target.execute('PRAGMA busy_timeout=1000')
        source.backup(target, pages=256, progress=progress, sleep=0.05)
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def create_backup(include_audio: bool = True) -> str:
    data_dir = paths.load_data_dir()
    dirs = paths.DataDirs(data_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_name = f"classroom-assistant-backup-{stamp}.zip"
    out_path = os.path.join(dirs.exports, out_name)

    with tempfile.TemporaryDirectory(prefix='.backup-snapshot-', dir=dirs.exports) as stage:
        assert os.path.commonpath([os.path.realpath(dirs.exports), os.path.realpath(stage)]) == os.path.realpath(dirs.exports)
        with recording_drafts._LOCK, db._WRITE_LOCK:
            settings_snapshot = db.get_all_settings()
            database_dir = os.path.join(stage, 'database')
            os.makedirs(database_dir)
            _sqlite_backup(dirs.db_path(), os.path.join(database_dir, os.path.basename(dirs.db_path())))
            for sub in _BACKUP_INCLUDES:
                if sub == 'database' or (not include_audio and sub in {'audio', 'recording-drafts'}):
                    continue
                root = os.path.join(data_dir, sub)
                if os.path.isdir(root):
                    shutil.copytree(root, os.path.join(stage, sub), copy_function=_snapshot_file,
                                    ignore=lambda _path, names: [name for name in names
                                        if _is_transient(name) or name.endswith(('.tmp', '.link-tmp'))])
        # Compression reads only the frozen names/inodes and never holds recording or DB locks.
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("settings.json", json.dumps(settings_snapshot, ensure_ascii=False, indent=2))
            zf.writestr("backup-manifest.json", json.dumps({"version": 1, "data_dir": data_dir}))
            for sub in _BACKUP_INCLUDES:
                for base, _dirs, files in os.walk(os.path.join(stage, sub)):
                    for name in files:
                        full = os.path.join(base, name)
                        zf.write(full, os.path.relpath(full, stage).replace(os.sep, '/'))
    LOG.info("已创建备份: %s", out_path)
    return out_path


def _safe_extract_members(zf: zipfile.ZipFile, dest: str) -> list[str]:
    """检查每个成员是否越界；返回允许解压的成员名列表。"""
    dest_abs = os.path.abspath(dest)
    allowed = []
    for name in zf.namelist():
        if name.replace('\\', '/').startswith('database/') and _is_transient(name):
            continue
        # 防 Zip Slip：绝对路径或包含 .. 一律拒绝
        if name.startswith("/") or name.startswith("\\") or ".." in name.split("/") or ".." in name.split("\\"):
            LOG.warning("备份成员被拒绝（路径穿越）: %s", name)
            continue
        target = os.path.abspath(os.path.join(dest_abs, name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            LOG.warning("备份成员被拒绝（越界）: %s", name)
            continue
        allowed.append(name)
    return allowed


def _move_inside_data(source, destination, data_dir):
    root = os.path.normcase(os.path.realpath(data_dir))
    for target in (source, destination):
        resolved = os.path.normcase(os.path.realpath(target))
        if resolved == root or os.path.commonpath([root, resolved]) != root:
            raise ValueError('草稿恢复目标不在应用数据目录内')
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.rename(source, destination)


def _restore_drafts(source, destination, snapshot, data_dir):
    os.makedirs(destination, exist_ok=True)
    for identifier in os.listdir(source):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            continue
        incoming = os.path.join(source, identifier)
        if not os.path.isdir(incoming):
            continue
        target = os.path.join(destination, identifier)
        previous = None
        if os.path.exists(target):
            previous = os.path.join(snapshot, 'displaced-drafts', identifier + '-' + uuid.uuid4().hex)
            _move_inside_data(target, previous, data_dir)
        try:
            shutil.copytree(incoming, target, copy_function=_replace_file)
        except BaseException:
            if os.path.exists(target):
                failed = os.path.join(snapshot, 'failed-drafts', identifier + '-' + uuid.uuid4().hex)
                _move_inside_data(target, failed, data_dir)
            if previous:
                _move_inside_data(previous, target, data_dir)
            raise


def _rebase_restored_audio(old_data_dir, data_dir):
    if not isinstance(old_data_dir, str) or not os.path.isabs(old_data_dir):
        return
    old_audio = os.path.normcase(os.path.abspath(os.path.join(old_data_dir, 'audio')))
    new_audio = os.path.abspath(os.path.join(data_dir, 'audio'))

    def rebase(path):
        if not isinstance(path, str) or not os.path.isabs(path):
            return path
        absolute = os.path.normcase(os.path.abspath(path))
        try:
            if os.path.commonpath([old_audio, absolute]) != old_audio:
                return path
        except ValueError:
            return path
        relative = os.path.relpath(absolute, old_audio)
        candidate = os.path.join(new_audio, relative)
        return candidate if os.path.isfile(candidate) else path

    for row in db.read_conn().execute('SELECT id,audio_path,audio_manifest FROM class_sessions').fetchall():
        updates = {}
        current = row['audio_path']
        if current and rebase(current) != current:
            updates['audio_path'] = rebase(current)
        if row['audio_manifest']:
            try:
                entries = json.loads(row['audio_manifest'])
                changed = False
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict) and entry.get('path'):
                            updated = rebase(entry['path'])
                            changed = changed or updated != entry['path']
                            entry['path'] = updated
                    if changed:
                        updates['audio_manifest'] = json.dumps(entries, ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        if updates:
            db.update_session(row['id'], **updates)


def restore_backup(zip_path: str) -> dict:
    from . import audio_archive
    # This lock order matches draft finalization and prevents a new recording/job from starting mid-restore.
    with audio_archive.during_restore(), recording_drafts._LOCK, db._WRITE_LOCK:
        if recording_drafts.has_active_recordings() or db.active_jobs():
            raise recording_drafts.DraftError('正在录音或处理课堂，请结束或取消后再恢复备份', 409)
        try:
            return _restore_backup(zip_path)
        finally:
            recording_drafts.reset_runtime_state()


def _restore_backup(zip_path: str) -> dict:
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"备份文件不存在: {zip_path}")
    data_dir = paths.load_data_dir()
    dirs = paths.DataDirs(data_dir)

    # 1) 恢复前自动快照
    snap_dir = os.path.join(dirs.exports, "pre-restore-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        os.makedirs(snap_dir, exist_ok=True)
        snapshot_database = os.path.join(snap_dir, 'database', os.path.basename(dirs.db_path()))
        _sqlite_backup(dirs.db_path(), snapshot_database)
        for sub in _BACKUP_INCLUDES:
            if sub == 'database':
                continue
            src = os.path.join(data_dir, sub)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(snap_dir, sub), dirs_exist_ok=True)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError('创建恢复前快照失败，已停止恢复，现有资料未覆盖') from e

    try:
        return _apply_backup(zip_path, data_dir, dirs, snap_dir)
    except BaseException as original:
        try:
            _rollback_snapshot(snap_dir, data_dir, dirs)
        except Exception as rollback_error:
            raise RuntimeError(f'恢复失败且自动回滚未能完成，原资料快照保留在：{snap_dir}') from rollback_error
        raise RuntimeError('恢复失败，已恢复原有资料；恢复前快照仍保留') from original


def _rollback_snapshot(snapshot, data_dir, dirs):
    errors = []
    for sub in _BACKUP_INCLUDES:
        source = os.path.join(snapshot, sub)
        destination = os.path.join(data_dir, sub)
        try:
            if sub == 'database':
                _sqlite_backup(os.path.join(source, os.path.basename(dirs.db_path())), dirs.db_path())
            elif sub == 'recording-drafts':
                original_ids = set(os.listdir(source)) if os.path.isdir(source) else set()
                if os.path.isdir(destination):
                    for identifier in os.listdir(destination):
                        if re.fullmatch(r'[a-f0-9]{32}', identifier) and identifier not in original_ids:
                            _move_inside_data(os.path.join(destination, identifier),
                                os.path.join(snapshot, 'failed-restore-drafts', identifier + '-' + uuid.uuid4().hex), data_dir)
                if os.path.isdir(source):
                    _restore_drafts(source, destination, snapshot, data_dir)
            elif os.path.isdir(source):
                shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=_replace_file)
        except Exception as error:
            errors.append(error)
    db.init_db()
    db.rebuild_fts()
    if errors:
        raise RuntimeError('部分原资料无法自动回滚，完整快照已保留') from errors[0]


def _apply_backup(zip_path, data_dir, dirs, snap_dir):
    # 3) 解压到临时目录后做穿越检查，再并入数据目录
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, "r") as zf:
            allowed = _safe_extract_members(zf, tmp)
            zf.extractall(tmp, members=allowed)
        restored_database = os.path.join(tmp, 'database', os.path.basename(dirs.db_path()))
        if not os.path.isfile(restored_database):
            raise ValueError('备份中缺少课堂数据库')
        old_data_dir = None
        metadata = os.path.join(tmp, 'backup-manifest.json')
        if os.path.isfile(metadata):
            try:
                with open(metadata, encoding='utf-8') as stream:
                    old_data_dir = json.load(stream).get('data_dir')
            except (ValueError, AttributeError):
                pass
        # 4) 迁移到数据目录
        for sub in _BACKUP_INCLUDES + ["settings.json"]:
            src = os.path.join(tmp, sub)
            if sub == "settings.json":
                if os.path.exists(src):
                    try:
                        with open(src, "r", encoding="utf-8") as f:
                            settings = json.load(f)
                        for k, v in settings.items():
                            db.set_setting(k, v)
                    except Exception as e:  # noqa: BLE001
                        LOG.warning("恢复设置失败: %s", e)
                continue
            if os.path.isdir(src):
                dst = os.path.join(data_dir, sub)
                if sub == 'database':
                    _sqlite_backup(restored_database, dirs.db_path())
                    continue
                if sub == 'recording-drafts':
                    _restore_drafts(src, dst, snap_dir, data_dir)
                    continue
                shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=_replace_file)

    # 旧备份可能没有新版本字段；先跑迁移，再重建搜索索引。
    db.init_db()
    _rebase_restored_audio(old_data_dir, data_dir)
    db.mark_interrupted_jobs()
    db.rebuild_fts()
    LOG.info("备份恢复完成: %s", zip_path)
    return {"restored": zip_path, "snapshot": snap_dir}
