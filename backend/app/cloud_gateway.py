# -*- coding: utf-8 -*-
"""云端授权网关（M19 对接位，P3/t3 授权链硬化版）。

默认云端：https://shouquan.frameweave.top（韩国授权服务器 43.108.23.200，Cloudflare HTTPS 反代 127.0.0.1:8789）。
默认即云化：登录/校验/激活走远程授权服务器（签名卡密 + 设备绑定 + 无离线宽限）。

P3 硬化点：
  1) verify 响应 HMAC 签名校验（④）：云端用 FRAMEWEAVE_VERIFY_SECRET 对 /api/auth/* 响应
     签名（signature 字段 = HMAC-SHA256(规范化正文)[:24]，正文排除 signature/sig/req_nonce
     并键升序拼接）。客户端 cloud_verify 强制验签 + nonce 防重放：
     无签名 / 签名不符 / nonce 回显不匹配 → 拒绝（防 fiddler 伪造 member 状态/重放旧响应）。
  2) 敏感字符串 obf 通道（⑤）：云端 URL / 环境变量名 / 密钥默认值 / 端点路径
     不以明文出现在源码与打包产物（配合 pyarmor mix-str 双保险）。
  3) cloud_enabled() 语义不变：显式环境变量 FRAMEWEAVE_CLOUD_URL（含空串）
     覆盖默认云端 → 本地联调/离线桩唯一开关。

本地桩/开发：显式设置环境变量 FRAMEWEAVE_CLOUD_URL=""（空）即回落本地校验。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import threading
import time
import uuid
from typing import Optional

from . import obf

CLIENT_VERSION = "0.3.4"  # 与 desktop/package.json / server.APP_VERSION / cloud_server.VERSION 保持一致

# urllib 默认遵循系统地址顺序；部分 VM 的 IPv6 可解析但不可出网。
# 仅在一次请求失败后临时优先 IPv4，不修改全局 socket 配置。
_IPV4_FALLBACK_LOCK = threading.Lock()

# 签名时跳过的字段（与 cloud_server.py 端一致）：仅排除签名自身字段；
# req_nonce 必须纳入签名——否则攻击者可改 nonce 回显骗取重放放行。
_SKIP_SIGN_FIELDS = {"signature", "sig"}
# HMAC 结果截取：云端的签名要用同样的 truncate 长度（24 字节 = 48 hex）
SIG_TRUNCATE_BYTES = 24
# 超时分层：连接阶段短预算（秒）——连接超时/冷连接用更小的 socket 默认超时
# 快速失败，读响应整体超时由 _post 的 attempt 级 timeout 控制（末次放宽到 ≥12s）。
CONNECT_TIMEOUT = 5.0


def _verify_secret() -> str:
    """云端响应验签密钥：环境变量 FRAMEWEAVE_VERIFY_SECRET 优先，否则内嵌默认（obf 混淆）。"""
    return os.environ.get(obf.verify_secret_env(), "") or obf.verify_secret_default()


def cloud_url() -> str:
    """云端根 URL：环境变量 FRAMEWEAVE_CLOUD_URL 覆盖，默认 https://shouquan.frameweave.top。"""
    return os.environ.get(obf.cloud_url_env(), obf.cloud_url_default()).rstrip("/")


def cloud_enabled() -> bool:
    return bool(cloud_url())


