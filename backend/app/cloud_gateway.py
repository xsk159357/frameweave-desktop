"""云端授权网关（M19 对接位，已正式云化）。

默认云端：https://frameweave.ameaaos.com（雨云日本，openresty HTTPS 反代 127.0.0.1:8789）。
默认即云化：登录/校验/激活走远程授权服务器（签名卡密 + 设备绑定 + 无离线宽限）。
本地桩/开发：显式设置环境变量 FRAMEWEAVE_CLOUD_URL=""（空）即回落本地校验。
"""
from __future__ import annotations
import json
import os
import socket
import threading
import urllib.request
from typing import Optional

def cloud_url() -> str:
    return os.environ.get("FRAMEWEAVE_CLOUD_URL", "https://frameweave.ameaaos.com").rstrip("/")

CLOUD_URL = ""
CLIENT_VERSION = "0.2.12"  # 与 desktop/package.json / server.APP_VERSION 保持一致

# urllib 默认遵循系统地址顺序；部分 VM 的 IPv6 可解析但不可出网。
# 仅在一次请求失败后临时优先 IPv4，不修改全局 socket 配置。
_IPV4_FALLBACK_LOCK = threading.Lock()
_IPV4_FALLBACK_DONE = False


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


def _urlopen_ipv4(req, timeout: int):
    """单次请求强制 IPv4，保留 HTTPS SNI/证书校验的原始主机名。"""
    original = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        rows = original(host, port, family, type, proto, flags)
        filtered = [row for row in rows if row[0] == socket.AF_INET]
        if not filtered:
            raise OSError("no IPv4 address available for %s" % host)
        return filtered

    with _IPV4_FALLBACK_LOCK:
        socket.getaddrinfo = ipv4_only
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        finally:
            socket.getaddrinfo = original


def _post(path: str, body: dict, timeout: int = 8) -> Optional[dict]:
    url = cloud_url()
    _dbg("CLOUD_URL=%r enabled=%s path=%s" % (url, cloud_enabled(), path))
    if not cloud_enabled():
        return None
    try:
        req = urllib.request.Request(url + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "FrameWeave-Client/" + CLIENT_VERSION,
                                              "Accept": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _body = json.loads(resp.read().decode() or "{}")
                _dbg("CLOUD_OK path=%s family=default resp=%s" % (path, str(_body)[:200]))
                return _body
        except Exception as _first_err:  # noqa: BLE001
            _dbg("CLOUD_RETRY_IPV4 path=%s err=%s" % (path, repr(_first_err)))
            try:
                with _urlopen_ipv4(req, timeout) as resp:
                    _body = json.loads(resp.read().decode() or "{}")
                    _dbg("CLOUD_OK path=%s family=ipv4 resp=%s" % (path, str(_body)[:200]))
                    return _body
            except Exception as _second_err:  # noqa: BLE001
                _dbg("CLOUD_ERR path=%s err=%s" % (path, repr(_second_err)))
                return None  # 云端不可达：正式版拒绝离线回落
    except Exception as _e:  # noqa: BLE001
        _dbg("CLOUD_ERR path=%s err=%s" % (path, repr(_e)))
        return None


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
                                              "User-Agent": "FrameWeave-Client/" + CLIENT_VERSION})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode() or "{}")
                items = (body or {}).get("items")
                return items if isinstance(items, list) else None
        except Exception as _first_err:
            _dbg("CLOUD_RETRY_IPV4 path=/api/market/items err=%s" % repr(_first_err))
            try:
                with _urlopen_ipv4(req, 8) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                    items = (body or {}).get("items")
                    return items if isinstance(items, list) else None
            except Exception as _second_err:
                _dbg("CLOUD_ERR path=/api/market/items err=%s" % repr(_second_err))
                return None
    except Exception as _e:  # noqa: BLE001
        _dbg("CLOUD_ERR path=/api/market/items err=%s" % repr(_e))
        return None
