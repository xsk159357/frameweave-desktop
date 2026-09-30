"""密钥保险箱：API Key / 卡密等敏感信息本机加密存储。

Windows DPAPI（CryptProtectData / CryptUnprotectData）：
- 加密绑定当前 Windows 用户（换用户/换机不可解密）
- 密文存 %APPDATA%/FrameWeave/secrets.json（打包模式）或 backend/data/secrets.json（源码）
- 同一密钥名重复写入即覆盖

用法（节点内）：
    from ..secrets import read_secret, store_secret
    key = read_secret("ameaao") or params.get("api_key")
"""
from __future__ import annotations
import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes


def _dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData：返回 base64 密文。"""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI 加密失败")
    try:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return out
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    """CryptUnprotectData：还原明文。"""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf_in = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI 解密失败（密钥可能来自其他用户/机器）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _secrets_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "FrameWeave")
    else:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "secrets.json")


def store_secret(name: str, value: str) -> None:
    """加密存储密钥。name 建议使用 ASCII（如 ameaao/tts_cloud）。"""
    name = str(name).strip()
    if not name:
        raise ValueError("密钥名不能为空")
    path = _secrets_path()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    enc = _dpapi_protect(value.encode("utf-8"))
    data[name] = base64.b64encode(enc).decode("ascii")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写入


def read_secret(name: str) -> str:
    """读取明文。未存储返回 None。"""
    name = str(name).strip()
    path = _secrets_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    raw = data.get(name)
    if not raw:
        return None
    try:
        blob = base64.b64decode(raw.encode("ascii"))
        return _dpapi_unprotect(blob).decode("utf-8")
    except Exception:
        return None


def list_secret_names() -> list:
    path = _secrets_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sorted(json.load(f).keys())
    except (json.JSONDecodeError, OSError):
        return []


def delete_secret(name: str) -> bool:
    name = str(name).strip()
    path = _secrets_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if name not in data:
        return False
    del data[name]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True