def _norm_seg(v) -> str:
    """规范化单个值（与 cloud_server.py 端完全一致，保证跨端可复算）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, (list, dict)):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return str(v)


def canonical_body(body: dict) -> str:
    """规范化签名正文：排除非业务字段，键升序拼接 k=v。"""
    items = []
    for k, v in body.items():
        if k in _SKIP_SIGN_FIELDS:
            continue
        if v is None:
            continue
        items.append((str(k), _norm_seg(v)))
    items.sort(key=lambda kv: kv[0])
    return "".join(f"{k}={v}" for k, v in items)


def sign_payload(secret: str, body: dict, truncate: int = SIG_TRUNCATE_BYTES) -> str:
    """HMAC-SHA256 签名（云端侧使用；客户端仅验签）。"""
    canon = canonical_body(body).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canon, hashlib.sha256).hexdigest()[: truncate * 2]


def check_response_signature(body: dict, secret: str = "") -> bool:
    """校验云端响应签名。body 必须包含 signature（hex HMAC-SHA256 截 48 字符）。"""
    try:
        sig = body.get("signature", "")
        if not sig or not isinstance(sig, str) or len(sig) != SIG_TRUNCATE_BYTES * 2:
            return False
        secret = secret or _verify_secret()
        calc = sign_payload(secret, body)
        return hmac.compare_digest(calc, sig.lower())
    except Exception:  # noqa: BLE001
        return False


def _dbg(msg: str) -> None:
    try:
        _log = os.environ.get("FRAMEWEAVE_DEBUG_LOG", "")
        if _log:
            import datetime
            with open(_log, "a", encoding="utf-8") as _f:
                _f.write(datetime.datetime.now().isoformat() + " " + msg + "\n")
    except Exception:
        pass


def _urlopen_ipv4(req, timeout: int):
    """单次请求强制 IPv4，保留 HTTPS SNI/证书校验的原始主机名。"""
    import urllib.request as _ur

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
            return _ur.urlopen(req, timeout=timeout)
        finally:
            socket.getaddrinfo = original


def _set_response_read_timeout(resp, timeout: float) -> None:
    """urllib 已完成连接/TLS/响应头后，把响应 socket 切换到 body 读取预算。

    CPython 的 HTTPResponse 使用 fp.raw._sock；若实现不暴露该内部字段，保持 urllib
    建立连接时的 timeout 即可（安全降级，不影响普通响应）。
    """
    try:
        sock = resp.fp.raw._sock
        sock.settimeout(float(timeout))
    except Exception:  # noqa: BLE001
        pass


def _http_body(resp, read_timeout: float) -> bytes:
    _set_response_read_timeout(resp, read_timeout)
    return resp.read()


def _post(path: str, body: dict, timeout: int = 8) -> Optional[dict]:
    """云端 POST 代理（含网络容错重试，纯云化）。

    重试策略：
    - 仅网络层异常重试（URLError/超时/EOF/连接重置/读响应失败），最多 2 次
      指数退避（0.5s/1s）；首请求/跨国冷连接偶发抖动用慢速容错（末次 attempt
      超时放宽到 ≥12s）。IPv4 回退逻辑（_urlopen_ipv4）保持不变。
    - 业务错误（读到 HTTP 响应体，含 4xx/5xx、ok:false、签名失败）绝不重试，
      避免注册/激活/扣费等副作用重复提交。
    - 重试后仍失败才返回 None（调用方按"云端不可达"拒绝离线回落）。
    """
    import urllib.error as ur_err
    import urllib.request as ur
    url = cloud_url()
    _dbg("CLOUD_URL=%r enabled=%s path=%s" % (url, cloud_enabled(), path))
    if not cloud_enabled():
        return None
    tries = ((timeout, 0.0), (timeout, 0.5), (max(timeout, 12), 1.0))
    last_net_err: Optional[BaseException] = None
    for attempt, (t, backoff) in enumerate(tries):
        if backoff > 0:
            try:
                time.sleep(backoff)
            except Exception:  # noqa: BLE001
                pass
        req = ur.Request(url + path,
                         data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json",
                                  "User-Agent": "FrameWeave-Client/" + CLIENT_VERSION,
                                  "Accept": "application/json"},
                         method="POST")
        # 超时分层：连接阶段用 CONNECT_TIMEOUT 短预算（快速失败），读响应 body 用
        # attempt 级 t（末次放宽容错首请求/冷连接）。不额外改全局 socket 默认超时
        # （避免 setdefaulttimeout/ipv4 锁的全局副作用）。
        connect_t = min(t, CONNECT_TIMEOUT)
        try:
            # VM 网络优先走 IPv4；若 IPv4 失败，再回退系统默认地址族（含 IPv6）。
            with _urlopen_ipv4(req, connect_t) as resp:
                _body = json.loads(_http_body(resp, t).decode() or "{}")
                _dbg("CLOUD_OK path=%s family=ipv4 resp=%s" % (path, str(_body)[:200]))
                return _body
            # 注意：不可在同一路径再持 _IPV4_FALLBACK_LOCK —— _urlopen_ipv4 内部已持锁（非重入）
        except ur_err.HTTPError as e:
            # 读到 HTTP 响应体（4xx/5xx）→ 业务层错误：不重试，返回解析后的响应
            try:
                raw = e.read().decode("utf-8", "replace") or ""
            except Exception:  # noqa: BLE001
                raw = ""
            try:
                parsed = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                parsed = {}
            _dbg("CLOUD_HTTP path=%s code=%s resp=%s" % (path, e.code, raw[:200]))
            if isinstance(parsed, dict):
                parsed.setdefault("ok", False)
                parsed.setdefault("message", "云端 HTTP " + str(e.code))
                return parsed
            return {"ok": False, "message": "云端 HTTP " + str(e.code) + ((": " + raw[:120]) if raw else ""),
                    "http_status": e.code}
        except Exception as _first_err:  # noqa: BLE001 —— 网络层异常，尝试默认地址族
            _dbg("CLOUD_RETRY_DEFAULT path=%s err=%s" % (path, repr(_first_err)))
            last_net_err = _first_err
            try:
                with ur.urlopen(req, timeout=connect_t) as resp:
                    _body = json.loads(_http_body(resp, t).decode() or "{}")
                    _dbg("CLOUD_OK path=%s family=default resp=%s" % (path, str(_body)[:200]))
                    return _body
            except ur_err.HTTPError as e:
                # 默认地址族也拿到业务响应体 → 不重试
                try:
                    raw = e.read().decode("utf-8", "replace") or ""
                except Exception:  # noqa: BLE001
                    raw = ""
                try:
                    parsed = json.loads(raw) if raw else {}
                except (ValueError, TypeError):
                    parsed = {}
                _dbg("CLOUD_HTTP path=%s code=%s resp=%s" % (path, e.code, raw[:200]))
                if isinstance(parsed, dict):
                    parsed.setdefault("ok", False)
                    parsed.setdefault("message", "云端 HTTP " + str(e.code))
                    return parsed
                return {"ok": False, "message": "云端 HTTP " + str(e.code) + ((": " + raw[:120]) if raw else ""),
                        "http_status": e.code}
            except Exception as _second_err:  # noqa: BLE001
                last_net_err = _second_err
                _dbg("CLOUD_ERR path=%s attempt=%d err=%s" % (path, attempt, repr(_second_err)))
    if last_net_err is not None:
        _dbg("CLOUD_GIVE_UP path=%s err=%s" % (path, repr(last_net_err)))
    return None  # 网络层重试后仍失败：云端不可达（调用方拒绝离线回落）


def _checked(resp: Optional[dict], require_signature: bool = False, nonce: str = "") -> Optional[dict]:
    """云端 auth 响应验签层。

    require_signature=False（登录/注册/激活/重置/验证码）：有 signature 必须验过；
        无 signature（旧版云端）宽容放行，保证向后兼容，但响应不回传"已验证"标记。
    require_signature=True（verify 门禁）：签名缺失/无效/nonce 不匹配 → 拒绝。
    失败统一返回 {"ok": False, "...", "sig_fail": True}，绝不把伪造响应当成功。
    """
    if resp is None:
        return None
    if not isinstance(resp, dict):
        return {"ok": False, "message": "云端响应格式异常", "sig_fail": True}
    if require_signature:
        # 门禁端点（verify）：签名缺失/无效/nonce 不匹配一律拒绝（fail-closed）。
        # nonce 检查仅在响应带签名时进行（现代云端）；无签名直接按"伪造/旧云端"拒绝。
        if not resp.get("signature"):
            return {"ok": False, "reason": "云端响应缺少签名（可能被中间人拦截或云端版本过旧）",
                    "sig_fail": True}
        if nonce and resp.get("req_nonce") != nonce:
            return {"ok": False, "reason": "云端响应重放/伪造（nonce 不匹配）", "sig_fail": True}
        if not check_response_signature(resp):
            return {"ok": False, "reason": "云端响应签名校验失败（可能被中间人篡改）", "sig_fail": True}
        resp.pop("signature", None)
        resp["cloud_verified"] = True
        return resp
    if resp.get("signature"):
        if not check_response_signature(resp):
            return {"ok": False, "message": "云端响应签名校验失败（可能被中间人篡改）", "sig_fail": True}
        resp.pop("signature", None)
    resp.setdefault("cloud", True)
    return resp


def cloud_login(email: str, password: str, device_id: str) -> Optional[dict]:
    resp = _post("/api/auth/login", {"email": email, "password": password, "device_id": device_id})
    return _checked(resp)


def cloud_verify(token: str, device_id: str) -> Optional[dict]:
    """验证令牌（门禁端点，强制验签 + 防重放）。

    请求带一次性 nonce，云端回显并纳入签名；签名无效 / nonce 不匹配 → 拒绝。
    """
    nonce = uuid.uuid4().hex
    resp = _post(obf.verify_path(),
                 {"token": token, "device_id": device_id, "nonce": nonce})
    return _checked(resp, require_signature=True, nonce=nonce)


def cloud_email_code(email: str, scene: str) -> Optional[dict]:
    resp = _post("/api/auth/email/send-code", {"email": email, "scene": scene})
    return _checked(resp)


def cloud_register(email: str, code: str, password: str, device_id: str) -> Optional[dict]:
    resp = _post("/api/auth/register",
                 {"email": email, "code": code, "password": password, "device_id": device_id})
    return _checked(resp)


def cloud_reset_password(email: str, code: str, new_password: str) -> Optional[dict]:
    resp = _post("/api/auth/password/reset",
                 {"email": email, "code": code, "new_password": new_password})
    return _checked(resp)


def cloud_activate(email: str, card: str) -> Optional[dict]:
    resp = _post("/api/auth/activate", {"email": email, "card": card})
    return _checked(resp)


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


def cloud_market_items(q: str = "", kind: str = "", official: str = "") -> Optional[list]:
    """拉取云端商城条目列表，并把搜索/类型/官方筛选参数转发到云端。"""
    import urllib.request as ur
    from urllib.parse import urlencode
    url = cloud_url()
    query = urlencode({k: v for k, v in {"q": q, "kind": kind, "official": official}.items() if v})
    if query:
        url += "/api/market/items?" + query
    else:
        url += "/api/market/items"
    if not cloud_enabled():
        return None
    try:
        req = ur.Request(url,
                         headers={"Accept": "application/json",
                                  "User-Agent": "FrameWeave-Client/" + CLIENT_VERSION})
        try:
            with ur.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode() or "{}")
                items = (body or {}).get("items")
                return items if isinstance(items, list) else None
        except Exception as _first_err:  # noqa: BLE001
            _dbg("CLOUD_RETRY_IPV4 path=/api/market/items err=%s" % repr(_first_err))
            try:
                with _urlopen_ipv4(req, 8) as resp:
                    body = json.loads(resp.read().decode() or "{}")
                    items = (body or {}).get("items")
                    return items if isinstance(items, list) else None
            except Exception as _second_err:  # noqa: BLE001
                _dbg("CLOUD_ERR path=/api/market/items err=%s" % repr(_second_err))
                return None
    except Exception as _e:  # noqa: BLE001
        _dbg("CLOUD_ERR path=/api/market/items err=%s" % repr(_e))
        return None