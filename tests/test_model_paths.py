"""模型路径一致性测试（回归防线）。

背景（真实缺陷）：
    whisper 模型的「写入目录」和「检测目录」曾经不是同一个：
      - 下载/导入写到      <speech_models>/.hf_cache/models/models--Systran--faster-whisper-<size>
      - is_model_downloaded 检测 <speech_models>/.hf_cache/models--Systran--faster-whisper-<size>
    结果是模型明明下载完了，UI/自检/就绪检查仍显示「未下载」；而在 /process 加了
    「缺模型就拒绝」的拦截后，会进一步退化成**永远无法开始转写**。

本文件用两层防线锁死这个 bug：
    1) 行为层：造出真实目录布局，验证 is_model_downloaded 的判定；
    2) 源码层：所有会计算「模型落盘目录」的调用点必须走 whisper_worker.models_root()，
       不得再出现 _cache_dir() + "models" 的手工拼接。
"""
from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from backend import api as api_mod  # noqa: E402
from backend import selftest as selftest_mod  # noqa: E402
from backend.ai import llm_provider, whisper_worker as ww  # noqa: E402

SIZE = "base"
DIRNAME = "models--Systran--faster-whisper-" + SIZE


def _make_model(root: str) -> str:
    """造出一个「已下载」的 HuggingFace 缓存布局。"""
    d = os.path.join(root, DIRNAME, "snapshots", "abc123")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "model.bin"), "wb") as f:
        f.truncate(ww._MIN_MODEL_BIN_BYTES + 1)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(d, "tokenizer.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(d, "vocabulary.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return d


# ---------------- 行为层 ----------------

def test_models_root_is_cache_models():
    assert ww.models_root() == os.path.join(ww._cache_dir(), "models")


def test_not_downloaded_when_nothing_exists():
    assert ww.is_model_downloaded(SIZE) is False


def test_placeholder_directory_is_not_a_downloaded_model():
    """历史测试遗留的 16 字节 model.bin 不能再让界面误报已就绪。"""
    d = os.path.join(ww.models_root(), DIRNAME, "snapshots", "abc123")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "model.bin"), "wb") as f:
        f.write(b"x" * 16)
    assert ww.is_model_downloaded(SIZE) is False


def test_gguf_requires_magic_and_reasonable_size(tmp_path):
    p = tmp_path / "model.gguf"
    p.write_bytes(b"GGUF" + b"x" * 16)
    assert llm_provider._valid_gguf(str(p)) is False
    with p.open("r+b") as f:
        f.truncate(llm_provider._MIN_GGUF_BYTES + 1)
    assert llm_provider._valid_gguf(str(p)) is True


def test_downloaded_when_model_in_models_root():
    _make_model(ww.models_root())
    assert ww.is_model_downloaded(SIZE) is True


def test_legacy_layout_still_recognized():
    """旧版本写到缓存根的用户，升级后不应被要求重新下载。"""
    _make_model(ww._cache_dir())
    assert ww.is_model_downloaded(SIZE) is True


def test_other_sizes_not_confused():
    _make_model(ww.models_root())
    assert ww.is_model_downloaded(SIZE) is True
    assert ww.is_model_downloaded("small") is False


def test_downloaded_flag_survives_config_reinit():
    """换一次配置初始化（相当于重启应用），判定必须仍然成立。"""
    from backend import config

    _make_model(ww.models_root())
    config.init_config()
    assert ww.is_model_downloaded(SIZE) is True


# ---------------- 源码层：单一来源 ----------------

_FORBIDDEN = ('_cache_dir(), "models"', "_cache_dir(), 'models'")

_CALLSITES = [
    ("whisper_worker.transcribe", ww.transcribe),
    ("api._download_whisper", api_mod.ApiHandler._download_whisper),
    ("api._import_model", api_mod.ApiHandler._import_model),
    ("selftest._check_whisper_ready", selftest_mod._check_whisper_ready),
]


def test_all_write_sites_use_models_root():
    for name, fn in _CALLSITES:
        src = inspect.getsource(fn)
        assert "models_root()" in src, f"{name} 未使用 models_root()，检测与写入会再次错位"
        for bad in _FORBIDDEN:
            assert bad not in src, f"{name} 仍在手工拼接缓存路径：{bad}"


def test_is_model_downloaded_checks_models_root_first():
    src = inspect.getsource(ww.is_model_downloaded)
    assert "models_root()" in src
    assert "_cache_dir()" in src, "应保留对旧布局的兼容判断"


def test_post_package_check_isolates_user_data():
    script = os.path.join(ROOT, "tests", "post_package_check.py")
    with open(script, encoding="utf-8") as f:
        src = f.read()
    assert 'tempfile.mkdtemp(prefix="ca_pkg_check_")' in src
    assert 'child_env["LOCALAPPDATA"]' in src
    assert 'child_env["APPDATA"]' in src
