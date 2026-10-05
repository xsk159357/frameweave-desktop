"""云端授权网关（M19 对接位，已正式云化）。

默认云端：https://frameweave.ameaaos.com（雨云日本，openresty HTTPS 反代 127.0.0.1:8789）。
默认即云化：登录/校验/激活走远程授权服务器（签名卡密 + 设备绑定 + 无离线宽限）。
本地桩/开发：显式设置环境变量 FRAMEWEAVE_CLOUD_URL=""（空）即回落本地校验。
"""
from __future__ import annotations
import json
import os
import urllib.request
from typing import Optional

def cloud_url() -> str:
    return os.environ.get("FRAMEWEAVE_CLOUD_URL", "https://frameweave.ameaaos.com").rstrip("/")

CLOUD_URL = ""

def _dbg(msg: str) -> None:
    try:
        import os as _os
        _log = _os.environ.get("FRAMEWEAVE_DEBUG_LOG", "")
        if _log:
            import datetime
            with open(_log, "a", encoding="utf-8") as _f:
                _f.write(datetime.datetime.now().isoformat() + " " + msg + "\n")
    except Exception:
        pass


def cloud_enabled() -> bool:
    return bool(cloud_url())


def _post(path: str, body: dict, timeout: int = 8) -> Optional[dict]:
    url = cloud_url()
    _dbg("CLOUD_URL=%r enabled=%s path=%s" % (url, cloud_enabled(), path))
    if not cloud_enabled():
        return None
    try:
        req = urllib.request.Request(url + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "FrameWeave-Client/0.2.10",
                                              "Accept": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _body = json.loads(resp.read().decode() or "{}")
            _dbg("CLOUD_OK path=%s resp=%s" % (path, str(_body)[:200]))
            return _body
    except Exception as _e:  # noqa: BLE001
        _dbg("CLOUD_ERR path=%s err=%s" % (path, repr(_e)))
        return None  # 云端不可达：本地桩回落（正式版这里应视为离线不允许使用）


def cloud_login(email: str, password: str, device_id: str) -> Optional[dict]:
    return _post("/api/auth/login", {"email": email, "password": password, "device_id": device_id})


def cloud_verify(token: str, device_id: str) -> Optional[dict]:
    return _post("/api/auth/verify", {"token": token, "device_id": device_id})


def cloud_email_code(email: str, scene: str) -> Optional[dict]:
    return _post("/api/auth/email/send-code", {"email": email, "scene": scene})

def cloud_register(email: str, code: str, password: str, device_id: str) -> Optional[dict]:
    return _post("/api/auth/register", {"email": email, "code": code, "password": password, "device_id": device_id})

def cloud_reset_password(email: str, code: str, new_password: str) -> Optional[dict]:
    return _post("/api/auth/password/reset", {"email": email, "code": code, "new_password": new_password})

def cloud_activate(email: str, card: str) -> Optional[dict]:
    return _post("/api/auth/activate", {"email": email, "card": card})


def cloud_market_download_auth(item_id: str, token: str, client_version: str = "") -> Optional[dict]:
    """商城下载授权/记录（云端）：授权、记账、记录下载，返回 GitHub Release 直链。"""
    return _post("/api/market/download-auth",
                 {"item_id": item_id, "token": token, "client_version": client_version})


def cloud_market_install_report(item_id: str, token: str, client_version: str = "",
                                result: str = "ok", error: str = "") -> Optional[dict]:
    """商城安装回报（云端）：记录安装计数与流水。"""
    return _post("/api/market/install-report",
                 {"item_id": item_id, "token": token, "client_version": client_version,
                  "result": result, "error": error})


def cloud_market_items() -> Optional[list]:
    """拉取云端商城条目列表（云化时前端浏览；不可达返回 None 由本地桩回落）。"""
    url = cloud_url()
    if not cloud_enabled():
        return None
    try:
        req = urllib.request.Request(url + "/api/market/items",
                                     headers={"Accept": "application/json",
                                              "User-Agent": "FrameWeave-Client/0.2.10"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode() or "{}")
            items = (body or {}).get("items")
            return items if isinstance(items, list) else None
    except Exception as _e:  # noqa: BLE001
        _dbg("CLOUD_ERR path=/api/market/items err=%s" % repr(_e))
        return None
