# -*- coding: utf-8 -*-
"""分层授权与离线降级判定（S1-T10，04 §3.6 建议落地 / R-工程-4）。

策略：
  - 本地节点执行引擎 / 基础节点集：完全离线可用——执行路径不经过本门（verify 只影响
    订阅状态展示，不阻塞本地画布/节点运行）。
  - 仅商城下载、付费节点、云同步/更新走云端验证（market 端点已有明确
    cloud_unavailable / cloud_not_configured 错误，不静默）。
  - 离线授权缓存（云端 HMAC 验签体系 + 到期前刷新）替代硬锁死：
      * 在线 verify 成功（云端验签通过）→ 把完整响应（含云端 signature）写入本地缓存；
      * 云端不可达 / 未配置 → 校验缓存：签名有效（= 云端签发）+ 订阅未过期 →
        降级放行（degraded + offline_cache 显式标记，绝不静默）；
      * 无缓存 / 缓存过期 / 签名被篡改 → 明确错误（cloud_unavailable_no_cache /
        subscription_expired / cache_tampered），不伪造授权。
  - 防伪依据：授权有效性由云端签发的 expires_at 决定（响应整体 HMAC 签名，本地改动任何
    业务字段都会破坏签名）；**不引入"本地时间戳宽限"**（规避原版 S3 的 24h 离线宽限
    伪造面，04 §3.6 风险 4）。

仅事件循环内/普通线程使用（FastAPI 端点、后台轮询）；缓存文件原子写 + 线程锁保护。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from . import cloud_gateway

_CACHE_LOCK = threading.Lock()
_CACHE_FILE: Optional[str] = None

# 明确提示文案（与 server.py 既有 _CLOUD_* 语义一致，绝不静默）
_MSG_UNAVAILABLE = "无法连接云端授权服务器，请联网后重试"
_MSG_NOT_CONFIGURED = "需要云端授权：云端授权服务器未配置，请检查网络后重试"
_MSG_DEGRADED = "离线授权缓存（未能连接云端验证，功能可能受限）"

_CACHE_VERSION = 1


def init(cache_dir: str) -> None:
    """设置离线授权缓存目录（server.py 传 DATA_DIR）。"""
    global _CACHE_FILE
    os.makedirs(cache_dir, exist_ok=True)
    _CACHE_FILE = os.path.join(cache_dir, "auth_cache.json")


def cache_file() -> str:
    """缓存文件路径（未 init 时回落系统临时目录，测试/独立脚本可用）。"""
    if _CACHE_FILE is not None:
        return _CACHE_FILE
    return os.path.join(tempfile.gettempdir(), "fw_auth_cache.json")


def _load_cache() -> dict:
    try:
        p = cache_file()
        if not os.path.isfile(p):
            return {}
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
        return d
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        p = cache_file()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError:
        pass  # 缓存写失败不阻断授权判定（在线路径仍可用）


def cache_write(token: str, device_id: str, resp: dict) -> bool:
    """写入离线授权缓存。

    仅接受云端验签通过的 ok 响应（防伪：signature 无效 / ok=False 一律不缓存）。
    返回是否写入成功。
    """
    if not token or not isinstance(resp, dict) or not resp.get("ok"):
        return False
    if not cloud_gateway.check_response_signature(resp):
        return False
    entry = {
        "token": token,
        "device_id": device_id or resp.get("device_id", ""),
        "cached_at": time.time(),
        "resp": resp,
    }
    with _CACHE_LOCK:
        cache = _load_cache()
        cache[token] = entry
        _save_cache(cache)
    return True


def cache_read(token: str) -> Optional[dict]:
    """读取某 token 的缓存条目（含 cached_at 与原始响应）；不存在返回 None。"""
    with _CACHE_LOCK:
        cache = _load_cache()
        entry = cache.get(token)
        if not isinstance(entry, dict):
            return None
        return dict(entry)


def cache_remove(token: str) -> None:
    with _CACHE_LOCK:
        cache = _load_cache()
        if token in cache:
            cache.pop(token, None)
            _save_cache(cache)


def _offline_fallback(token: str, device_id: str,
                      reason_code: str = "cloud_unavailable") -> dict:
    """云端不可达/未配置时的离线授权缓存判定（复用云端 HMAC 验签体系，绝不静默）。"""
    entry = cache_read(token)
    if entry is None:
        if reason_code == "cloud_unavailable":
            # 云端已配置但不可达：明确 cloud_unavailable（新增 code，旧行为只有 reason）
            return {"ok": False,
                    "reason": _MSG_UNAVAILABLE + "（本机无有效离线授权缓存）",
                    "code": "cloud_unavailable_no_cache", "degraded": True}
        # 云端未配置：保持既有 code=cloud_not_configured（06 §1.3 API 向后兼容）
        return {"ok": False,
                "reason": _MSG_NOT_CONFIGURED + "（本机无有效离线授权缓存，本地节点运行不受影响）",
                "code": "cloud_not_configured", "degraded": True}
    resp = entry.get("resp") or {}
    # ① 云端 HMAC 验签体系：签名无效 = 缓存被篡改 → 拒绝并清除（防伪造授权）
    if not cloud_gateway.check_response_signature(resp):
        cache_remove(token)
        return {"ok": False, "reason": "离线授权缓存签名校验失败（缓存被篡改）",
                "code": "cache_tampered", "degraded": True}
    if not resp.get("ok"):
        cache_remove(token)
        return {"ok": False, "reason": "离线授权缓存无效",
                "code": reason_code + "_no_cache", "degraded": True}
    # ② 设备一致性（与在线 device_kick 语义一致）
    if device_id and resp.get("device_id") and resp["device_id"] != device_id:
        return {"ok": False, "reason": "账号已在其他设备登录", "device_kick": True,
                "degraded": True}
    # ③ 订阅有效：云端签发的 expires_at 权威（本地不自行发宽限）
    exp = resp.get("expires_at")
    if exp and time.time() > float(exp):
        return {"ok": False,
                "reason": "订阅已过期：离线授权缓存已到期，请联网续费/刷新后使用",
                "code": "subscription_expired", "degraded": True}
    # 降级放行：显式标记 offline_cache + degraded，绝不静默
    out: Dict[str, Any] = dict(resp)
    out["degraded"] = True
    out["offline_cache"] = True
    out["reason"] = _MSG_DEGRADED
    out["cached_at"] = entry.get("cached_at")
    return out


def verify(token: str, device_id: str = "") -> dict:
    """分层授权入口：云端优先（在线成功 → 到期前刷新缓存）；不可达/未配置 → 离线缓存降级。

    返回与既有 /api/auth/verify 兼容的 dict；任何路径都不存在"静默永远通过"。
    """
    token = (token or "").strip()
    if not token:
        return {"ok": False, "reason": "token 无效"}
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_verify(token, device_id)
        if resp is None:
            # 云端不可达 → 离线缓存降级
            return _offline_fallback(token, device_id, reason_code="cloud_unavailable")
        if resp.get("sig_fail"):
            # 验签失败/重放：fail-closed，不动缓存（可能中间人；也不降级防伪造）
            return resp
        if resp.get("ok"):
            # 在线成功 → 到期前刷新缓存（后续断网可降级放行）
            cache_write(token, device_id, resp)
        else:
            # 云端权威业务失败（过期/禁用/device_kick）→ 移除本地缓存防旧缓存复活
            cache_remove(token)
        return resp
    # 云端未配置（FRAMEWEAVE_CLOUD_URL="" 显式离线）→ 缓存降级或明确 cloud_not_configured
    return _offline_fallback(token, device_id, reason_code="cloud_not_configured")


def auth_status() -> dict:
    """当前授权模式（供 /api/health 与壳层提示）。

    mode: online（云端为主且缓存可用作降级）| offline_cache（离线缓存降级）|
          not_configured（云端未配置且无缓存）| cloud_unverified（云端已配置、无缓存）。
    """
    entries = _load_cache()
    valid = False
    for entry in entries.values():
        resp = (entry or {}).get("resp")
        if isinstance(resp, dict) and resp.get("ok") and cloud_gateway.check_response_signature(resp):
            valid = True
            break
    if not cloud_gateway.cloud_enabled():
        mode = "offline_cache" if valid else "not_configured"
    else:
        mode = "online" if valid else "cloud_unverified"
    return {
        "mode": mode,
        "cloud_enabled": cloud_gateway.cloud_enabled(),
        "degraded": mode == "offline_cache",
        "cache_valid": valid,
        "cache_entries": len(entries),
    }
