"""订阅授权服务（M17 本地桩升级版，与服务端云端授权同构）。

与正式云端一致的模型：
- 卡密 = 服务端签名（HMAC-SHA256）的短令牌：FW-<b64(payload)>-<sig12>
  payload: {"plan": "month|quarter|year", "days": N, "serial": 随机}
  本地桩用内置 dev 密钥签发/验签；云端换成真实密钥（M19）。
- 账号状态：plan(trial/member) + expires_at + device_id（1 账号 1 台设备）
- 设备绑定：新设备登录 → 旧设备立即出提示并下线（本地桩返回 device_kick 标记）
- 无离线宽限：每次启动在线校验（本地桩即本地校验，云端化后改远程）
- 积分字段预留（credits，M18/M19 商城分成/充值使用）
存储：backend/data/license.json
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from . import cloud_gateway

# 本地桩卡密签名密钥（正式版由云端服务端持有，客户端绝不内置）
_CARD_SECRET = os.environ.get("FRAMEWEAVE_CARD_SECRET", "frameweave-dev-card-secret-2026")
TRIAL_DAYS = 1  # 注册试用 1 天


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


def _new_session(email: str, device_id: str) -> dict:
    now = time.time()
    return {
        "user_id": uuid.uuid4().hex[:12],
        "email": email,
        "token": uuid.uuid4().hex[:32],
        "plan": "trial",                 # trial|member
        "expires_at": now + TRIAL_DAYS * 86400,
        "device_id": device_id,
        "credits": 0,                    # 积分（预留，M18/M19）
        "cards_used": [],                # 已使用卡密 serial（防重复）
        "last_verified": now,
    }


class LicenseService:
    """本地授权服务（M17：签名卡密 + 设备绑定 + 状态机）。"""

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

    # ---------- 账号生命周期 ----------
    def register(self, email: str, password: str, device_id: str = "") -> dict:
        """注册（本地桩）：新会员账号 + N 天试用。密码兼容字段。"""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return {"ok": False, "message": "邮箱格式不正确"}
        if self._find_by_email(email) is not None:
            return {"ok": False, "message": "该邮箱已注册，请直接登录"}
        session = _new_session(email, device_id)
        self._sessions[session["user_id"]] = session
        self._save()
        return {"ok": True, "session": self._public(session), "plan": session["plan"]}

    def login(self, email: str, password: str, device_id: str = "") -> dict:
        """登录：已注册账号校验；未注册自动注册（保持旧行为）。
        M17 设备绑定：1 账号 1 设备。新设备登录 → 旧设备下线（返回 device_kick）。
        M19 云化：配置 FRAMEWEAVE_CLOUD_URL 后改走云端授权。"""
        # 云端优先（服务端主键：手机号绑定 + JWT + 设备踢下线）
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_login(email, password, device_id)
            if resp is not None:
                return resp
        email = (email or "").strip().lower()
        s = self._find_by_email(email)
        if s is None:
            return self.register(email, password, device_id)
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
        resp = {"ok": True, "session": self._public(s), "plan": s["plan"]}
        if kick:
            resp["device_kick"] = kick
        return resp

    def activate_card(self, email: str, card: str) -> dict:
        """卡密激活：验签 → 兑换时长/套餐 → 计入账号（卡密单次有效）。
        M19 云化：云端签发/校验签名卡密。"""
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_activate(email, card)
            if resp is not None:
                return resp
        email = (email or "").strip().lower()
        payload = _parse_card(card)
        if payload is None:
            return {"ok": False, "message": "卡密无效或签名校验失败"}
        s = self._find_by_email(email)
        if s is None:
            s = _new_session(email, "")
            self._sessions[s["user_id"]] = s
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
        return {"ok": True, "message": f"激活成功：{payload.get('plan')} +{days} 天",
                "plan": "member", "expires_at": s["expires_at"]}

    def verify(self, token: str, device_id: str = "") -> dict:
        """token 校验：返回订阅状态 + 账号信息。无离线宽限（每次启动校验）。
        M19 云化：云端在线校验（不可达即视为离线不允许使用）。"""
        if cloud_gateway.cloud_enabled():
            resp = cloud_gateway.cloud_verify(token, device_id)
            if resp is not None:
                return resp
        for s in self._sessions.values():
            if s.get("token") == token:
                now = time.time()
                if now > float(s.get("expires_at") or 0):
                    return {"ok": False, "reason": "订阅已过期，请续费"}
                if device_id and s.get("device_id") and s["device_id"] != device_id:
                    return {"ok": False, "reason": "账号已在其他设备登录", "device_kick": True}
                s["last_verified"] = now
                self._save()
                return {"ok": True, **self._public(s)}
        return {"ok": False, "reason": "token 无效"}

    # ---------- 工具 ----------
    @staticmethod
    def issue_card(plan: str = "month", days: int = 30) -> str:
        """"管理后台"发行卡密（本地桩/云端预演）。"""
        return _issue_card(plan, days)

    def _public(self, s: dict) -> dict:
        return {
            "user_id": s["user_id"], "email": s["email"], "token": s["token"],
            "plan": s.get("plan", "trial"), "expires_at": s.get("expires_at"),
            "credits": s.get("credits", 0), "device_id": s.get("device_id", ""),
        }
