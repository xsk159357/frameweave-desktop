# -*- coding: utf-8 -*-
"""t1 云端管理后台 HTTP 契约测试（真实 ThreadingHTTPServer + 临时 DB）。

覆盖补全的管理后台 API 与统一错误码/签名/分页：
  1) /api/health 契约字段（protocol_version=3 / auth_revision=email-code-v2 / market=v1）不变；
  2) /api/admin/login 统一错误码（bad_credentials / rate_limited / ip_forbidden）；
  3) /api/admin/devices + /api/admin/device/reset 新 API（设备登记源自 login/register）；
  4) /api/admin/audit 分页/筛选（q / scene / per_page / pages）；
  5) /api/admin/cards 状态筛选 + /api/admin/card/revoke → activate 拒绝 card_revoked；
  6) /api/auth/verify 带 HMAC signature 且含 code 字段；
  7) 404/坏 JSON/超大请求体错误码（not_found / bad_json / body_too_large）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import cloud_server as cs


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """真实 cloud_server HTTP 服务（临时 DB/商城文件 + 测试密钥）。"""
    dbfile = tmp_path / "cloud.db"
    mfile = tmp_path / "market_items.json"
    monkeypatch.setattr(cs, "DB_PATH", str(dbfile))
    monkeypatch.setattr(cs, "MARKET_FILE", str(mfile))
    # 模块常量在 import 时读取一次，必须直接改模块属性（env 已过期）
    monkeypatch.setattr(cs, "ADMIN_KEY", "http-admin-key-1")
    monkeypatch.setattr(cs, "CARD_SECRET", "http-card-secret")
    monkeypatch.setattr(cs, "VERIFY_SECRET", "http-verify-secret")
    monkeypatch.setattr(cs, "ADMIN_ALLOW_IPS", [])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    yield {"base": base, "tmp": tmp_path}
    srv.shutdown()
    th.join(timeout=5)


def _admin_login(server):
    st, la = _post(server["base"], "/api/admin/login", {"admin_key": "http-admin-key-1"})
    assert la.get("ok") is True and la.get("token"), str(la)
    return la["token"]


def _post(base, path, body: dict):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def test_health_contract_unchanged(server):
    st, j = _get(server["base"], "/api/health")
    assert st == 200 and j["ok"] is True
    assert j["protocol_version"] == 3
    assert j["auth_revision"] == "email-code-v2"
    assert j["market"] == "v1"
    assert j["version"]


def test_admin_login_error_codes(server):
    st, j = _post(server["base"], "/api/admin/login", {"admin_key": "wrong"})
    assert st == 200 and j["ok"] is False and j["code"] == "bad_credentials"
    assert j["message"]


def test_admin_devices_and_reset(server):
    base = server["base"]
    tk = _admin_login(server)
    # 未登录 → unauthorized 统一错误码
    _, unauth = _post(base, "/api/admin/devices", {})
    assert unauth.get("code") == "unauthorized"
    # 造 2 用户（register 失败验证码时直接 save，但 login 触发设备登记）
    for e, dev in (("h1@qq.com", "http-dev-1"), ("h2@qq.com", "http-dev-2")):
        if not cs.find(e):
            cs.save(cs.new(e, dev, "password123", ip="127.0.0.9"))
        cs.login({"email": e, "password": "password123", "device_id": dev, "ip": "127.0.0.9"})
    st, dv = _post(base, "/api/admin/devices", {"token": tk, "per_page": 1})
    assert dv["ok"] is True and dv["total"] >= 2 and len(dv["devices"]) == 1 and dv["pages"] >= 2
    # kick 后设备 kicked=1，device/reset 解踢
    _post(base, "/api/admin/user/kick", {"token": tk, "email": "h1@qq.com"})
    _, dv2 = _post(base, "/api/admin/devices", {"token": tk, "q": "http-dev-1"})
    assert dv2["ok"] and any(d["device_id"] == "http-dev-1" and d["kicked"] == 1 for d in dv2["devices"])
    _, dr = _post(base, "/api/admin/device/reset", {"token": tk, "email": "h1@qq.com", "device_id": "http-dev-1"})
    assert dr["ok"] is True and dr["kicked"] == 0
    _, dr2 = _post(base, "/api/admin/device/reset", {"token": tk, "email": "h1@qq.com", "device_id": "nope"})
    assert dr2["ok"] is False and dr2["code"] == "not_found"


def test_admin_audit_pagination_and_filter(server):
    base = server["base"]
    tk = _admin_login(server)
    for i in range(3):
        cs.save(cs.new(f"aud{i}@qq.com", "d", "password123", ip="127.0.0.9"))
    cs.admin_user_credits({"token": tk, "email": "aud0@qq.com", "delta": 5, "ip": "127.0.0.9"})
    _, au = _post(base, "/api/admin/audit", {"token": tk, "scene": "admin_", "per_page": 2})
    assert au["ok"] is True and len(au["logs"]) == 2 and au["total"] >= 1 and au["pages"] >= 1
    assert all(str(l["scene"]).startswith("admin_") for l in au["logs"])
    _, auq = _post(base, "/api/admin/audit", {"token": tk, "q": "aud0@qq"})
    assert auq["ok"] and any(l.get("email") == "aud0@qq.com" for l in auq["logs"])


def test_admin_card_revoke_blocks_activate(server):
    base = server["base"]
    tk = _admin_login(server)
    cs.save(cs.new("cardu@qq.com", "d", "password123", ip="127.0.0.9"))
    _, iss = _post(base, "/api/admin/issue-card", {"token": tk, "plan": "month", "days": 30, "count": 1})
    assert iss["ok"] is True and len(iss["cards"]) == 1
    card = iss["cards"][0]
    _, c1 = _post(base, "/api/admin/cards", {"token": tk, "q": card[-12:], "page": 1, "per_page": 50})
    assert c1["ok"] and len(c1["cards"]) == 1
    cid = c1["cards"][0]["id"]
    # 未作废：激活成功
    _, a1 = _post(base, "/api/auth/activate", {"email": "cardu@qq.com", "card": card})
    assert a1["ok"] is True and a1["plan"] == "member" and a1.get("signature")
    # 作废后再激活 → card_revoked
    _, rv = _post(base, "/api/admin/card/revoke", {"token": tk, "id": cid})
    assert rv["ok"] is True
    cs.save(cs.new("cardu2@qq.com", "d", "password123", ip="127.0.0.9"))
    _, a2 = _post(base, "/api/auth/activate", {"email": "cardu2@qq.com", "card": card})
    assert a2["ok"] is False and a2["code"] == "card_revoked"


def test_verify_has_signature_and_code(server):
    base = server["base"]
    s = cs.new("verify@qq.com", "dev-v", "password123", ip="127.0.0.9")
    cs.save(s)
    st, j = _post(base, "/api/auth/verify", {"token": s["token"], "device_id": "dev-v", "nonce": "n1"})
    assert st == 200 and j["ok"] is True
    assert j.get("req_nonce") == "n1"
    assert j.get("code") == "ok"
    assert j.get("signature") and len(j["signature"]) == 48
    # 错误 token → 统一 code + 签名
    _, bad = _post(base, "/api/auth/verify", {"token": "no-such-token", "nonce": "n2"})
    assert bad["ok"] is False and bad["code"] == "invalid_token" and bad.get("signature")


def test_http_protocol_error_codes(server):
    base = server["base"]
    # 404
    st, j = _post(base, "/api/admin/nope", {})
    assert st == 404 and j.get("code") == "not_found"
    # 坏 JSON
    req = urllib.request.Request(base + "/api/admin/login", data=b"{bad json", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            st2, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        st2, body = e.code, e.read()
    j2 = json.loads(body.decode() or "{}")
    assert st2 == 400 and j2.get("code") == "bad_json"
    # JSON 数组体（非对象）→ 400 bad_json（t1 参数校验）
    arr = b"[1,2,3]"
    req2 = urllib.request.Request(base + "/api/admin/login", data=arr,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req2, timeout=10) as r:
            st2b, body2 = r.status, r.read()
    except urllib.error.HTTPError as e:
        st2b, body2 = e.code, e.read()
    j2b = json.loads(body2.decode() or "{}")
    assert st2b == 400 and j2b.get("code") == "bad_json"
    # 超大请求体 → 413（MAX_BODY 由 t3 加固提供）
    # 2026-10-19：服务端在读到超限后立即 413 并关闭连接，而客户端仍在写 1MB 体，
    # Windows 上表现为 ConnectionAbortedError(10053)/ConnectionReset(10054) 而不是
    # HTTPError——两者都证明「超限请求被拒」，此前断言只认 413 导致随机红（复跑 3 次才复现）。
    big = b"x" * (cs.MAX_BODY + 10)
    req3 = urllib.request.Request(base + "/api/admin/login", data=big, method="POST")
    st3 = 0
    aborted = False
    try:
        with urllib.request.urlopen(req3, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        st3 = e.code
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
        aborted = True
    except urllib.error.URLError as e:
        # Linux 上 urllib 把对端提前关闭包成 URLError(BrokenPipeError/ConnectionResetError)
        reason = getattr(e, "reason", None)
        if isinstance(reason, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            aborted = True
        else:
            raise
    assert st3 == 413 or aborted, "超限请求未被拒绝: status=%s aborted=%s" % (st3, aborted)