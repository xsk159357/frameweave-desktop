# -*- coding: utf-8 -*-
"""插件离线验签工具（P3 第一阶段）：对已签名的 zip / 包目录验 Ed25519 签名。

用法：
  python packaging/verify_plugin.py <zip 或 pkg_dir> [--pubkey <b64|PEM文件>]
      [--anchor] [--no-archive] [--verbose]

- --anchor：用 L0 官方锚公钥（fw_official_pubkey.json）验签（core 包推荐）。
- --pubkey：发布者公钥（原始 32B base64，或 PEM 文件路径）。
- 默认行为：先验 manifest 签名（zip 内 <pkg>/manifest.json，over=manifest）；
  zip 旁 <zip>.sig 存在时再验 archive 签名（zip 整体字节）。
- 退出码：0=全部通过；1=验签失败/非法；2=用法错误。

离线验签不依赖后端服务；发布者/审计方在任意装好 cryptography 的环境运行。
"""
from __future__ import annotations

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import argparse
import json
import os
import sys
import tempfile
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
sys.path.insert(0, _ROOT)

from app import signatures as sg  # noqa: E402


def _pubkey_from_arg(arg: str):
    """arg 是原始 base64 或 PEM 文件路径 → Ed25519PublicKey。"""
    if os.path.isfile(arg):
        with open(arg, "rb") as f:
            return sg.public_key_from_pem(f.read())
    return sg.pubkey_from_b64(arg)


def verify(target: str, pubkey_arg: str = "", use_anchor: bool = False,
           check_archive: bool = True, verbose: bool = False) -> int:
    pub = None
    if pubkey_arg:
        try:
            pub = _pubkey_from_arg(pubkey_arg)
        except ValueError as e:
            print(f"[verify] 公钥非法: {e}")
            return 1
    elif use_anchor:
        pub = sg.official_public_key()
        if pub is None:
            print("[verify] L0 官方锚缺失（fw_official_pubkey.json）")
            return 1

    zip_path = ""
    pkg_dir = ""
    if os.path.isdir(target):
        pkg_dir = target
    elif target.endswith(".zip"):
        zip_path = target
        pkg_dir = ""
    else:
        print(f"[verify] 目标必须是插件包目录或 .zip: {target}")
        return 2

    # 解压 zip 拿 manifest（离线验签：临时目录）
    tmp = ""
    if zip_path and not pkg_dir:
        if not zipfile.is_zipfile(zip_path):
            print(f"[verify] 不是有效 zip: {zip_path}")
            return 1
        tmp = tempfile.mkdtemp(prefix="fw_verify_")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = [n for n in zf.namelist()
                         if n.endswith("/manifest.json") or n == "manifest.json"]
                if not names:
                    print("[verify] zip 内无 manifest.json")
                    return 1
                name = sorted(names, key=lambda n: -len(n))[0]
                zf.extract(name, tmp)
                pkg_dir = os.path.dirname(os.path.join(tmp, name))
        except Exception as e:  # noqa: BLE001
            print(f"[verify] zip 解压失败（可能被篡改/损坏）: {e}")
            return 1
    mpath = os.path.join(pkg_dir, "manifest.json")
    if not os.path.isfile(mpath):
        print(f"[verify] 缺少 manifest.json: {mpath}")
        return 1
    with open(mpath, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    sig = manifest.get("signature")
    if not isinstance(sig, dict):
        print("[verify] manifest 未签名（无 signature）→ 社区级 L2，无法离线验签")
        return 1
    if sg.is_revoked(str(sig.get("key_id") or "")):
        print(f"[verify] 签名密钥已撤销（key_id={sig.get('key_id')}），拒绝")
        return 1
    if pub is None:
        print("[verify] 未提供公钥/锚（--pubkey 或 --anchor），无法验签")
        return 1

    over = sig.get("over")
    ok = False
    if over == sg.SIG_OVER_MANIFEST:
        ok = sg.verify_manifest_signature(manifest, pub)
        print(f"[verify] manifest 签名（over=manifest, key_id={sig.get('key_id')}）: "
              + ("通过" if ok else "失败——manifest 被篡改或签名不匹配"))
    elif over == sg.SIG_OVER_ARCHIVE:
        if not zip_path:
            print("[verify] over=archive 需要 zip 原件（传 zip 而非目录）")
            return 1
        ok = sg.verify_archive_signature(zip_path, str(sig.get("value") or ""), pub)
        print(f"[verify] archive 签名（over=archive, key_id={sig.get('key_id')}）: "
              + ("通过" if ok else "失败——zip 被篡改或签名不匹配"))
    else:
        print(f"[verify] signature.over 非法: {over!r}")
        return 1
    if not ok:
        return 1

    # 独立 .sig 文件（zip 旁）的 archive 验签（可选增强）
    if check_archive and zip_path and over == sg.SIG_OVER_MANIFEST:
        sigp = zip_path + ".sig"
        if os.path.isfile(sigp):
            try:
                with open(sigp, "r", encoding="utf-8") as f:
                    asig = json.load(f)
            except (OSError, json.JSONDecodeError):
                asig = None
            if isinstance(asig, dict) and asig.get("over") == sg.SIG_OVER_ARCHIVE:
                aok = sg.verify_archive_signature(zip_path, str(asig.get("value") or ""), pub)
                print(f"[verify] archive 签名（.sig 文件, key_id={asig.get('key_id')}）: "
                      + ("通过" if aok else "失败——zip 被篡改"))
                if not aok:
                    return 1
    if verbose:
        print(f"[verify] pubkey = {sg.pubkey_b64(pub)}")
        print(f"[verify] sha256 = {sg.sha256_bytes(zip_path).hex() if zip_path else 'n/a'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="插件离线验签（Ed25519）")
    ap.add_argument("target", help="插件包目录或 .zip")
    ap.add_argument("--pubkey", default="", help="发布者公钥（原始 base64 或 PEM 文件路径）")
    ap.add_argument("--anchor", action="store_true", help="用 L0 官方锚公钥验签")
    ap.add_argument("--no-archive", action="store_true", help="跳过 .sig archive 验签")
    ap.add_argument("--verbose", action="store_true", help="输出更多信息")
    args = ap.parse_args()
    return verify(args.target, args.pubkey, args.anchor,
                  check_archive=not args.no_archive, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())