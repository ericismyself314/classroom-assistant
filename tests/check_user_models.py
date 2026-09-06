"""用真实用户数据上的小/中模型跑 validate，作为最终证据。

这一份是 standalone 脚本（不走 pytest），输出格式给用户看。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 只有用户明确指定目录时才检查真实模型，避免脚本误读个人数据。
real_local = os.environ.get("CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA")
if not real_local:
    raise SystemExit(
        "这是可选的真实模型检查。请先设置 "
        "CLASSROOM_ASSISTANT_REAL_LOCALAPPDATA，再重新运行。"
    )
os.environ["LOCALAPPDATA"] = real_local
os.environ["APPDATA"] = os.environ.get("CLASSROOM_ASSISTANT_REAL_APPDATA", real_local)

from backend import config
config.get_config.__globals__["_DIRS"] = None

from backend.ai import whisper_worker as w

print("=" * 60)
print("真实数据：validate whisper 模型完整性")
print("=" * 60)
print(f"data_dir = {config.get_config().dirs.data_dir}")
print(f"models_root = {w.models_root()}")
print(f"cuda_runtime_ready = {w.cuda_runtime_ready()}")
print()
for size in ("tiny", "base", "small", "medium", "large-v3"):
    print(f"--- {size} ---")
    v = w.validate_model_installation(size)
    is_dl = w.is_model_downloaded(size)
    print(f"  is_model_downloaded : {is_dl}")
    print(f"  validate ok         : {v.get('ok')}")
    print(f"  validate reason     : {v.get('reason') or '(ok)'}")
    if v.get("missing_files"):
        print(f"  missing files       : {v['missing_files']}")
    print(f"  detail              : {v.get('detail', '')}")
    if v.get("repo_dir"):
        try:
            import os
            total = 0
            for base, _, files in os.walk(v["repo_dir"]):
                for f in files:
                    try: total += os.path.getsize(os.path.join(base, f))
                    except OSError: pass
            print(f"  disk usage          : {total//1024//1024} MB")
        except Exception as e:
            print(f"  disk usage          : ERR {e}")
    print()

print("=" * 60)
print("结论：")
print("- 'ok=True'  → 模型文件完整可用，可以直接上传录音转写。")
print("- 'ok=False, reason=no_snapshot' → 没下载；点设置页「下载语音模型」。")
print("- 'ok=False, reason=blob_missing/blob_truncated' → 缓存损坏；点设置页")
print("   「重新校验」会显示详情，「清理」按钮一键删掉 + 重新下载。")
