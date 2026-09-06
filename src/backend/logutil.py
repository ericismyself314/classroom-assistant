"""日志工具。

隐私约束（见隐私说明）：
- 不记录完整课堂转写内容；
- 不记录 API 密钥；
- 技术错误可详细记录到本地文件，但 UI 只展示用户可理解的中文摘要。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOGGERS: dict[str, logging.Logger] = None  # type: ignore


def configure_logging(config) -> None:
    global _LOGGERS
    log_dir = config.dirs.logs
    log_file = os.path.join(log_dir, "app.log")
    level = getattr(logging, str(getattr(config, "log_level", "INFO")).upper(), logging.INFO)

    root = logging.getLogger("classroom_assistant")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # 第三方库降噪
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _LOGGERS = {}


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("classroom_assistant." + name)


def sanitize(msg: str, *, strip_transcript: bool = True, strip_secret: bool = True) -> str:
    """对可能写入日志的内容做脱敏：去除密钥与超长转写片段。"""
    if strip_secret:
        import re

        msg = re.sub(r"(sk-[A-Za-z0-9]{8,})", "***API_KEY***", msg)
        msg = re.sub(r"(api[_-]?key[\"'=:\s]+)[^\s\",}]{8,}", r"\1***", msg, flags=re.IGNORECASE)
    if strip_transcript and len(msg) > 500:
        msg = msg[:500] + "...(已截断，不记录完整内容)"
    return msg
