"""下载推荐离线模型：whisper(small) + llama.cpp + Qwen2.5-7B GGUF。

任一下载不完整都会以非零退出，适合发布前真实验收。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from backend import config
from backend.ai import manifest, llm_provider
from backend.ai import whisper_worker as ww

config.init_config()
print("[download] data_dir =", config.get_config().dirs.data_dir, flush=True)

print("[download] whisper small ...", flush=True)
from faster_whisper import WhisperModel

WhisperModel(
    "small",
    device="cpu",
    compute_type="int8",
    download_root=ww.models_root(),
)
if not ww.is_model_downloaded("small"):
    raise RuntimeError("whisper small 下载后完整性检查失败")
print("[download] whisper small OK", flush=True)

# 2) llama.cpp server exe
print("[download] llama.cpp server exe ...", flush=True)
side = llm_provider.LlamaCppSidecarLLM(manifest.default_llm_model()["id"])
side._ensure_binary()
if not side.is_binary_downloaded():
    raise RuntimeError("llama.cpp 服务端下载后完整性检查失败")
print("[download] llama.cpp server exe OK ->", side._bin_path(), flush=True)

# 3) 7B GGUF
print("[download] Qwen2.5-7B GGUF ...", flush=True)
side._ensure_model()
if not side.is_model_downloaded():
    raise RuntimeError("Qwen2.5-7B 下载后完整性检查失败")
print("[download] Qwen2.5-7B GGUF OK ->", side.gguf, flush=True)

print("[download] ALL DONE", flush=True)
