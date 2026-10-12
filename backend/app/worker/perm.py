"""PermissionGate 五作用域（P3，t7 §4.2）：fs/network/subprocess/secret/env 默认 deny-all。

按 manifest.permission 声明放行；未声明即零权限。worker 内唯一出口：
  - fs_read/fs_write  → rel 解析 realpath 后必须落在 roots 内（拒绝 ..、symlink 逃逸、盘符）
  - http（网络）      → permission.network.allow 白名单（通配符 "*" 生效 + 显式本地服务
                        声明放行）+ 元数据面拒绝未声明地址（SSRF 防护）
  - spawn（子进程）   → permission.subprocess.argv_allowlist 前缀匹配（无 shell 语义）
  - resolve_secret    → ref ∈ permission.secret.refs
  - hello env         → 仅注入 permission.env.vars 声明项（绝不传 os.environ 全文）

拒绝语义：抛 PermissionDenied（worker 端转换为 PLUGIN_PERMISSION_DENIED + 审计事件）。

网络门策略（t29，P0）：
  1) `network.allow:["*"]` 通配真实生效——已声明通配时任何 http/https URL 先过
     network.deny（精确覆盖），再按 URL 主机段判定：
       - 显式声明的本地服务（127.0.0.1/localhost/::1 + 声明端口）放行；
       - 未声明的环回/链路本地/私网地址仍拒绝（SSRF 防护保留）；
       - 公网地址放行（通配语义）。
  2) 白名单具体匹配（非通配）：域名/主机/前缀。对本地服务（127.0.0.1/localhost/
     ::1）先按「显式本地服务声明」（allow 中 host[:port] 或纯 host）匹配；命中即
     放行；未命中且未声明通配 → 拒绝。公网仍按原前缀匹配。
  3) 未声明 network.allow → 全部拒绝（默认 deny）。
  4) network.deny 优先级最高：URL 前缀命中 deny → 恒拒绝（含通配场景）。
"""
from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Dict, List, Optional, Tuple

SCOPES = ("fs", "network", "subprocess", "secret", "env")

# 元数据面恒拒绝（SSRF 防护，§4.2）：链路本地/环回/私有网段 + 任何主机形式 IP 拒绝。
# 该集合是「未声明时的默认拒绝」：仅当网络白名单显式声明对应本地/私有主机时才会放行
# （显式声明 = 用户/官方包信任该地址，见 check_network 判定顺序）。
# 注：正则仅覆盖常见 IPv4 形态；权威判定走 _host_class()（ipaddress 全类别）。
_META_BLOCKED_HOST_RE = re.compile(
    r"^(?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0|"
    r"(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))\.\d{1,3}\.\d{1,3}|"
    r"169\.254\.\d{1,3}\.\d{1,3})$"
)

# 本地服务主机集合：仅这些「环回/本机」地址允许被显式声明（allow 列出的 host[:port]）。
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host_class(host: str) -> str:
    """地址类别权威判定：loopback | nonpublic | public | hostname。

    - loopback：localhost/127.0.0.1/::1/0.0.0.0（显式声明才放行）
    - nonpublic：私网/链路本地/保留/未指定等非公网 IP（10/8、172.16-31/12、
      192.168/16、169.254/16、100.64/10 CGNAT、0.0.0.0/8 等；显式声明才放行）
    - public：公网单播 IP（ipaddress.is_global）
    - hostname：非 IP 主机名（按 allowlist 域名/子域匹配，不解析 DNS）
    """
    h = str(host).strip().strip("[]").lower()
    if h in _LOCAL_HOSTS:
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "hostname"
    if ip.is_global:
        return "public"
    return "nonpublic"


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


