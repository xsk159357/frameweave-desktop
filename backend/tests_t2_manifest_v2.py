# -*- coding: utf-8 -*-
"""t2 插件规范强化与大插件支持 验收测试。

验收项：
  R1 坏 manifest 显式错误码（G1-G10 门 → PluginManifestError/scan_errors/上传响应 code）
  R2 v1 兼容通道（读入注入默认值不写盘；hello 风格 v1 包可安装，磁盘 manifest 原样）
  R3 大包可通过上传安装（>旧 50MB 上限的 zip 在 2GB 新上限下上传+安装成功；
     总量/单文件/成员数超限显式拒绝——与 tests_t7_limits.py 共用 worker/limits.py）
  R4 SDK 校验正反例（check_manifest.py 正/反例退出码、package_plugin.py 打包通过）
  R5 商城流式下载 + sha256 校验（file:// 直链离线验证）
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_AUDIT_LOG",
                      os.path.join(tempfile.gettempdir(), "fw_t2_audit_test.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402
from app import server  # noqa: E402
from app.plugin import manifest_v2 as mv  # noqa: E402
from app.plugin.errors import PluginManifestError  # noqa: E402
from app.worker import limits  # noqa: E402

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))

TMP_ROOT = tempfile.mkdtemp(prefix="fw_t2_")
tmp_nodes = os.path.join(TMP_ROOT, "user_nodes")
os.makedirs(tmp_nodes, exist_ok=True)
server.declarative.set_user_nodes_dir(tmp_nodes)
server.declarative.scan()

SDK_DIR = r"F:\1223\解说工坊\frameweave-plugins\sdk"

GOOD_V2 = {
    "schema_version": 2, "engine_api": "1.0", "type_id": "user/demo",
    "title": "Demo", "category": "用户节点", "description": "d", "version": "1.0.0",
    "kind": "transform", "inputs": [{"name": "a", "type": "STRING"}],
    "outputs": [{"name": "out", "type": "STRING"}], "params": [],
    "transform": {"out": {"op": "upper", "source": {"$input": "a"}}},
    "permission": {"fs": {"write": ["scratch/**"]}},
    "dependencies": [{"id": "ffmpeg", "version": ">=6", "channel": "bundle", "optional": True}],
    "ui": {"entry": "ui/index.js", "sandbox": {"allow": []}, "capabilities": ["readonly"]},
}

def make_zip(members, top="pkg_demo", manifest=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "/", "")
        if manifest is not None:
            zf.writestr(top + "/manifest.json", json.dumps(manifest))
        for rel, data in members:
            zf.writestr(top + "/" + rel, data)
    return buf

def write_tmp_zip(buf):
    fd, path = tempfile.mkstemp(suffix=".zip", dir=TMP_ROOT)
    with os.fdopen(fd, "wb") as f:
        f.write(buf.getvalue())
    return path

def expect_code(name, manifest, code):
    try:
        mv.validate_manifest(manifest)
        check(name, False, "未抛出")
    except PluginManifestError as ex:
        check(name, ex.code == code, f"code={ex.code}")

# ================= R1: G1-G10 显式错误码 =================
print("\n==== R1 G1-G10 门显式错误码 ====")
expect_code("G1 schema_version=3", {**GOOD_V2, "schema_version": 3}, "manifest_schema_version")
expect_code("G1 engine_api 缺失", {k: v for k, v in GOOD_V2.items() if k != "engine_api"},
            "manifest_engine_api")
expect_code("G2 前缀未登记", {**GOOD_V2, "type_id": "zzz/x"}, "manifest_type_id")
expect_code("G2 core 无签名", {**GOOD_V2, "type_id": "core/x"}, "manifest_type_id")
expect_code("G3 version 非 semver", {**GOOD_V2, "version": "1.0"}, "manifest_version")
expect_code("G4 kind 未知", {**GOOD_V2, "kind": "hybrid"}, "manifest_kind")
expect_code("G4 entrypoint 逃逸", {**GOOD_V2, "kind": "python", "runtime": "python",
                                   "entrypoint": "../evil.py", "class_name": "X"}, "manifest_kind")
expect_code("G5 端口缺失", {k: v for k, v in GOOD_V2.items() if k != "inputs"},
            "manifest_ports")
expect_code("G5 端口类型未注册", {**GOOD_V2, "inputs": [{"name": "a", "type": "FLOAT64"}]},
            "manifest_ports")
expect_code("G5 端口重名", {**GOOD_V2, "inputs": [{"name": "a", "type": "STRING"},
                                                  {"name": "a", "type": "STRING"}]}, "manifest_ports")
expect_code("G6 未知 op", {**GOOD_V2, "transform": {"out": {"op": "eval", "source": {"$input": "a"}}}},
            "manifest_op")
expect_code("G6 transform 键不在 outputs", {**GOOD_V2, "transform": {"nope": {"op": "upper", "source": {"$input": "a"}}}},
            "manifest_op")
expect_code("G6 媒体端口禁入 transform", {**GOOD_V2, "inputs": [{"name": "v", "type": "VIDEO"}],
                                          "transform": {"out": {"op": "upper", "source": {"$input": "v"}}}},
            "manifest_op")
expect_code("G7 permission 未知作用域", {**GOOD_V2, "permission": {"exec": {}}}, "manifest_permission")
expect_code("G8 dependency 通道未知", {**GOOD_V2, "dependencies": [{"id": "a", "version": "1", "channel": "apt"}]},
            "manifest_dependencies")
expect_code("G8 ui entry 逃逸", {**GOOD_V2, "ui": {"entry": "../evil.js"}}, "manifest_ui")
expect_code("G10 signature 结构非法", {**GOOD_V2, "signature": {"alg": "ed25519"}}, "manifest_signature")
expect_code("v2 严格 未知顶层字段", {**GOOD_V2, "bogus": 1}, "manifest_unknown_field")

# ================= R2: v1 兼容通道（注入不写盘） =================
print("\n==== R2 v1 兼容通道 ====")
V1_HELLO = {
    "type_id": "user/hello", "title": "问候", "category": "用户节点", "description": "x",
    "version": "1.0.0", "kind": "transform",
    "inputs": [{"name": "name", "type": "STRING", "label": "名字", "required": True}],
    "outputs": [{"name": "greeting", "type": "STRING"}, {"name": "length", "type": "INT"}],
    "params": [{"name": "prefix", "type": "STRING", "default": "你好"}],
    "transform": {"greeting": {"op": "concat", "sources": [{"$param": "prefix"}, {"$input": "name"}], "sep": "，"},
                  "length": {"op": "len", "source": {"$input": "name"}}},
}
r = mv.validate_manifest(V1_HELLO)
check("R2 v1 manifest 通过（compat=True）", r.compat is True and not r.issues, f"compat={r.compat}")
check("R2 注入 schema_version=2", r.manifest.get("schema_version") == 2,
      f"sv={r.manifest.get('schema_version')}")
check("R2 注入 engine_api=1.0", r.manifest.get("engine_api") == "1.0",
      f"ea={r.manifest.get('engine_api')}")
check("R2 注入 permission={}", r.manifest.get("permission") == {},
      f"perm={r.manifest.get('permission')}")
check("R2 原始输入不被改写（不写盘前提）",
      V1_HELLO.get("schema_version") is None and V1_HELLO.get("engine_api") is None,
      f"sv={V1_HELLO.get('schema_version')} ea={V1_HELLO.get('engine_api')}")

# v1 shorthand op 归一化（{$concat: [...]}）
V1_SHORTHAND = dict(V1_HELLO)
V1_SHORTHAND["transform"] = {"greeting": {"$concat": [{"$param": "prefix"}, {"$input": "name"}]},
                             "length": {"op": "len", "source": {"$input": "name"}}}
r3 = mv.validate_manifest(V1_SHORTHAND)
check("R2 v1 shorthand $concat 归一化",
      r3.manifest["transform"]["greeting"].get("op") == "concat"
      and isinstance(r3.manifest["transform"]["greeting"].get("sources"), list),
      f"norm={r3.manifest['transform']['greeting']}")

# v1 包安装：磁盘 manifest 不被改写（不写盘）
v1_zip = make_zip([("node.py", b"x")], top="pkg_v1", manifest=V1_HELLO)
v1_path = write_tmp_zip(v1_zip)
dest = server.declarative.install_zip(v1_path)
on_disk = json.load(open(os.path.join(dest, "manifest.json"), encoding="utf-8"))
check("R2 v1 包安装成功", os.path.isdir(dest) and "user/hello" in [s.type_id for s in server.declarative.list_specs()],
      f"dest={dest}")
check("R2 安装后磁盘 manifest 仍无 schema_version（不写盘）",
      on_disk.get("schema_version") is None and on_disk.get("engine_api") is None,
      f"on_disk keys 含 sv? {on_disk.get('schema_version')}")

# v1 python 包无 runtime 字段（仅 entrypoint/class_name）→ kind 探测为 python 分支加载
V1_PY = {"type_id": "user/pyonly", "title": "P", "category": "用户节点", "description": "d",
         "version": "1.0.0", "entrypoint": "node.py", "class_name": "PyNode"}
py_zip = make_zip([("node.py", b"class PyNode:\n    async def run(self, ctx, i, p):\n        return {}\n")],
                  top="pkg_py", manifest=V1_PY)
py_dest = server.declarative.install_zip(write_tmp_zip(py_zip))
from app.declarative import get_class  # noqa: E402
py_cls = get_class("user/pyonly")
check("R2 v1 python 包（无 runtime）按 kind 探测加载为 python 插件",
      py_cls is not None and py_cls.__name__.startswith("Plugin_"),
      f"cls={py_cls.__name__ if py_cls else None}")

# ================= R3: 大包上传安装（>50MB 旧上限） =================
print("\n==== R3 大包可通过上传安装 ====")
check("R3 上限已提升（上传 2GB）", limits.MAX_UPLOAD_MB == 2048
      and limits.UPLOAD_MAX_BYTES == 2048 * 1024 * 1024, f"MAX_UPLOAD_MB={limits.MAX_UPLOAD_MB}")
check("R3 解压上限 8GB/单文件 4GB/成员 20000",
      limits.ZIP_TOTAL_UNCOMPRESSED == 8 * 1024 * 1024 * 1024
      and limits.ZIP_SINGLE_FILE == 4 * 1024 * 1024 * 1024
      and limits.ZIP_MEMBER_COUNT == 20000,
      f"total={limits.ZIP_TOTAL_UNCOMPRESSED} single={limits.ZIP_SINGLE_FILE} members={limits.ZIP_MEMBER_COUNT}")

# 构造 52MB（ZIP_STORED 使 zip 本体 >50MB，旧上限会 413，新上限 2GB 放行）
big_buf = io.BytesIO()
with zipfile.ZipFile(big_buf, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("bigpkg/", "")
    zf.writestr("bigpkg/manifest.json", json.dumps(GOOD_V2))
    zf.writestr("bigpkg/model.bin", b"\x07" * (52 * 1024 * 1024))
zip_bytes = big_buf.getvalue()
check("R3 大 zip 本体 >50MB（旧上限）", len(zip_bytes) > 50 * 1024 * 1024,
      f"size={len(zip_bytes)}")
client = TestClient(server.app)
r = client.post("/api/user_nodes/install",
                files={"file": ("big.zip", zip_bytes, "application/zip")},
                headers={"X-FW-Local-Token": "dev-local-token"})
body = r.json()
check("R3 大包上传安装成功", r.status_code == 200 and body.get("ok") is True,
      f"status={r.status_code} body={str(body)[:200]}")
check("R3 大包节点已注册", "user/demo" in [s.type_id for s in server.declarative.list_specs()])

# 坏 manifest 经上传接口 → 显式 manifest_* 错误码
bad_mf = {**GOOD_V2, "type_id": "zzz/x"}
bad_zip = make_zip([("node.py", b"x")], manifest=bad_mf)
r2 = client.post("/api/user_nodes/install",
                 files={"file": ("bad.zip", bad_zip.getvalue(), "application/zip")},
                 headers={"X-FW-Local-Token": "dev-local-token"})
b2 = r2.json()
check("R3 坏 manifest 上传 → 显式 manifest_type_id 错误码",
      r2.status_code == 200 and b2.get("ok") is False and b2.get("code") == "manifest_type_id",
      f"body={str(b2)[:200]}")

# install_zip 直连：坏 manifest → PluginManifestError(manifest_ports)
bad_zip2 = make_zip([("node.py", b"x")], top="pkg_bad2",
                    manifest={k: v for k, v in GOOD_V2.items() if k != "outputs"})
try:
    server.declarative.install_zip(write_tmp_zip(bad_zip2))
    check("R3 install_zip 坏 manifest 拒绝", False, "未抛出")
except PluginManifestError as ex:
    check("R3 install_zip 坏 manifest 拒绝（manifest_ports）",
          ex.code == "manifest_ports", f"code={ex.code}")

# ================= R4: SDK 校验正反例 =================
print("\n==== R4 SDK 正反例 ====")
sdk_good_dir = os.path.join(TMP_ROOT, "sdk_good")
sdk_bad_dir = os.path.join(TMP_ROOT, "sdk_bad")
sdk_v1_dir = os.path.join(TMP_ROOT, "sdk_v1")
for d in (sdk_good_dir, sdk_bad_dir, sdk_v1_dir):
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "node.py"), "w", encoding="utf-8").write("class X:\n    pass\n")
json.dump(GOOD_V2, open(os.path.join(sdk_good_dir, "manifest.json"), "w", encoding="utf-8"))
json.dump({**GOOD_V2, "type_id": "zzz/x", "transform": {"out": {"op": "eval"}}},
          open(os.path.join(sdk_bad_dir, "manifest.json"), "w", encoding="utf-8"))
json.dump(V1_HELLO, open(os.path.join(sdk_v1_dir, "manifest.json"), "w", encoding="utf-8"))

def run_sdk(*args):
    py = sys.executable
    return subprocess.run([py, os.path.join(SDK_DIR, args[0]), *args[1:]],
                          capture_output=True, text=True, timeout=120)

r_good = run_sdk("check_manifest.py", os.path.join(sdk_good_dir, "manifest.json"))
check("R4 SDK 正例 exit 0", r_good.returncode == 0, f"rc={r_good.returncode} out={r_good.stdout[-200:]}")
r_bad = run_sdk("check_manifest.py", os.path.join(sdk_bad_dir, "manifest.json"))
check("R4 SDK 反例 exit 1 + FAILED 输出",
      r_bad.returncode == 1 and "FAILED" in r_bad.stdout,
      f"rc={r_bad.returncode} out={r_bad.stdout[-300:]}")
r_v1 = run_sdk("check_manifest.py", os.path.join(sdk_v1_dir, "manifest.json"))
check("R4 SDK v1 兼容正例 exit 0 + 警告",
      r_v1.returncode == 0 and "v1 兼容" in r_v1.stdout,
      f"rc={r_v1.returncode} out={r_v1.stdout[-200:]}")
r_pkg = run_sdk("package_plugin.py", sdk_good_dir, "-o", os.path.join(TMP_ROOT, "dist"))
check("R4 SDK package_plugin 打包通过", r_pkg.returncode == 0 and "OK" in r_pkg.stdout,
      f"rc={r_pkg.returncode} out={r_pkg.stdout[-200:]}")
check("R4 SDK 打包产出 zip + sha256",
      os.path.isfile(os.path.join(TMP_ROOT, "dist", "sdk_good.zip")) and "sha256" in r_pkg.stdout,
      f"out={r_pkg.stdout[-200:]}")
r_pkg_bad = run_sdk("package_plugin.py", sdk_bad_dir, "-o", os.path.join(TMP_ROOT, "dist2"))
check("R4 SDK 坏 manifest 拒绝打包 exit 1",
      r_pkg_bad.returncode == 1 and "拒绝" in r_pkg_bad.stdout,
      f"rc={r_pkg_bad.returncode} out={r_pkg_bad.stdout[-200:]}")

# ================= R5: 商城流式下载 + sha256 校验 =================
print("\n==== R5 商城流式下载与 sha256 ====")
from app.market import MarketService  # noqa: E402
svc = MarketService(os.path.join(TMP_ROOT, "mdata"))
dl_zip = make_zip([("node.py", b"x")], top="m_pkg", manifest=GOOD_V2)
dl_path = write_tmp_zip(dl_zip)
import hashlib
sha = hashlib.sha256(open(dl_path, "rb").read()).hexdigest()
it = {"id": "t2dl01", "download_url": "file://" + dl_path.replace("\\", "/"),
      "sha256": sha, "size": os.path.getsize(dl_path)}
got = svc._fetch_zip(it, tmp_nodes)
check("R5 流式下载成功（file:// 直链）", got is not None and os.path.isfile(got) and zipfile.is_zipfile(got),
      f"got={got}")
it_bad = dict(it); it_bad["sha256"] = "0" * 64
got2 = svc._fetch_zip(it_bad, tmp_nodes)
check("R5 sha256 不匹配 → 拒绝并清理", got2 is None
      and not os.path.exists(os.path.join(tmp_nodes, "_dl_t2dl01.zip")),
      f"got2={got2}")

# ================= 汇总 =================
print("\n==== 汇总 ====")
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, detail in PASS:
    print("  PASS", name)
for name, detail in FAIL:
    print("  FAIL", name, "|", detail)
shutil.rmtree(TMP_ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)
