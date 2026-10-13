# -*- coding: utf-8 -*-
"""t5 商城端到端验收（纯云化版）：真实 HTTP 下载→校验→安装→回报 闭环。

覆盖（全部走真实 HTTP / 真实 uvicorn 服务 + 真实 cloud_server，非 TestClient）：
  A  云端商城列表 → download-auth → 客户端下载(流式) → SHA-256/大小校验 → 上传安装
     → install-report → /api/specs 出现 → 列表计数（downloads/installs）
  B  付费流：云端扣积分 / 20-80 分成 / 回报；错误码契约（login_required /
     insufficient_credits / client_version_too_old / item_disabled / item_not_found）
  C  纯云化容错：云端 item_not_found / 云端不可达 → 明确错误码，绝不回落本地 hub
     （t41 回归：移除「云端不可达回落本地桩」语义）
  D  大插件适配：12MB 包 上传安装（multipart 流式） + 服务端 _fetch_zip 流式下载
     （真实 file server URL + sha256 登记校验） + /install 服务端安装
  E  云端直链契约：download-auth 返回直链（无 stub_path）；客户端按直链下载校验

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
import sqlite3
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

PORT = 8788          # 本地 app 服务
CLOUD_PORT = 9455    # 真实 cloud_server（云端桩）
FILE_PORT = 9456     # 大插件 zip 静态文件服务
TOKEN = "dev-local-token"
EVIDENCE = os.path.join(TMP, "t5_market_e2e.log")
CLOUD_DB = os.path.join(TMP, "t5_cloud.db")
# cloud_server 的 MARKET_FILE = os.path.join(os.path.dirname(DB_PATH), 'market_items.json')
CLOUD_MARKET = os.path.join(TMP, "market_items.json")
CLOUD_ADMIN_KEY = "t5-admin-key"
CARD_SECRET = "t5-card-secret"

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
    """对本地 app 服务发请求（带本地授权令牌，模拟渲染进程）。"""
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
        "ALIYUN_DM_ENABLED": "false",          # 云端走 dev 日志，不发送真实邮件
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
        raise RuntimeError("本地 app 服务启动失败")
    return proc


def start_cloud_server():
    """启动真实云端授权桩（backend/cloud_server.py，127.0.0.1:9455）。"""
    log(f"[cloud] 启动 cloud_server :{CLOUD_PORT}")
    wait_port_free(CLOUD_PORT, timeout=20)
    logf = open(os.path.join(TMP, "t5_cloud_stdout.log"), "ab")
    env = dict(os.environ)
    env.update({
        "CLOUD_DB": CLOUD_DB,
        "FRAMEWEAVE_CARD_SECRET": CARD_SECRET,
        "FRAMEWEAVE_ADMIN_KEY": CLOUD_ADMIN_KEY,
        "ALIYUN_DM_ENABLED": "false",
        "FRAMEWEAVE_VERIFY_SECRET": "fw-verify-secret-2026",
        "PYTHONUNBUFFERED": "1",
    })
    proc = subprocess.Popen([PY, os.path.join(BACKEND, "cloud_server.py"),
                             "--port", str(CLOUD_PORT), "--bind", "127.0.0.1"],
                            cwd=BACKEND, env=env, stdout=logf, stderr=subprocess.STDOUT)
    ok = wait_health(CLOUD_PORT)
    check("cloud_server 健康检查", ok, f"port={CLOUD_PORT}")
    if not ok:
        proc.kill()
        raise RuntimeError("云端授权桩启动失败")
    return proc


def seed_cloud_market():
    """清空云端商城文件（每轮全新）。"""
    try:
        os.remove(CLOUD_MARKET)
    except OSError:
        pass


def cloud_publish(item: dict) -> dict:
    """把条目写入云端商城文件（cloud_server 每次请求实时读 market_items.json）。"""
    data = {}
    if os.path.exists(CLOUD_MARKET):
        try:
            with open(CLOUD_MARKET, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    iid = str(item.get("id") or ("it_" + uuid.uuid4().hex[:10]))
    data[iid] = {"id": iid,
                 "kind": item.get("kind", "node"),
                 "title": item.get("title", ""),
                 "description": item.get("description", ""),
                 "author": item.get("author", ""),
                 "price": int(item.get("price", 0)),
                 "official": bool(item.get("official", False)),
                 "download_url": item.get("download_url", ""),
                 "version": item.get("version", "1.0.0"),
                 "type_id": item.get("type_id", ""),
                 "sha256": item.get("sha256", ""),
                 "size": int(item.get("size", 0)),
                 "min_client_version": item.get("min_client_version", ""),
                 "status": item.get("status", "active"),
                 "downloads": 0, "installs": 0,
                 "tags": item.get("tags", []),
                 "created_at": time.time(),
                 "publisher_pubkey": item.get("publisher_pubkey", ""),
                 "archive_signature": item.get("archive_signature", ""),
                 }
    with open(CLOUD_MARKET, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "item": data[iid]}


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
                if n in ("data", "user_nodes", "__pycache__", ".venv", "models"):
                    out.add(n)
            else:
                if n.endswith((".pyc", ".pyo")) or n in ("cloud.db", ".gitkeep") or n.startswith("tests_"):
                    out.add(n)
        return out

    shutil.copytree(BACKEND, SNAP_BACKEND, ignore=_ignored)
    os.makedirs(SNAP_DATA, exist_ok=True)
    os.makedirs(SNAP_NODES, exist_ok=True)
    # 云端 DB（sessions/cards）与云端商城文件落到测试目录（非仓库）
    for p in (CLOUD_DB, CLOUD_DB + "-journal", CLOUD_MARKET):
        try:
            os.remove(p)
        except OSError:
            pass
    os.makedirs(TMP, exist_ok=True)
    _init_cloud_db()
    # 本地 app 数据：空商城（仅占位；实际条目在云端）
    with open(os.path.join(SNAP_DATA, "market.json"), "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False)
    log("[snapshot] 已创建代码快照（数据/节点目录已隔离）；云端桩 DB 已重置")


def _init_cloud_db() -> None:
    """初始化云端 DB：建表 + 注入买家/作者账号（密码 pass1234，可登录）与一张测试卡密。"""
    c = sqlite3.connect(CLOUD_DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(email TEXT PRIMARY KEY,password_hash TEXT NOT NULL DEFAULT '',salt TEXT NOT NULL DEFAULT '',user_id TEXT NOT NULL,token TEXT NOT NULL DEFAULT '',plan TEXT NOT NULL DEFAULT 'trial',expires_at REAL NOT NULL DEFAULT 0,device_id TEXT NOT NULL DEFAULT '',credits INTEGER NOT NULL DEFAULT 0,cards_used TEXT NOT NULL DEFAULT '[]',last_verified REAL NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0,status INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS email_codes(email TEXT NOT NULL,scene TEXT NOT NULL,code_hash TEXT NOT NULL,expires_at REAL NOT NULL,sent_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(email,scene));
    CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT,card TEXT NOT NULL UNIQUE,plan TEXT NOT NULL,days INTEGER NOT NULL,created_at REAL NOT NULL,used_by TEXT NOT NULL DEFAULT '',used_at REAL NOT NULL DEFAULT 0,revoked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS admin_tokens(token TEXT PRIMARY KEY,created_at REAL NOT NULL,expires_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS market_installs(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',client_version TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS market_downloads(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT '',price INTEGER NOT NULL DEFAULT 0,paid INTEGER NOT NULL DEFAULT 0,client_version TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '',ip TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS send_log(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,scene TEXT NOT NULL,sent_at REAL NOT NULL,ip TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS rate_limits(scope TEXT NOT NULL,key TEXT NOT NULL,window_start INTEGER NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(scope,key,window_start));
    CREATE TABLE IF NOT EXISTS mail_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,scene TEXT NOT NULL,ip TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
    """)
    now = time.time()
    # 买家：200 积分；作者：0 积分（密码 pass1234，scrypt 哈希与 cloud_server.pw_hash 同构）
    rows = [
        ("buyer@qq.com", "buyer", "BUY-D", 200),
        ("author_a@qq.com", "author_a", "AU-D", 0),
    ]
    for email, uid, dev, credits in rows:
        salt = "e2e" + uuid.uuid4().hex[:4]
        pw_encoded = b"pass1234"
        dk = hashlib.scrypt(pw_encoded, salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
        pw_hash_val = "scrypt$16384$8$1$" + dk.hex()
        c.execute("INSERT OR REPLACE INTO sessions(email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                  (email, pw_hash_val, salt, uid, uuid.uuid4().hex[:32], "member", now + 86400 * 30, dev, credits, "[]", now, now))
    # 一张有效测试卡密（signature 与 CARD_SECRET 匹配）
    import base64 as _b64
    import hmac as _hm
    payload = {"plan": "month", "days": 30, "serial": uuid.uuid4().hex[:10]}
    b64 = _b64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    sig = _hm.new(CARD_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()[:12]
    card_val = f"FW-{b64}-{sig}"
    c.execute("INSERT OR IGNORE INTO cards(card,plan,days,created_at) VALUES(?,?,?,?)", (card_val, "month", 30, now))
    c.commit()
    c.close()
    # 云端商城文件
    with open(CLOUD_MARKET, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False)


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
    """模拟 MarketPage.install 的授权重试策略：item_not_found → 刷新列表重试一次；
    瞬时失败延时重试一次。返回 (auth, attempts)。纯云化下 item_not_found 保持云端错误。"""
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
        if auth.get("code") in ("network", "timeout", "cloud_unavailable") and attempt == 0:
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
    snapshot_create()

    procs = []          # 需要清理的进程
    servers = []        # 需要关闭的 http 服务
    try:
        # ================= Phase 1：免费节点 全链路（云端商城） =================
        log("\n===== Phase 1: 免费节点 商城列表→下载→校验→安装→回报（云端） =====")
        cloud_proc = start_cloud_server()
        procs.append(cloud_proc)
        proc = start_server(cloud_url=f"http://127.0.0.1:{CLOUD_PORT}")
        procs.append(proc)
        # 包静态文件服务（模拟 GitHub Release 外域直链）
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

        # A3 发布条目（写入云端商城；download_url 指向外域直链）
        seed_cloud_market()
        pub = cloud_publish({
            "kind": "node", "title": "问候节点·专业版", "description": "t5 端到端免费节点",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/hello_export.zip",
            "tags": ["问候", "t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize,
        })
        item_a = (pub.get("item") or {}).get("id")
        check("A3 云端发布条目（免费）", pub.get("ok") and item_a, f"id={item_a}")

        # A4 商城列表（云端）
        st, lst = http("/api/market/items?kind=node", "GET")
        found = next((i for i in lst.get("items", []) if i.get("id") == item_a), None)
        check("A4 云端商城列表命中", found is not None and found.get("sha256") == zsha and found.get("size") == zsize,
              f"count={lst.get('count')}")

        # A5 download-auth（免费无需登录；云端授权）
        st, auth = http(f"/api/market/items/{item_a}/download-auth", "POST",
                        {"item_id": item_a, "token": "", "client_version": "0.2.12"})
        dl = auth.get("download") or {}
        check("A5 download-auth 云端授权", auth.get("ok") and auth.get("status") == "authorized"
              and dl.get("url", "").startswith("http") and dl.get("sha256") == zsha
              and dl.get("size") == zsize and not dl.get("stub_path"),
              f"code={auth.get('code')} url={dl.get('url','')[:60]}")

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

        # A8 install-report（云端记录）
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

        # ================= Phase 2：付费 + 错误码契约（云端） =================
        log("\n===== Phase 2: 付费流（云端扣积分/分成）+ 错误码契约 =====")
        st, login_r = http("/api/auth/login", "POST", {"email": "buyer@qq.com", "password": "pass1234", "device_id": "BUY-D"})
        buyer_tok = (login_r.get("session") or {}).get("token")
        check("B0 买家云端登录", login_r.get("ok") and bool(buyer_tok), f"resp={str(login_r)[:140]}")
        st, vr = http(f"/api/auth/verify?token={buyer_tok}&device_id=BUY-D")
        check("B0b 买家积分 200", vr.get("credits") == 200, f"credits={vr.get('credits')}")

        pub2 = cloud_publish({
            "kind": "node", "title": "付费节点·专业版", "description": "t5 付费流验证",
            "author": "author_a@qq.com", "price": 100,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/hello_export.zip",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize,
        })
        item_b = (pub2.get("item") or {}).get("id")
        check("B1 云端发布付费条目", pub2.get("ok") and item_b, f"id={item_b}")

        st, authb = http(f"/api/market/items/{item_b}/download-auth", "POST",
                         {"item_id": item_b, "token": buyer_tok, "client_version": "0.2.12"})
        check("B2 付费 download-auth 云端授权", authb.get("ok") and authb.get("price") == 100, f"resp={str(authb)[:160]}")
        st, vr2 = http(f"/api/auth/verify?token={buyer_tok}&device_id=BUY-D")
        st, login_a = http("/api/auth/login", "POST", {"email": "author_a@qq.com", "password": "pass1234", "device_id": "AU-D"})
        check("B3 分成：买家 -100 / 作者 +80（云端）", vr2.get("credits") == 100
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
        check("B5c 高分版本正常（积分不足→若已扣则失败；此处 buyer 已 100，再购需 100）",
              e3.get("ok") is True or e3.get("code") == "insufficient_credits",
              f"code={e3.get('code')} resp={str(e3)[:120]}")
        # 版本过低条目
        pub3 = cloud_publish({
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
        pub4 = cloud_publish({
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

        # ================= Phase 3：纯云化容错（云端不可达/item_not_found 不回落） =================
        log("\n===== Phase 3: 纯云化容错（不回落本地 hub） =====")
        # C1 云端不可达（关闭 cloud_server）→ download-auth 明确报错
        cloud_proc.terminate()
        cloud_proc.wait(timeout=8)
        procs.remove(cloud_proc)
        time.sleep(0.3)
        st, auth_unreach = http(f"/api/market/items/{item_a}/download-auth", "POST",
                                {"item_id": item_a, "token": "", "client_version": "0.2.12"})
        check("C1 云端不可达 → cloud_unavailable（不回落本地 hub）",
              auth_unreach.get("ok") is False and auth_unreach.get("code") == "cloud_unavailable",
              f"code={auth_unreach.get('code')} resp={str(auth_unreach)[:140]}")
        st, lst_unreach = http("/api/market/items", "GET")
        check("C2 云端不可达 → 商城列表明确报错（不回落本地 hub）",
              lst_unreach.get("ok") is False and lst_unreach.get("code") == "cloud_unavailable",
              f"code={lst_unreach.get('code')} resp={str(lst_unreach)[:140]}")

        # C3 重启云端 → item_not_found 由云端权威返回（不回落本地）
        cloud_proc = start_cloud_server()
        procs.append(cloud_proc)
        st, auth_c3 = http(f"/api/market/items/nonexistent/download-auth", "POST",
                           {"item_id": "nonexistent", "token": "", "client_version": "0.2.12"})
        check("C3 云端 item_not_found → 返回云端错误（不回落）",
              auth_c3.get("ok") is False and auth_c3.get("code") == "item_not_found",
              f"code={auth_c3.get('code')}")

        # ================= Phase 4：大插件适配（云端） =================
        log("\n===== Phase 4: 大插件（12MB）上传安装 + 服务端流式下载安装 =====")
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

        seed_cloud_market()
        pub5 = cloud_publish({
            "kind": "node", "title": "大插件流式安装", "description": "12MB 服务端流式",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/bigmodel.zip",
            "tags": ["t5", "big"], "version": "1.0.0", "type_id": "user/bigmodel",
            "sha256": big_sha, "size": big_size,
        })
        item_e = (pub5.get("item") or {}).get("id")
        check("D2 云端发布大包条目（sha256 登记）", pub5.get("ok") and item_e, f"id={item_e}")

        t0 = time.time()
        st, inse = http(f"/api/market/items/{item_e}/install", "POST", {"token": ""}, timeout=180)
        dt = time.time() - t0
        check("D3 服务端 /install：云端 download-auth → _fetch_zip 流式下载+sha256 校验+安装",
              inse.get("ok") and "user/bigmodel" in (inse.get("type_ids") or []),
              f"{dt:.2f}s resp={str(inse)[:160]}")
        st, lst_e = http("/api/market/items", "GET")
        fe = next((i for i in lst_e.get("items", []) if i.get("id") == item_e), None)
        check("D4 服务端安装计数", (fe or {}).get("downloads", 0) >= 1 and (fe or {}).get("installs", 0) >= 1,
              f"downloads={fe.get('downloads')} installs={fe.get('installs')}")

        # 无 sha256 声明时也允许（旧条目兼容）
        pub6 = cloud_publish({
            "kind": "node", "title": "无摘要大包", "description": "sha256 空",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/bigmodel.zip",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/bigmodel",
            "sha256": "", "size": big_size,
        })
        item_f = (pub6.get("item") or {}).get("id")
        st, insf = http(f"/api/market/items/{item_f}/install", "POST", {"token": ""}, timeout=180)
        check("D5 无 sha256 旧条目仍可安装（兼容）", insf.get("ok"), f"resp={str(insf)[:140]}")

        # ================= Phase 5：云端直链契约（无 stub_path） =================
        log("\n===== Phase 5: 云端直链契约（download-auth 返回直链，无 stub_path） =====")
        hello_export_path = pkg_zip_path("hello_export", zip_bytes)
        pub7 = cloud_publish({
            "kind": "node", "title": "直链节点", "description": "download_url 直链",
            "author": "author_a@qq.com", "price": 0,
            "download_url": f"http://127.0.0.1:{FILE_PORT}/hello_export.zip",
            "tags": ["t5"], "version": "1.0.0", "type_id": "user/hello",
            "sha256": zsha, "size": zsize,
        })
        item_g = (pub7.get("item") or {}).get("id")
        st, authg = http(f"/api/market/items/{item_g}/download-auth", "POST",
                         {"item_id": item_g, "token": "", "client_version": "0.2.12"})
        dl_g = (authg.get("download") or {})
        check("E1 云端直链：download-auth 返回直链且无 stub_path", authg.get("ok")
              and dl_g.get("url", "").startswith("http") and not dl_g.get("stub_path"),
              f"url={dl_g.get('url','')[:60]} stub={dl_g.get('stub_path','')}")
        st, gotg = http_abs(dl_g["url"])
        check("E2 直链下载 + 摘要校验", st == 200 and sha256_hex(gotg) == zsha,
              f"bytes={len(gotg)}")

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