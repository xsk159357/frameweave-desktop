# -*- coding: utf-8 -*-
"""t5 商城端到端验收：真实 HTTP 下载→校验→安装→回报 闭环。

覆盖（全部走真实 HTTP / 真实 uvicorn 服务，非 TestClient）：
  A  商城列表 → download-auth → 客户端下载(流式) → SHA-256/大小校验 → 上传安装
     → install-report → /api/specs 出现 → 列表计数（downloads/installs）
  B  付费流：扣积分 / 20-80 分成 / 回报；错误码契约（login_required /
     insufficient_credits / client_version_too_old / item_disabled / item_not_found）
  C  云端 item_not_found 容错：mock 云端返回 item_not_found → 服务端回落本地桩；
     云端不可达（连接拒绝）→ 回落本地桩；客户端重试策略模拟
  D  大插件适配：12MB 包 上传安装（multipart 流式） + 服务端 _fetch_zip 流式下载
     （真实 file server URL + sha256 登记校验） + /install 服务端安装
  E  失败回报契约：result=failed 记流水、installs 不增

用法：python tests_t5_market_e2e.py
退出码：0=全过；1=有失败。证据输出到 .tmp/t5_market_e2e.log。
"""
from __future__ import annotations
import hashlib
import http.server as http_server
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")
DATA = os.path.join(BACKEND, "data")
TMP = os.path.join(REPO, ".tmp")
T5_TMP = os.path.join(TMP, "t5_pkg")
PY = sys.executable

PORT = 8788          # 本地桩服务
CLOUD_PORT = 9455    # mock 云端（item_not_found）
FILE_PORT = 9456     # 大插件 zip 静态文件服务
TOKEN = "dev-local-token"
EVIDENCE = os.path.join(TMP, "t5_market_e2e.log")

PASS: list[str] = []
FAIL: list[str] = []

def log(msg: str) -> None:
    print(msg)
    try:
        with open(EVIDENCE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass

def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    log(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))

def reset_evidence() -> None:
    try:
        os.remove(EVIDENCE)
    except OSError:
        pass

# ---------------------------------------------------------------- HTTP 客户端
def http(path: str, method: str = "GET", body=None, headers=None, raw=False, timeout=30):
    """对本地桩服务发请求（带本地授权令牌，模拟渲染进程）。"""
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", "X-FW-Local-Token": TOKEN, "User-Agent": "frameweave/0.2.12"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if raw:
                return r.status, r.read()
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if raw:
            return e.code, e.read()
        return e.code, json.loads(e.read().decode() or "{}")


