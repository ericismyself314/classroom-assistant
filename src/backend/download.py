"""可断点续传的模型/二进制下载（带进度与哈希校验）。

仅用于首次下载语音/文字模型与 llama.cpp 服务端二进制；运行时不做任何其它网络请求。
"""
from __future__ import annotations

import hashlib
import os
import ssl
import urllib.request

from .logutil import get_logger

LOG = get_logger("download")


def download_file(url: str, dest: str, progress_cb=None, sha256: str | None = None, timeout: int = 30) -> str:
    """下载到 dest，支持 Range 续传。progress_cb(downloaded, total)。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    existing = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    req = urllib.request.Request(url)
    if existing > 0:
        req.add_header("Range", f"bytes={existing}-")
    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法连接下载地址: {e}")

    if resp.status == 206:
        mode = "ab"
        downloaded = existing
        total = existing + int(resp.headers.get("Content-Length", 0) or 0)
    else:
        mode = "wb"
        downloaded = 0
        total = int(resp.headers.get("Content-Length", 0) or 0)

    h = hashlib.sha256()
    with open(tmp, mode) as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            h.update(chunk)
            if progress_cb:
                progress_cb(downloaded, total)
    if sha256 and h.hexdigest().lower() != sha256.lower():
        os.remove(tmp)
        raise ValueError("模型文件哈希校验失败，可能下载不完整或被篡改")
    os.replace(tmp, dest)
    LOG.info("下载完成: %s (%d bytes)", dest, downloaded)
    return dest
