"""validate_model_installation 的单元测试。

覆盖 4 种状态：ok / no_snapshot / blob_missing（snapshot 在但 model.bin 不在）/ blob_truncated（size 不足）。

实现：把 _cache_dir / models_root monkeypatch 到临时目录，互不污染。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from backend.ai import whisper_worker as w


def _build_repo(root: str, size: str, *,
                with_model_bin=True, model_size=472213 * 1024,
                with_config=True, with_tokenizer=True, with_vocab=True,
                revision="r1", owner="Systran", vocab_name="vocabulary.txt"):
    """在 root/.hf_cache/models/models--Systran--faster-whisper-<size> 下造一个 repo。"""
    repo = os.path.join(root, "models", f"models--{owner}--faster-whisper-{size}")
    rev_dir = os.path.join(repo, "snapshots", revision)
    os.makedirs(rev_dir, exist_ok=True)
    if with_config:
        with open(os.path.join(rev_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"model_type": "whisper"}, f)
    if with_tokenizer:
        with open(os.path.join(rev_dir, "tokenizer.json"), "w", encoding="utf-8") as f:
            f.write("{}" * 100)
    if with_vocab:
        with open(os.path.join(rev_dir, vocab_name), "w", encoding="utf-8") as f:
            f.write("a\n" * 100)
    if with_model_bin:
        with open(os.path.join(rev_dir, "model.bin"), "wb") as f:
            f.write(b"\x00" * model_size)
    return repo, rev_dir


def _isolated():
    """返回 (tmp_dir, restore_fn) —— 测试期间 w._cache_dir / models_root 指向 tmp。"""
    tmp = tempfile.mkdtemp(prefix="ca_v_")
    orig_cache = w._cache_dir
    orig_root = w.models_root
    w._cache_dir = lambda: tmp  # type: ignore[assignment]
    w.models_root = lambda: os.path.join(tmp, "models")  # type: ignore[assignment]

    def restore():
        w._cache_dir = orig_cache  # type: ignore[assignment]
        w.models_root = orig_root  # type: ignore[assignment]
    return tmp, restore


def test_ok():
    tmp, restore = _isolated()
    try:
        _build_repo(tmp, "small", with_model_bin=True, model_size=472213 * 1024)
        v = w.validate_model_installation("small")
        assert v["ok"] is True, v
        assert v["missing_files"] == [], v
        print("[1] ok scenario: pass")
    finally:
        restore()


def test_no_snapshot():
    tmp, restore = _isolated()
    try:
        v = w.validate_model_installation("small")
        assert v["ok"] is False, v
        assert v["reason"] == "no_snapshot", v
        print("[2] no_snapshot: pass")
    finally:
        restore()


def test_blob_missing():
    """snapshot 在但 model.bin 不在 → broken（用户真实场景是断链）"""
    tmp, restore = _isolated()
    try:
        repo, _ = _build_repo(tmp, "small", with_model_bin=False)
        v = w.validate_model_installation("small")
        assert v["ok"] is False, v
        assert "model.bin" in v["missing_files"], v
        assert v["reason"] == "blob_missing", v
        print("[3] blob_missing: pass, missing =", v["missing_files"])
    finally:
        restore()


def test_blob_truncated():
    """snapshot 在但 model.bin 只有 1MB（下载到一半断电） → broken"""
    tmp, restore = _isolated()
    try:
        _build_repo(tmp, "small", with_model_bin=True, model_size=1024 * 1024)
        v = w.validate_model_installation("small")
        assert v["ok"] is False, v
        assert any("model.bin" in m for m in v["missing_files"]), v
        assert v["reason"] == "blob_truncated", v
        print("[4] blob_truncated: pass, missing =", v["missing_files"])
    finally:
        restore()


def test_turbo_uses_mobius_cache_and_json_vocabulary():
    """faster-whisper maps large-v3-turbo to mobiuslabsgmbh, not Systran."""
    tmp, restore = _isolated()
    try:
        _build_repo(tmp, "large-v3-turbo", owner="mobiuslabsgmbh",
                    vocab_name="vocabulary.json")
        v = w.validate_model_installation("large-v3-turbo")
        assert v["ok"] is True, v
        assert "models--mobiuslabsgmbh--" in v["repo_dir"], v
    finally:
        restore()


def test_cleanup_broken():
    """cleanup_broken_model 真的删掉 broken repo"""
    tmp, restore = _isolated()
    try:
        repo, _ = _build_repo(tmp, "small", with_model_bin=False)
        assert os.path.isdir(repo)
        r = w.cleanup_broken_model("small")
        assert r["size"] == "small"
        assert r["now_downloaded"] is False
        # 再次校验应该 no_snapshot
        v = w.validate_model_installation("small")
        assert v["reason"] == "no_snapshot", v
        print("[5] cleanup_broken: pass, removed =", r["removed"])
    finally:
        restore()


def test_user_small_actually_ok():
    """在用户真实数据上：small 是完整的（即使 blobs/ 是空）。"""
    import pytest
    real_local = os.environ.get("CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA")
    if not real_local:
        pytest.skip("set CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA to run against real models")
    if not os.path.isdir(os.path.join(real_local, "ClassroomAssistant")):
        pytest.skip("real data dir not present in this env")
    # 在 pytest 下 conftest 重定向了 LOCALAPPDATA，所以本测试只能手动跑：
    #   python tests/test_validate_model.py  （非 pytest 入口）
    if os.environ.get("_CA_VAL_USER_REAL") != "1":
        pytest.skip("only runnable via _CA_VAL_USER_REAL=1 (skipped under pytest)")
    os.environ["LOCALAPPDATA"] = real_local
    os.environ["APPDATA"] = os.environ.get("CLASSROOM_ASSISTANT_REAL_APPDATA", real_local)
    # 重新加载 config（whisper_worker 用 config.get_config() 拿 dirs）
    from backend import config
    config.get_config.__globals__["_DIRS"] = None
    v = w.validate_model_installation("small")
    assert v["ok"] is True, v
    print("[6] user small validate ok: pass; detail =", v["detail"])


if __name__ == "__main__":
    test_ok()
    test_no_snapshot()
    test_blob_missing()
    test_blob_truncated()
    test_cleanup_broken()
    test_user_small_actually_ok()
    print("VALIDATE_OK")
