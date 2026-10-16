# -*- coding: utf-8 -*-
"""S1-T10 分层授权与离线降级 验收测试（04 §3.6 / R-工程-4）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T10 acceptance）：
  1. 离线授权缓存：云端 HMAC 验签响应原样缓存（cache_write 仅接受验签通过 ok 响应）；
  2. 云端不可达 + 有效缓存 → degraded + offline_cache 显式降级放行（绝不静默）；
  3. 云端不可达 + 无缓存 → cloud_unavailable_no_cache 明确错误（不静默失败）；
  4. 云端未配置（FRAMEWEAVE_CLOUD_URL=""）行为：有缓存降级 / 无缓存明确报错；
  5. 缓存边界：签名篡改 → cache_tampered 拒绝并清除；订阅过期 → subscription_expired
     明确提示；device_id 不一致 → device_kick（复用在线语义）；
  6. 在线 verify 成功 → 到期前刷新缓存（写缓存），断网后可降级；
  7. auth_status 模式报告（online / offline_cache / not_configured / cloud_unverified）；
  8. API 级：/api/health 带 auth 字段；/api/auth/verify 缓存降级与明确错误；
     断网（cloud 未配置）状态本地基础节点仍可执行（R-工程-4 直采）。

运行方式：
  - T9 统一 pytest 前先以独立脚本断言：`python tests/test_authz.py`（退出码 0=全绿）；
  - pytest 收集兼容：顶层轻量 import，test_* 函数无参。
"""
import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "tok_" + os.urandom(6).hex()
DEVICE = "dev-test-1"
SECRET = "fw-t10-test-secret"
# 缓存验签/签名统一用测试密钥（cloud_gateway._verify_secret 运行时读环境）
os.environ["FRAMEWEAVE_VERIFY_SECRET"] = SECRET
# 云端不可达桩：127.0.0.1:1（端口 1 通常关闭 → 连接拒绝 = 云端不可达语义）
REFUSED_URL = "http://127.0.0.1:1"
_SAVED_ENV = {}


def _set_env(**kw):
    """保存并设置环境（测试结束由 _restore_env 还原）。"""
    for k, v in kw.items():
        _SAVED_ENV.setdefault(k, os.environ.get(k))
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _restore_env():
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _SAVED_ENV.clear()


