"""文件哈希、音频元信息读取、重复导入检测。"""
from __future__ import annotations

import hashlib
import os

from .logutil import get_logger

LOG = get_logger("hashutil")

_CHUNK = 1024 * 1024


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_size(path: str) -> int:
    return os.path.getsize(path)


def _timebase_seconds(value, time_base) -> float | None:
    """把 PyAV 的时间刻度换算为秒。

    PyAV 不同版本对全局 ``av.time_base`` 的暴露形式不完全一致：
    有的版本是每秒刻度数（1000000），有的版本是 Fraction(1, 1000000)。
    流级 time_base 通常是“每刻度多少秒”。统一在这里处理，避免 m4a
    时长被多除一次 1e6，导致 2 小时录音显示成 0.008 秒。
    """
    if value is None or time_base is None:
        return None
    try:
        val = float(value)
        tb = float(time_base)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if tb == 0:
        return None
    seconds = val / tb if abs(tb) >= 1 else val * tb
    if seconds < 0:
        return None
    return seconds


def audio_duration_seconds(path: str) -> float | None:
    """读取音频时长（秒），包含浏览器录音常见的无时长 WebM。"""
    try:
        import av

        with av.open(path) as container:
            if container.duration is not None:
                duration = _timebase_seconds(container.duration, av.time_base)
                if duration is not None and duration > 0:
                    return duration
            # 回退：用流帧数估算
            audio_stream = None
            for stream in container.streams:
                if stream.type != "audio":
                    continue
                if audio_stream is None:
                    audio_stream = stream
                if stream.duration is None:
                    continue
                duration = _timebase_seconds(stream.duration, getattr(stream, "time_base", None))
                if duration is not None and duration > 0:
                    return duration
                # 极旧的 PyAV/解码器可能没有流 time_base，最后才用采样率兜底。
                if getattr(stream, "rate", None) and stream.duration > 0:
                    return float(stream.duration) / float(stream.rate)
            if audio_stream is None:
                return None
            # MediaRecorder 生成的 WebM 通常没有容器/流级时长；读取最后一个
            # 音频包的结束时间，保留多段课堂之间的停顿与正确时间轴。
            last_end = 0.0
            for packet in container.demux(audio_stream):
                timestamp = packet.pts if packet.pts is not None else packet.dts
                if timestamp is None:
                    continue
                end = timestamp + (packet.duration or 0)
                seconds = _timebase_seconds(end, getattr(packet, "time_base", None)
                                            or getattr(audio_stream, "time_base", None))
                if seconds is not None:
                    last_end = max(last_end, seconds)
            return last_end or None
    except Exception as e:  # noqa: BLE001
        LOG.warning("无法读取音频时长 %s: %s", path, e)
        return None


SUPPORTED_AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".webm", ".mp4", ".mov"}


def is_supported_audio(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_AUDIO_EXT


def is_audio_corrupted(path: str) -> bool:
    """简单探测：能否被解码器打开并至少读到一帧。"""
    try:
        import av

        with av.open(path) as container:
            audio = next((s for s in container.streams if s.type == "audio"), None)
            if audio is None:
                return True
            for _ in container.decode(audio):
                return False
            return True  # 没有任何音频帧
    except Exception:
        return True
