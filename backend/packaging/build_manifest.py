# -*- coding: utf-8 -*-
"""P3/t3 打包期完整性清单生成器（在 PyInstaller 构建完成后、electron-builder 之前运行）。

用法：
  python packaging/build_manifest.py [dist_root] [out_path] [integrity_key?]
  默认  dist_root = packaging/dist/backend-dist
        out_path  = <dist_root>/integrity.manifest（随安装包分发，与 exe 同目录）
  密钥  FRAMEWEAVE_INTEGRITY_KEY 环境变量优先，否则参数，否则内嵌默认（构建期一致即可；
        打包版运行时用同一默认/环境值验证）。

产物：backend-dist/integrity.manifest（JSON：enc/body/sig/nonce）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
sys.path.insert(0, _ROOT)

from app.integrity import _integrity_key, build_manifest  # noqa: E402


def main() -> int:
    dist_root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _ROOT, "packaging", "dist", "backend-dist")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(dist_root, "integrity.manifest")
    if len(sys.argv) > 3:
        os.environ["FRAMEWEAVE_INTEGRITY_KEY"] = sys.argv[3]
    if not os.path.isdir(dist_root):
        print(f"[build-manifest] 错误：dist 目录不存在 {dist_root}")
        return 2
    key = _integrity_key()
    m = build_manifest(dist_root, out_path, key)
    n = len(m.get("body", ""))
    print(f"[build-manifest] 已生成 {out_path}（清单体 {n} 字节，enc={m.get('enc')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())