# -*- coding: utf-8 -*-
"""P0.4 + t2 平台插件加载器安全 验收测试。

覆盖：
  A1 上传 >上限返回 413（MAX_UPLOAD_MB 实际引用：Content-Length 预检 + 流式截断）
  A2 install_zip 五上限（总量/单文件/成员数/压缩比/符号链接；t2 上限已提升：
     上传 2GB/总量 8GB/单文件 4GB/成员 20000，参数唯一引用 worker/limits.py）
  A3 staging 原子安装（临时目录 -> 校验 -> rename，失败无残留）
  A4 scan/install 对坏包显式报错误码，不静默吞异常（含 manifest v2 G1-G10 门）
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_AUDIT_LOG",
                      os.path.join(tempfile.gettempdir(), "fw_t7_audit_test.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402
from app import server  # noqa: E402
from app.declarative import install_zip, USER_NODES_DIR, _RESERVED_DIRS  # noqa: E402
from app.worker import limits  # noqa: E402

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))

# ---- 0. 隔离环境：临时 user_nodes，不碰仓库真实数据 ----
TMP_ROOT = tempfile.mkdtemp(prefix="fw_t7_")
tmp_nodes = os.path.join(TMP_ROOT, "user_nodes")
os.makedirs(tmp_nodes, exist_ok=True)
server.declarative.set_user_nodes_dir(tmp_nodes)
server.declarative.scan()

def make_zip(members, top="pkg_demo", manifest=None):
    """members: list of (relpath, data_bytes) or (relpath, None)=dir, or special (relpath, 'symlink', target)"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "/", "")
        if manifest is not None:
            zf.writestr(top + "/manifest.json", json.dumps(manifest))
        for item in members:
            rel, data = item[0], item[1]
            if data is None:
                zf.writestr(top + "/" + rel + "/", "")
            elif isinstance(data, str) and data.startswith("SYMLINK@"):
                zi = zipfile.ZipInfo(top + "/" + rel)
                zi.create_system = 3
                zi.external_attr = (0xA1FF) << 16
                zf.writestr(zi, data[len("SYMLINK@"):])
            else:
                zf.writestr(top + "/" + rel, data)
    return buf

def write_tmp_zip(buf):
    fd, path = tempfile.mkstemp(suffix=".zip", dir=TMP_ROOT)
    with os.fdopen(fd, "wb") as f:
        f.write(buf.getvalue())
    return path

