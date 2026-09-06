"""处理任务状态与取消控制。

单一活动任务约束（默认 max_concurrency=1）在 pipeline 层强制；这里只提供状态中文标签与
取消标志位（用于让长时间运行的转写/分析循环及时退出）。
"""
from __future__ import annotations

from .logutil import get_logger

LOG = get_logger("jobs")

# 处理状态（与需求文档一致）
STATUS_LABELS = {
    "waiting_upload": "等待上传",
    "file_check": "文件检查",
    "waiting_model": "等待模型",
    "transcribing": "正在转写",
    "analyzing": "正在分析",
    "indexing": "正在建立搜索索引",
    "completed": "已完成",
    "failed": "处理失败",
    "cancelled": "已取消",
}

JOB_TYPE_LABELS = {
    "transcribe": "转写并分析",
    "analyze": "仅分析（使用已有转写）",
    "retranscribe": "重新转写",
    "reanalyze": "仅重新分析",
}

# 取消标志：被用户在 UI 点击「取消」后置位
_CANCEL: set[str] = set()


def request_cancel(session_id: str) -> None:
    _CANCEL.add(session_id)
    LOG.info("用户请求取消 session=%s", session_id)


def is_cancelled(session_id: str) -> bool:
    return session_id in _CANCEL


def clear_cancel(session_id: str) -> None:
    _CANCEL.discard(session_id)


def reset_cancel() -> None:
    _CANCEL.clear()
