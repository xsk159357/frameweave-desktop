# -*- coding: utf-8 -*-
"""【DEPRECATED · 纯云化后不可达】本地授权桩（LicenseService）。

t41 纯云化改造后，本模块不再是任何授权路由的调用目标：
- server.py 已删除所有 `license_svc.xxx()` 回落分支（send-code/register/reset/
  login/activate/verify 全部强制走 cloud_gateway，云 URL 为空或云端不可达一律拒绝）。
- market.py 的 install/download_auth/install_report 也已改为云端直连，不再接收
  license_svc。
- 类定义仅保留供旧快照/审计工具引用；任何调用本类的方法都会遇到
  cloud_gateway.cloud_enabled() 分支已被移除的问题——新代码不得再引用本模块。

S1-T10（分层授权，04 §3.6）：离线可用性改由 `authz.py`（离线授权缓存：云端 HMAC
验签 + 到期前刷新）承担——**不复用本模块的本地卡密/时间戳逻辑**（原版 S3 伪造面）。
本模块继续冻结：无路由引用、不参与授权判定，仅保留定义供审计对照。

历史职责（供审计参考，不再执行）：卡密本地签名/验签、本地账号会话、桩替换检测。
服务端授权权威 = cloud_server.py（HMAC 验签 + 云端 DB）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import marshal
import os
import sys
import time
import uuid
from typing import Optional

from . import cloud_gateway, obf

# 本地桩卡密签名密钥（正式版由云端服务端持有；dev 默认值经 obf 混淆）
_CARD_SECRET = os.environ.get(obf.card_secret_env(), "") or obf.dev_card_secret()
TRIAL_DAYS = 1  # 注册试用 1 天

# 本地桩不再是主授权路径：任何本地求解都标记为降级
_DEGRADED_REASON = "本地离线授权模式（未连接云端），功能可能受限"


def _degraded(d: dict) -> dict:
    """给本地桩响应打上明确降级标记（防"静默永远通过"）。"""
    d["degraded"] = True
    d["degraded_reason"] = _DEGRADED_REASON
    return d


def _sign(payload_b64: str) -> str:
    return hmac.new(_CARD_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:12]


def _issue_card(plan: str = "month", days: int = 30, secret_override: Optional[str] = None) -> str:
    """签发一张签名卡密（模拟管理后台/云端发行）。"""
    payload = {
        "plan": plan,
        "days": days,
        "serial": uuid.uuid4().hex[:10],
    }
    b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    key = secret_override or _CARD_SECRET
    sig = hmac.new(key.encode(), b64.encode(), hashlib.sha256).hexdigest()[:12]
    return f"FW-{b64}-{sig}"


def _parse_card(card: str) -> Optional[dict]:
    """验证签名并解析卡密。返回 payload 或 None。"""
    card = (card or "").strip()
    if not card.startswith("FW-"):
        return None
    try:
        _, b64, sig = card.split("-", 2)
        calc = hmac.new(_CARD_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()[:12]
        if not hmac.compare_digest(calc, sig):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("plan") or not payload.get("days"):
            return None
        return payload
    except (ValueError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _hash_pw(password: str, salt: str) -> str:
    """兼容现有账号的密码哈希；新密码后续迁移到 scrypt/Argon2id。"""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _new_session(email: str, device_id: str, password: str = "") -> dict:
    now = time.time()
    salt = uuid.uuid4().hex[:8]
    return {
        "user_id": uuid.uuid4().hex[:12],
        "email": email,
        "token": uuid.uuid4().hex[:32],
        "plan": "trial",                 # trial|member
        "expires_at": now + TRIAL_DAYS * 86400,
        "device_id": device_id,
        "credits": 0,                    # 积分（预留）
        "cards_used": [],                # 已使用卡密 serial（防重复）
        "last_verified": now,
        "salt": salt,
        "password_hash": _hash_pw(password, salt) if password else "",
    }


class LicenseService:
    """【DEPRECATED】本地授权服务（纯云化后无任何路由调用；仅保留定义供审计引用）。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "license.json")
        self._sessions: dict = {}
        os.makedirs(data_dir, exist_ok=True)
        self._load()

    # ---------- 存储 ----------
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._sessions = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._sessions = {}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, ensure_ascii=False, indent=2)

    def _find_by_email(self, email: str) -> Optional[dict]:
        for s in self._sessions.values():
            if s.get("email", "").lower() == email.lower():
                return s
        return None

    # ---------- 桩替换检测 ----------
    def _tamper_check(self) -> bool:
        """授权核心函数是否被（内存 monkey-patch）替换为"永远通过"桩。

        返回 True = 检测到篡改。frozen 打包版由 exe 完整性清单兜底（此处恒 False）。
        """
        return stub_tampered()

    # ---------- 账号生命周期 ----------
    def register_verified(self, email: str, password: str, device_id: str = "") -> dict:
        """注册（离线桩）：新会员账号 + N 天试用。"""
        email = (email or "").strip().lower()
        if not email or "@" not in email or not email.endswith("@qq.com"):
            return {"ok": False, "message": "邮箱格式不正确"}
        if self._find_by_email(email) is not None:
            return {"ok": False, "message": "该邮箱已注册，请直接登录"}
        if self._tamper_check():
            return {"ok": False, "message": "本地授权组件完整性校验失败", "tampered": True}
        session = _new_session(email, device_id, password)
        self._sessions[session["user_id"]] = session
        self._save()
        return _degraded({"ok": True, "session": self._public(session), "plan": session["plan"]})

    def reset_password(self, email: str, password: str) -> dict:
        email = (email or "").strip().lower()
        s = self._find_by_email(email)
        if s is None:
            return {"ok": False, "message": "账号不存在"}
        s["salt"] = uuid.uuid4().hex[:8]
        s["password_hash"] = _hash_pw(password, s["salt"])
        self._save()
        return {"ok": True, "message": "密码重置成功"}

    def login(self, email: str, password: str, device_id: str = "") -> dict:
        """登录：云端启用时强制云端；否则本地离线桩（带降级标记）。

        云端不可达：正式语义下拒绝服务（无离线宽限），绝不静默回落本地桩——
        只有显式配置 FRAMEWEAVE_CLOUD_URL=""（离线/联调桩）才走本地。
        """
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_login(email, password, device_id)
            if resp is None:
                return {"ok": False, "message": "无法连接云端授权服务器，请联网后重试"}
            if resp.get("sig_fail"):
                return {"ok": False, "message": resp.get("message") or resp.get("reason") or "云端响应校验失败",
                        "sig_fail": True}
            return resp
        email = (email or "").strip().lower()
        s = self._find_by_email(email)
        if s is None:
            return {"ok": False, "message": "该邮箱尚未注册，请先注册"}
        if self._tamper_check():
            return {"ok": False, "message": "本地授权组件完整性校验失败", "tampered": True}
        # 密码校验（本地桩）：新账号已存哈希，必须匹配
        if s.get("password_hash"):
            if not password or _hash_pw(password, s.get("salt", "")) != s["password_hash"]:
                return {"ok": False, "message": "密码不正确"}
        else:
            # 旧账号（无哈希，历史数据）：首次登录用本次密码绑定，之后校验
            if password:
                s["salt"] = uuid.uuid4().hex[:8]
                s["password_hash"] = _hash_pw(password, s["salt"])
            else:
                return {"ok": False, "message": "该账号需设置密码后才能登录"}
        now = time.time()
        kick = None
        old_device = s.get("device_id")
        if device_id and old_device and old_device != device_id:
            kick = {"old_device": old_device, "new_device": device_id,
                    "message": "账号已在其他设备登录，旧设备已下线"}
        if device_id:
            s["device_id"] = device_id
        s["last_verified"] = now
        self._save()
        resp = _degraded({"ok": True, "session": self._public(s), "plan": s["plan"]})
        if kick:
            resp["device_kick"] = kick
        return resp

    def activate_card(self, email: str, card: str) -> dict:
        """卡密激活：云端启用时强制云端；否则本地离线桩（带降级标记）。"""
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_activate(email, card)
            if resp is None:
                return {"ok": False, "message": "无法连接云端授权服务器，请联网后重试"}
            if resp.get("sig_fail"):
                return {"ok": False, "message": resp.get("message") or "云端响应校验失败", "sig_fail": True}
            return resp
        email = (email or "").strip().lower()
        payload = _parse_card(card)
        if payload is None:
            return {"ok": False, "message": "卡密无效或签名校验失败"}
        s = self._find_by_email(email)
        if s is None:
            s = _new_session(email, "")
            self._sessions[s["user_id"]] = s
        if self._tamper_check():
            return {"ok": False, "message": "本地授权组件完整性校验失败", "tampered": True}
        serial = payload.get("serial", "")
        if serial in s.get("cards_used", []):
            return {"ok": False, "message": "该卡密已被使用"}
        days = int(payload.get("days") or 30)
        now = time.time()
        base = max(now, float(s.get("expires_at") or now))  # 续费叠加
        s["expires_at"] = base + days * 86400
        s["plan"] = "member"
        s["cards_used"] = s.get("cards_used", []) + [serial]
        s["last_verified"] = now
        self._save()
        return _degraded({"ok": True, "message": f"激活成功：{payload.get('plan')} +{days} 天",
                          "plan": "member", "expires_at": s["expires_at"]})

    def verify(self, token: str, device_id: str = "") -> dict:
        """token 校验：云端启用时强制云端（云端响应 HMAC 验签）；否则本地离线桩。

        本地桩校验结果一律带 degraded 降级标记，且授权核心被替换（桩替换）时
        直接返回失败。
        """
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_verify(token, device_id)
            if resp is None:
                return {"ok": False, "reason": "无法连接云端授权服务器，请联网后重试"}
            if resp.get("sig_fail"):
                return {"ok": False, "reason": resp.get("reason") or "云端响应校验失败", "sig_fail": True}
            return resp
        if self._tamper_check():
            return {"ok": False, "reason": "本地授权组件完整性校验失败", "tampered": True}
        for s in self._sessions.values():
            if s.get("token") == token:
                now = time.time()
                if now > float(s.get("expires_at") or 0):
                    return _degraded({"ok": False, "reason": "订阅已过期，请续费"})
                if device_id and s.get("device_id") and s["device_id"] != device_id:
                    return _degraded({"ok": False, "reason": "账号已在其他设备登录", "device_kick": True})
                s["last_verified"] = now
                self._save()
                return _degraded({"ok": True, **self._public(s)})
        return _degraded({"ok": False, "reason": "token 无效"})

    # ---------- 工具 ----------
    @staticmethod
    def issue_card(plan: str = "month", days: int = 30) -> str:
        """"管理后台"发行卡密（离线桩/云端预演）。"""
        return _issue_card(plan, days)

    def register(self, email: str, password: str, device_id: str = "") -> dict:
        """兼容旧调用：正式注册必须先通过邮箱验证码。"""
        return {"ok": False, "message": "注册需要邮箱验证码"}

    def _public(self, s: dict):
        return {
            "user_id": s["user_id"], "email": s["email"], "token": s["token"],
            "plan": s.get("plan", "trial"), "expires_at": s.get("expires_at"),
            "credits": s.get("credits", 0), "device_id": s.get("device_id", ""),
            "degraded": True,  # 本地桩状态显式透出
        }


