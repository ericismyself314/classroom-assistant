"""诊断包导出（需求文档阶段十九）。

导出一份用于排错的 ZIP，明确不包含：音频、转写原文、密钥、模型权重。
包含：日志（脱敏）、脱敏后的设置、环境信息、数据库表行数（不含内容）。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import zipfile

from . import config, db
from .ai import manifest, whisper_worker
from .ai.provider import detect_status
from .logutil import get_logger
from .selftest import _detect_webview2

LOG = get_logger("diagnostics")

_KEY_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|api_key[\"'=:\s]+[^\s,'\"]+)", re.IGNORECASE)


def _redact(text: str) -> str:
    return _KEY_RE.sub("<redacted>", text)


def _env_info() -> dict:
    try:
        import subprocess

        pip = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        pip = ""
    wv_ok, wv_detail = _detect_webview2()
    st = detect_status(db.get_all_settings())
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": config.get_config().dirs.data_dir,
        "webview2": {"ok": wv_ok, "detail": wv_detail},
        "gpu": whisper_worker.gpu_info(),
        "whisper_downloaded": whisper_worker.is_model_downloaded(
            manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"]),
        "llm_status": st,
        "pip_freeze": pip.splitlines(),
    }


def _db_summary() -> dict:
    tables = ["semesters", "courses", "timetable_slots", "class_sessions",
              "transcript_segments", "session_topics", "important_items",
              "processing_jobs", "app_settings"]
    out = {}
    for t in tables:
        try:
            out[t] = db.read_conn().execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:  # noqa: BLE001
            out[t] = "n/a"
    # 重要事项按 verification_status 分布（不含内容）
    try:
        rows = db.read_conn().execute(
            "SELECT verification_status, COUNT(*) FROM important_items GROUP BY verification_status").fetchall()
        out["important_by_status"] = {r["verification_status"]: r["COUNT(*)"] for r in rows}
    except Exception:  # noqa: BLE001
        pass
    return out


def export_diagnostics() -> str:
    """生成诊断 ZIP，返回其路径（位于数据目录的 exports/ 下）。"""
    cfg = config.init_config()
    db.init_db()
    out_dir = cfg.dirs.exports
    os.makedirs(out_dir, exist_ok=True)
    import tempfile

    tmp = tempfile.mkdtemp(prefix="diag_", dir=out_dir)
    os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)

    # 1) 环境信息
    with open(os.path.join(tmp, "environment.json"), "w", encoding="utf-8") as f:
        json.dump(_env_info(), f, ensure_ascii=False, indent=2, default=str)

    # 2) 脱敏设置
    try:
        settings = {r["key"]: r["value"] for r in
                    db.read_conn().execute("SELECT key, value FROM app_settings").fetchall()}
        settings = {k: ("<redacted>" if "key" in k.lower() or "secret" in k.lower() else v)
                    for k, v in settings.items()}
    except Exception:  # noqa: BLE001
        settings = {}
    with open(os.path.join(tmp, "settings_redacted.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    # 3) 数据库摘要（仅行数，不含转写/音频内容）
    with open(os.path.join(tmp, "db_summary.json"), "w", encoding="utf-8") as f:
        json.dump(_db_summary(), f, ensure_ascii=False, indent=2)

    # 4) 日志（脱敏后复制）
    log_dir = cfg.dirs.logs
    if os.path.isdir(log_dir):
        for name in os.listdir(log_dir):
            if name.endswith(".log"):
                src = os.path.join(log_dir, name)
                try:
                    with open(src, "r", encoding="utf-8", errors="replace") as fin, \
                         open(os.path.join(tmp, "logs", name), "w", encoding="utf-8") as fout:
                        for line in fin:
                            fout.write(_redact(line))
                except Exception:  # noqa: BLE001
                    pass

    # 打包（不含音频/转写原文/密钥/模型）
    zip_path = os.path.join(out_dir, "classroom-assistant-diagnostics.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(tmp):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    LOG.info("诊断包已生成: %s", zip_path)
    return zip_path
