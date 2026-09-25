"""模型清单（manifest）。

模型清单。产品只提供一条受支持的本地链路：
- Qwen3-ASR-0.6B 把录音转为原文；
- Qwen3-4B GGUF 通过 llama.cpp 把原文整理为中文课堂笔记。

旧版本的语音档位仍可能出现在历史设置中，但运行时统一迁移到 Qwen3-ASR，
避免界面显示与实际使用的模型不一致。

注意：以下 HuggingFace 地址为 Qwen 官方发布的 GGUF 文件，长期稳定；如需更换模型只需改此清单。
"""
from __future__ import annotations

# ---- 唯一语音模型 ----
WHISPER_TIERS = {
    "qwen": {
        "label": "Qwen3-ASR-0.6B",
        "model_size": "Qwen3-ASR-0.6B",
        "note": "唯一语音识别模型。本机运行，自动使用可用的 NVIDIA GPU，否则使用 CPU。",
    },
}
DEFAULT_WHISPER_TIER = "qwen"
WHISPER_EXTRA_SIZES = []

# ---- 文字模型（GGUF，用于应用自管 llama.cpp sidecar）----
# url 使用 HuggingFace resolve 直链；size_bytes 为约数（用于下载前磁盘检查）。
LLM_MODELS = [
    {
        "id": "qwen3-4b-q5_k_m",
        "display": "Qwen3-4B (Q5_K_M)",
        "repo": "Qwen/Qwen3-4B-GGUF",
        "shards": [
            "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q5_K_M.gguf",
        ],
        "url": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q5_K_M.gguf",
        "size_bytes": 2_889_513_184,
        # Official Xet/LFS checksum for Qwen3-4B-Q5_K_M.gguf.
        "sha256": "aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f",
        "context_length": 32768,
        "vram_gb": 3.4,
        "params": "-ngl 99",
        "license": "Apache-2.0",
        "is_default": True,
        "note": "推荐默认：中文整理能力与速度平衡，适合 6GB 显存本地运行。",
    },
]

# ---- llama.cpp 服务端二进制（应用自管 sidecar）----
# 下载最新 release 中的 Windows CUDA 版 llama-server；仅监听 127.0.0.1。
SIDECAR = {
    "repo": "ggml-org/llama.cpp",
    "asset_keywords": ["llama-server", "win", "cuda"],
    "dir": "bin",
    "exe_name": "llama-server.exe",
    "sha256": None,
}


def default_llm_model() -> dict:
    for m in LLM_MODELS:
        if m.get("is_default"):
            return m
    return LLM_MODELS[0]


def llm_model_by_id(model_id: str) -> dict | None:
    for m in LLM_MODELS:
        if m["id"] == model_id:
            return m
    return None