# t2 大插件：手工构造 zip——中央目录声明超大 uncompressed 尺寸（>4GB 用 ZIP64 extra），
# 实际只写少量数据。尺寸门在解压前按声明值拒绝，zipfile 不读成员数据，因此无需真实写入
# 8GB/4GB 文件即可驱动总量/单文件/压缩比门（与 declarative._extract_zip_safe 预检逻辑一致）。
def _dos_datetime(t=None):
    st = time.localtime(t)
    dosdate = ((st.tm_year - 1980) << 9) | (st.tm_mon << 5) | st.tm_mday
    dostime = (st.tm_hour << 11) | (st.tm_min << 5) | (st.tm_sec // 2)
    return dostime, dosdate

def make_fake_size_zip(members, top="bigpkg", manifest=None):
    """members: [(relpath, declared_uncompressed_size, actual_data_bytes)]"""
    buf = io.BytesIO()
    central = []
    offset = 0

    def _local(name, data, usz):
        nonlocal offset
        crc = zlib.crc32(data) & 0xFFFFFFFF
        csz = len(data)
        need64 = usz > 0xFFFFFFFF or csz > 0xFFFFFFFF
        fsize = 0xFFFFFFFF if need64 else usz
        fsize2 = 0xFFFFFFFF if need64 else csz
        extra = b""
        if need64:
            extra = struct.pack("<HHQQ", 0x0001, 16, usz, csz)
        dostime, dosdate = _dos_datetime()
        header = struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0, 0, dostime, dosdate,
                             crc, fsize2, fsize, len(name.encode()), len(extra))
        record = header + name.encode() + extra + data
        buf.write(record)
        off = offset
        offset += len(record)
        return off, crc, csz

    def _central(name, usz, off, crc, csz):
        need64 = usz > 0xFFFFFFFF or csz > 0xFFFFFFFF
        fsize = 0xFFFFFFFF if need64 else usz
        fsize2 = 0xFFFFFFFF if need64 else csz
        extra = b""
        if need64:
            extra = struct.pack("<HHQQQ", 0x0001, 24, usz, csz, off)
        dostime, dosdate = _dos_datetime()
        header = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, 0x0314, 20, 0, 0,
                             dostime, dosdate, crc, fsize2, fsize, len(name.encode()),
                             len(extra), 0, 0, 0, 0x10 if name.endswith("/") else 0, off)
        return header + name.encode() + extra

    off, crc, csz = _local(top + "/", b"", 0)
    central.append(_central(top + "/", 0, off, crc, csz))
    if manifest is not None:
        data = json.dumps(manifest).encode()
        off, crc, csz = _local(top + "/manifest.json", data, len(data))
        central.append(_central(top + "/manifest.json", len(data), off, crc, csz))
    for rel, usz, data in members:
        off, crc, csz = _local(top + "/" + rel, data, usz)
        central.append(_central(top + "/" + rel, usz, off, crc, csz))
    cd = b"".join(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, len(central), len(central),
                       len(cd), offset, 0)
    buf.write(cd)
    buf.write(eocd)
    return buf

GOOD_MANIFEST = {
    "schema_version": 2,
    "engine_api": "1.0",
    "type_id": "user/demo",
    "title": "Demo",
    "category": "用户节点",
    "description": "demo",
    "version": "1.0.0",
    "kind": "transform",
    "inputs": [{"name": "a", "type": "STRING"}],
    "outputs": [{"name": "out", "type": "STRING"}],
    "params": [],
    "transform": {"out": {"op": "upper", "source": {"$input": "a"}}},
}

# ================= A1: 上传 413 =================
print("\n==== A1 上传上限 ====")
client = TestClient(server.app)

# A1a: Content-Length 预检 >上限 -> 413（不落盘；引用 limits 常量，自动适配 2GB）
big_len = limits.UPLOAD_MAX_BYTES + 1
check("A1a 预检 413（Content-Length 声明 >上限）",
      limits.upload_over_limit(str(big_len)),
      f"CL={big_len}")

# 真实上传流：构造小 zip 本体 + 声明超大 Content-Length 仍 413
small_zip = make_zip([("a.txt", b"x" * 1000)], manifest=GOOD_MANIFEST)
files = {"file": ("big.zip", small_zip.getvalue(), "application/zip")}
r = client.post("/api/user_nodes/install", files=files,
                headers={"X-FW-Local-Token": "dev-local-token",
                         "Content-Length": str(big_len)})
check("A1b 上传接口声明超限返回 413", r.status_code == 413, f"status={r.status_code} body={r.text[:120]}")

# 流式截断：驱动 server 端截断分支（无 Content-Length，读到 >上限 即 413）。
# t2 上限 2GB，真实写 2GB+ 不现实 → 临时把 limits.UPLOAD_MAX_BYTES 压到 8MB（唯一引用点，
# monkeypatch 模块常量即可），验证截断分支逻辑，随后恢复。
import uuid as _uuid

def no_cl_multipart_upload(body_bytes: bytes):
    boundary = "----t7boundary" + _uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"big.zip\"\r\n"
        "Content-Type: application/zip\r\n\r\n"
    ).encode()
    tail = ("\r\n--" + boundary + "--\r\n").encode()
    raw = head + body_bytes + tail
    return client.post(
        "/api/user_nodes/install",
        content=raw,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-FW-Local-Token": "dev-local-token",
        },
    )