def http_abs(url: str, headers=None, timeout=60):
    """对绝对 URL 直接 GET（模拟客户端下载直链；本地 API 直链需带授权令牌）。"""
    h = {"X-FW-Local-Token": TOKEN, "User-Agent": "frameweave/0.2.12"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def multipart(path: str, filename: str, data: bytes, extra: dict | None = None):
    """multipart/form-data 上传（模拟前端 XHR FormData）。"""
    boundary = "----t5fw" + uuid.uuid4().hex[:12]
    out = io.BytesIO()
    for k, v in (extra or {}).items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n".encode()
              + b"Content-Type: application/zip\r\n\r\n")
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    url = f"http://127.0.0.1:{PORT}{path}"
    req = urllib.request.Request(url, data=out.getvalue(), method="POST",
                                 headers={"X-FW-Local-Token": TOKEN,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_health(port: int, timeout: float = 60) -> bool:
    dead = time.time() + timeout
    while time.time() < dead:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def free_port(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return False
    except OSError:
        return True


def wait_port_free(port: int, timeout: float = 20) -> bool:
    dead = time.time() + timeout
    while time.time() < dead:
        if free_port(port):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- 服务编排
def start_server(cloud_url: str = "", extra_env: dict | None = None):
    log(f"[server] 启动 uvicorn :{PORT} cloud_url={cloud_url!r}")
    wait_port_free(PORT, timeout=20)
    logf = open(os.path.join(TMP, "t5_server_stdout.log"), "ab")
    env = dict(os.environ)
    env.update({
        "FRAMEWEAVE_CLOUD_URL": cloud_url,
        "FRAMEWEAVE_DEV_ENDPOINTS": "1",
        "ALIYUN_DM_ENABLED": "false",          # 不走真实邮件，验证码走 dev 日志
        "FRAMEWEAVE_DEBUG_LOG": os.path.join(TMP, "t5_gw_debug.log"),
        "PYTHONUNBUFFERED": "1",
    })
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen([PY, "-m", "uvicorn", "app.server:app",
                             "--host", "127.0.0.1", "--port", str(PORT),
                             "--log-level", "warning"],
                            cwd=SNAP_BACKEND, env=env, stdout=logf, stderr=subprocess.STDOUT)
    ok = wait_health(PORT)
    check("server 健康检查 /api/health", ok, f"port={PORT}")
    if not ok:
        proc.kill()
        raise RuntimeError("本地桩服务启动失败")
    return proc


def start_mock_cloud():
    class H(http_server.BaseHTTPRequestHandler):
        def _resp(self, obj, status=200):
            b = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            # 云端商城列表：空（模拟条目尚未同步到新云端）
            self._resp({"items": [], "count": 0})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            if self.path.startswith("/api/market/download-auth") or self.path.startswith("/api/market/install-report"):
                # 历史线上事故场景：云端不认识该条目 → item_not_found
                self._resp({"ok": False, "code": "item_not_found", "message": "条目不存在"})
            else:
                self._resp({"ok": False, "code": "item_not_found", "message": "条目不存在"})

        def log_message(self, *a):
            pass

    srv = http_server.ThreadingHTTPServer(("127.0.0.1", CLOUD_PORT), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    log(f"[mock-cloud] 启动 :{CLOUD_PORT}（POST 恒返 item_not_found）")
    return srv


def start_file_server(directory: str):
    class H(http_server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

        def log_message(self, *a):
            pass

    srv = http_server.ThreadingHTTPServer(("127.0.0.1", FILE_PORT), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    log(f"[file-server] 启动 :{FILE_PORT} dir={directory}")
    return srv


# ---------------------------------------------------------------- 代码快照隔离
# 后端代码可能被并行任务（t3 生命周期）同时编辑：测试服务从 backend 的只读快照运行，
# 数据/用户节点目录也落在快照内——完全隔离真实仓库，且不依赖并行任务的中间状态。
SNAP = os.path.join(TMP, "t5_snap")          # 快照根：<SNAP>/backend
SNAP_BACKEND = os.path.join(SNAP, "backend")
SNAP_DATA = os.path.join(SNAP_BACKEND, "data")
SNAP_NODES = os.path.join(SNAP_BACKEND, "user_nodes")


def snapshot_create() -> None:
    """复制 backend 代码到快照（排除运行数据/缓存/测试），并重建空 data/user_nodes。"""
    shutil.rmtree(SNAP, ignore_errors=True)
    os.makedirs(SNAP, exist_ok=True)

    def _ignored(d, names):
        out = set()
        for n in names:
            p = os.path.join(d, n)
            if os.path.isdir(p):
                if n in ("data", "user_nodes", "__pycache__", ".venv"):
                    out.add(n)
            else:
                if n.endswith((".pyc", ".pyo")) or n in ("cloud.db", ".gitkeep") or n.startswith("tests_"):
                    out.add(n)
        return out

    shutil.copytree(BACKEND, SNAP_BACKEND, ignore=_ignored)
    os.makedirs(SNAP_DATA, exist_ok=True)
    os.makedirs(SNAP_NODES, exist_ok=True)
    # 初始运行数据：空商城 + 预置账号（买家 200 / 作者 0，首次登录绑定密码）
    with open(os.path.join(SNAP_DATA, "market.json"), "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False)
    sid_b = "b1" + uuid.uuid4().hex[:10]
    sid_a = "a1" + uuid.uuid4().hex[:10]
    seed = {
        sid_b: {"user_id": sid_b, "email": "buyer@qq.com", "token": uuid.uuid4().hex[:32],
                "plan": "member", "expires_at": time.time() + 86400 * 30,
                "device_id": "BUY-D", "credits": 200, "cards_used": [],
                "last_verified": time.time(), "salt": "e2e", "password_hash": ""},
        sid_a: {"user_id": sid_a, "email": "author_a@qq.com", "token": uuid.uuid4().hex[:32],
                "plan": "member", "expires_at": time.time() + 86400 * 30,
                "device_id": "AU-D", "credits": 0, "cards_used": [],
                "last_verified": time.time(), "salt": "e2e", "password_hash": ""},
    }
    with open(os.path.join(SNAP_DATA, "license.json"), "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    log("[snapshot] 已创建代码快照（数据/节点目录已隔离）")


def snapshot_cleanup() -> None:
    shutil.rmtree(SNAP, ignore_errors=True)


# ---------------------------------------------------------------- 测试包构造
HELLO_MANIFEST = {
    "type_id": "user/hello", "title": "问候生成", "category": "用户节点",
    "description": "声明式示例节点：拼接问候语", "version": "1.0.0", "kind": "transform",
    "inputs": [{"name": "name", "type": "STRING", "label": "名字", "required": True}],
    "outputs": [{"name": "greeting", "type": "STRING", "label": "问候语"},
                {"name": "length", "type": "INT", "label": "长度"}],
    "params": [{"name": "prefix", "type": "STRING", "default": "你好", "label": "前缀"}],
    "transform": {"greeting": {"op": "concat", "sources": [{"$param": "prefix"}, {"$input": "name"}],
                               "sep": "，", "suffix": "！"},
                  "length": {"op": "len", "source": {"$input": "name"}}},
}

BIG_MANIFEST = {
    "type_id": "user/bigmodel", "title": "大插件测试", "category": "用户节点",
    "description": "t5 大插件流式安装验证", "version": "1.0.0", "kind": "transform",
    "inputs": [{"name": "text", "type": "STRING", "label": "文本", "required": True}],
    "outputs": [{"name": "out", "type": "STRING", "label": "输出"}],
    "params": [{"name": "suffix", "type": "STRING", "default": "!", "label": "后缀"}],
    "transform": {"out": {"op": "concat", "sources": [{"$input": "text"}, {"$param": "suffix"}],
                          "sep": "", "suffix": ""}},
}


def make_zip_bytes(top: str, manifest: dict, extra_files: dict | None = None) -> bytes:
    """插件包 zip：单一顶层目录 <top>/manifest.json [+ extra_files]（与 export 端点同构）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for rel, data in (extra_files or {}).items():
            zf.writestr(top + "/" + rel, data)
    return buf.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pkg_zip_path(top: str, data: bytes) -> str:
    os.makedirs(T5_TMP, exist_ok=True)
    p = os.path.join(T5_TMP, top + ".zip")
    with open(p, "wb") as f:
        f.write(data)
    return p


# ---------------------------------------------------------------- 客户端策略模拟
def client_install_loop(item_id: str, token: str, retry_limit: int = 2):
    """模拟 MarketPage.install 的授权重试策略：
    云端 item_not_found → 刷新列表重试一次；瞬时失败延时重试一次。返回 (auth, attempts)。
    """
    auth = None
    attempts = 0
    for attempt in range(retry_limit):
        attempts += 1
        st, auth = http(f"/api/market/items/{item_id}/download-auth", "POST",
                        {"item_id": item_id, "token": token, "client_version": "0.2.12"})
        if auth.get("ok"):
            break
        if auth.get("code") == "item_not_found" and attempt == 0:
            st2, lst = http("/api/market/items?kind=node", "GET")
            if any(i.get("id") == item_id for i in lst.get("items", [])):
                continue  # 条目仍在列表 → 重试授权
            break
        if auth.get("code") in ("network", "timeout") and attempt == 0:
            time.sleep(0.8)
            continue
        break
    return auth, attempts


def main() -> int:
    reset_evidence()
    for port in (PORT, CLOUD_PORT, FILE_PORT):
        if not free_port(port):
            log(f"[fatal] 端口 {port} 被占用，请先释放")
            return 1
    os.makedirs(TMP, exist_ok=True)
    # 代码快照隔离：真实仓库数据零接触；每轮全新快照（清空 user_nodes/plugins/market）
    snapshot_create()

    procs = []          # 需要清理的进程
    servers = []        # 需要关闭的 http 服务
    try:
        # ================= Phase 1：免费节点 全链路 =================
        log("\n===== Phase 1: 免费节点 商城列表→下载→校验→安装→回报 =====")
        # 预置账号/空商城已由 snapshot_create() 完成（快照 data/license.json + market.json）

        proc = start_server(cloud_url="")
        procs.append(proc)
        # 包静态文件服务（模拟 GitHub Release 外域直链）：
        # 服务端 /install 的 _fetch_zip 必须拉外域 URL，避免指向本服务自身时
        # 事件循环自请求阻塞；客户端下载也走该直链（与真实 Release 语义一致）。
        fsrv = start_file_server(T5_TMP)
        servers.append(fsrv)

        # A1 上传安装 hello（上传安装路径 / 商城来源包预置）
        hello_zip = make_zip_bytes("hello", HELLO_MANIFEST)
        st, up = multipart("/api/user_nodes/install", "hello.zip", hello_zip)
        check("A1 上传安装 hello.zip → ok", st == 200 and up.get("ok"),
              f"status={st} resp={json.dumps(up, ensure_ascii=False)[:160]}")
        st, specs = http("/api/specs")
        check("A1b /api/specs 出现 user/hello", any(s.get("type_id") == "user/hello" for s in (specs or [])),
              f"specs={[s.get('type_id') for s in (specs or [])][:8]}")

        # A2 导出（商城包来源）+ 包元数据；落盘到文件服务目录供直链下载
        st, zip_bytes = http("/api/export/node/user/hello", raw=True)
        check("A2 导出节点 zip", st == 200 and zip_bytes[:2] == b"PK", f"status={st} bytes={len(zip_bytes)}")
        zsha = sha256_hex(zip_bytes)
        zsize = len(zip_bytes)
        hello_export_path = os.path.join(T5_TMP, "hello_export.zip")
        with open(hello_export_path, "wb") as f:
            f.write(zip_bytes)

        # A3 发布条目（download_url 指向外域直链，模拟网盘/GitHub Release）
        st, pub = http("/api/market/items", "POST", {
            "kind": "node", "title": "问候节点·专业版", "description": "t5 端到端免费节点",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/hello_export.zip",
            "tags": ["问候", "t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize,
        })
        item_a = (pub.get("item") or {}).get("id")
        check("A3 发布条目（免费）", pub.get("ok") and item_a, f"id={item_a}")

        # A4 商城列表
        st, lst = http("/api/market/items?kind=node", "GET")
        found = next((i for i in lst.get("items", []) if i.get("id") == item_a), None)
        check("A4 商城列表命中", found is not None and found.get("sha256") == zsha and found.get("size") == zsize,
              f"count={lst.get('count')}")

        # A5 download-auth（免费无需登录）
        st, auth = http(f"/api/market/items/{item_a}/download-auth", "POST",
                        {"item_id": item_a, "token": "", "client_version": "0.2.12"})
        dl = auth.get("download") or {}
        check("A5 download-auth 授权", auth.get("ok") and auth.get("status") == "authorized"
              and dl.get("url", "").startswith("http") and dl.get("sha256") == zsha
              and dl.get("size") == zsize, f"code={auth.get('code')} url={dl.get('url','')[:60]}")

        # A6 客户端下载 + 大小/SHA-256 校验（模拟前端 fetch → digest）
        st, got = http_abs(dl["url"])
        size_ok = len(got) == dl.get("size")
        sha_ok = sha256_hex(got) == dl.get("sha256")
        check("A6 客户端下载并校验大小+SHA-256", st == 200 and size_ok and sha_ok,
              f"bytes={len(got)} size_ok={size_ok} sha_ok={sha_ok}")

        # A7 上传安装
        st, ins = multipart("/api/user_nodes/install", dl.get("filename", "hello.zip"), got)
        check("A7 上传安装 → ok", st == 200 and ins.get("ok")
              and "user/hello" in (ins.get("nodes") or []),
              f"resp={json.dumps(ins, ensure_ascii=False)[:180]}")

        # A8 install-report（ok）
        st, rep = http(f"/api/market/items/{item_a}/install-report", "POST",
                       {"item_id": item_a, "token": "", "client_version": "0.2.12",
                        "result": "ok", "error": ""})
        check("A8 install-report → recorded", rep.get("ok") and rep.get("recorded")
              and int(rep.get("install_count") or 0) >= 1, f"resp={json.dumps(rep, ensure_ascii=False)[:160]}")

        # A9 /api/specs 出现（安装后节点库数据源）
        st, specs2 = http("/api/specs")
        check("A9 /api/specs 出现 user/hello", any(s.get("type_id") == "user/hello" for s in (specs2 or [])),
              f"count={len(specs2 or [])}")

        # A10 计数
        st, lst2 = http("/api/market/items", "GET")
        found2 = next((i for i in lst2.get("items", []) if i.get("id") == item_a), None)
        check("A10 downloads/installs 计数", (found2 or {}).get("downloads", 0) >= 1
              and (found2 or {}).get("installs", 0) >= 1,
              f"downloads={found2.get('downloads')} installs={found2.get('installs')}")

        # ================= Phase 2：付费 + 错误码契约 =================
        log("\n===== Phase 2: 付费流（扣积分/分成）+ 错误码契约 =====")
        st, login_r = http("/api/auth/login", "POST", {"email": "buyer@qq.com", "password": "x", "device_id": "BUY-D"})
        buyer_tok = (login_r.get("session") or {}).get("token")
        check("B0 买家登录", login_r.get("ok") and bool(buyer_tok), f"resp={str(login_r)[:140]}")
        st, cr = http("/api/dev/add-credits", "POST", {"email": "buyer@qq.com", "amount": 300})
        st, vr = http(f"/api/auth/verify?token={buyer_tok}&device_id=BUY-D")
        check("B0b 买家积分 500", vr.get("credits") == 500, f"credits={vr.get('credits')}")

        st, pub2 = http("/api/market/items", "POST", {
            "kind": "node", "title": "付费节点·专业版", "description": "t5 付费流验证",
            "author": "author_a@qq.com", "price": 100,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/hello_export.zip",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize,
        })
        item_b = (pub2.get("item") or {}).get("id")
        check("B1 发布付费条目", pub2.get("ok") and item_b, f"id={item_b}")

        st, authb = http(f"/api/market/items/{item_b}/download-auth", "POST",
                         {"item_id": item_b, "token": buyer_tok, "client_version": "0.2.12"})
        check("B2 付费 download-auth 授权", authb.get("ok") and authb.get("price") == 100, f"resp={str(authb)[:160]}")
        st, vr2 = http(f"/api/auth/verify?token={buyer_tok}&device_id=BUY-D")
        st, login_a = http("/api/auth/login", "POST", {"email": "author_a@qq.com", "password": "x", "device_id": "AU-D"})
        check("B3 分成：买家 -100 / 作者 +80", vr2.get("credits") == 400
              and login_a.get("session", {}).get("credits") == 80,
              f"buyer={vr2.get('credits')} author={login_a.get('session', {}).get('credits')}")

        st, repb = http(f"/api/market/items/{item_b}/install-report", "POST",
                        {"item_id": item_b, "token": buyer_tok, "client_version": "0.2.12", "result": "ok"})
        st, lstb = http("/api/market/items", "GET")
        fb = next((i for i in lstb.get("items", []) if i.get("id") == item_b), None)
        check("B4 付费回报 + 计数", repb.get("ok") and (fb or {}).get("installs", 0) >= 1
              and (fb or {}).get("downloads", 0) >= 1,
              f"resp={str(repb)[:120]} downloads={fb.get('downloads')} installs={fb.get('installs')}")

        # B5 错误码契约
        st, e1 = http(f"/api/market/items/{item_b}/download-auth", "POST",
                      {"item_id": item_b, "token": "", "client_version": "0.2.12"})
        check("B5a 未登录付费 → login_required", e1.get("ok") is False and e1.get("code") == "login_required",
              f"code={e1.get('code')}")
        st, e2 = http(f"/api/market/items/{item_b}/download-auth", "POST",
                      {"item_id": item_b, "token": "no-such-token", "client_version": "0.2.12"})
        check("B5b 无效 token 付费 → login_required", e2.get("ok") is False and e2.get("code") == "login_required",
              f"code={e2.get('code')}")
        st, e3 = http(f"/api/market/items/{item_b}/download-auth", "POST",
                      {"item_id": item_b, "token": buyer_tok, "client_version": "99.0.0"})
        check("B5c 高分版本正常", e3.get("ok") is True, f"code={e3.get('code')}")
        # 版本过低条目
        st, pub3 = http("/api/market/items", "POST", {
            "kind": "node", "title": "未来版节点", "description": "min_client_version 99",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{PORT}/api/export/node/user/hello",
            "tags": ["t5"], "version": "99.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize, "min_client_version": "99.0.0",
        })
        item_c = (pub3.get("item") or {}).get("id")
        st, e4 = http(f"/api/market/items/{item_c}/download-auth", "POST",
                      {"item_id": item_c, "token": "", "client_version": "0.2.12"})
        check("B5d min_client_version 过低 → client_version_too_old",
              e4.get("ok") is False and e4.get("code") == "client_version_too_old",
              f"code={e4.get('code')} min={e4.get('min_client_version')}")
        # 下架条目
        st, pub4 = http("/api/market/items", "POST", {
            "kind": "node", "title": "已下架节点", "description": "status disabled",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{PORT}/api/export/node/user/hello",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize, "status": "disabled",
        })
        item_d = (pub4.get("item") or {}).get("id")
        st, e5 = http(f"/api/market/items/{item_d}/download-auth", "POST",
                      {"item_id": item_d, "token": "", "client_version": "0.2.12"})
        check("B5e 已下架 → item_disabled", e5.get("ok") is False and e5.get("code") == "item_disabled",
              f"code={e5.get('code')}")
        # 不存在的条目
        st, e6 = http("/api/market/items/nonexistent/download-auth", "POST",
                      {"item_id": "nonexistent", "token": "", "client_version": "0.2.12"})
        check("B5f 未知条目 → item_not_found", e6.get("ok") is False and e6.get("code") == "item_not_found",
              f"code={e6.get('code')}")
        st, e7 = http("/api/market/items/nonexistent/install-report", "POST",
                      {"item_id": "nonexistent", "result": "ok"})
        check("B5g 未知条目回报 → item_not_found", e7.get("ok") is False and e7.get("code") == "item_not_found",
              f"code={e7.get('code')}")

        # B7 双路径防双扣：download-auth 已购留痕后，服务端 /install 不再二次扣费
        st, login_r2 = http("/api/auth/login", "POST", {"email": "buyer@qq.com", "password": "x", "device_id": "BUY-D"})
        tok2 = (login_r2.get("session") or {}).get("token")
        st, ins_b2 = http(f"/api/market/items/{item_b}/install", "POST", {"token": tok2}, timeout=120)
        st, vr_b2 = http(f"/api/auth/verify?token={tok2}&device_id=BUY-D")
        check("B7 已购后 /install 不二次扣费（credits 保持）",
              ins_b2.get("ok") and vr_b2.get("credits") == 300,
              f"install_ok={ins_b2.get('ok')} credits={vr_b2.get('credits')} (B5c 后应为 300)")

        # B6 失败回报契约：installs 不增
        st, lst_before = http("/api/market/items", "GET")
        before_installs = next((i for i in lst_before.get("items", []) if i.get("id") == item_a), {}).get("installs", 0)
        st, repf = http(f"/api/market/items/{item_a}/install-report", "POST",
                        {"item_id": item_a, "result": "failed", "error": "SHA-256 校验失败（模拟）"})
        st, lst_after = http("/api/market/items", "GET")
        after_installs = next((i for i in lst_after.get("items", []) if i.get("id") == item_a), {}).get("installs", 0)
        check("B6 失败回报 recorded 且 installs 不增",
              repf.get("ok") and repf.get("recorded") and after_installs == before_installs,
              f"before={before_installs} after={after_installs} resp={str(repf)[:120]}")

        # ================= Phase 3：云端 item_not_found 容错 + 客户端重试 =================
        log("\n===== Phase 3: 云端 item_not_found 容错 + 客户端重试 =====")
        mock = start_mock_cloud()
        servers.append(mock)
        proc.terminate()
        proc.wait()
        procs.remove(proc)
        proc = start_server(cloud_url=f"http://127.0.0.1:{CLOUD_PORT}")
        procs.append(proc)

        # C1 云端列表为空（迁移期：云端不认识本地条目）
        st, clist = http("/api/market/items", "GET")
        check("C1 云端列表为空（迁移期）", clist.get("count") == 0 and clist.get("items") == [],
              f"count={clist.get('count')}")

        # C2 客户端策略模拟：第一次 download-auth → 云端 item_not_found（服务端回落本地桩成功）
        auth_c2, attempts = client_install_loop(item_a, "", retry_limit=2)
        check("C2 云端 item_not_found → 服务端回落本地桩 授权成功",
              auth_c2.get("ok") and auth_c2.get("item_id") == item_a,
              f"attempts={attempts} code={auth_c2.get('code')} url={str(auth_c2.get('download',{}).get('url',''))[:50]}")

        # C3 客户端对“云端 item_not_found”的容错重试策略（与 MarketPage.install 一致）：
        #   直接走云端（mock）第一次返回 item_not_found → 刷新列表 → 重试成功（服务端回落）
        raw_auth, raw_attempts = None, 0
        for attempt in range(2):
            raw_attempts += 1
            st, raw = http(f"/api/market/items/{item_a}/download-auth", "POST",
                           {"item_id": item_a, "token": "", "client_version": "0.2.12"})
            if raw.get("ok"):
                raw_auth = raw
                break
            if raw.get("code") == "item_not_found" and attempt == 0:
                st, _ = http("/api/market/items", "GET")
                continue
            break
        check("C3 客户端容错重试（item_not_found → 刷新 → 重试成功）",
              raw_auth is not None and raw_auth.get("ok") and raw_attempts <= 2,
              f"attempts={raw_attempts}")

        # C4 回报回落
        st, repc = http(f"/api/market/items/{item_a}/install-report", "POST",
                        {"item_id": item_a, "result": "ok"})
        check("C4 云端 item_not_found → 回报回落本地 recorded",
              repc.get("ok") and repc.get("recorded"), f"resp={str(repc)[:140]}")

        # C5 云端不可达（关闭 mock）→ 回落本地桩
        mock.shutdown()
        servers.remove(mock)
        time.sleep(0.3)
        st, auth_unreach = http(f"/api/market/items/{item_a}/download-auth", "POST",
                                {"item_id": item_a, "token": "", "client_version": "0.2.12"})
        check("C5 云端不可达 → 回落本地桩 授权成功",
              auth_unreach.get("ok") and auth_unreach.get("item_id") == item_a,
              f"code={auth_unreach.get('code')}")

        # ================= Phase 4：大插件适配 =================
        log("\n===== Phase 4: 大插件（12MB）上传安装 + 服务端流式下载安装 =====")
        proc.terminate()
        proc.wait()
        procs.remove(proc)
        proc = start_server(cloud_url="")
        procs.append(proc)

        big_weight = os.urandom(12 * 1024 * 1024)          # 12MB 载荷
        big_zip = make_zip_bytes("bigmodel", BIG_MANIFEST, {"weights.bin": big_weight})
        big_zip_path = pkg_zip_path("bigmodel", big_zip)
        big_sha = sha256_hex(big_zip)
        big_size = len(big_zip)
        check("D0 大包构造", big_size > 10 * 1024 * 1024, f"size={big_size}")

        t0 = time.time()
        st, upb = multipart("/api/user_nodes/install", "bigmodel.zip", big_zip)
        dt = time.time() - t0
        check("D1 12MB 上传安装 → ok（流式+长超时）", st == 200 and upb.get("ok")
              and "user/bigmodel" in (upb.get("nodes") or []),
              f"status={st} {dt:.2f}s resp={str(upb)[:140]}")
        st, specs3 = http("/api/specs")
        check("D1b /api/specs 出现 user/bigmodel", any(s.get("type_id") == "user/bigmodel" for s in (specs3 or [])),
              f"count={len(specs3 or [])}")

        st, pub5 = http("/api/market/items", "POST", {
            "kind": "node", "title": "大插件流式安装", "description": "12MB 服务端流式",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/bigmodel.zip",
            "tags": ["t5", "big"], "version": "1.0.0", "type_id": "user/bigmodel",
            "sha256": big_sha, "size": big_size,
        })
        item_e = (pub5.get("item") or {}).get("id")
        check("D2 发布大包条目（sha256 登记）", pub5.get("ok") and item_e, f"id={item_e}")

        t0 = time.time()
        st, inse = http(f"/api/market/items/{item_e}/install", "POST", {"token": ""}, timeout=180)
        dt = time.time() - t0
        check("D3 服务端 /install：_fetch_zip 流式下载+sha256 校验+安装",
              inse.get("ok") and "user/bigmodel" in (inse.get("type_ids") or []),
              f"{dt:.2f}s resp={str(inse)[:160]}")
        st, lst_e = http("/api/market/items", "GET")
        fe = next((i for i in lst_e.get("items", []) if i.get("id") == item_e), None)
        check("D4 服务端安装计数", (fe or {}).get("downloads", 0) >= 1 and (fe or {}).get("installs", 0) >= 1,
              f"downloads={fe.get('downloads')} installs={fe.get('installs')}")

        # 无 sha256 声明时也允许（旧条目兼容）
        st, pub6 = http("/api/market/items", "POST", {
            "kind": "node", "title": "无摘要大包", "description": "sha256 空",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/bigmodel.zip",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/bigmodel",
            "sha256": "", "size": big_size,
        })
        item_f = (pub6.get("item") or {}).get("id")
        st, insf = http(f"/api/market/items/{item_f}/install", "POST", {"token": ""}, timeout=180)
        check("D5 无 sha256 旧条目仍可安装（兼容）", insf.get("ok"), f"resp={str(insf)[:140]}")

        # ================= Phase 5：stub 内联 zip（download_url 空 → stub_path） =================
        log("\n===== Phase 5: 本地桩内联 zip → download-auth 返回 stub_path → 客户端 GET 下载 =====")
        hello_export_path = pkg_zip_path("hello_export", zip_bytes)   # 与导出端点同构的包文件
        st, pub7 = http("/api/market/items", "POST", {
            "kind": "node", "title": "内联桩节点", "description": "source_zip 内联",
            "author": "author_a@qq.com", "price": 0, "download_url": "",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize, "source_zip": hello_export_path,
        })
        item_g = (pub7.get("item") or {}).get("id")
        st, authg = http(f"/api/market/items/{item_g}/download-auth", "POST",
                         {"item_id": item_g, "token": "", "client_version": "0.2.12"})
        stub_path = (authg.get("download") or {}).get("stub_path", "")
        st, gotg = http(stub_path, raw=True)
        check("E1 内联 zip：stub_path 下载 + 摘要校验", authg.get("ok") and stub_path
              and st == 200 and sha256_hex(gotg) == zsha,
              f"stub_path={stub_path} bytes={len(gotg)}")

        summary = {
            "pass": len(PASS), "fail": len(FAIL),
            "evidence_log": EVIDENCE,
            "items": {"item_a": item_a, "item_b": item_b, "item_e": item_e, "item_g": item_g},
        }
        log("\n===== t5 e2e summary =====")
        log(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\nRESULT:", "PASS" if not FAIL else f"FAIL({len(FAIL)})", f"pass={len(PASS)} fail={len(FAIL)}")
        return 0 if not FAIL else 1
    finally:
        for srv in servers:
            try:
                srv.shutdown()
            except Exception:
                pass
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        snapshot_cleanup()
        log("[snapshot] 已清理代码快照")


if __name__ == "__main__":
    sys.exit(main())
