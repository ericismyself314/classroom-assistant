"""路径与数据目录管理。

设计要点：
- 固定根配置位于 %APPDATA%/ClassroomAssistant/app_root.json，记录真正的数据目录位置。
- 默认数据目录也为 %APPDATA%/ClassroomAssistant（C 盘空间紧张时可在设置中改到其它盘）。
- 所有子目录（database / audio / transcripts / models / logs / exports）都在数据目录下，
  不使用任何写死的绝对路径。
- 对导入/恢复时出现的路径穿越（path traversal）提供防护工具。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _appdata() -> str:
    # 用户数据放在 %LOCALAPPDATA%/ClassroomAssistant（安装目录之外，升级/卸载均保留）
    return os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")


def app_root_config_dir() -> str:
    p = os.path.join(_appdata(), "ClassroomAssistant")
    os.makedirs(p, exist_ok=True)
    return p


def root_config_path() -> str:
    """根配置文件路径（**必须每次调用时重新计算**）。

    曾经写成模块级常量，导致「先 import 再改环境变量」的场景（典型就是测试里
    把数据目录重定向到临时目录）读到的是旧路径，测试会直接读写用户真实数据目录。
    """
    return os.path.join(app_root_config_dir(), "app_root.json")


def load_data_dir() -> str:
    """读取真实数据目录；读取失败或目录不存在时回退到默认位置。"""
    default = app_root_config_dir()
    cfg_path = root_config_path()
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            dd = d.get("data_dir")
            if dd and os.path.isdir(dd):
                return dd
    except Exception:
        pass
    return default


def save_data_dir(data_dir: str) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(root_config_path(), "w", encoding="utf-8") as f:
        json.dump({"data_dir": data_dir}, f, ensure_ascii=False, indent=2)


@dataclass
class DataDirs:
    data_dir: str

    def __post_init__(self) -> None:
        self.database = os.path.join(self.data_dir, "database")
        self.audio = os.path.join(self.data_dir, "audio")
        self.transcripts = os.path.join(self.data_dir, "transcripts")
        self.models = os.path.join(self.data_dir, "models")
        self.logs = os.path.join(self.data_dir, "logs")
        self.exports = os.path.join(self.data_dir, "exports")
        self.speech_models = os.path.join(self.models, "speech")
        self.llm_models = os.path.join(self.models, "llm")
        self.llm_bin = os.path.join(self.llm_models, "bin")
        for d in (
            self.database,
            self.audio,
            self.transcripts,
            self.models,
            self.logs,
            self.exports,
            self.speech_models,
            self.llm_models,
            self.llm_bin,
        ):
            os.makedirs(d, exist_ok=True)

    # ---- 具体子路径 ----
    def db_path(self) -> str:
        return os.path.join(self.database, "classroom-assistant.db")

    def audio_dir_for(self, semester_id: str, course_id: str, session_id: str) -> str:
        # UUID-backed database ids are already unique, but writing all three full
        # values into the directory hierarchy can exceed Windows' legacy MAX_PATH
        # limit when the user chooses a deep data directory. Keep enough entropy
        # for collision resistance while leaving room for generated filenames.
        def compact_id(value: str) -> str:
            token = value.rsplit("_", 1)[-1]
            return token[:16]

        d = os.path.join(
            self.audio,
            compact_id(semester_id),
            compact_id(course_id),
            compact_id(session_id),
        )
        os.makedirs(d, exist_ok=True)
        return d

    def transcript_dir_for(self, session_id: str) -> str:
        d = os.path.join(self.transcripts, session_id)
        os.makedirs(d, exist_ok=True)
        return d

    def speech_model_dir(self, model_id: str) -> str:
        d = os.path.join(self.speech_models, model_id)
        os.makedirs(d, exist_ok=True)
        return d

    def llm_model_path(self, model_id: str) -> str:
        # GGUF 文件名在 manifest 中给出，这里仅返回目录
        return self.llm_models

    def disk_usage(self) -> dict:
        """返回数据目录占用情况（字节）。"""
        total = 0

        def _walk(root):
            nonlocal total
            for entry in os.scandir(root):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
                elif entry.is_dir():
                    _walk(entry)

        _walk(self.data_dir)
        return {
            "data_dir": self.data_dir,
            "total_bytes": total,
            "audio_bytes": _dir_size(self.audio),
            "models_bytes": _dir_size(self.models),
            "db_bytes": _dir_size(self.database),
        }


def _dir_size(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for entry in os.scandir(path):
        try:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += _dir_size(entry.path)
        except OSError:
            pass
    return total


def safe_join(base: str, *parts: str) -> str:
    """安全地拼接路径，防止路径穿越（path traversal）。

    任何 part 含有 '..' 或绝对路径都会抛 ValueError。
    """
    base_abs = os.path.abspath(base)
    for p in parts:
        if not isinstance(p, str) or p == "":
            raise ValueError(f"非法路径片段: {p!r}")
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            raise ValueError(f"不允许绝对路径: {p!r}")
        if ".." in p.split("/") or ".." in p.split("\\"):
            raise ValueError(f"不允许路径穿越: {p!r}")
    candidate = os.path.abspath(os.path.join(base_abs, *parts))
    if candidate != base_abs and not candidate.startswith(base_abs + os.sep):
        raise ValueError("路径超出允许范围")
    return candidate
