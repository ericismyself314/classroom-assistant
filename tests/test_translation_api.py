"""Exercise the optional translation service using synthetic text and loopback HTTP only."""
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

import pytest

from backend import api, backup, config, credential, db
from backend.ai import llm_provider, prompts, provider, translation_provider
from backend.ai.inference_cache import InferenceCache


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def local_app():
    with running(api.make_server("127.0.0.1", 0)) as base:
        def request(method, route, body=None, **headers):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + route, data=data, method=method,
                                         headers={"Content-Type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)
        yield request


@pytest.fixture
def upstream():
    calls = []
    behavior = {"status": 200, "content": '{"translations":[{"index":0,"text":"你好。"}]}'}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization"),
                          json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(behavior["status"])
            if "location" in behavior:
                self.send_header("Location", behavior["location"])
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": behavior["content"]}}]}).encode())

        def log_message(self, *_args):
            pass

    with running(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as base:
        yield base + "/v1", calls, behavior


def test_reading_and_testing_do_not_change_defaults_or_send_classroom_data(local_app, upstream):
    base, calls, _ = upstream
    db.set_setting("ai_provider", "ollama")
    db.set_setting("whisper_device", "cpu")
    before = db.get_all_settings()
    status, public = local_app("GET", "/api/settings")
    assert status == 200 and public["settings"].get("translation_provider") == "local"
    assert db.get_all_settings() == before and not calls

    draft = {"translation_base_url": base, "translation_model": "sample-model", "translation_api_key": "sample-secret"}
    status, result = local_app("POST", "/api/settings/test-translation", draft)
    assert status == 200 and result["ok"]
    assert db.get_all_settings() == before
    assert not (Path(config.get_config().dirs.data_dir) / "credentials.json").exists()
    [(path, auth, sent)] = calls
    assert path == "/v1/chat/completions" and auth == "Bearer sample-secret"
    assert sent["model"] == "sample-model" and sent["messages"][-1]["content"].endswith("[0] Hello.\n")
    assert "你好" not in str(result) and "sample-secret" not in str(result)


def test_key_is_bound_to_endpoint_and_model_and_can_be_cleared(local_app, upstream):
    base, calls, _ = upstream
    secret = "example-key-one-123"
    draft = {"translation_base_url": base, "translation_model": "sample-model", "translation_api_key": secret}
    status, _ = local_app("PUT", "/api/settings", {**draft, "translation_provider": "api"})
    assert status == 200 and db.get_setting("translation_provider") == "api"
    assert not calls
    status, info = local_app("GET", "/api/settings")
    assert info["settings"]["translation_api_key_set"] == "1" and secret not in str(info)
    assert secret not in str(db.get_all_settings())
    vault = Path(config.get_config().dirs.data_dir) / "credentials.json"
    assert secret.encode() not in vault.read_bytes()
    with ZipFile(backup.create_backup()) as archive:
        assert not any("credential" in name or secret.encode() in archive.read(name) for name in archive.namelist())

    public_draft = {"translation_base_url": base, "translation_model": "sample-model"}
    status, result = local_app("POST", "/api/settings/test-translation", public_draft)
    assert status == 200 and result["ok"] and calls[-1][1] == "Bearer " + secret
    for patch in ({"translation_base_url": base + "/different"}, {"translation_model": "another-model"}):
        status, _ = local_app("POST", "/api/settings/test-translation", {**public_draft, **patch})
        assert status == 400
    assert len(calls) == 1
    # A restored database may name a different model while this PC still holds the old encrypted key.
    db.set_setting("translation_model", "restored-model")
    _, restored = local_app("GET", "/api/settings")
    assert restored["settings"]["translation_api_key_set"] == "0"
    assert restored["settings"]["translation_api_key_stored"] == "1"
    status, _ = local_app("PUT", "/api/settings", {"translation_provider": "local", "translation_clear_key": True})
    assert status == 200 and db.get_setting("translation_provider") == "local"
    _, info = local_app("GET", "/api/settings")
    assert info["settings"]["translation_api_key_set"] == "0"
    assert info["settings"]["translation_api_key_stored"] == "0"
    assert len(calls) == 1


@pytest.mark.parametrize("status_code", [307, 401, 429])
def test_upstream_failures_are_safe_and_never_follow_redirects(local_app, upstream, status_code):
    base, calls, behavior = upstream
    secret = "sample-failure-key"
    behavior.update(status=status_code, content=secret, location=base + "/unexpected")
    status, result = local_app("POST", "/api/settings/test-translation", {
        "translation_base_url": base, "translation_model": "test", "translation_api_key": secret,
    })
    assert status >= 400 and not result.get("ok")
    assert str(status_code) in result["error"] and secret not in str(result)
    assert len(calls) == 1 and calls[0][0] == "/v1/chat/completions"
    assert db.get_setting("translation_provider") is None


def test_only_translation_can_use_the_chosen_service_and_switching_local_stops_it(local_app, upstream):
    base, calls, _ = upstream
    draft = {"translation_base_url": base, "translation_model": "sample-model",
             "translation_api_key": "sample-secret", "translation_provider": "api"}
    assert local_app("PUT", "/api/settings", draft)[0] == 200
    translator = translation_provider.build_translation_provider(db.get_all_settings(), object())
    with pytest.raises(llm_provider.TranslationAPIError):
        translator.chat("organize notes", "synthetic course title")
    assert not calls
    assert "你好" in translator.chat(prompts.TRANSLATION_SYSTEM, "[0] Hello.")
    assert local_app("PUT", "/api/settings", {"translation_provider": "local"})[0] == 200
    with pytest.raises(llm_provider.TranslationAPIError, match="设置已更改"):
        translator.chat(prompts.TRANSLATION_SYSTEM, "[0] another synthetic sentence")
    assert len(calls) == 1
    existing = object()
    assert translation_provider.build_translation_provider(db.get_all_settings(), existing) is existing


def test_legacy_explicit_api_is_preserved_but_auto_never_sends_notes(monkeypatch, local_app, upstream):
    base, calls, _ = upstream
    db.set_setting("openai_base_url", base)
    db.set_setting("openai_model", "legacy-model")
    db.set_setting("ai_provider", "openai")
    credential.set_credential("openai_api_key", "example-legacy-key")
    previous = db.get_all_settings()
    monkeypatch.setattr(llm_provider.LlamaCppSidecarLLM, "available", lambda self: False)
    monkeypatch.setattr(llm_provider.OllamaLLM, "available", lambda *args, **kwargs: False)
    explicit = provider.build_provider(previous)
    assert isinstance(explicit, llm_provider.OpenAICompatibleLLM)
    assert explicit.api_key == "example-legacy-key"
    assert local_app("PUT", "/api/settings", {"openai_base_url": base + "/changed"})[0] == 400
    assert db.get_all_settings() == previous
    monkeypatch.setattr(credential, "get_credential", lambda *args, **kwargs: pytest.fail("decrypted a key"))
    assert isinstance(provider.build_provider({**previous, "ai_provider": "auto"}), llm_provider.LlamaCppSidecarLLM)
    assert local_app("GET", "/api/settings")[1]["settings"]["ai_provider"] == "openai"
    assert not calls


def test_api_cache_uses_canonical_endpoint_and_model_but_never_the_key(tmp_path):
    cache = InferenceCache(tmp_path, {})
    def fingerprint(base, key, model):
        llm = translation_provider.TranscriptTranslationAPI(base, key, model)
        return cache.key(llm, "system", "synthetic", temperature=0, json_mode=True)

    original = fingerprint("http://127.0.0.1:3210/v1/", "test-key", "model-a")
    assert original == fingerprint("http://127.0.0.1:3210/v1/chat/completions", "another-key", "model-a")
    assert original != fingerprint("http://127.0.0.1:3220/v1", "test-key", "model-a")
    assert original != fingerprint("http://127.0.0.1:3210/v1", "test-key", "model-b")


def test_rejects_unsafe_address_wrong_origin_and_caller_supplied_test_text(local_app, upstream):
    base, calls, _ = upstream
    draft = {"translation_base_url": base, "translation_model": "test-model", "translation_api_key": "sample-key"}
    for bad in ("http://outside.invalid/v1", "https://user:password@outside.invalid/v1",
                base + "?api_key=sample-key", base + "/sample-key"):
        status, result = local_app("PUT", "/api/settings", {**draft, "translation_base_url": bad,
                                                            "translation_provider": "api"})
        assert status == 400 and "sample-key" not in str(result)
    for body, headers in (({**draft, "text": "caller-provided private text"}, {}),
                          (draft, {"Origin": "https://unrelated.invalid"})):
        status, result = local_app("POST", "/api/settings/test-translation", body, **headers)
        assert status == 400 and "sample-key" not in str(result)
    assert not calls and not db.get_all_settings()


def test_malformed_and_echoed_replies_do_not_escape_or_cache(local_app, upstream):
    base, calls, behavior = upstream
    secret = "synthetic-bearer-123"
    draft = {"translation_base_url": base, "translation_model": "sample-model", "translation_api_key": secret}
    for content in ("not a translation", json.dumps({"translations": [{"index": 0, "text": secret}]}),
                    json.dumps({"translations": [{"index": 0, "text": "fallback English only"}]})):
        behavior["content"] = content
        code, result = local_app("POST", "/api/settings/test-translation", draft)
        assert code == 502 and secret not in str(result) and content not in str(result)
    assert len(calls) == 3
    logs = Path(config.get_config().dirs.logs) / "app.log"
    assert secret.encode() not in logs.read_bytes()


def test_credentials_roll_back_if_saving_settings_fails(local_app, upstream, monkeypatch):
    base, calls, _ = upstream
    draft = {"translation_base_url": base, "translation_model": "sample-model",
             "translation_api_key": "synthetic-first", "translation_provider": "api"}
    assert local_app("PUT", "/api/settings", draft)[0] == 200
    original = db.get_all_settings()

    def fail(_key, _value):
        raise OSError()

    monkeypatch.setattr(db, "set_setting", fail)
    status, result = local_app("PUT", "/api/settings", {**draft, "translation_api_key": "synthetic-second"})
    assert status == 500 and "synthetic-second" not in str(result)
    assert original == db.get_all_settings()
    assert local_app("POST", "/api/settings/test-translation", {})[0] == 200
    assert len(calls) == 1 and calls[0][1] == "Bearer synthetic-first"