def _fresh_authz():
    """把 authz 缓存指到独立临时目录（建在 gettempdir() 一级，DSH 沙箱对
    mkdtemp 随机子目录内的文件写入有限制），返回 (authz, tmpdir)。"""
    import app.authz as authz
    tmp = os.path.join(tempfile.gettempdir(), "fw_t10_authz_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    authz.init(tmp)
    return authz, tmp


def _signed_resp(**over):
    """构造带云端 HMAC 签名的 verify 响应（test-secret）。"""
    from app import cloud_gateway
    resp = {
        "ok": True,
        "user_id": "u_test",
        "email": "a@qq.com",
        "token": TOKEN,
        "plan": "member",
        "expires_at": time.time() + 3600,
        "credits": 12,
        "device_id": DEVICE,
        "status": 1,
        "req_nonce": "nonce1",
    }
    resp.update(over)
    resp["signature"] = cloud_gateway.sign_payload(SECRET, resp)
    return resp


# ================================================================ 单元级：缓存与降级判定
def test_cache_write_guard():
    """① cache_write 仅接受验签通过的 ok 响应（防本地伪造/未验签缓存）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    ok1 = authz.cache_write(TOKEN, DEVICE, _signed_resp())
    assert ok1 is True
    # 未签名响应 → 拒绝
    ok2 = authz.cache_write(TOKEN + "x", DEVICE, {"ok": True, "plan": "member"})
    assert ok2 is False
    # ok=False 响应 → 拒绝
    ok3 = authz.cache_write(TOKEN + "y", DEVICE, _signed_resp(ok=False, reason="expired"))
    assert ok3 is False
    assert authz.cache_read(TOKEN) is not None
    assert authz.cache_read(TOKEN + "x") is None


def test_offline_fallback_valid_cache():
    """② 云端不可达 + 有效缓存 → degraded+offline_cache 显式降级放行（不静默）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is True, r
        assert r["degraded"] is True and r["offline_cache"] is True, r
        assert "离线授权缓存" in (r.get("reason") or ""), r
        assert r["plan"] == "member" and r["credits"] == 12
    finally:
        _restore_env()


def test_offline_no_cache_explicit():
    """③ 云端不可达 + 无缓存 → 明确错误（不静默失败）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is False, r
        assert r["code"] == "cloud_unavailable_no_cache", r
        assert r["degraded"] is True and "联网" in (r.get("reason") or ""), r
    finally:
        _restore_env()


def test_not_configured_no_cache_explicit():
    """④ 云端未配置 + 无缓存 → cloud_not_configured 明确错误（code 与旧 API 兼容）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    _set_env(FRAMEWEAVE_CLOUD_URL="")
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is False and r["code"] == "cloud_not_configured", r
        assert r["degraded"] is True
    finally:
        _restore_env()


def test_not_configured_with_cache_degraded():
    """⑤ 云端未配置 + 有效缓存 → 降级放行（显式标记）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    _set_env(FRAMEWEAVE_CLOUD_URL="")
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is True and r["offline_cache"] is True and r["degraded"] is True, r
    finally:
        _restore_env()


def test_cache_tampered_rejected():
    """⑥ 缓存签名篡改 → cache_tampered 拒绝并清除（防伪造授权）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    # 篡改业务字段（签名随之失效）
    entry = authz.cache_read(TOKEN)
    entry["resp"]["plan"] = "enterprise"
    with open(authz.cache_file(), "w", encoding="utf-8") as f:
        json.dump({TOKEN: entry}, f, ensure_ascii=False, indent=2)
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is False and r["code"] == "cache_tampered", r
        assert authz.cache_read(TOKEN) is None, "篡改缓存应被清除"
    finally:
        _restore_env()


def test_cache_expired_explicit():
    """⑦ 缓存订阅过期（云端签发的 expires_at 权威）→ subscription_expired 明确提示。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp(expires_at=time.time() - 10))
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is False and r["code"] == "subscription_expired", r
        assert "续费" in (r.get("reason") or ""), r
        assert r["degraded"] is True
    finally:
        _restore_env()


def test_device_mismatch_kick():
    """⑧ 离线缓存 device_id 不一致 → device_kick（复用在线语义，防缓存跨设备）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r = authz.verify(TOKEN, "dev-other")
        assert r["ok"] is False and r.get("device_kick") is True, r
    finally:
        _restore_env()


def test_online_then_offline_cache():
    """⑨ 在线 verify 成功 → 写缓存（到期前刷新）；断网后缓存降级放行。"""
    import app.authz as authz
    from app import cloud_gateway
    authz, _ = _fresh_authz()
    _set_env(FRAMEWEAVE_VERIFY_SECRET=SECRET)

    # 本地 mock 云端：响应 verify 并回显 nonce + 签名
    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            try:
                req = json.loads(self.rfile.read(n).decode() or "{}")
            except Exception:
                req = {}
            resp = {
                "ok": True, "user_id": "u_test", "email": "a@qq.com",
                "token": req.get("token", TOKEN), "plan": "member",
                "expires_at": time.time() + 3600, "credits": 12,
                "device_id": DEVICE, "status": 1, "req_nonce": req.get("nonce", ""),
            }
            resp["signature"] = cloud_gateway.sign_payload(SECRET, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        _set_env(FRAMEWEAVE_CLOUD_URL="http://127.0.0.1:%d" % port)
        r1 = authz.verify(TOKEN, DEVICE)
        assert r1["ok"] is True and r1.get("cloud_verified") is True, r1
        entry = authz.cache_read(TOKEN)
        assert entry is not None, "在线成功应写离线缓存（到期前刷新）"
        assert entry["resp"].get("signature"), "缓存应保留云端 signature（离线验签依据）"
    finally:
        srv.shutdown()
        srv.server_close()
    # 云端不可达（指向拒绝端口）→ 缓存降级放行
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        r2 = authz.verify(TOKEN, DEVICE)
        assert r2["ok"] is True and r2["offline_cache"] is True and r2["degraded"] is True, r2
        assert r2["plan"] == "member"
    finally:
        _restore_env()


def test_online_business_failure_clears_cache():
    """⑩ 在线业务失败（过期/禁用）→ 云端权威，本地缓存清除（防旧缓存复活）。"""
    import app.authz as authz
    from app import cloud_gateway
    authz, _ = _fresh_authz()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    _set_env(FRAMEWEAVE_VERIFY_SECRET=SECRET)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            try:
                req = json.loads(self.rfile.read(n).decode() or "{}")
            except Exception:
                req = {}
            resp = {"ok": False, "reason": "订阅已过期，请续费",
                    "req_nonce": req.get("nonce", "")}
            resp["signature"] = cloud_gateway.sign_payload(SECRET, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        _set_env(FRAMEWEAVE_CLOUD_URL="http://127.0.0.1:%d" % port)
        r = authz.verify(TOKEN, DEVICE)
        assert r["ok"] is False and "过期" in (r.get("reason") or ""), r
        assert authz.cache_read(TOKEN) is None, "云端业务失败应清除本地缓存"
    finally:
        srv.shutdown()
        srv.server_close()
        _restore_env()


def test_auth_status_modes():
    """⑪ auth_status 模式报告（online / offline_cache / not_configured）。"""
    import app.authz as authz
    authz, _ = _fresh_authz()
    _set_env(FRAMEWEAVE_CLOUD_URL="")
    try:
        st = authz.auth_status()
        assert st["mode"] == "not_configured" and st["degraded"] is False, st
    finally:
        _restore_env()
    authz.cache_write(TOKEN, DEVICE, _signed_resp())
    _set_env(FRAMEWEAVE_CLOUD_URL="")
    try:
        st = authz.auth_status()
        assert st["mode"] == "offline_cache" and st["degraded"] is True and st["cache_valid"] is True, st
    finally:
        _restore_env()
    _set_env(FRAMEWEAVE_CLOUD_URL=REFUSED_URL)
    try:
        st = authz.auth_status()
        assert st["mode"] == "online" and st["cache_valid"] is True, st
    finally:
        _restore_env()


# ================================================================ API 级（TestClient + app.server）
def _prepare_server_offline():
    """导入 app.server（离线模式：FRAMEWEAVE_CLOUD_URL=""），隔离数据/缓存目录。"""
    _set_env(FRAMEWEAVE_CLOUD_URL="", FRAMEWEAVE_VERIFY_SECRET=SECRET,
             FRAMEWEAVE_LOCAL_TOKEN="fw-t10-test-token")
    import app.server as srv
    from app.assets import create_store
    from app.wfstore import WorkflowStore
    from app import authz
    # 临时目录建在 gettempdir() 一级（DSH 沙箱对 mkdtemp 随机子目录的子目录有限制）
    tmp = os.path.join(tempfile.gettempdir(), "fw_t10_api_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    authz.init(tmp)          # 覆盖 server 导入时指向 backend/data 的缓存目录
    srv.authz = authz
    srv.store = create_store("local", os.path.join(tmp, "assets"))
    srv.wfstore = WorkflowStore(os.path.join(tmp, "workflows"))
    return srv, tmp


def _api_headers():
    """请求头令牌：使用 server 模块导入时固定的 LOCAL_TOKEN（t9 conftest 统一为
    fw-t9-test-token；本文件 _set_env 改 env 仅影响云端模拟，不改变 server 固定值）。"""
    from app.obf import x_fw_token_header
    import app.server as _srv
    return {x_fw_token_header(): _srv.LOCAL_TOKEN}


def test_api_health_auth_field():
    """⑫ API：/api/health 携带 auth 模式字段（壳层提示数据源）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server_offline()
    with TestClient(srv.app) as c:
        j = c.get("/api/health").json()
        assert j["ok"] is True
        assert set(j["auth"].keys()) >= {"mode", "cloud_enabled", "degraded", "cache_valid"}
        assert j["auth"]["cloud_enabled"] is False and j["auth"]["mode"] == "not_configured"


def test_api_verify_no_cache_explicit():
    """⑬ API：离线模式（cloud 未配置）无缓存 → verify 明确报错（不静默，code 兼容）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server_offline()
    with TestClient(srv.app) as c:
        r = c.post("/api/auth/verify", json={"token": TOKEN, "device_id": DEVICE}).json()
        assert r["ok"] is False and r["code"] == "cloud_not_configured", r
        assert r["degraded"] is True


def test_api_verify_offline_cache_degraded():
    """⑭ API：离线模式 + 有效缓存 → verify 降级放行（degraded+offline_cache 显式）。"""
    from fastapi.testclient import TestClient
    srv, tmp = _prepare_server_offline()
    from app import cloud_gateway
    srv.authz.cache_write(TOKEN, DEVICE, _signed_resp())
    with TestClient(srv.app) as c:
        r = c.post("/api/auth/verify", json={"token": TOKEN, "device_id": DEVICE}).json()
        assert r["ok"] is True, r
        assert r["degraded"] is True and r["offline_cache"] is True, r
        assert r["plan"] == "member"
        # 会话保持：ok=True → 前端不误登出（R-工程-4 离线体验）
        j = c.get("/api/health").json()
        assert j["auth"]["mode"] == "offline_cache"


def test_api_local_node_runs_offline():
    """⑮ API：断网（cloud 未配置）状态本地基础节点仍可执行（R-工程-4 直采）。"""
    import app.declarative as decl
    from fastapi.testclient import TestClient
    srv, tmp = _prepare_server_offline()
    # 临时 echo 节点包（离线本地执行，不触碰云端）
    nodes_dir = os.path.join(tmp, "user_nodes")
    pkg_dir = os.path.join(nodes_dir, "echo_off")
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 2, "engine_api": "1.0", "type_id": "user/echo_off",
            "title": "echo_off", "category": "用户节点", "description": "offline echo",
            "version": "1.0.0", "kind": "python", "runtime": "python",
            "entrypoint": "node.py", "class_name": "EchoOffNode",
            "inputs": [],
            "outputs": [{"name": "out", "type": "JSON", "label": "out", "required": False}],
            "params": [], "permission": {},
        }, f, ensure_ascii=False, indent=2)
    with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
        f.write(
            "from app.assets import Asset\n"
            "class EchoOffNode:\n"
            "    async def run(self, ctx, inputs, params):\n"
            "        store = ctx['store'] if isinstance(ctx, dict) else ctx.store\n"
            "        out = store.save_asset(Asset(id='', kind='JSON', meta={}), payload={'offline': True})\n"
            "        return {'out': out}\n"
        )
    decl.set_user_nodes_dir(nodes_dir)
    assert decl.scan() >= 1 and decl.pkg_dir("user/echo_off") is not None
    with TestClient(srv.app) as c:
        h = _api_headers()
        r = c.post("/api/workflows", json={
            "name": "offline", "nodes": [{"id": "n1", "type": "user/echo_off",
                                          "x": 0, "y": 0, "params": {}, "inputs": {}}],
            "edges": [],
        }, headers=h)
        assert r.status_code == 201, r.text
        wid = r.json()["id"]
        r2 = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h)
        assert r2.status_code == 200, r2.text
        deadline = time.time() + 20
        wf = None
        while time.time() < deadline:
            wf = c.get(f"/api/workflows/{wid}").json()
            if wf["nodes"][0]["status"] in ("success", "failed", "cancelled"):
                break
            time.sleep(0.05)
        assert wf["nodes"][0]["status"] == "success", wf


# ================================================================ 独立脚本入口（T9 前断言）
def main():
    failures = 0
    tests = [
        test_cache_write_guard,
        test_offline_fallback_valid_cache,
        test_offline_no_cache_explicit,
        test_not_configured_no_cache_explicit,
        test_not_configured_with_cache_degraded,
        test_cache_tampered_rejected,
        test_cache_expired_explicit,
        test_device_mismatch_kick,
        test_online_then_offline_cache,
        test_online_business_failure_clears_cache,
        test_auth_status_modes,
        test_api_health_auth_field,
        test_api_verify_no_cache_explicit,
        test_api_verify_offline_cache_degraded,
        test_api_local_node_runs_offline,
    ]
    for fn in tests:
        name = fn.__name__
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as e:
            failures += 1
            print("FAIL %s | %s" % (name, e))
        except Exception as e:  # noqa: BLE001
            failures += 1
            print("ERROR %s | %s: %s" % (name, type(e).__name__, e))
    print("=" * 56)
    print("TOTAL FAIL=%d/%d" % (failures, len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