class _FakeRequest:
    headers = {}

class _FakeFile:
    def __init__(self, total):
        self._left = total
        self._chunk = 1024 * 1024
    def read(self, n=-1):
        if self._left <= 0:
            return b""
        take = min(self._chunk, self._left)
        self._left -= take
        return b"z" * take

class _FakeUpload:
    def __init__(self, total):
        self.file = _FakeFile(total)
        self.filename = "big.zip"

async def _drive_truncation(total):
    fake_upload = _FakeUpload(total)
    from starlette.exceptions import HTTPException as StarletteHTTPException
    try:
        await server.user_nodes_install(_FakeRequest(), fake_upload)
        return None
    except StarletteHTTPException as e:
        return e.status_code

import asyncio as _asyncio

_SAVE_UPLOAD = limits.UPLOAD_MAX_BYTES
try:
    limits.UPLOAD_MAX_BYTES = 8 * 1024 * 1024          # 临时 8MB，驱动截断无需真实 2GB
    big_body = b"y" * (limits.UPLOAD_MAX_BYTES + 1024 * 1024)   # 9MB > 8MB
    r_nocl = no_cl_multipart_upload(big_body)
    check("A1c 流式截断：>上限 body（无 CL）返回 413",
          r_nocl.status_code == 413, f"status={r_nocl.status_code} body={r_nocl.text[:120]}")

    # A1d: 直接驱动 server 端截断分支（无 Content-Length 头，读到 >上限 即 413）
    code = _asyncio.run(_drive_truncation(limits.UPLOAD_MAX_BYTES + 5 * 1024 * 1024))
    check("A1d 流式截断分支（无 CL，>上限）抛 413", code == 413, f"code={code}")

    # ---- F1 回归：413 截断后零临时文件残留（含 Windows 句柄场景） ----
    def _collect_tmp_zips(root):
        found = []
        if not os.path.isdir(root):
            return found
        for name in os.listdir(root):
            if name.startswith("tmp") and name.endswith(".zip"):
                found.append(os.path.join(root, name))
        return found

    _tmp_dir = server._tmp_upload_dir()
    _node_tmp_before = set(_collect_tmp_zips(tmp_nodes))
    _platform_tmp_before = set(_collect_tmp_zips(_tmp_dir))
    _cwd = os.path.dirname(os.path.abspath(__file__))
    _cwd_before = set(_collect_tmp_zips(_cwd))

    _asyncio.run(_drive_truncation(limits.UPLOAD_MAX_BYTES + 5 * 1024 * 1024))

    check("F1 413 后 user_nodes 无 tmp*.zip 残留",
          set(_collect_tmp_zips(tmp_nodes)) == _node_tmp_before,
          f"before={_node_tmp_before} after={set(_collect_tmp_zips(tmp_nodes))}")
    check("F1 413 后平台临时目录(DATA_DIR/tmp)无 tmp*.zip 残留",
          set(_collect_tmp_zips(_tmp_dir)) == _platform_tmp_before,
          f"before={_platform_tmp_before} after={set(_collect_tmp_zips(_tmp_dir))}")
    check("F1 413 后 CWD(backend/)无新增 tmp*.zip 残留",
          set(_collect_tmp_zips(_cwd)) == _cwd_before,
          f"before={_cwd_before} after={set(_collect_tmp_zips(_cwd))}")
finally:
    limits.UPLOAD_MAX_BYTES = _SAVE_UPLOAD

# ================= A2: 五上限（t2 新上限） =================
print("\n==== A2 解压五上限（t2: 总量 8GB/单文件 4GB/成员 20000） ====")

# A2a: 总量 > 8GB（声明值欺骗：3 个 3GB 成员，单成员 < 4GB，合计 9GB > 8GB）
def make_big_total_zip():
    return write_tmp_zip(make_fake_size_zip(
        [(f"huge{i}.bin", 3 * 1024 * 1024 * 1024, b"x" * 1024) for i in range(3)],
        manifest=GOOD_MANIFEST))

