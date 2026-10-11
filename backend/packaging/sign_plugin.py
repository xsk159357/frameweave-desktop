# -*- coding: utf-8 -*-
"""插件签名打包工具（P3 第一阶段）：对插件包目录签名并打 zip + archive 签名。

用法：
  python packaging/sign_plugin.py <pkg_dir> [-o <out_dir>] [--key-id <hex>]
      [--over manifest|archive] [--no-archive-sig]
      [--gen-keypair <私钥落盘目录>] [--print-pubkey]

流程：
  1. 密钥：backend/data/sign-keys/signing.key（默认，仓库外）已有则加载；
     没有则生成并落盘（私钥绝不入库）。--gen-keypair <dir> 显式生成到指定目录。
  2. 读 <pkg_dir>/manifest.json → 对"剥离 signature 后"的 JCS 规范化体签 Ed25519
     （over=manifest）→ 写回 manifest["signature"]。
  3. 打 zip（顶层目录 = pkg 名，复用 SDK package 上限校验，纯标准库）。
  4. archive 签名：对 zip 整体字节 sha256 → 再签（over=archive）→ 默认写
     <zip>.sig 文件（JSON {alg,key_id,value,over}，随发布分发）；manifest 内
     signature 保持 over=manifest。
  5. 输出 sha256 / size / pubkey b64（发布表单登记 publisher_pubkey /
     archive_signature 用）。

密钥安全：私钥仅打包/发布机持有，绝不写入仓库或日志；本工具不打印私钥。
"""
from __future__ import annotations

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import argparse
import hashlib
import json
import os
import sys
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
sys.path.insert(0, _ROOT)

from app import signatures as sg  # noqa: E402


def _walk_safe(base: str):
    """逐文件枚举；成员一律相对 base（复用 SDK package 语义）。"""
    base_abs = os.path.abspath(base)
    for root, _dirs, files in os.walk(base_abs):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, base_abs).replace("\\", "/")
            if rel.startswith("..") or os.path.isabs(rel):
                raise ValueError(f"非法相对路径: {rel}")
            yield rel, full


def _zip_bytes(zip_path: str) -> bytes:
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def package(pkg_dir: str, out_dir: str, key_path: str = "",
            key_id_hex: str = "", over: str = "manifest",
            write_archive_sig: bool = True) -> dict:
    """签名 + 打包。返回产物信息（zip/sha256/size/pubkey_b64/archive_sig）。"""
    base_abs = os.path.abspath(pkg_dir)
    mp = os.path.join(base_abs, "manifest.json")
    if not os.path.isfile(mp):
        raise ValueError(f"{pkg_dir} 缺少 manifest.json")
    with open(mp, "r", encoding="utf-8") as f:
        m = json.load(f)
    # 密钥：--gen-keypair 目录或默认 key_dir；缺则生成并落盘（仓库外）
    if key_path:
        with open(key_path, "rb") as f:
            priv = sg.serialization.load_pem_private_key(f.read(), password=None)
    else:
        priv = sg.ensure_private_key()
    pub = priv.public_key()
    pubkey_b64 = sg.pubkey_b64(pub)
    # 1) manifest 签名（over=manifest；签名体=剥离 signature 后的 JCS 规范化字节）
    signed = sg.sign_manifest(m, priv, pub, over="manifest")
    if key_id_hex:
        signed["signature"]["key_id"] = key_id_hex
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(signed, f, ensure_ascii=False, indent=2)
    # 2) 打 zip（顶层目录 = pkg 名）
    pkg_name = os.path.basename(base_abs.rstrip("/\\")) or "plugin"
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, pkg_name + ".zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in _walk_safe(base_abs):
            zf.write(full, pkg_name + "/" + rel)
    sha = _zip_bytes(out).hex()
    size = os.path.getsize(out)
    # 3) archive 签名（对 zip 整体字节签；独立 .sig 文件）
    archive_sig = sg.sign_archive_signature(out, priv, pub)
    sig_path = ""
    if write_archive_sig:
        sig_path = out + ".sig"
        with open(sig_path, "w", encoding="utf-8") as f:
            json.dump(archive_sig, f, ensure_ascii=False)
    return {"zip": out, "bytes": size, "sha256": sha, "pkg": pkg_name,
            "pubkey_b64": pubkey_b64, "key_id": archive_sig["key_id"],
            "archive_sig": archive_sig, "sig_path": sig_path}


def main() -> int:
    ap = argparse.ArgumentParser(description="插件签名打包（Ed25519：manifest + archive）")
    ap.add_argument("pkg_dir", help="插件包目录（含 manifest.json）")
    ap.add_argument("-o", "--out", default="dist", help="输出目录（默认 dist/）")
    ap.add_argument("--key", default="", help="私钥 PEM 路径（默认 backend/data/sign-keys/signing.key，缺失自动生成）")
    ap.add_argument("--key-id", default="", help="key_id 覆盖（默认=公钥 sha256 前 16 hex）")
    ap.add_argument("--gen-keypair", default="", help="显式生成新密钥对到该目录（私钥落盘，仓库外）")
    ap.add_argument("--no-archive-sig", action="store_true", help="不写 .sig archive 签名文件")
    ap.add_argument("--print-pubkey", action="store_true", help="打印发布用公钥/archive 签名登记信息")
    args = ap.parse_args()
    try:
        if args.gen_keypair:
            d = os.path.abspath(args.gen_keypair)
            os.makedirs(d, exist_ok=True)
            priv, pub = sg.generate_keypair()
            with open(os.path.join(d, "signing.key"), "wb") as f:
                f.write(priv.private_bytes(
                    encoding=sg.serialization.Encoding.PEM,
                    format=sg.serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=sg.serialization.NoEncryption()))
            sg.save_public_key_pem(pub, os.path.join(d, "signing.pub.pem"))
            print(f"[sign] 已生成密钥对到 {d}（私钥仅发布机持有，绝不入库）")
            print(f"  pubkey_b64 = {sg.pubkey_b64(pub)}")
            print(f"  key_id     = {sg.key_id(pub)}")
            return 0
        r = package(args.pkg_dir, args.out, args.key, args.key_id,
                    write_archive_sig=not args.no_archive_sig)
    except ValueError as ex:
        print(f"[sign] 拒绝: {ex}")
        return 1
    print(f"[sign] OK: {r['pkg']}")
    print(f"  zip      : {r['zip']}")
    print(f"  size     : {r['bytes']} bytes")
    print(f"  sha256   : {r['sha256']}")
    print(f"  key_id   : {r['key_id']}")
    print(f"  pubkey   : {r['pubkey_b64']}")
    if r["sig_path"]:
        print(f"  .sig     : {r['sig_path']}（archive 签名，随发布分发）")
    if args.print_pubkey or True:
        print("[sign] 发布表单登记（publisher_pubkey / archive_signature 字段）:")
        print(f"  publisher_pubkey = {r['pubkey_b64']}")
        print(f"  archive_signature = {r['archive_sig']['value']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())