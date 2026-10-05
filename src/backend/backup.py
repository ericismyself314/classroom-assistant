"""数据备份与恢复（ZIP 形式）。

安全要点：
- 备份仅包含已知子目录（database / transcripts / audio / 设置导出），不遍历任意路径。
- 恢复前自动创建当前数据快照（以防恢复出错可回退）。
- 恢复时对每一个 ZIP 成员做路径穿越检查（防 Zip Slip），任何越界成员直接丢弃并告警。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime

from . import db, paths
from .logutil import get_logger

LOG = get_logger("backup")

_BACKUP_INCLUDES = ["database", "transcripts", "audio"]

# SQLite WAL 模式的临时文件，不应进备份、也不应在恢复时覆盖
_TRANSIENT_DB_SUFFIXES = ("-shm", "-wal", "-journal")


def _is_transient(name: str) -> bool:
    base = os.path.basename(name)
    return base.endswith(_TRANSIENT_DB_SUFFIXES)


def create_backup(include_audio: bool = True) -> str:
    cfg = db.get_config() if hasattr(db, "get_config") else None
    data_dir = paths.load_data_dir()
    dirs = paths.DataDirs(data_dir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"classroom-assistant-backup-{stamp}.zip"
    out_path = os.path.join(dirs.exports, out_name)

    # 先把 WAL 中未落盘数据刷回主库，确保只依赖主 .db 文件即可拿到全部已提交数据
    try:
        db.checkpoint("TRUNCATE")
    except Exception as e:  # noqa: BLE001
        LOG.warning("备份前 checkpoint 失败（继续）: %s", e)

    settings_snapshot = db.get_all_settings()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 设置导出
        zf.writestr("settings.json", json.dumps(settings_snapshot, ensure_ascii=False, indent=2))
        # 已知子目录
        for sub in _BACKUP_INCLUDES:
            if not include_audio and sub == "audio":
                continue
            root = os.path.join(data_dir, sub)
            if not os.path.isdir(root):
                continue
            for base, _dirs, files in os.walk(root):
                for fn in files:
                    if _is_transient(fn):
                        continue
                    full = os.path.join(base, fn)
                    arc = os.path.relpath(full, data_dir).replace(os.sep, "/")
                    zf.write(full, arc)
    LOG.info("已创建备份: %s", out_path)
    return out_path


def _safe_extract_members(zf: zipfile.ZipFile, dest: str) -> list[str]:
    """检查每个成员是否越界；返回允许解压的成员名列表。"""
    dest_abs = os.path.abspath(dest)
    allowed = []
    for name in zf.namelist():
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


def restore_backup(zip_path: str) -> dict:
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"备份文件不存在: {zip_path}")
    data_dir = paths.load_data_dir()
    dirs = paths.DataDirs(data_dir)

    # 1) 恢复前自动快照
    snap_dir = os.path.join(dirs.exports, "pre-restore-" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    try:
        os.makedirs(snap_dir, exist_ok=True)
        for sub in _BACKUP_INCLUDES:
            src = os.path.join(data_dir, sub)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(snap_dir, sub), dirs_exist_ok=True)
        shutil.copy(dirs.db_path(), os.path.join(snap_dir, "classroom-assistant.db"))
    except Exception as e:  # noqa: BLE001
        LOG.warning("创建恢复前快照失败（继续恢复）: %s", e)

    # 2) 关闭本进程对数据库的占用，并清理被内存映射锁定的 WAL 临时文件，
    #    否则恢复复制数据库目录时会因 -shm 被占用而失败（WinError 1224）。
    try:
        db.close_all_connections()
    except Exception:  # noqa: BLE001
        pass
    for suffix in _TRANSIENT_DB_SUFFIXES:
        _tp = dirs.db_path() + suffix
        if os.path.exists(_tp):
            try:
                os.remove(_tp)
            except OSError as e:
                LOG.warning("清理临时库文件失败: %s", e)

    # 3) 解压到临时目录后做穿越检查，再并入数据目录
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, "r") as zf:
            allowed = _safe_extract_members(zf, tmp)
            zf.extractall(tmp, members=allowed)
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
                # 数据库目录不复制 WAL 临时文件（SQLite 会重建），避免覆盖锁冲突
                ignore = shutil.ignore_patterns("*-shm", "*-wal", "*-journal") if sub == "database" else None
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)

    # 旧备份可能没有新版本字段；先跑迁移，再重建搜索索引。
    db.init_db()
    db.rebuild_fts()
    LOG.info("备份恢复完成: %s", zip_path)
    return {"restored": zip_path, "snapshot": snap_dir}