try:
    install_zip(make_big_total_zip())
    check("A2a 总量上限拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2a 总量上限拒绝", e.code == limits.ERR_TOTAL_SIZE, f"code={e.code}")

# A2b: 单文件 > 4GB（声明值欺骗，>4GB 走 ZIP64 extra）
def make_big_file_zip():
    return write_tmp_zip(make_fake_size_zip(
        [("huge.bin", 4 * 1024 * 1024 * 1024 + 1, b"x" * 1024)],
        manifest=GOOD_MANIFEST))

try:
    install_zip(make_big_file_zip())
    check("A2b 单文件上限拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2b 单文件上限拒绝", e.code == limits.ERR_FILE_SIZE, f"code={e.code}")

# A2c: 成员数 > 20000（含顶层目录 = 20001 > 20000）
def make_many_members(n=20000):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bigpkg/", "")
        for i in range(n):
            zf.writestr(f"bigpkg/f{i}.txt", b"x")
    return write_tmp_zip(buf)

try:
    install_zip(make_many_members())
    check("A2c 成员数上限拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2c 成员数上限拒绝", e.code == limits.ERR_MEMBER_COUNT, f"code={e.code}")

# A2d: 压缩比 > 100:1（声明 100MB / 实际 zip ~1KB）
def make_bomb():
    return write_tmp_zip(make_fake_size_zip(
        [("zeros.bin", 100 * 1024 * 1024, b"\x00" * 1024)],
        manifest=GOOD_MANIFEST))

try:
    install_zip(make_bomb())
    check("A2d 压缩比上限拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2d 压缩比上限拒绝", e.code == limits.ERR_ZIP_BOMB, f"code={e.code}")

# A2e: symlink 成员拒绝
def make_symlink_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bigpkg/", "")
        zi = zipfile.ZipInfo("bigpkg/link")
        zi.create_system = 3
        zi.external_attr = (0xA1FF) << 16
        zf.writestr(zi, "/etc/passwd")
    return write_tmp_zip(buf)

try:
    install_zip(make_symlink_zip())
    check("A2e symlink 拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2e symlink 拒绝", e.code == limits.ERR_SYMLINK, f"code={e.code}")

# A2f: 穿越成员（zip-slip）拒绝
def make_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bigpkg/", "")
        zf.writestr("../evil.txt", b"x")
    return write_tmp_zip(buf)
try:
    install_zip(make_slip())
    check("A2f 路径穿越拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A2f 路径穿越拒绝", e.code == limits.ERR_TRAVERSAL, f"code={e.code}")

# ================= A3: staging 原子安装 =================
print("\n==== A3 staging 原子安装 ====")

# A3a: 正常包 -> 安装成功，包在 user_nodes/<pkg>/，staging 无残留
good = make_zip([("node.py", b"class PluginNode:\n    async def run(self, ctx, i, p):\n        return {}\n"),
                 ("README.md", b"hi")], manifest=GOOD_MANIFEST)
good_path = write_tmp_zip(good)
dest = install_zip(good_path)
check("A3a 正常安装成功", os.path.isdir(dest) and os.path.basename(dest) == "pkg_demo",
      f"dest={dest}")
check("A3a staging 无残留", not os.path.exists(os.path.join(tmp_nodes, ".staging", "unpack"))
      and os.listdir(os.path.join(tmp_nodes, ".staging")) == [],
      f"staging 内容={os.listdir(os.path.join(tmp_nodes, '.staging'))}")
check("A3a manifest 已落盘", os.path.isfile(os.path.join(dest, "manifest.json")))
check("A3a scan 已加载", "user/demo" in [s.type_id for s in server.declarative.list_specs()])

