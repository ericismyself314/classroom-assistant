"""API 密钥等敏感凭据的安全存储。

仅使用 Windows 当前用户的 DPAPI 加密。服务地址与模型可以绑定到同一份凭据。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import tempfile
import threading
from contextlib import contextmanager

from .config import get_config
from .logutil import get_logger

LOG = get_logger("credential")

CRED_FILE = "credentials.json"
_LOCK = threading.RLock()


def _dpapi_encrypt(plain: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(plain, len(plain))
    blob_in = DATA_BLOB(len(plain), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = DATA_BLOB(0, None)
    res = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not res:
        raise OSError("DPAPI 加密失败")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(blob_out.pbData))
    return out


def _dpapi_decrypt(cipher: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(cipher, len(cipher))
    blob_in = DATA_BLOB(len(cipher), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = DATA_BLOB(0, None)
    res = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not res:
        raise OSError("DPAPI 解密失败")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(blob_out.pbData))
    return out


def _cred_path() -> str:
    return os.path.join(get_config().dirs.data_dir, CRED_FILE)


def _read_entries() -> dict:
    path = _cred_path()
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise OSError("无法读取本机密钥文件") from None


def _write_entries(data: dict) -> None:
    path = _cred_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(path), encoding="utf-8",
                                         prefix=".credentials-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _ciphertext(entry, scope, allow_legacy):
    if isinstance(entry, dict):
        return entry.get("ciphertext") if entry.get("scope") == scope else None
    return entry if isinstance(entry, str) and (scope is None or allow_legacy) else None


def credential_presence(key: str, scope: str | None = None, *, allow_legacy=False) -> tuple[bool, bool]:
    """Return (stored, matches scope) without decrypting a secret, also after restoring a backup."""
    try:
        with _LOCK:
            entry = _read_entries().get(key)
            return bool(entry), bool(_ciphertext(entry, scope, allow_legacy))
    except OSError:
        return False, False


def has_credential(key: str, scope: str | None = None, *, allow_legacy=False) -> bool:
    return credential_presence(key, scope, allow_legacy=allow_legacy)[1]


def get_credential(key: str, scope: str | None = None, *, allow_legacy=False) -> str | None:
    with _LOCK:
        try:
            entries = _read_entries()
            entry = entries.get(key)
            value = _ciphertext(entry, scope, allow_legacy)
            if value:
                plain = _dpapi_decrypt(base64.b64decode(value, validate=True)).decode("utf-8")
                if isinstance(entry, str) and scope and allow_legacy:
                    entries[key] = {"scope": scope, "ciphertext": value}
                    _write_entries(entries)
                return plain
        except (OSError, ValueError, AttributeError):
            LOG.warning("无法解密本机凭据")
        return None


@contextmanager
def credential_change(key: str, value: str | None, scope: str | None = None):
    """Keep the previous encrypted record if committing its associated settings fails."""
    with _LOCK:
        original = _read_entries()
        updated = original.copy()
        if value is None:
            updated.pop(key, None)
        else:
            if os.name != "nt":
                raise OSError("保存 API 密钥需要 Windows 密钥保护")
            cipher = base64.b64encode(_dpapi_encrypt(value.encode("utf-8"))).decode("ascii")
            updated[key] = {"scope": scope, "ciphertext": cipher} if scope else cipher
        if updated == original:
            yield
            return
        _write_entries(updated)
        try:
            yield
        except BaseException:
            _write_entries(original)
            raise


def set_credential(key: str, value: str, scope: str | None = None) -> None:
    with credential_change(key, value, scope):
        pass


def delete_credential(key: str) -> None:
    with credential_change(key, None):
        pass
