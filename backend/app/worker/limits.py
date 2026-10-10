"""上传/解压安全参数与审计（P0.4 U1-U4）。

依据《万物插件化_改造_运行时安全隔离.md》§6.1：
  上传预检（Content-Length -> 413 + 流式截断）、解压五上限（总量/单文件/成员数/
  压缩比/特殊成员）、staging 原子安装、失败审计事件。

三入口（server.py 上传、market.py 下载安装、declarative.install_zip）唯一引用本模块，
杜绝参数旁路。
"""
from __future__ import annotations

import json
import os
import stat
import time
from typing import Any, Optional


# ---------------------------------------------------------------- 上限参数
# 参数表（唯一真源；别名 upload_max_bytes 等与文档表对齐）
# 大插件支持（t2，规范 v3）：上传 50MB→2GB、解压总量 500MB→8GB、单文件 200MB→4GB、
# 成员 4096→20000；压缩比 100:1 与流式块 1MB 不变（字节级累计防声明撒谎）。
MAX_UPLOAD_MB = 2048
UPLOAD_MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024          # 上传包体积上限（zip 本体）
upload_max_bytes = UPLOAD_MAX_BYTES

ZIP_TOTAL_UNCOMPRESSED = 8 * 1024 * 1024 * 1024         # 解压总量上限（逐文件累计）
zip_total_uncompressed = ZIP_TOTAL_UNCOMPRESSED
ZIP_SINGLE_FILE = 4 * 1024 * 1024 * 1024                # 单成员上限
zip_single_file = ZIP_SINGLE_FILE
ZIP_MEMBER_COUNT = 20000                                # 成员数上限
zip_member_count = ZIP_MEMBER_COUNT
ZIP_RATIO = 100                                         # 压缩比上限（累计 uncompressed/zip_size）
zip_ratio = ZIP_RATIO
ZIP_BLOCK = 1024 * 1024                                 # 解压流式块大小（实际字节级累计用）

# ---------------------------------------------------------------- 错误码
ERR_NOT_ZIP = "not_zip"
ERR_ZIP_TOO_LARGE = "zip_too_large"          # >2GB（上传 413）或 zip 本体超限
ERR_MEMBER_COUNT = "member_count_exceeded"   # 成员数 > 20000
ERR_ZIP_BOMB = "zip_bomb_ratio"              # 压缩比 > 100:1
ERR_TOTAL_SIZE = "total_size_exceeded"       # 解压总量 > 8GB
ERR_FILE_SIZE = "file_size_exceeded"         # 单文件 > 4GB
ERR_ENCRYPTED = "encrypted_member"           # 加密成员拒绝
ERR_SYMLINK = "symlink_member"               # symlink/hardlink 成员拒绝
ERR_SPECIAL = "special_member"               # 设备/管道等特殊成员拒绝
ERR_TRAVERSAL = "path_traversal"             # 目录穿越（zip-slip）
ERR_EMPTY = "empty_zip"                      # 空包
ERR_LAYOUT = "pkg_layout"                    # 顶层目录非法/缺失单一顶层
ERR_MANIFEST = "bad_manifest"                # manifest 缺失或结构非法
ERR_STAGING = "staging_failed"               # staging 事务失败
ERR_INSTALL = "install_failed"               # 其他安装失败


class PluginInstallError(Exception):
    """带错误码的插件安装失败（scan/install 显式报错误码，不静默吞异常）。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# ---------------------------------------------------------------- 审计事件
_AUDIT_PATH: Optional[str] = None


def set_audit_path(path: Optional[str]) -> None:
    """设置审计日志路径（server 启动时指向 DATA_DIR/audit_log.jsonl）。"""
    global _AUDIT_PATH
    _AUDIT_PATH = path


def _default_audit_path() -> str:
    env = os.environ.get("FRAMEWEAVE_AUDIT_LOG")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))          # .../backend/app/worker
    return os.path.join(os.path.dirname(os.path.dirname(here)), "data", "audit_log.jsonl")


def audit(event: str, **fields: Any) -> None:
    """追加一条审计事件（JSONL）；审计失败不影响主流程。"""
    path = _AUDIT_PATH or _default_audit_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec: dict = {"ts": time.time(), "event": event}
        rec.update(fields)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 上传预检
def upload_over_limit(content_length: Optional[str]) -> bool:
    """Content-Length 预检：缺失/非法视为不受信，由流式截断兜底。"""
    if not content_length:
        return False
    try:
        return int(content_length) > UPLOAD_MAX_BYTES
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- zip 成员判定
def special_member_kind(info) -> Optional[str]:
    """按 external_attr 判定特殊成员：symlink/hardlink/reparse/设备等 -> 种类名。

    §6.1 zip_special_members：拒绝 symlink/hardlink（external_attr 判定）、加密成员。
    """
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode:
        fmt = stat.S_IFMT(unix_mode)
        if fmt == stat.S_IFLNK:
            return "symlink"
        # 无类型位（0o600 等纯权限位，Windows/zipfile.writestr 常见）按普通文件处理；
        # 仅当显式设置了非常规类型（字符/块设备、FIFO、socket）才判特殊
        if fmt and fmt not in (stat.S_IFREG, stat.S_IFDIR):
            return "special"
    # Windows 属性低位：FILE_ATTRIBUTE_REPARSE_POINT = 0x400（符号链接/交接点）
    if (info.external_attr & 0xFFFF) & 0x400:
        return "reparse"
    return None


def member_is_encrypted(info) -> bool:
    """zip 加密标志（general purpose bit 0）。"""
    return bool(info.flag_bits & 0x1)
