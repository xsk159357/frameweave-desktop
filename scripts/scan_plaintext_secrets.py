#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""明文凭据扫描（仓库门禁 + 本地自查）。

背景：2026-10-19 全量审阅发现 `.tmp/` 下 32 个一次性部署脚本含韩国服务器 root
明文口令（审计 A12 遗留）。本脚本把「明文凭据不得入库」变成可执行门禁。

用法：
    python scripts/scan_plaintext_secrets.py            # 扫仓库（默认）
    python scripts/scan_plaintext_secrets.py --root .   # 指定根
    python scripts/scan_plaintext_secrets.py --all      # 连 _archive/ 一起扫（归档区默认跳过）

退出码：0 = 无命中；1 = 有命中（CI 失败）；2 = 用法/路径错误。

检测类别：
    ssh_password    password/passwd/pwd 字面量（>=6 字符）
    sshpass         sshpass -p <字面量>
    github_token    gh[pousr]_* / github_pat_*
    cloud_key       sk-* / AKIA* / LTAI*
    admin_key       admin_key 字面量
    private_key     BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".obf", ".obf_main", ".uvcache", ".wheels", ".pipcache", ".npm-cache",
    ".cache", ".dl", ".electron-cache", "release", "models", "user_nodes",
    "_archive", "Python", ".tmp",
}
TEXT_EXT = {
    ".py", ".ps1", ".psm1", ".sh", ".bat", ".cmd", ".js", ".cjs", ".mjs",
    ".ts", ".tsx", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env",
    ".md", ".txt", ".nsi", ".nsh", ".spec",
}
# 文档/示例里允许出现的占位符（小写比较）
PLACEHOLDERS = {
    "", "password", "passwd", "pwd", "none", "null", "***", "<redacted>",
    "your_password", "your-password", "changeme", "xxxxxx", "******",
    "${password}", "$password", "%password%", "@password",
}
PATTERNS = {
    "ssh_password": re.compile(r"(?i)\b(?:password|passwd|pwd)\s*=\s*(['\"])([^'\"]{6,})\1"),
    "sshpass": re.compile(r"(?i)\bsshpass\b[^\n]{0,30}?\s-p\s*(['\"]?)([^\s'\"]{6,})\1"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "cloud_key": re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|LTAI[0-9A-Za-z]{12,})\b"),
    "admin_key": re.compile(r"(?i)\badmin_?key\s*=\s*(['\"])([^'\"]{6,})\1"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
}


def iter_files(root: str, include_archive: bool):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS or (include_archive and d == "_archive")
        ]
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            yield os.path.join(dirpath, name)


def scan(root: str, include_archive: bool) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in iter_files(root, include_archive):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for kind, pat in PATTERNS.items():
            for m in pat.finditer(text):
                literal = ""
                if m.lastindex and m.lastindex >= 2:
                    literal = (m.group(2) or "").strip()
                    if literal.lower() in PLACEHOLDERS or set(literal) <= {"*", "x", "X", "."}:
                        continue
                line = text.count("\n", 0, m.start()) + 1
                findings.append((os.path.relpath(path, root), line, kind))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="明文凭据扫描")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    help="扫描根目录（默认仓库根）")
    ap.add_argument("--all", action="store_true", help="包含 _archive/ 归档区")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"[secret-scan] 根目录不存在: {root}")
        return 2

    findings = scan(root, args.all)
    if not findings:
        print(f"[secret-scan] OK 无明文凭据（root={root}）")
        return 0

    print(f"[secret-scan] FAIL 命中 {len(findings)} 处明文凭据：")
    for path, line, kind in findings[:50]:
        print(f"  {path}:{line}  [{kind}]")
    if len(findings) > 50:
        print(f"  ... 另有 {len(findings) - 50} 处")
    print("[secret-scan] 处置：改为环境变量/凭据管理器读取，并把历史脚本移入 _archive/")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