def _split_url(url: str) -> Tuple[str, str, int, str]:
    """解析 http/https URL → (scheme, host, port, path_with_query)。host 小写、去 IPv6 括号。"""
    scheme, _, rest = url.partition("://")
    hostport, _, path = rest.partition("/")
    if ":" in hostport and hostport.count(":") > 1:
        # IPv6：[::1]:8791 或 [::1]/path
        if hostport.startswith("["):
            hb, _, port_part = hostport[1:].partition("]")
            host = hb.lower()
            port = int(port_part[1:]) if port_part.startswith(":") and port_part[1:].isdigit() else 0
        else:
            host = hostport.lower()
            port = 0
    else:
        h, _, p = hostport.partition(":")
        host = h.lower().strip("[]")
        port = int(p) if p.isdigit() else 0
    return scheme, host, port, path


def _is_local_host(host: str) -> bool:
    """是否为环回/本机主机名（localhost、127.0.0.1、::1、0.0.0.0）。"""
    return host in _LOCAL_HOSTS


def _wildcard_declared(allow: List[str]) -> bool:
    """allow 列表是否声明了通配符 "*"（真实生效语义）。"""
    return any(str(x).strip() == "*" for x in allow)


def _matches_url_pattern(url: str, pat: str, *, _split=_split_url) -> bool:
    """白名单条目 → URL 匹配。

    支持形态（pat 小写归一后判定）：
      - "*"                                  → 通配（调用方先判 _wildcard_declared）
      - "https://api.example.com/v1"         → scheme+主机+路径前缀
      - "api.example.com" / "api.example.com/v1"（无 scheme）→ https 与 http 双 scheme 匹配
      - "127.0.0.1" / "127.0.0.1:3900" / "localhost:8791"    → 主机（[:port]）匹配
      - "[::1]:8791"                         → IPv6 环回（[:port]）匹配
      - "*.example.com"                      → 子域通配（*. 前缀；不含裸域本身）
    拒绝形态：路径中段匹配（"/v1/blocked" 不能命中 "api.example.com/v1" 之外）。
    """
    pat = str(pat).strip().lower()
    if not pat:
        return False
    scheme, host, port, path = _split(url)
    # 通配
    if pat == "*":
        return True
    # 本地服务（host 或 host:port；支持 [::1]:port 括号形态）
    host_part = ""
    rest = ""
    if pat.startswith("["):
        hb, _sep, hp = pat[1:].partition("]")
        host_part = hb
        rest = hp                       # ":8791" 或 ""
    else:
        hh, sep, hp = pat.partition(":")
        host_part = hh
        rest = (":" + hp) if sep else ""
    if _is_local_host(host_part):
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return False
        if host != host_part:
            return False
        if rest and rest[1:].isdigit() and port != int(rest[1:]):
            return False
        return True
    # 子域通配 *.example.com（仅子域，不含裸域本身）
    if pat.startswith("*."):
        suffix = pat[2:]
        return host.endswith("." + suffix)
    # 显式 scheme 前缀
    if pat.startswith(("http://", "https://")):
        return url.lower().startswith(pat)
    # 无 scheme：主机[:端口][/路径前缀] 双 scheme 匹配
    bare = pat
    if bare.startswith("*/"):
        bare = bare[2:]
    host2, _, path2 = bare.partition("/")
    port2 = 0
    if host2.startswith("["):
        hb, _sep, hp = host2[1:].partition("]")
        host2 = hb
        if hp.startswith(":") and hp[1:].isdigit():
            port2 = int(hp[1:])
    elif ":" in host2 and host2.count(":") == 1:
        hh, _, hp = host2.partition(":")
        host2 = hh
        if hp and hp.isdigit():
            port2 = int(hp)
    if host2 and host != host2:
        return False
    if port2 and port != port2:
        return False
    if path2:
        # 路径必须作为 URL 路径的前缀（从 path 起点比较）
        return path.startswith(path2) or (path2 == "/" and True)
    return True


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
        # 网络白名单（域名/主机[:端口]/路径前缀/通配）
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
    @staticmethod
    def _explicit(pat: str) -> bool:
        """是否为「显式声明」条目（非 * 通配）。显式条目可解锁本地/私网地址。"""
        p = str(pat).strip()
        return p != "*" and not p.startswith("*/") and not p.lower().startswith(("http://*", "https://*"))

    def check_network(self, url: str) -> str:
        """网络出口：仅 http/https + permission.network.allow 白名单；未声明恒拒。

        判定顺序（t29 修复版）：
          1) scheme 必须是 http/https；
          2) network.allow 未声明 → 拒绝（默认 deny）；
          3) URL 命中 network.deny 前缀 → 拒绝（deny 优先）；
          4) 环回/本机地址（localhost/127.0.0.1/::1/0.0.0.0）：
               - 仅当 allow 中显式声明（host 或 host:port，如 127.0.0.1:3900）→ 放行；
               - 否则拒绝（含 "*" 通配场景——通配不隐含信任本地服务，SSRF 防护保留）；
          5) 非公网 IP（私网/链路本地/保留等，10.*/192.168.*/172.16-31.*/169.254.*
             100.64/10 CGNAT 等）：
               - 仅当 allow 中显式声明该主机 → 放行；
               - 否则拒绝（SSRF 防护保留；"*" 通配不解锁）；
          6) 公网 IP 与主机名：allow 声明 "*" 通配 → 放行；否则具体前缀/子域匹配 → 放行；
          7) 其余 → 拒绝（不在 network.allow）。
        """
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise PermissionDenied("network", url, "仅 http/https 出口")
        scheme, host, port, _ = _split_url(url)
        if not self.network_allow:
            raise PermissionDenied("network", url, "未声明 network.allow（默认 deny）")
        # deny 优先：URL 前缀命中恒拒绝
        for deny in self.network_deny:
            d = str(deny).lower().strip()
            if d and url.lower().startswith(d):
                raise PermissionDenied("network", url, "命中 network.deny")
        cls = _host_class(host)
        # 环回/非公网 IP：仅显式声明（host 或 host:port）放行；通配不放行
        if cls in ("loopback", "nonpublic"):
            for pat in self.network_allow:
                p = str(pat).strip().lower()
                if p and self._explicit(p) and _matches_url_pattern(url, p):
                    return url
            hint = "本地服务未在 network.allow 显式声明" if cls == "loopback" \
                else "元数据面/私网地址未在 network.allow 显式声明"
            raise PermissionDenied("network", url, "%s（SSRF 防护）" % hint)
        # 公网 IP 与主机名：通配放行
        if _wildcard_declared(self.network_allow):
            return url
        # 具体白名单匹配
        for pat in self.network_allow:
            p = str(pat).strip().lower()
            if p and _matches_url_pattern(url, p):
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

    # ---------- 网络门状态报告（t29：worker 模式依赖检查） ----------
    def network_gate_report(self, url: str = "") -> Dict[str, Any]:
        """网络门生效情况：allow/deny/通配/本地服务声明 + 对给定 URL 的判定。

        供 worker 模式依赖检查（healthcheck/诊断）直接消费；不抛异常——
        判定结果以 allow/deny 布尔与 reason 表达。
        """
        allow = list(self.network_allow)
        deny = list(self.network_deny)
        wildcard = _wildcard_declared(allow)
        local_declared: List[str] = []
        for p in allow:
            norm = str(p).strip().lower().lstrip("*/")
            host = norm.split(":", 1)[0]
            if _is_local_host(host):
                local_declared.append(norm)
        local_declared = sorted(set(local_declared))
        verdict = ""
        if not allow:
            verdict = "deny: 未声明 network.allow（默认 deny）"
        elif url:
            try:
                self.check_network(url)
                verdict = "allow"
            except PermissionDenied as e:
                verdict = "deny: %s" % e.detail
        return {
            "allow": allow, "deny": deny, "wildcard": wildcard,
            "local_services": local_declared,
            "url": url, "verdict": verdict,
        }