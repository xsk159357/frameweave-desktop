# -*- coding: utf-8 -*-
"""内置基础节点种子生成器（t6，M7 供应链缺口根治）。

打包前置步骤：从 backend/user_nodes 提取 24 个官方 core 基础节点，构建
backend/packaging/build/builtin_basics/（随包内置种子树；PyInstaller spec 的
datas 引用它，打包产物落 _internal/builtin_basics/<pkg>/），并生成
seed_manifest.json（逐包 type_id/version/manifest_sha256/文件 sha256 +
官方锚 key_id；发布私钥在场时对清单本体签名——审计链，非加载门）。

签名纪律（不得绕过签名）：
  - 每个基础节点 manifest 必须先通过 manifest v2 强校验（G1-G10，与 scan 同门）；
  - 必须以官方公钥锚（backend/app/fw_official_pubkey.json）验签通过
    （signature.key_id == 锚 key_id 且 ed25519 签名有效）；
  - 任一包验签失败 → 整个构建中止（退出码非 0），不产出部分种子。

用法：
  python packaging/build_seed.py [--out <dir>] [--source <user_nodes_dir>]
  默认 out    = backend/packaging/build/builtin_basics
       source = backend/user_nodes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
sys.path.insert(0, _ROOT)

# 官方 core 基础节点种子清单（与 t7 服务器商城 core 基础组 B 组一致，24 项）
BASIC_NODE_DIRS: List[str] = [
    "core_asset_ref",
    "core_audio_info",
    "core_bool_input",
    "core_bool_not",
    "core_console_output",
    "core_float_input",
    "core_gate",
    "core_group",
    "core_image_info",
    "core_int_input",
    "core_json_array",
    "core_json_input",
    "core_json_merge",
    "core_json_output",
    "core_json_set",
    "core_list_get",
    "core_list_length",
    "core_path_input",
    "core_select",
    "core_subgraph",
    "core_text_concat",
    "core_text_input",
    "core_text_length",
    "core_video_info",
]

_SEED_NAME = "builtin-basics"
_SEED_SCHEMA = 1
# 种子树只携带运行所需文件：排除字节码缓存与安装事务目录
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".staging", ".backup")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_seed(source_dir: str, out_dir: str) -> Dict[str, Any]:
    """构建内置基础节点种子树；任何验签失败 → SystemExit(非 0)。

    返回 seed_manifest 字典（已落盘 <out_dir>/seed_manifest.json）。
    """
    from app.plugin import manifest_v2  # noqa: PLC0415
    from app import signatures as sg  # noqa: PLC0415

    anchor = sg.load_official_anchor() or {}
    anchor_b64 = str(anchor.get("public_key") or "")
    anchor_kid = str(anchor.get("key_id") or "")
    if not anchor_b64 or not anchor_kid:
        print("[seed-build] 错误：官方公钥锚缺失/为空（fw_official_pubkey.json）", file=sys.stderr)
        raise SystemExit(3)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    packages: List[Dict[str, Any]] = []
    for name in BASIC_NODE_DIRS:
        src = os.path.join(source_dir, name)
        mpath = os.path.join(src, "manifest.json")
        if not os.path.isdir(src) or not os.path.isfile(mpath):
            print(f"[seed-build] 错误：基础节点缺失 {name}（{mpath}）", file=sys.stderr)
            raise SystemExit(4)
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = manifest_v2.validate_manifest(data)  # G1-G10 结构门（scan 同门）
        norm = result.manifest
        sig = data.get("signature") if isinstance(data.get("signature"), dict) else {}
        type_id = str(norm.get("type_id") or "")
        version = str(norm.get("version") or "")
        # 不绕过签名：core/ 前缀 + 官方锚 key_id + ed25519 验签通过
        if not (type_id.startswith("core/")
                and sig.get("key_id") == anchor_kid
                and sg.verify_manifest_signature_b64(data, anchor_b64)):
            print(f"[seed-build] 错误：基础节点验签失败 {name}（type_id={type_id}）", file=sys.stderr)
            raise SystemExit(5)
        dst = os.path.join(out_dir, name)
        shutil.copytree(src, dst, ignore=_IGNORE)
        files: Dict[str, str] = {}
        for dirpath, _dirs, filenames in os.walk(dst):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, dst).replace("\\", "/")
                files[rel] = _sha256_file(full)
        packages.append({
            "dir": name,
            "type_id": type_id,
            "version": version,
            "manifest_sha256": _sha256_file(mpath),
            "files": files,
        })
        for w in result.warnings:
            print(f"[seed-build] {name}: {w}")
        print(f"[seed-build] 已入种子: {name} ({type_id} v{version}) {len(files)} 文件")

    seed_manifest: Dict[str, Any] = {
        "schema_version": _SEED_SCHEMA,
        "seed": _SEED_NAME,
        "official": True,
        "anchor_key_id": anchor_kid,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(packages),
        "packages": packages,
    }
    # 发布私钥在场 → 对种子清单本体签名（审计链；加载门=逐包验签，不依赖此签名）
    priv = sg.load_private_key()
    if priv is not None:
        seed_manifest = sg.sign_manifest(seed_manifest, priv, sg.official_public_key())
        print("[seed-build] 种子清单已用官方私钥签名")
    else:
        print("[seed-build] 警告：官方私钥缺失，种子清单未签名（审计链降级；逐包验签不受影响）")
    out_manifest = os.path.join(out_dir, "seed_manifest.json")
    with open(out_manifest, "w", encoding="utf-8") as f:
        json.dump(seed_manifest, f, ensure_ascii=False, indent=2)
    print(f"[seed-build] ✅ 种子树已生成: {out_dir}（{len(packages)} 包）")
    print(f"[seed-build]   清单: {out_manifest}")
    return seed_manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 FrameWeave 内置基础节点种子树（t6）")
    ap.add_argument("--out", default=os.path.join(_ROOT, "packaging", "build", "builtin_basics"),
                    help="种子树输出目录（PyInstaller datas 引用）")
    ap.add_argument("--source", default=os.path.join(_ROOT, "user_nodes"),
                    help="基础节点源目录（backend/user_nodes）")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    build_seed(args.source, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
