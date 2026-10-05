"""阶段二：模型手动导入的安全测试（重点防 Zip-Slip）。

覆盖：
- 正常 zip 安全解压；
- 路径穿越成员（../、绝对路径、Windows 反斜杠）一律拒绝且不落盘；
- LLM .gguf（裸文件 / zip 内）落到 llm_models/<id>.gguf；
- whisper zip 整体解压到缓存 models/。
"""
import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from backend.ai import model_import as mi  # noqa: E402


def _make_zip(entries: dict) -> bytes:
    """entries: 成员名 -> 内容(bytes)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zi = zipfile.ZipInfo(name)
            zf.writestr(zi, content)
    return buf.getvalue()


def test_safe_extract_normal(tmp_path):
    z = _make_zip({"a/model.bin": b"MODEL", "b/info.txt": b"hello"})
    dest = tmp_path / "out"
    names = mi.safe_extract_zip(zipfile.ZipFile(io.BytesIO(z)), str(dest))
    assert set(names) == {"a/model.bin", "b/info.txt"}
    assert (dest / "a" / "model.bin").read_bytes() == b"MODEL"
    assert (dest / "b" / "info.txt").read_bytes() == b"hello"


def test_zip_slip_parent_rejected(tmp_path):
    z = _make_zip({"../evil.txt": b"pwned", "ok/model.bin": b"x"})
    dest = tmp_path / "out"
    escaped = tmp_path / "evil.txt"
    try:
        mi.safe_extract_zip(zipfile.ZipFile(io.BytesIO(z)), str(dest))
        assert False, "应拦截路径穿越"
    except mi.SecurityError:
        pass
    assert not escaped.exists(), "恶意文件不应落盘到受管目录之外"


def test_zip_slip_absolute_rejected(tmp_path):
    z = _make_zip({"/etc/passwd": b"root:x:0:0", "ok/model.bin": b"x"})
    dest = tmp_path / "out"
    try:
        mi.safe_extract_zip(zipfile.ZipFile(io.BytesIO(z)), str(dest))
        assert False, "应拦截绝对路径"
    except mi.SecurityError:
        pass


def test_zip_slip_windows_backslash_rejected(tmp_path):
    z = _make_zip({"..\\..\\evil.dll": b"x", "ok/model.bin": b"x"})
    dest = tmp_path / "out"
    try:
        mi.safe_extract_zip(zipfile.ZipFile(io.BytesIO(z)), str(dest))
        assert False, "应拦截 Windows 反斜杠穿越"
    except mi.SecurityError:
        pass


def test_import_raw_gguf(tmp_path):
    gguf = b"\x00GGUF" + b"x" * 64
    llm_dir = tmp_path / "llm"
    res = mi.import_model_archive(gguf, model_type="llm", model_id="qwen2.5",
                                  llm_models_dir=str(llm_dir))
    assert res["type"] == "llm"
    out = llm_dir / "qwen2.5.gguf"
    assert out.exists() and out.read_bytes() == gguf


def test_import_gguf_in_zip(tmp_path):
    gguf = b"\x00GGUF" + b"y" * 64
    z = _make_zip({"some/sub/model.gguf": gguf, "readme.txt": b"note"})
    llm_dir = tmp_path / "llm"
    res = mi.import_model_archive(z, model_type="auto", model_id="qwen2.5",
                                 llm_models_dir=str(llm_dir))
    assert res["type"] == "llm"
    out = llm_dir / "qwen2.5.gguf"
    assert out.exists() and out.read_bytes() == gguf


def test_import_whisper_zip_rejected(tmp_path):
    z = _make_zip({"legacy-speech/snapshots/abc/model.bin": b"W"})
    try:
        mi.import_model_archive(z, model_type="whisper", model_id="legacy",
                                llm_models_dir=str(tmp_path / "llm"))
        assert False, "旧语音模型导入应被拒绝"
    except ValueError as exc:
        assert "Qwen3-ASR-0.6B" in str(exc)


def test_import_empty_rejected(tmp_path):
    try:
        mi.import_model_archive(b"", model_type="auto", model_id="x",
                                llm_models_dir=str(tmp_path / "l"))
        assert False
    except ValueError:
        pass
