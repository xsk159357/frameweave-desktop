#!/usr/bin/env python3
"""FrameWeave 云端授权服务端（M19 云端化）。

纯标准库实现（http.server + sqlite3），零第三方依赖。
契约对齐 backend/app/cloud_gateway.py 的客户端网关：
    POST /api/auth/login     {email, password, device_id}
    POST /api/auth/verify    {token, device_id}
    POST /api/auth/activate  {email, card}
    POST /api/admin/issue-card  {plan, days, admin_key}   (管理签发卡密)

环境变量：
    FRAMEWEAVE_CARD_SECRET  卡密签名密钥（生产必须替换默认值）
    FRAMEWEAVE_ADMIN_KEY    管理端点签发卡密的密钥（生产必须设置）
    CLOUD_PORT              监听端口（默认 8789）
    CLOUD_DB                数据库路径（默认 ./cloud.db）

启动：python3 cloud_server.py  (绑定 0.0.0.0)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CARD_SECRET = os.environ.get("FRAMEWEAVE_CARD_SECRET", "frameweave-dev-card-secret-2026")
ADMIN_KEY = os.environ.get("FRAMEWEAVE_ADMIN_KEY", "frameweave-admin-key-2026")
TRIAL_DAYS = 1
DB_PATH = os.environ.get("CLOUD_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud.db"))


# ---------- SQLite ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        email TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL DEFAULT '',
        salt TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL,
        token TEXT NOT NULL DEFAULT '',
        plan TEXT NOT NULL DEFAULT 'trial',
        expires_at REAL NOT NULL DEFAULT 0,
        device_id TEXT NOT NULL DEFAULT '',
        credits INTEGER NOT NULL DEFAULT 0,
        cards_used TEXT NOT NULL DEFAULT '[]',
        last_verified REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL DEFAULT 0
    )""")
    return conn


def find_by_email(email: str):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_session(s: dict):
    conn = get_db()
    try:
        conn.execute("""INSERT INTO sessions (email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at)
            VALUES (:email,:password_hash,:salt,:user_id,:token,:plan,:expires_at,:device_id,:credits,:cards_used,:last_verified,:created_at)
            ON CONFLICT(email) DO UPDATE SET
              password_hash=excluded.password_hash, salt=excluded.salt, user_id=excluded.user_id,
              token=excluded.token, plan=excluded.plan, expires_at=excluded.expires_at,
              device_id=excluded.device_id, credits=excluded.credits, cards_used=excluded.cards_used,
              last_verified=excluded.last_verified""", s)
        conn.commit()
    finally:
        conn.close()


# ---------- 密码 ----------
def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def new_session(email: str, device_id: str, password: str = "") -> dict:
    now = time.time()
    salt = uuid.uuid4().hex[:8]
    return {
        "email": email, "password_hash": hash_password(password, salt) if password else "",
        "salt": salt, "user_id": uuid.uuid4().hex[:12],
        "token": uuid.uuid4().hex[:32],
        "plan": "trial", "expires_at": now + TRIAL_DAYS * 86400,
        "device_id": device_id, "credits": 0,
        "cards_used": "[]", "last_verified": now, "created_at": now,
    }


def public(s: dict) -> dict:
    return {
        "user_id": s["user_id"], "email": s["email"], "token": s["token"],
        "plan": s.get("plan", "trial"), "expires_at": s.get("expires_at"),
        "credits": s.get("credits", 0), "device_id": s.get("device_id", ""),
    }


# ---------- 签名卡密 ----------
def _sign(payload_b64: str) -> str:
    return hmac.new(CARD_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:12]


def issue_card(plan: str = "month", days: int = 30) -> str:
    """签发签名卡密：FW-{b64(payload)}-{hmac12}（与本地桩格式一致）。"""
    payload = {"plan": plan, "days": days, "serial": uuid.uuid4().hex[:10]}
    b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"FW-{b64}-{_sign(b64)}"


