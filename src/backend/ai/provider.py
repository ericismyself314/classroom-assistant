"""AI Provider 注册与解析（统一接口，避免未来换模型重写业务逻辑）。

见需求文档第九章：默认优先应用自管 sidecar；其次本机 Ollama；最后可选的 OpenAI 兼容 API。
"""
from __future__ import annotations

import threading
import time

from ..credential import get_credential
from ..logutil import get_logger
from . import manifest
from .llm_provider import (
    BaseLLM,
    LlamaCppSidecarLLM,
    OllamaLLM,
    OpenAICompatibleLLM,
)

LOG = get_logger("provider")

# 设置页、就绪检查和模型状态会在同一时刻重复查询 Provider。Ollama 未启动
# 时，Windows 本机连接失败可能要等待约 2 秒；短缓存可避免一次打开设置触发
# 多次相同等待，同时仍会定期刷新外部服务状态。
_OLLAMA_STATUS_TTL = 15.0
_ollama_status_lock = threading.Lock()
_ollama_status_cache: tuple[float, bool, list[str]] | None = None


def _ollama_status(model: str) -> tuple[bool, list[str]]:
    global _ollama_status_cache
    now = time.monotonic()
    with _ollama_status_lock:
        if _ollama_status_cache is not None:
            cached_at, available, models = _ollama_status_cache
            if now - cached_at < _OLLAMA_STATUS_TTL:
                return available, list(models)

        ol = OllamaLLM(model)
        # 状态页不应被未启动的可选服务拖慢；真实聊天仍使用其正常超时。
        available = ol.available(timeout=0.3)
        models = ol.list_models(timeout=0.75) if available else []
        _ollama_status_cache = (time.monotonic(), available, list(models))
        return available, models


def build_provider(settings: dict) -> BaseLLM:
    """根据设置构造一个可用的 Provider 实例。

    settings 字段：ai_provider( auto|sidecar|ollama|openai )、llm_model_id、
    ollama_model、openai_base_url、openai_model。
    """
    provider_type = (settings or {}).get("ai_provider", "auto")
    llm_model_id = (settings or {}).get("llm_model_id") or manifest.default_llm_model()["id"]

    if provider_type in ("sidecar", "auto"):
        sidecar = LlamaCppSidecarLLM(llm_model_id)
        if provider_type == "sidecar" or sidecar.available():
            return sidecar

    if provider_type in ("ollama", "auto"):
        ol = OllamaLLM((settings or {}).get("ollama_model") or "")
        if ol.available():
            return ol

    if provider_type in ("openai", "auto"):
        key = get_credential("openai_api_key")
        if key:
            return OpenAICompatibleLLM(
                (settings or {}).get("openai_base_url", "https://api.openai.com/v1"),
                key,
                (settings or {}).get("openai_model", "gpt-4o-mini"),
            )

    # 兜底：返回 sidecar 对象（前端会引导下载模型）
    return LlamaCppSidecarLLM(llm_model_id)


def detect_status(settings: dict) -> dict:
    """返回各 Provider 可用性，供设置页展示。"""
    llm_model_id = (settings or {}).get("llm_model_id") or manifest.default_llm_model()["id"]
    sidecar = LlamaCppSidecarLLM(llm_model_id)
    # The supported default is the bundled local sidecar. Avoid probing an
    # optional Ollama service whenever Settings opens; that made the page slow.
    pref = str((settings or {}).get("ai_provider") or "sidecar").lower()
    if pref == "ollama":
        ollama_available, ollama_models = _ollama_status((settings or {}).get("ollama_model") or "")
    else:
        ollama_available, ollama_models = False, []
    return {
        "sidecar": {
            "available": sidecar.available(),
            "binary": sidecar.is_binary_downloaded(),
            "model": sidecar.is_model_downloaded(),
            "label": sidecar.model["display"],
        },
        "ollama": {
            "available": ollama_available,
            "models": ollama_models,
        },
        "openai": {"configured": bool(get_credential("openai_api_key"))},
    }


def provider_is_ready(settings: dict, status: dict | None = None) -> bool:
    """按当前 Provider 选择判断文字分析是否真的可用。

    ``detect_status`` 按 Provider 分组返回状态，不能再用不存在的顶层
    ``llm_downloaded`` 字段判断。这里与 ``build_provider`` 的选择顺序保持一致，
    供 API、首启就绪状态和命令行自检共用。
    """
    settings = settings or {}
    status = status if isinstance(status, dict) else detect_status(settings)
    pref = str(settings.get("ai_provider") or "auto").lower()
    sidecar = status.get("sidecar") if isinstance(status.get("sidecar"), dict) else {}
    ollama = status.get("ollama") if isinstance(status.get("ollama"), dict) else {}
    openai = status.get("openai") if isinstance(status.get("openai"), dict) else {}

    sidecar_ready = bool(sidecar.get("available") and sidecar.get("binary") and sidecar.get("model"))
    ollama_ready = bool(ollama.get("available"))
    openai_ready = bool(openai.get("configured"))
    if pref == "sidecar":
        return sidecar_ready
    if pref == "ollama":
        return ollama_ready
    if pref == "openai":
        return openai_ready
    return sidecar_ready or ollama_ready or openai_ready
