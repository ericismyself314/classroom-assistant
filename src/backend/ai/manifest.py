"""模型清单（manifest）。

设计要点（见需求文档第九、十章）：
- 模型名称不写死在业务代码里；通过清单配置（id / 显示名 / 下载地址 / 大小 / 哈希 / 上下文长度 /
  推荐显存 / 推理参数 / 许可证 / 是否默认）。
- 语音模型提供「快速 / 平衡 / 准确」三档，映射到 faster-whisper 尺寸；默认档需按真机测试选择。
- 文字模型提供多个 GGUF（用于应用自管 llama.cpp sidecar），并允许本机已有 Ollama。
- 哈希若暂未提供则为 None，下载后由 HuggingFace/服务端 etag 保证完整性。

注意：以下 HuggingFace 地址为 Qwen 官方发布的 GGUF 文件，长期稳定；如需更换模型只需改此清单。
"""
from __future__ import annotations

# ---- 语音模型三档（faster-whisper 尺寸）----
WHISPER_TIERS = {
    "fast": {
        "label": "快速",
        "model_size": "base",
        "note": "速度优先、占用最低；中文准确度一般，适合快速预览。",
    },
    "balanced": {
        "label": "推荐",
        "model_size": "large-v3-turbo",
        "note": "推荐档。中文课堂识别质量更高，RTX 4050 6GB 可使用 GPU。",
    },
    "accurate": {
        "label": "准确",
        "model_size": "medium",
        "note": "准确度更高，占用更大；中文课堂推荐。显存不足会自动回退 CPU。",
    },
}
DEFAULT_WHISPER_TIER = "balanced"

# 额外可选尺寸（在设置中可手动选择，如 large-v3 准确度最高但很慢）
WHISPER_EXTRA_SIZES = ["tiny", "large-v2", "large-v3", "large-v3-turbo"]

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
    {
        "id": "qwen2.5-7b-instruct-q4_k_m",
        "display": "Qwen2.5-7B-Instruct (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "shards": [
            "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
            "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
        ],
        "url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
        "size_bytes": 4_700_000_000,
        "sha256": None,
        "context_length": 32768,
        "vram_gb": 5.0,
        "params": "-ngl 35",
        "license": "Qwen License / Apache-2.0",
        "is_default": False,
        "note": "兼容旧版本结果的模型；中文能力稳定，但不再作为新安装默认。",
    },
    {
        "id": "qwen2.5-3b-instruct-q4_k_m",
        "display": "Qwen2.5-3B-Instruct (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "shards": [
            "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        ],
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_bytes": 2_200_000_000,
        "sha256": None,
        "context_length": 32768,
        "vram_gb": 2.6,
        "params": "-ngl 35",
        "license": "Qwen License / Apache-2.0",
        "is_default": False,
        "note": "显存紧张时的轻量选择；分析质量略低。",
    },
    {
        "id": "qwen2.5-1.5b-instruct-q4_k_m",
        "display": "Qwen2.5-1.5B-Instruct (Q4_K_M)",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "shards": [
            "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        ],
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_bytes": 1_200_000_000,
        "sha256": None,
        "context_length": 32768,
        "vram_gb": 1.5,
        "params": "-ngl 35",
        "license": "Qwen License / Apache-2.0",
        "is_default": False,
        "note": "最小模型，仅用于低配机器或快速验证；重要事项识别能力有限。",
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
