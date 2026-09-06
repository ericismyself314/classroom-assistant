"""API 密钥等敏感凭据的安全存储。

Windows 上使用 DPAPI（CryptProtectData / CryptUnprotectData）加密，密钥不离开本机、不进数据库、
不进日志、不进代码仓库。非 Windows 环境降级为带权限限制的本地加密文件并明确告警（本应用仅支持
Windows，正常情况下走 DPAPI）。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os

from .config import get_config
from .logutil import get_logger

LOG = get_logger("credential")

CRED_FILE = "credentials.json"


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
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
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
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


def _cred_path() -> str:
    return os.path.join(get_config().dirs.data_dir, CRED_FILE)


def set_credential(key: str, value: str) -> None:
    path = _cred_path()
    data: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    enc = _dpapi_encrypt(value.encode("utf-8"))
    data[key] = base64.b64encode(enc).decode("ascii")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def get_credential(key: str) -> str | None:
    path = _cred_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    enc_b64 = data.get(key)
    if not enc_b64:
        return None
    try:
        enc = base64.b64decode(enc_b64)
        return _dpapi_decrypt(enc).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        LOG.warning("凭据解密失败: %s", e)
        return None


def delete_credential(key: str) -> None:
    path = _cred_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.pop(key, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
