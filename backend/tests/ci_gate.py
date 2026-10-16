# -*- coding: utf-8 -*-
"""S1-T9 CI 门禁入口（R-平台-9）。

门禁顺序（任一失败则退出码 1）：
  1. pytest -q tests/（引擎/节点/API：prompt/object_info/queue/ws）；
  2. declarative.scan() 错误数为零（插件 manifest G1-G10 + 后端加载层）；
  3. core/ 官方包 manifest Ed25519 签名全部通过 L0 锚；
  4. sdk/check_all.py 全量 manifest G1-G10。

本地：
    cd FrameWeave/backend
    python tests/ci_gate.py

插件目录解析：
  - FRAMEWEAVE_PLUGINS_DIR 环境变量优先；
  - 默认尝试 workspace/frameweave-plugins/packages；
  - 若独立插件仓库不在 checkout，则退回 backend/user_nodes（发布 CI 可在
    workflow 先 checkout 插件仓库并设置 FRAMEWEAVE_PLUGINS_DIR）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
WORKSPACE = BACKEND.parents[1]
PLUGIN_ROOT = WORKSPACE / "frameweave-plugins"
PLUGIN_PACKAGES = Path(os.environ.get("FRAMEWEAVE_PLUGINS_DIR", "")) \
    if os.environ.get("FRAMEWEAVE_PLUGINS_DIR") else PLUGIN_ROOT / "packages"
if not PLUGIN_PACKAGES.is_dir():
    PLUGIN_PACKAGES = BACKEND / "user_nodes"
SDK = PLUGIN_PACKAGES.parent / "sdk" if (PLUGIN_PACKAGES.parent / "sdk").is_dir() else None


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    print("[gate] RUN:", " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(cwd or BACKEND), text=True)
    print("[gate] exit:", p.returncode)
    return p.returncode


def gate_pytest() -> bool:
    # pytest 配置已关闭 cacheprovider，避免受限沙箱 .pytest_cache 写入失败。
    return _run([sys.executable, "-m", "pytest", "-q"], cwd=BACKEND) == 0


def gate_scan() -> bool:
    os.environ["FRAMEWEAVE_USER_NODES"] = str(PLUGIN_PACKAGES)
    sys.path.insert(0, str(BACKEND))
    try:
        from app import declarative
        count = declarative.scan()
        errors = declarative.scan_errors()
    except Exception as exc:  # noqa: BLE001
        print(f"[gate] FAIL scan exception: {type(exc).__name__}: {exc}")
        return False
    print(f"[gate] scan packages={PLUGIN_PACKAGES} loaded={count} errors={len(errors)}")
    if errors:
        for err in errors[:20]:
            print("  ERROR", err)
        return False
    return True


def gate_signatures() -> bool:
    """官方 core/ 包必须携带可由 L0 锚验签的 Ed25519 signature。"""
    sys.path.insert(0, str(BACKEND))
    try:
        from app import signatures
        anchor = signatures.official_pubkey_b64()
    except Exception as exc:  # noqa: BLE001
        print(f"[gate] FAIL signature setup: {type(exc).__name__}: {exc}")
        return False
    if not anchor:
        print("[gate] FAIL: official L0 public-key anchor missing")
        return False
    failed = []
    checked = 0
    for pkg in sorted(PLUGIN_PACKAGES.iterdir() if PLUGIN_PACKAGES.is_dir() else []):
        mp = pkg / "manifest.json"
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            failed.append((pkg.name, f"manifest parse: {exc}"))
            continue
        if not str(m.get("type_id", "")).startswith("core/"):
            continue
        checked += 1
        sig = m.get("signature") or {}
        if not isinstance(sig, dict) or not sig.get("value"):
            failed.append((pkg.name, "core/ manifest unsigned"))
            continue
        try:
            ok = signatures.verify_manifest_signature_b64(m, anchor)
        except Exception as exc:  # noqa: BLE001
            ok = False
            failed.append((pkg.name, f"verify exception: {exc}"))
        if not ok:
            failed.append((pkg.name, "L0 anchor verification failed"))
    print(f"[gate] signatures core_checked={checked} failed={len(failed)}")
    for name, reason in failed[:20]:
        print("  FAIL", name, reason)
    return not failed


def gate_sdk() -> bool:
    check = (PLUGIN_PACKAGES.parent / "sdk" / "check_all.py")
    if not check.is_file():
        # 独立 frameweave-plugins 未 checkout 时，manifest_v2 scan 已覆盖门禁；
        # 不把缺失外部仓库误报为失败，workflow 会在插件 checkout 后启用此项。
        print("[gate] sdk/check_all.py unavailable (external plugin repo absent): SKIP")
        return True
    return _run([sys.executable, str(check), str(PLUGIN_PACKAGES)], cwd=BACKEND) == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FrameWeave S1-T9 CI gate")
    ap.add_argument("--skip-pytest", action="store_true", help="仅跑 scan/signature/sdk 门禁")
    args = ap.parse_args()
    results = []
    if not args.skip_pytest:
        results.append(("pytest", gate_pytest()))
    results.extend([
        ("scan", gate_scan()),
        ("signatures", gate_signatures()),
        ("sdk", gate_sdk()),
    ])
    print("[gate] SUMMARY", " ".join(f"{name}={'PASS' if ok else 'FAIL'}" for name, ok in results))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())