def parse_card(card: str):
    """验签并解析卡密，失败返回 None。"""
    card = (card or "").strip()
    if not card.startswith("FW-"):
        return None
    try:
        _, b64, sig = card.split("-", 2)
        calc = hmac.new(CARD_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()[:12]
        if not hmac.compare_digest(calc, sig):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("plan") or not payload.get("days"):
            return None
        return payload
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


# ---------- 业务 ----------
def api_login(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    device_id = body.get("device_id") or ""
    if not email or "@" not in email:
        return {"ok": False, "message": "邮箱格式不正确"}
    s = find_by_email(email)
    if s is None:
        s = new_session(email, device_id, password)
        save_session(s)
        return {"ok": True, "session": public(s), "plan": s["plan"]}
    if s.get("password_hash"):
        if hash_password(password, s.get("salt", "")) != s.get("password_hash"):
            return {"ok": False, "message": "密码不正确"}
    kick = None
    old_device = s.get("device_id")
    if device_id and old_device and old_device != device_id:
        kick = {"old_device": old_device, "new_device": device_id,
                "message": "账号已在其他设备登录，旧设备已下线"}
    if device_id:
        s["device_id"] = device_id
    s["last_verified"] = time.time()
    save_session(s)
    resp = {"ok": True, "session": public(s), "plan": s["plan"]}
    if kick:
        resp["device_kick"] = kick
    return resp


def api_verify(body: dict) -> dict:
    token = body.get("token") or ""
    device_id = body.get("device_id") or ""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        s = dict(row) if row else None
    finally:
        conn.close()
    if s is None:
        return {"ok": False, "reason": "token 无效"}
    now = time.time()
    if now > float(s.get("expires_at") or 0):
        return {"ok": False, "reason": "订阅已过期，请续费"}
    if device_id and s.get("device_id") and s["device_id"] != device_id:
        return {"ok": False, "reason": "账号已在其他设备登录", "device_kick": True}
    s["last_verified"] = now
    save_session(s)
    return {"ok": True, **public(s)}


def api_activate(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    card = body.get("card") or ""
    payload = parse_card(card)
    if payload is None:
        return {"ok": False, "message": "卡密无效或签名校验失败"}
    s = find_by_email(email)
    if s is None:
        s = new_session(email, "")
        save_session(s)
    serial = payload.get("serial", "")
    used = json.loads(s.get("cards_used") or "[]")
    if serial in used:
        return {"ok": False, "message": "该卡密已被使用"}
    days = int(payload.get("days") or 30)
    now = time.time()
    base = max(now, float(s.get("expires_at") or now))
    s["expires_at"] = base + days * 86400
    s["plan"] = "member"
    s["cards_used"] = json.dumps(used + [serial])
    s["last_verified"] = now
    save_session(s)
    return {"ok": True, "message": f"激活成功：{payload.get('plan')} +{days} 天",
            "plan": "member", "expires_at": s["expires_at"], "credits": s.get("credits", 0)}


def api_issue_card(body: dict) -> dict:
    if (body.get("admin_key") or "") != ADMIN_KEY:
        return {"ok": False, "message": "管理密钥错误"}
    plan = body.get("plan", "month")
    days = int(body.get("days", 30))
    return {"ok": True, "card": issue_card(plan, days), "plan": plan, "days": days}


ROUTES = {
    "/api/auth/login": api_login,
    "/api/auth/verify": api_verify,
    "/api/auth/activate": api_activate,
    "/api/admin/issue-card": api_issue_card,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj: dict, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        handler = ROUTES.get(path)
        if handler is None:
            self._send({"ok": False, "message": "Not Found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        except Exception:
            self._send({"ok": False, "message": "请求体不是合法 JSON"}, 400)
            return
        try:
            result = handler(body)
        except Exception as e:  # noqa: BLE001
            print(f"[cloud] handler error: {e!r}")
            self._send({"ok": False, "message": "服务器内部错误"}, 500)
            return
        status = 200 if result.get("ok", False) else 200
        self._send(result, status)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send({"ok": True, "service": "frameweave-cloud", "version": "1.0"})
            return
        self._send({"ok": False, "message": "Not Found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[cloud] {self.client_address[0]} - {fmt % args}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("CLOUD_PORT", "8789")))
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()
    print(f"[cloud] FrameWeave 云端授权服务启动  bind={args.bind} port={args.port} db={DB_PATH}")
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
