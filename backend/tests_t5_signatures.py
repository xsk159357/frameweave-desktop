# -*- coding: utf-8 -*-
"""P3 第一阶段 插件签名链 验收测试（backend/app/signatures.py）。

验收项：
  S1 Ed25519 签名/验签正反例（manifest：JCS 规范化 + 剥离 signature）
  S2 篡改后验签失败（manifest 字段篡改 / zip 字节篡改 → archive 验签失败）
  S3 无签名 → 社区级 L2 警告（不拒绝）
  S4 core/* 官方强制验签（L0）：缺签名/无锚/验签失败 → 拒绝（ERR_SIGNATURE_VERIFY）
  S5 撤销命中 → 拒绝（manifest 校验 G10 + 安装管线）
  S6 安装管线集成：签名包可安装、篡改签名包拒绝、trust_pubkey（市场 L1）绑定验签
  S7 打包工具 sign_plugin.py / verify_plugin.py 端到端（实测签名→验签→篡改→验签失败）
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
                      os.path.join(tempfile.gettempdir(), "fw_t5_sig_audit.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402
from app import server  # noqa: E402
from app import signatures as sg  # noqa: E402
from app.plugin import manifest_v2 as mv  # noqa: E402
from app.plugin.errors import PluginManifestError  # noqa: E402
from app.worker import limits  # noqa: E402

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))

TMP_ROOT = tempfile.mkdtemp(prefix="fw_t5sig_")
tmp_nodes = os.path.join(TMP_ROOT, "user_nodes")
os.makedirs(tmp_nodes, exist_ok=True)
server.declarative.set_user_nodes_dir(tmp_nodes)
server.declarative.scan()

GOOD_V2 = {
    "schema_version": 2, "engine_api": "1.0", "type_id": "user/demo",
    "title": "Demo", "category": "用户节点", "description": "d", "version": "1.0.0",
    "kind": "transform", "inputs": [{"name": "a", "type": "STRING"}],
    "outputs": [{"name": "out", "type": "STRING"}], "params": [],
    "transform": {"out": {"op": "upper", "source": {"$input": "a"}}},
}

# 测试密钥对（与官方锚独立——测试用发布者 key）
PRIV, PUB = sg.generate_keypair()
PUB_B64 = sg.pubkey_b64(PUB)
KEY_ID = sg.key_id(PUB)

def make_zip(members, top="pkg_demo", manifest=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "/", "")
        if manifest is not None:
            zf.writestr(top + "/manifest.json", json.dumps(manifest, ensure_ascii=False))
        for rel, data in members:
            zf.writestr(top + "/" + rel, data)
    return buf

def write_tmp_zip(buf, name="pkg.zip"):
    fd, path = tempfile.mkstemp(suffix=".zip", dir=TMP_ROOT)
    with os.fdopen(fd, "wb") as f:
        f.write(buf.getvalue())
    return path

def expect_verify_reject(name, manifest, pubkey_b64, required=False, zip_path=None, code=limits.ERR_SIGNATURE_VERIFY):
    """安装管线级别：check_install_signature 必须拒绝。"""
    v = sg.check_install_signature(manifest, pubkey_b64, required=required, zip_path=zip_path)
    check(name, v.get("ok") is False, f"detail={v.get('detail')}")

# ================= S1: 签名/验签正反例 =================
print("\n==== S1 Ed25519 签名/验签正反例 ====")
sm = sg.sign_manifest(GOOD_V2, PRIV, PUB)
check("S1 签名后带 signature 块", isinstance(sm.get("signature"), dict)
      and sm["signature"]["alg"] == "ed25519" and sm["signature"]["over"] == "manifest"
      and sm["signature"]["key_id"] == KEY_ID and sm["signature"]["value"],
      f"sig={sm.get('signature')}")
check("S1 正例验签通过", sg.verify_manifest_signature(sm, PUB) is True)
check("S1 公钥 b64 验签通过", sg.verify_manifest_signature_b64(sm, PUB_B64) is True)

# 反例：错误公钥
OTHER_PRIV, OTHER_PUB = sg.generate_keypair()
check("S1 反例：错误公钥验签失败",
      sg.verify_manifest_signature(sm, OTHER_PUB) is False)
# 反例：缺 signature
check("S1 反例：无 signature 验签失败", sg.verify_manifest_signature(GOOD_V2, PUB) is False)
# 反例：over 非 manifest
bad_over = dict(sm); bad_over["signature"] = dict(sm["signature"]); bad_over["signature"]["over"] = "archive"
check("S1 反例：over=archive 不能当作 manifest 验",
      sg.verify_manifest_signature(bad_over, PUB) is False)

# JCS 规范化：键序无关（加字段改变签名体）
reordered = {k: v for k, v in reversed(list(GOOD_V2.items()))}
sm2 = sg.sign_manifest(reordered, PRIV, PUB)
check("S1 JCS 规范化：键序无关（重新排序后验签通过）",
      sg.verify_manifest_signature(sm2, PUB) is True)
canon_a = sg.jcs_canonicalize({"b": 1, "a": {"x": [1, 2], "y": "s"}})
canon_b = sg.jcs_canonicalize({"a": {"y": "s", "x": [1, 2]}, "b": 1})
check("S1 JCS 规范化：键升序幂等", canon_a == canon_b, f"{canon_a} vs {canon_b}")

# ================= S2: 篡改后验签失败 =================
print("\n==== S2 篡改验签失败 ====")
tampered = dict(sm); tampered["title"] = "EVIL"
check("S2 篡改 manifest 字段 → 验签失败",
      sg.verify_manifest_signature(tampered, PUB) is False)
tampered2 = dict(sm); tampered2["inputs"] = [{"name": "evil", "type": "SCRIPT"}]
check("S2 篡改端口声明 → 验签失败",
      sg.verify_manifest_signature(tampered2, PUB) is False)
# 剥离 signature 篡改（防移除签名攻击）
stripped = dict(sm); stripped.pop("signature", None)
check("S2 剥除 signature → 验签失败", sg.verify_manifest_signature(stripped, PUB) is False)

# archive 签名：zip 整体
zip_buf = make_zip([("node.py", b"print(1)")], manifest=sm)
zp = write_tmp_zip(zip_buf)
asig = sg.sign_archive_signature(zp, PRIV, PUB)
check("S2 archive 签名结构", asig["alg"] == "ed25519" and asig["over"] == "archive"
      and asig["key_id"] == KEY_ID and asig["value"])
check("S2 archive 正例验签通过", sg.verify_archive_signature(zp, asig["value"], PUB) is True)
# 篡改 zip 字节 → archive 验签失败
with open(zp, "rb") as f:
    data = bytearray(f.read())
data[-4:] = b"ZZZZ"  # 尾部 EOCD 注释区
tampered_zip = os.path.join(TMP_ROOT, "tampered.zip")
with open(tampered_zip, "wb") as f:
    f.write(bytes(data))
check("S2 篡改 zip 字节 → archive 验签失败",
      sg.verify_archive_signature(tampered_zip, asig["value"], PUB) is False,
      f"tampered={os.path.getsize(tampered_zip)} orig={os.path.getsize(zp)}")
check("S2 archive 错误公钥验签失败",
      sg.verify_archive_signature(zp, asig["value"], OTHER_PUB) is False)

# ================= S3: 无签名 L2 警告 =================
print("\n==== S3 无签名 L2 警告 ====")
r = mv.validate_manifest(GOOD_V2)
check("S3 无签名校验通过 + signed=False", r.signed is False and not r.issues)
check("S3 无签名含 G10 警告", any("未签名" in w for w in r.warnings), f"{r.warnings}")
# 安装管线：无签名 user/ 包安装成功（不拒绝）
plain_zip = make_zip([("node.py", b"print(1)")], manifest=GOOD_V2)
plain_dest = server.declarative.install_zip(write_tmp_zip(plain_zip))
check("S3 无签名社区包安装成功（L2 不拒绝）",
      os.path.isdir(plain_dest) and os.path.basename(plain_dest) == "pkg_demo",
      f"dest={plain_dest}")

# ================= S4: core/* 官方强制验签 =================
print("\n==== S4 core L0 强制验签 ====")
CORE_MF = {**GOOD_V2, "type_id": "core/demo", "title": "CoreDemo"}
# 缺签名 → manifest 校验 G10 已拒（type_id core 无 signature 占位）
try:
    mv.validate_manifest(CORE_MF)
    check("S4 core 缺签名 manifest 拒绝", False, "未抛出")
except PluginManifestError as ex:
    check("S4 core 缺签名 manifest 拒绝", ex.code == "manifest_type_id", f"code={ex.code}")
# 安装管线：无签名 core → 拒绝（即使 G10 未拦，管线 also 拒绝）
expect_verify_reject("S4 core 缺签名安装管线拒绝", CORE_MF, sg.official_pubkey_b64(),
                     required=True)

# core 有签名但密钥非官方锚 → 拒绝（安装管线 required + 非锚公钥）
core_signed_wrong = sg.sign_manifest(CORE_MF, OTHER_PRIV, OTHER_PUB)
expect_verify_reject("S4 core 用非官方密钥签名 → 拒绝",
                     core_signed_wrong, sg.official_pubkey_b64(), required=True)
# core 有签名且官方锚验签通过 → 放行（用官方锚公钥）
official_pub = sg.official_public_key()
official_b64 = sg.pubkey_b64(official_pub) if official_pub else ""
if official_pub is not None:
    # 用官方私钥签名（backend/data/sign-keys/signing.key）
    from cryptography.hazmat.primitives import serialization
    keyp = os.path.join(sg.key_dir(), "signing.key")
    with open(keyp, "rb") as f:
        official_priv = serialization.load_pem_private_key(f.read(), password=None)
    core_signed_ok = sg.sign_manifest(CORE_MF, official_priv, official_priv.public_key())
    v = sg.check_install_signature(core_signed_ok, official_b64, required=True)
    check("S4 core 官方签名 + 锚验签 → 放行", v.get("ok") is True and v.get("level") == "L1",
          f"detail={v.get('detail')}")
    # 篡改 core manifest → 拒绝
    core_tampered = dict(core_signed_ok); core_tampered["title"] = "EVIL"
    expect_verify_reject("S4 core 篡改后拒绝", core_tampered, official_b64, required=True)
    # 安装管线级：官方签名的 core 包可安装
    core_zip = make_zip([("node.py", b"print(1)")], top="pkg_core", manifest=core_signed_ok)
    core_dest = server.declarative.install_zip(write_tmp_zip(core_zip))
    check("S4 core 官方签名包安装成功", os.path.isdir(core_dest)
          and os.path.basename(core_dest) == "pkg_core", f"dest={core_dest}")
else:
    check("S4 core 官方签名 + 锚验签 → 放行（锚缺失）", False, "官方锚文件缺失")

# ================= S5: 撤销命中拒绝 =================
print("\n==== S5 撤销命中拒绝 ====")
rev_path = os.path.join(TMP_ROOT, "revocations.json")
with open(rev_path, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "all": False, "keys": {KEY_ID: "test-revoked"}}, f)
sg.set_revocations_path_for_test(rev_path)
try:
    # G10 门：撤销 key_id → 拒绝
    revoked_mf = dict(sm)
    try:
        mv.validate_manifest(revoked_mf)
        check("S5 manifest 校验撤销拒绝", False, "未抛出")
    except PluginManifestError as ex:
        check("S5 manifest 校验撤销拒绝", ex.code == "manifest_signature"
              and any("撤销" in i for i in ex.issues), f"code={ex.code} issues={ex.issues}")
    # 安装管线：撤销 → 拒绝
    expect_verify_reject("S5 安装管线撤销拒绝", revoked_mf, PUB_B64, required=False)
    expect_verify_reject("S5 安装管线撤销拒绝（core required）", revoked_mf, PUB_B64, required=True)
    # all=true 熔断
    with open(rev_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "all": True, "keys": {}}, f)
    expect_verify_reject("S5 all=true 熔断拒绝", sm, PUB_B64, required=False)
finally:
    sg.reset_revocations_path()

# ================= S6: 安装管线集成 + trust_pubkey =================
print("\n==== S6 安装管线集成（市场 L1 绑定验签） ====")
# 签名 user 包 + trust_pubkey（市场条目绑定发布者公钥）→ 安装成功
sig_zip = make_zip([("node.py", b"print(1)")], top="pkg_signed", manifest=sm)
sig_zip_path = write_tmp_zip(sig_zip)
dest_sig = server.declarative.install_zip(sig_zip_path, trust_pubkey=PUB_B64)
check("S6 签名包 + 绑定公钥安装成功",
      os.path.isdir(dest_sig) and os.path.basename(dest_sig) == "pkg_signed",
      f"dest={dest_sig}")

# 签名包 + 错误公钥（绑定另一个发布者）→ 拒绝
dest_wrong = None
OTHER_B64 = sg.pubkey_b64(OTHER_PUB)
try:
    server.declarative.install_zip(sig_zip_path, trust_pubkey=OTHER_B64)
    check("S6 错误公钥绑定 → 拒绝", False, "未抛出")
except limits.PluginInstallError as ex:
    check("S6 错误公钥绑定 → 拒绝", ex.code == limits.ERR_SIGNATURE_VERIFY, f"code={ex.code}")

# 签名包无 trust_pubkey（无锚）→ L2 降级警告安装
v_none = sg.check_install_signature(sm, None, required=False)
check("S6 有签名无锚 → L2 降级", v_none.get("ok") is True and v_none.get("level") == "L2",
      f"detail={v_none.get('detail')}")

# 篡改 manifest 的签名包 → 安装拒绝
tampered_sm = dict(sm); tampered_sm["title"] = "EVIL"
tampered_zip = make_zip([("node.py", b"print(1)")], top="pkg_tampered", manifest=tampered_sm)
try:
    server.declarative.install_zip(write_tmp_zip(tampered_zip), trust_pubkey=PUB_B64)
    check("S6 篡改签名包安装拒绝", False, "未抛出")
except limits.PluginInstallError as ex:
    check("S6 篡改签名包安装拒绝", ex.code == limits.ERR_SIGNATURE_VERIFY, f"code={ex.code}")

# archive over 签名：zip 内 manifest signature over=archive（对当前 zip 字节签名，
# 签名对象与 zip 原件分离传入——部署流 .sig/市场条目 archive_signature 同构）
arch_zip = make_zip([("node.py", b"print(1)")], top="pkg_arch", manifest=dict(GOOD_V2))
arch_zip_path = write_tmp_zip(arch_zip)
arch_sig = sg.sign_archive_signature(arch_zip_path, PRIV, PUB)
arch_mf = dict(GOOD_V2)
arch_mf["signature"] = arch_sig  # over=archive，value=对 arch_zip_path 字节的签名
v_arch = sg.check_install_signature(arch_mf, PUB_B64, required=False, zip_path=arch_zip_path)
check("S6 archive over 验签（zip 原件）", v_arch.get("ok") is True and v_arch.get("level") == "L1",
      f"detail={v_arch.get('detail')}")
# 篡改 archive zip → 拒绝
with open(arch_zip_path, "rb") as f:
    d = bytearray(f.read())
d[-4:] = b"ZZZZ"
tarch = os.path.join(TMP_ROOT, "tarch.zip")
with open(tarch, "wb") as f:
    f.write(bytes(d))
v_arch_t = sg.check_install_signature(arch_mf, PUB_B64, required=False, zip_path=tarch)
check("S6 archive over 篡改 → 拒绝", v_arch_t.get("ok") is False,
      f"detail={v_arch_t.get('detail')}")

# ================= S7: 打包工具端到端（实测） =================
print("\n==== S7 sign_plugin.py / verify_plugin.py 端到端 ====")
PKG_DIR = os.path.join(TMP_ROOT, "e2e_pkg")
os.makedirs(PKG_DIR, exist_ok=True)
open(os.path.join(PKG_DIR, "node.py"), "w", encoding="utf-8").write("class X:\n    pass\n")
json.dump({**GOOD_V2, "type_id": "user/e2e", "title": "E2E"},
          open(os.path.join(PKG_DIR, "manifest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

py = sys.executable
sign_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "packaging", "sign_plugin.py")
verify_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "packaging", "verify_plugin.py")
keydir = os.path.join(TMP_ROOT, "e2e_keys")
r_sign = subprocess.run([py, sign_script, PKG_DIR, "-o", os.path.join(TMP_ROOT, "e2e_dist"),
                         "--gen-keypair", keydir],
                        capture_output=True, text=True, timeout=120)
check("S7 sign_plugin --gen-keypair 生成密钥对",
      r_sign.returncode == 0 and os.path.isfile(os.path.join(keydir, "signing.key")),
      f"rc={r_sign.returncode} out={r_sign.stdout[-200:]}")
r_sign2 = subprocess.run([py, sign_script, PKG_DIR, "-o", os.path.join(TMP_ROOT, "e2e_dist"),
                          "--key", os.path.join(keydir, "signing.key")],
                         capture_output=True, text=True, timeout=120)
e2e_zip = os.path.join(TMP_ROOT, "e2e_dist", "e2e_pkg.zip")
check("S7 sign_plugin 签名打包 OK",
      r_sign2.returncode == 0 and os.path.isfile(e2e_zip)
      and os.path.isfile(e2e_zip + ".sig") and "OK" in r_sign2.stdout,
      f"rc={r_sign2.returncode} out={r_sign2.stdout[-300:]}")
# 解析 pubkey / archive sig
pub_b64 = ""
for line in r_sign2.stdout.splitlines():
    if line.strip().startswith("pubkey"):
        pub_b64 = line.split("=", 1)[1].strip()
    if line.strip().startswith("publisher_pubkey"):
        pub_b64 = line.split("=", 1)[1].strip()
check("S7 sign_plugin 输出公钥", len(pub_b64) == 44, f"pub_b64={pub_b64!r}")

# 验签通过
r_ver = subprocess.run([py, verify_script, e2e_zip, "--pubkey", pub_b64],
                       capture_output=True, text=True, timeout=120)
check("S7 verify_plugin 验签通过（manifest + archive）",
      r_ver.returncode == 0 and "通过" in r_ver.stdout,
      f"rc={r_ver.returncode} out={r_ver.stdout[-400:]}")
# 篡改 zip 字节 → 验签失败（archive 不匹配）
with open(e2e_zip, "rb") as f:
    d = bytearray(f.read())
d[-4:] = b"ZZZZ"
e2e_tampered = os.path.join(TMP_ROOT, "e2e_tampered.zip")
with open(e2e_tampered, "wb") as f:
    f.write(bytes(d))
r_ver_bad = subprocess.run([py, verify_script, e2e_tampered, "--pubkey", pub_b64],
                           capture_output=True, text=True, timeout=120)
check("S7 篡改 zip 字节 → verify_plugin 验签失败",
      r_ver_bad.returncode == 1 and "失败" in r_ver_bad.stdout,
      f"rc={r_ver_bad.returncode} out={r_ver_bad.stdout[-400:]}")
# 错误公钥 → 验签失败
r_ver_wrong = subprocess.run([py, verify_script, e2e_zip,
                              "--pubkey", sg.pubkey_b64(OTHER_PUB)],
                             capture_output=True, text=True, timeout=120)
check("S7 错误公钥 → verify_plugin 验签失败", r_ver_wrong.returncode == 1,
      f"rc={r_ver_wrong.returncode} out={r_ver_wrong.stdout[-300:]}")
# 官方锚验签 core 包（sign_plugin 用官方私钥签 core → verify --anchor）
core_pkg = os.path.join(TMP_ROOT, "core_pkg")
os.makedirs(core_pkg, exist_ok=True)
open(os.path.join(core_pkg, "node.py"), "w", encoding="utf-8").write("class X:\n    pass\n")
json.dump({**GOOD_V2, "type_id": "core/e2e", "title": "CoreE2E"},
          open(os.path.join(core_pkg, "manifest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
keyp = os.path.join(sg.key_dir(), "signing.key")
r_core = subprocess.run([py, sign_script, core_pkg, "-o", os.path.join(TMP_ROOT, "core_dist"),
                         "--key", keyp],
                        capture_output=True, text=True, timeout=120)
core_zip = os.path.join(TMP_ROOT, "core_dist", "core_pkg.zip")
r_core_ver = subprocess.run([py, verify_script, core_zip, "--anchor"],
                            capture_output=True, text=True, timeout=120)
check("S7 core 官方锚验签通过",
      r_core.returncode == 0 and r_core_ver.returncode == 0 and "通过" in r_core_ver.stdout,
      f"sign_rc={r_core.returncode} ver_rc={r_core_ver.returncode} out={r_core_ver.stdout[-300:]}")
# 安装 core 官方签名包（上传管线级）
r_core_install = server.declarative.install_zip(core_zip)
check("S7 core 官方签名包经 install_zip 安装成功", os.path.isdir(r_core_install),
      f"dest={r_core_install}")

# ================= 汇总 =================
print("\n==== 汇总 ====")
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, detail in PASS:
    print("  PASS", name)
for name, detail in FAIL:
    print("  FAIL", name, "|", detail)
shutil.rmtree(TMP_ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)