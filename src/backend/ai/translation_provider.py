"""Settings and credentials for translating transcript text only."""
from __future__ import annotations

import json
import re
import threading

from .. import credential, db, schemas
from . import prompts
from .llm_provider import (
    LlamaCppSidecarLLM, OpenAICompatibleLLM, TranslationAPIError,
    api_identity, normalize_api_endpoint, normalize_api_model,
)

SETTINGS_LOCK = threading.RLock()
_KEY = "translation_api_key"
_CONNECTION_FIELDS = {"translation_base_url", "translation_model", _KEY}
SETTING_FIELDS = _CONNECTION_FIELDS | {"translation_provider", "translation_clear_key"}


def _connection(settings):
    base = normalize_api_endpoint(settings.get("translation_base_url", ""))
    model = normalize_api_model(settings.get("translation_model", ""))
    return base, model, api_identity(base, model)


def validate_key(value, base, model):
    if not isinstance(value, str):
        raise ValueError("请填写有效的 API 密钥。")
    value = value.strip()
    if (not value or len(value) > 8192 or not value.isascii()
            or not value.isprintable() or any(ch.isspace() for ch in value)):
        raise ValueError("密钥中含有空白或无效字符，请重新粘贴。")
    if value in base or value in model:
        raise ValueError("请只在密钥栏中填写密钥。")
    return value


def public_settings(settings):
    """Return a displayable draft without reading the decrypted key or writing defaults."""
    mode = "api" if settings.get("translation_provider") == "api" else "local"
    try:
        base, model, scope = _connection(settings)
    except ValueError:
        base, model, scope = "", "", None
    stored, present = credential.credential_presence(_KEY, scope)
    return {"translation_provider": mode, "translation_base_url": base,
            "translation_model": model, "translation_api_key_set": "1" if present else "0",
            "translation_api_key_stored": "1" if stored else "0"}


def prepare_update(current, patch, changes):
    """Stage a credential change together with the caller's database transaction."""
    if not SETTING_FIELDS.intersection(patch):
        return {}
    mode = patch.get("translation_provider", current.get("translation_provider", "local"))
    if mode not in ("local", "api"):
        raise ValueError("请选择本机翻译或兼容 API。")
    if "translation_clear_key" in patch and not isinstance(patch["translation_clear_key"], bool):
        raise ValueError("清除密钥需要明确确认。")
    updates = {"translation_provider": mode} if "translation_provider" in patch else {}
    if patch.get("translation_clear_key"):
        if mode != "local" or patch.get(_KEY):
            raise ValueError("请先选择本机翻译，再清除密钥。")
        changes.enter_context(credential.credential_change(_KEY, None))
        return updates
    if mode == "local" and not _CONNECTION_FIELDS.intersection(patch):
        return updates
    base, model, scope = _connection({**current, **patch})
    key = patch.get(_KEY)
    if key not in (None, ""):
        changes.enter_context(credential.credential_change(_KEY, validate_key(key, base, model), scope))
    elif mode == "api" and not credential.has_credential(_KEY, scope):
        raise ValueError("请为这个服务地址和模型填写 API 密钥。")
    updates.update(translation_base_url=base, translation_model=model)
    return updates


class TranscriptTranslationAPI(OpenAICompatibleLLM):
    """Only the numbered text translation prompt may reach the selected service."""
    def __init__(self, base_url, key, model, *, use_saved_settings=False):
        super().__init__(base_url, key, model)
        self.name = "translation_api"
        self.use_saved_settings = use_saved_settings

    def chat(self, system, user, json_mode=True, temperature=0):
        if system != prompts.TRANSLATION_SYSTEM:
            raise TranslationAPIError("此接口仅用于转写翻译。")
        if self.use_saved_settings:
            # A user may switch back to local or rotate the key during a long class.
            with SETTINGS_LOCK:
                current = db.get_all_settings()
                try:
                    scope = _connection(current)[2] if current.get("translation_provider") == "api" else None
                except ValueError:
                    scope = None
                if scope != self.cache_identity:
                    raise TranslationAPIError("翻译设置已更改。请继续任务以使用新设置。")
                key = credential.get_credential(_KEY, self.cache_identity)
                if not key:
                    raise TranslationAPIError("翻译密钥不可用。请检查设置后继续任务。")
                self.api_key = key
        content = super().chat(system, user, json_mode, temperature)
        from .segment_analysis import _partial_translation_rows
        try:
            payload = schemas._extract_json(content)
            values = payload["translations"]
            if not isinstance(values, list):
                raise ValueError()
            rows = []
            for entry in values:
                if not isinstance(entry, dict):
                    continue
                index, value = entry.get("index"), entry.get("text")
                if isinstance(index, str) and index.isascii() and index.isdecimal():
                    index = int(index)
                if type(index) is int and index >= 0 and isinstance(value, str) and value.strip():
                    rows.append({"index": index, "text": value.strip()})
        except (ValueError, TypeError, KeyError):
            rows = [{"index": i, "text": t} for i, t in (_partial_translation_rows(content) or {}).items()]
        if not rows or any(self.api_key in row["text"] for row in rows):
            raise TranslationAPIError("API 没有返回可用的逐段译文，请检查所选模型。")
        # Keep only translated rows. Extra fields from an upstream service never enter the local cache.
        return json.dumps({"translations": rows}, ensure_ascii=False)


def _api_provider(settings, *, use_saved_settings=False):
    base, model, scope = _connection(settings)
    key = settings.get(_KEY)
    if key not in (None, ""):
        key = validate_key(key, base, model)
    else:
        key = credential.get_credential(_KEY, scope)
    if not key:
        raise ValueError("请为这个服务地址和模型填写 API 密钥。")
    return TranscriptTranslationAPI(base, key, model, use_saved_settings=use_saved_settings)


def build_translation_provider(settings, existing_provider):
    if settings.get("translation_provider") == "api":
        try:
            # Runtime secrets come exclusively from the vault, never from database settings.
            return _api_provider({key: settings.get(key, "") for key in ("translation_base_url", "translation_model")},
                                 use_saved_settings=True)
        except ValueError as error:
            raise TranslationAPIError(str(error)) from None
    if isinstance(existing_provider, OpenAICompatibleLLM):
        # A legacy explicit remote organizer can coexist with the user's choice of *local* translation.
        from .provider import build_provider
        local = build_provider({**settings, "ai_provider": "auto"})
        if isinstance(local, LlamaCppSidecarLLM) and not local.available():
            raise TranslationAPIError("本机翻译需要本地文字模型，请先在设置中准备模型。")
        return local
    return existing_provider


def test_connection(draft, saved=None):
    if not isinstance(draft, dict) or draft.keys() - _CONNECTION_FIELDS:
        raise ValueError("连接测试只接受服务地址、模型名称和密钥。")
    saved = saved or {}
    provider = _api_provider({**{k: saved.get(k, "") for k in ("translation_base_url", "translation_model")}, **draft})
    provider.timeout = 12
    response = provider.chat(prompts.TRANSLATION_SYSTEM,
                             prompts.TRANSLATION_PROMPT.format(segments="[0] Hello."))
    rows = json.loads(response)["translations"]
    if len(rows) != 1 or rows[0]["index"] != 0 or not re.search(r"[\u3400-\u9fff]", rows[0]["text"]):
        raise TranslationAPIError("已连接，但模型没有为样例返回中文译文。")
