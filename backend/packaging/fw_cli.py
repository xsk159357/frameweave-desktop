# -*- coding: utf-8 -*-
"""FrameWeave 后端命令行工具入口。

用法：
    python packaging/fw_cli.py env-check [--json]
    python packaging/fw_cli.py env-check --json   # 机器可读输出

`fw env-check` = 首次运行依赖检查向导（t32）：ffmpeg/ffprobe/Python/模型/本地服务/
SecretStore/权限门/数据目录，每项给可复制修复命令（下载/放置/接线三步）。
"""
from __future__ import annotations

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
sys.path.insert(0, _ROOT)


def cmd_env_check(args) -> int:
    from app.plugin import envcheck
    if args.json:
        print(json.dumps(envcheck.run_envcheck(), ensure_ascii=False, indent=2))
        return 0
    return envcheck.main(argv=[])


def main() -> int:
    ap = argparse.ArgumentParser(description="FrameWeave 后端 CLI")
    sub = ap.add_subparsers(dest="command", required=True)

    p_env = sub.add_parser("env-check", help="首次运行依赖检查向导")
    p_env.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    p_env.set_defaults(func=cmd_env_check)

    args = ap.parse_args()
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001
        print(f"[fw-cli] {args.command} 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())