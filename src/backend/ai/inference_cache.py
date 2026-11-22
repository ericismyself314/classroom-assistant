"""Local, disposable checkpoints for already validated model replies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


class InferenceCache:
    def __init__(self, directory, namespace):
        self.directory = Path(directory)
        self.namespace = namespace

    def key(self, provider, system, user, *, temperature, json_mode):
        model_file = getattr(provider, "gguf", None)
        fingerprint = None
        if model_file:
            try:
                stat = os.stat(model_file)
                fingerprint = [os.fspath(model_file), stat.st_size, stat.st_mtime_ns]
            except OSError:
                pass
        label = getattr(provider, "model_label", lambda: type(provider).__name__)()
        request = {
            "version": 1, "namespace": self.namespace,
            "provider": type(provider).__qualname__, "model": label,
            "manifest": getattr(provider, "model", None), "model_file": fingerprint,
            "system": system, "user": user,
            "temperature": temperature, "json_mode": json_mode,
        }
        if getattr(provider, "cache_identity", None):
            request["api_endpoint_and_model"] = provider.cache_identity
        serialized = json.dumps(request, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, key, validate=None):
        try:
            path = self.directory / f"{key}.json"
            if path.stat().st_size > 1024 * 1024:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            response = payload.get("response") if payload.get("version") == 1 else None
            if isinstance(response, str) and (validate is None or validate(response)):
                return response
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            pass
        return None

    def put(self, key, response):
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".reply-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "response": response}, stream, ensure_ascii=False)
            os.replace(temporary, self.directory / f"{key}.json")
        except (OSError, ValueError, TypeError):
            pass
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
