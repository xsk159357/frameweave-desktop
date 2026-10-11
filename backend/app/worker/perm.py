"""PermissionGate 五作用域（P3，t7 §4.2）：fs/network/subprocess/secret/env 默认 deny-all。

按 manifest.permission 声明放行；未声明即零权限。worker 内唯一出口：
  - fs_read/fs_write  → rel 解析 realpath 后必须落在 roots 内（拒绝 ..、symlink 逃逸、盘符）
  - http（网络）      → permission.network.allow 域名/路径前缀白名单 + 元数据面恒拒绝
  - spawn（子进程）   → permission.subprocess.argv_allowlist 前缀匹配（无 shell 语义）
  - resolve_secret    → ref ∈ permission.secret.refs
  - hello env         → 仅注入 permission.env.vars 声明项（绝不传 os.environ 全文）

拒绝语义：抛 PermissionDenied（worker 端转换为 PLUGIN_PERMISSION_DENIED + 审计事件）。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

SCOPES = ("fs", "network", "subprocess", "secret", "env")

# 元数据面恒拒绝（SSRF 防护，§4.2）：链路本地/环回/私有网段 + 任何主机形式 IP 拒绝
_META_BLOCKED_HOST_RE = re.compile(
    r"^(?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0|"
    r"(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}|"
    r"169\.254\.\d{1,3}\.\d{1,3})$"
)


class PermissionDenied(Exception):
    """越权拒绝（带作用域与资源描述）。"""

    def __init__(self, scope: str, resource: str, detail: str = ""):
        super().__init__(f"[{scope}] 权限拒绝: {resource}" + (f"（{detail}）" if detail else ""))
        self.scope = scope
        self.resource = resource
        self.detail = detail


def _norm_scope_list(permission: Dict[str, Any], scope: str, key: str) -> List[str]:
    val = (permission.get(scope) or {}).get(key) or []
    if not isinstance(val, list):
        return []
    return [str(x) for x in val]


class PermissionGate:
    """按 manifest.permission 声明构建的只读权限门（worker 内执行体）。

    roots: {"pkg": 只读包目录, "scratch": 读写落地根, "in": 只读输入, "out": 写输出}
    env_declared: permission.env.vars 声明项 -> host 注入值（由 supervisor 预解析）
    """

    def __init__(self, manifest: Dict[str, Any], roots: Dict[str, str],
                 env_values: Optional[Dict[str, str]] = None,
                 trust_level: str = "L2"):
        self._manifest = manifest or {}
        self._permission = self._manifest.get("permission") or {}
        if not isinstance(self._permission, dict):
            self._permission = {}
        self.roots: Dict[str, str] = {k: os.path.abspath(str(v)) for k, v in (roots or {}).items()}
        self.trust_level = trust_level
        # fs 写白名单（manifest.permission.fs.write：glob 模式相对 scratch/out）
        self.fs_write_patterns: List[str] = _norm_scope_list(self._permission, "fs", "write")
        # 网络白名单（域名/路径前缀）
        self.network_allow: List[str] = _norm_scope_list(self._permission, "network", "allow")
        self.network_deny: List[str] = _norm_scope_list(self._permission, "network", "deny")
        # 子进程 argv 白名单（前缀匹配）
        self.argv_allowlist: List[str] = _norm_scope_list(self._permission, "subprocess", "argv_allowlist")
        self.subprocess_enabled: bool = bool((self._permission.get("subprocess") or {}).get("enabled"))
        # secret 白名单
        self.secret_refs: List[str] = _norm_scope_list(self._permission, "secret", "refs")
        # env：仅声明项（值来自 host 配置，绝不 os.environ 全文）
        self.env_declared: Dict[str, str] = {
            k: str(v) for k, v in (env_values or {}).items()
            if k in _norm_scope_list(self._permission, "env", "vars")
        }

    # ---------- fs ----------
    def resolve_fs(self, rel: str, base_key: str = "pkg") -> str:
        """把 worker 相对路径解析为 roots 内绝对路径（拒绝 ..、盘符、symlink 逃逸）。"""
        base = self.roots.get(base_key)
        if not base:
            raise PermissionDenied("fs", rel, f"roots.{base_key} 未配置")
        if isinstance(rel, bytes):
            rel = rel.decode("utf-8", "replace")
        rel = str(rel).replace("\\", "/")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise PermissionDenied("fs", rel, "绝对路径拒绝")
        target = os.path.abspath(os.path.join(base, rel))
        if target != base and not target.startswith(base + os.sep):
            raise PermissionDenied("fs", rel, "路径逃逸 roots")
        real = os.path.realpath(target)
        rbase = os.path.realpath(base)
        if real != rbase and not real.startswith(rbase + os.sep):
            raise PermissionDenied("fs", rel, "symlink/realpath 逃逸 roots")
        return real

    def fs_read(self, rel: str) -> str:
        """读权限：roots.pkg（及 in/）只读默认；其余一律拒绝。"""
        for key in ("pkg", "in"):
            if self._inside(rel, key):
                return self.resolve_fs(rel, key)
        raise PermissionDenied("fs", rel, "仅包目录与输入区可读")

    def fs_write(self, rel: str) -> str:
        """写权限：仅 manifest.permission.fs.write 声明模式（相对 scratch/out）放行。"""
        if not self.fs_write_patterns:
            raise PermissionDenied("fs", rel, "未声明 fs.write（默认 deny）")
        norm = rel.replace("\\", "/")
        hit = False
        for pat in self.fs_write_patterns:
            p = pat.replace("\\", "/").lstrip("./")
            if p == norm or (p.endswith("**") and norm.startswith(p[:-2].rstrip("/") + "/")) \
                    or (p.endswith("/*") and norm.startswith(p[:-1])):
                hit = True
                break
        if not hit:
            raise PermissionDenied("fs", rel, f"不在 fs.write 白名单: {self.fs_write_patterns}")
        return self.resolve_fs(rel, "scratch")

    def _inside(self, rel: str, key: str) -> bool:
        base = self.roots.get(key)
        if not base:
            return False
        try:
            target = os.path.abspath(os.path.join(base, rel.replace("\\", "/")))
        except Exception:  # noqa: BLE001
            return False
        return target == base or target.startswith(base + os.sep)

    # ---------- network ----------
    def check_network(self, url: str) -> str:
        """网络出口：仅 http/https + permission.network.allow 白名单；元数据面恒拒绝。"""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise PermissionDenied("network", url, "仅 http/https 出口")
        host = url.split("/")[2].split(":")[0].strip("[]").lower()
        if _META_BLOCKED_HOST_RE.match(host):
            raise PermissionDenied("network", url, "元数据面地址恒拒绝（SSRF 防护）")
        if not self.network_allow:
            raise PermissionDenied("network", url, "未声明 network.allow（默认 deny）")
        for pat in self.network_allow:
            p = str(pat).lower().lstrip("*/")
            if p and url.lower().startswith(p if p.startswith("http") else "https://" + p) \
                    or (p and "://" not in p and url.lower().split("://", 1)[1].startswith(p)):
                if any(deny and url.lower().startswith(deny) for deny in self.network_deny):
                    raise PermissionDenied("network", url, "命中 network.deny")
                return url
        raise PermissionDenied("network", url, f"不在 network.allow: {self.network_allow}")

    # ---------- subprocess ----------
    def check_argv(self, argv: Any) -> List[str]:
        """子进程：argv 必须匹配 permission.subprocess.argv_allowlist 前缀（无 shell）。"""
        if not isinstance(argv, (list, tuple)) or not argv:
            raise PermissionDenied("subprocess", str(argv), "argv 必须为非空数组")
        if not self.subprocess_enabled:
            raise PermissionDenied("subprocess", str(argv), "未声明 subprocess.enabled（默认 deny）")
        cmd = str(argv[0]).replace("\\", "/")
        base = os.path.basename(cmd)
        base_noext = os.path.splitext(base)[0]
        for pat in self.argv_allowlist:
            p = str(pat).replace("\\", "/")
            if p == cmd or p == base:                     # 精确（全路径 或 basename）
                return list(argv)
            if p.endswith("**"):
                stem = p[:-2].rstrip("/")
                if cmd == stem or cmd.startswith(stem + "/"):
                    return list(argv)
            if p.endswith("*") and (cmd.startswith(p[:-1]) or base.startswith(p[:-1])):
                return list(argv)
            if os.path.splitext(p)[0] == base_noext and "/" not in p:  # 无扩展名匹配
                return list(argv)
        raise PermissionDenied("subprocess", cmd, f"不在 argv_allowlist: {self.argv_allowlist}")

    # ---------- secret ----------
    def check_secret(self, ref: str) -> str:
        """secret：ref 必须在 permission.secret.refs 白名单（"*"=任意官方节点声明）。"""
        if not isinstance(ref, str) or not ref.strip():
            raise PermissionDenied("secret", str(ref), "ref 必须为非空字符串")
        if "*" in self.secret_refs:
            return ref
        if ref not in self.secret_refs:
            raise PermissionDenied("secret", ref, f"不在 secret.refs: {self.secret_refs}")
        return ref

    # ---------- env ----------
    def allowed_env(self) -> Dict[str, str]:
        """hello 阶段允许注入的环境变量（仅 permission.env.vars 声明项）。"""
        return dict(self.env_declared)

    # ---------- 审计 ----------
    def deny_info(self, e: PermissionDenied) -> Dict[str, Any]:
        return {"scope": e.scope, "resource": e.resource, "detail": e.detail,
                "trust": self.trust_level}
