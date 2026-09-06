"""直接验证 _stream_to_file：流式 + 边写边算 hash，与一次性读再算 hash 一致。"""
import hashlib
import io
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from backend.api import _stream_to_file, _MAX_UPLOAD_BYTES  # noqa


class _FakeHandler(BaseHTTPRequestHandler):
    """模拟 BaseHTTPRequestHandler 暴露 rfile + headers，但不真正 bind。"""
    def log_message(self, *a, **k):  # noqa
        pass


def _make_handler(body: bytes, content_length: int | None):
    h = _FakeHandler.__new__(_FakeHandler)
    h.rfile = io.BufferedReader(io.BytesIO(body))  # type: ignore[assignment]
    h.headers = {}
    if content_length is not None:
        h.headers["Content-Length"] = str(content_length)
    return h


def test_stream_matches_onerun():
    body = os.urandom(3 * 1024 * 1024 + 17)  # ~3MB
    expected_sha = hashlib.sha256(body).hexdigest()
    expected_len = len(body)

    # Content-Length 模式
    h = _make_handler(body, expected_len)
    tmp = tempfile.mkdtemp(prefix="ca_strm_")
    dest = os.path.join(tmp, "out.bin")
    written, sha = _stream_to_file(h, dest)
    assert written == expected_len, (written, expected_len)
    assert sha == expected_sha, (sha, expected_sha)
    with open(dest, "rb") as f:
        assert f.read() == body
    print("[1] Content-Length mode: written, sha256, bytes all match")


def test_stream_too_large_declared():
    body = b""
    h = _make_handler(body, _MAX_UPLOAD_BYTES + 1)
    h.headers["Content-Length"] = str(_MAX_UPLOAD_BYTES + 1)
    tmp = tempfile.mkdtemp(prefix="ca_strm_")
    dest = os.path.join(tmp, "out.bin")
    try:
        _stream_to_file(h, dest)
    except Exception as e:
        print("[2] declared too large raised:", type(e).__name__, str(e)[:60])
        assert "过大" in str(e)
        return
    raise AssertionError("expected _UploadTooLarge")


def test_stream_truncates_to_declared():
    # 声明小就只读 declared 那么多（HTTP/1.1 服务端语义：Content-Length 是 ground truth）。
    # 防止恶意 client 谎报长度把内存打爆。
    body = os.urandom(2 * 1024 * 1024)  # 真实有 2MB
    h = _make_handler(body, 1024)  # 声明只 1KB
    tmp = tempfile.mkdtemp(prefix="ca_strm_")
    dest = os.path.join(tmp, "out.bin")
    written, sha = _stream_to_file(h, dest)
    assert written == 1024
    assert os.path.getsize(dest) == 1024
    print("[3] declared small -> only reads declared 1024 bytes (not 2MB)")


def test_stream_no_length_respects_max():
    # 无 Content-Length（chunked/EOF）下，写到上限就抛错并清理文件。
    body = os.urandom(_MAX_UPLOAD_BYTES + 4096)  # 比上限大一点
    h = _make_handler(body, None)  # 不带 Content-Length
    h.headers.pop("Content-Length", None)
    tmp = tempfile.mkdtemp(prefix="ca_strm_")
    dest = os.path.join(tmp, "out.bin")
    raised = False
    try:
        _stream_to_file(h, dest)
    except Exception as e:
        raised = True
        assert "过大" in str(e), str(e)
        assert not os.path.exists(dest), "out.bin should be removed"
    assert raised
    print("[4] no Content-Length + exceeds max -> raises + cleans up")


if __name__ == "__main__":
    test_stream_matches_onerun()
    test_stream_too_large_declared()
    test_stream_truncates_to_declared()
    test_stream_no_length_respects_max()
    print("STREAM_OK")
