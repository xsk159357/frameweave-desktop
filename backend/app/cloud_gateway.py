"""云端授权网关（M19 对接位）。

本地桩：未配置时全部走本地校验（开发/单机）。
云化：设置环境变量 FRAMEWEAVE_CLOUD_URL 后，登录/校验/激活改走远程授权服务器
（服务端 JWT + 签名卡密 + 设备绑定 + 手机号主键 + 无离线宽限）。
正式上线前需：公网服务器 + 域名(HTTPS) + 阿里云/腾讯云 SMS + 服务端实现。
"""
from __future__ import annotations
import json
import os
import urllib.request
from typing import Optional

CLOUD_URL = os.environ.get("FRAMEWEAVE_CLOUD_URL", "").rstrip("/")

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
    return bool(CLOUD_URL)


def _post(path: str, body: dict, timeout: int = 8) -> Optional[dict]:
    _dbg("CLOUD_URL=%r enabled=%s path=%s" % (CLOUD_URL, cloud_enabled(), path))
    if not cloud_enabled():
        return None
    try:
        req = urllib.request.Request(CLOUD_URL + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
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


def cloud_activate(email: str, card: str) -> Optional[dict]:
    return _post("/api/auth/activate", {"email": email, "card": card})