# A3b: 失败包（manifest 缺失）-> 抛错误码 + user_nodes 无残留
bad = make_zip([("node.py", b"x")])  # 无 manifest
bad_path = write_tmp_zip(bad)
before = set(os.listdir(tmp_nodes))
try:
    install_zip(bad_path)
    check("A3b 缺 manifest 拒绝", False, "未抛出")
except limits.PluginInstallError as e:
    check("A3b 缺 manifest 拒绝", e.code == limits.ERR_MANIFEST, f"code={e.code}")
after = set(os.listdir(tmp_nodes))
check("A3b 失败无残留（无 pkg_demo，无 .staging 内容）",
      after == before - {".staging"} or after <= before,
      f"before={before} after={after}")
check("A3b .staging 目录为空或不存在",
      not os.path.exists(os.path.join(tmp_nodes, ".staging")) or
      os.listdir(os.path.join(tmp_nodes, ".staging")) == [],
      f"staging={os.listdir(os.path.join(tmp_nodes, '.staging')) if os.path.exists(os.path.join(tmp_nodes, '.staging')) else 'missing'}")

# A3c: 原子替换：已存在的同名包被新版本原子替换
v2 = make_zip([("node.py", b"class PluginNode:\n    async def run(self, ctx, i, p):\n        return {}\n")],
              manifest={**GOOD_MANIFEST, "version": "2.0.0"})
v2_path = write_tmp_zip(v2)
dest2 = install_zip(v2_path)
check("A3c 更新安装成功", dest2 == dest and os.path.isdir(dest2))
v2m = json.load(open(os.path.join(dest2, "manifest.json"), encoding="utf-8"))
check("A3c 新版本生效", v2m["version"] == "2.0.0", f"version={v2m['version']}")
check("A3c 替换后 staging/old 已清理",
      not os.path.exists(os.path.join(tmp_nodes, ".staging", "old_pkg_demo")))

# ================= A4: 显式错误码 =================
print("\n==== A4 显式错误码 ====")
# A4a: install_zip 对坏包抛 PluginInstallError（带 code）
for name, path in [("symlink", make_symlink_zip()), ("slip", make_slip()),
                   ("bomb", make_bomb())]:
    try:
        install_zip(path)
        check(f"A4a {name} 抛错", False, "未抛出")
    except limits.PluginInstallError as e:
        check(f"A4a {name} 抛带 code 错误", e.code in (limits.ERR_SYMLINK, limits.ERR_TRAVERSAL, limits.ERR_ZIP_BOMB),
              f"code={e.code}")
    except Exception as e:
        check(f"A4a {name} 抛 PluginInstallError", False, f"wrong type {type(e)}")

# A4b: 上传接口返回显式错误码（非 200 吞异常）
with open(make_symlink_zip(), "rb") as f:
    r = client.post("/api/user_nodes/install",
                    files={"file": ("evil.zip", f.read(), "application/zip")},
                    headers={"X-FW-Local-Token": "dev-local-token"})
check("A4b 上传毒包返回显式错误码", r.status_code == 200 and r.json().get("ok") is False
      and r.json().get("code") == limits.ERR_SYMLINK,
      f"status={r.status_code} body={r.text[:160]}")

# A4c: scan 对坏包记录 per-package 错误（scan_errors）
broken_dir = os.path.join(tmp_nodes, "broken_pkg")
os.makedirs(broken_dir, exist_ok=True)
with open(os.path.join(broken_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump({"title": "No type_id"}, f)
server.declarative.scan()
errs = server.declarative.scan_errors()
check("A4c scan_errors 记录坏包", any(e.get("pkg") == "broken_pkg" for e in errs),
      f"errs={errs}")
os.remove(os.path.join(broken_dir, 'manifest.json')); os.rmdir(broken_dir)

# ================= 汇总 =================
print("\n==== 汇总 ====")
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, detail in PASS:
    print("  PASS", name)
for name, detail in FAIL:
    print("  FAIL", name, "|", detail)
shutil.rmtree(TMP_ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)