# ---------- 桩替换检测（模块级快照） ----------
def _code_hash(fn) -> str:
    try:
        return hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest()
    except Exception:  # pyarmor 包装/不可序列化时无法比较
        return ""


def _snapshot_auth_hashes() -> dict:
    return {
        "verify": _code_hash(LicenseService.verify),
        "login": _code_hash(LicenseService.login),
        "activate_card": _code_hash(LicenseService.activate_card),
        "parse_card": _code_hash(_parse_card),
    }


# 导入完成时快照授权核心函数 code-object —— 仅在源码模式执行：
# frozen 打包版 pyarmor 会把函数代码替换为运行时包装对象，marhsal 序列化会触发
# 原生访问冲突（0xC0000005），且打包版的完整性由 exe 哈希清单兜底（integrity.py），
# 快照无意义。
_AUTH_HASH_BASELINE: dict = {}
if not getattr(sys, "frozen", False):
    _AUTH_HASH_BASELINE = _snapshot_auth_hashes()


def stub_tampered() -> bool:
    """检测授权核心逻辑是否被替换（内存 monkey-patch）为"永远通过"桩。

    - 源码模式：code-object 快照不一致 → True。
    - frozen 打包模式：快照为空恒 False，由启动完整性清单（exe 哈希）兜底。
    """
    if getattr(sys, "frozen", False) or not _AUTH_HASH_BASELINE:
        return False
    return _snapshot_auth_hashes() != _AUTH_HASH_BASELINE