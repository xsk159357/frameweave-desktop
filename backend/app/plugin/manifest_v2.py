"""manifest v2 强校验器（P1，t2）：G1-G10 校验门 + v1 兼容通道。

权威依据：schemas/manifest_v2.json（结构层）+ 本模块（跨字段语义层）。
被 declarative.scan / declarative.install_zip 唯一接入，替换旧的 _validate_package
基础门（declarative.py 校验门）。

- G1  schema_version / JSON 可解析 / engine_api 必填
- G2  type_id 格式 + 前缀白名单（core=官方签名包、user=任意、自定义需登记）/ 元字段
- G3  version semver / engine_api 与 min/max_engine_api 兼容性比较
- G4  kind ∈ {transform, python} / runtime / entrypoint / class_name 白名单
- G5  端口声明必填（inputs/outputs/params）+ PortSpec 合法（类型/widget/重名/媒体禁入）
- G6  transform op 白名单（递归）+ 键 ⊆ outputs + 媒体端口禁入
- G7  permission 五作用域（fs/network/subprocess/secret/env），未知键拒绝
- G8  dependencies（channel: pip|bundle|market）+ ui 声明（entry 须在 ui/ 子树内）
- G9  大小/成员/压缩比上限 —— 由 worker/limits.py + declarative._extract_zip_safe 实施
- G10 signature 占位（无签名=社区级+警告；结构非法=拒绝；core/ 前缀强制要求签名）

v1 兼容通道（读入注入默认值，不写盘）：
- 触发：schema_version 缺失或非 2。
- 注入：engine_api=1.0、permission={}、kind 探测（runtime/entrypoint/transform）、
  python 包端口缺失补 []、category/description/title 缺省、v1 shorthand op 归一化。
- 注入只发生在返回的 ManifestResult.manifest 内存副本；磁盘 manifest.json 保持原样。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..worker import limits
from .. import ports  # P2 端口类型注册制：已登记扩展类型在 G5 放行
from .errors import (
    ERR_DEPENDENCIES,
    ERR_ENGINE_API,
    ERR_KIND,
    ERR_META,
    ERR_OP,
    ERR_PERMISSION,
    ERR_PORTS,
    ERR_SCHEMA_VERSION,
    ERR_SIGNATURE,
    ERR_TYPE_ID,
    ERR_UI,
    ERR_UNKNOWN_FIELD,
    ERR_VERSION,
    PluginManifestError,
    first_gate_code,
)

# ---------------------------------------------------------------- 平台常量
SCHEMA_VERSION = 2
ENGINE_API = "1.0"                       # 当前平台 ABI（主.次）
ENGINE_API_RE = re.compile(r"^[0-9]+\.[0-9]+$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$")
TYPE_ID_RE = re.compile(r"^([a-z0-9]{2,16})/[a-z0-9][a-z0-9_.-]*$")
PORT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENTRYPOINT_RE = re.compile(r"^[A-Za-z0-9_./-]+\.py$")
CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UI_ENTRY_RE = re.compile(r"^ui/[A-Za-z0-9_./-]+\.(js|mjs|css)$")

CATEGORIES = {"输入", "语义", "分析", "音频", "剪辑", "输出", "控制", "用户节点"}
KINDS = {"transform", "python"}
RUNTIMES = {"transform", "python"}
WIDGETS = {"text", "number", "select", "file", "toggle", "password"}
CORE_PORT_TYPES = {
    "VIDEO", "IMAGE", "AUDIO", "SCRIPT", "SUBTITLE", "SEGMENTS",
    "TIMELINE", "JSON", "STRING", "INT", "FLOAT", "BOOL", "ANY",
}
MEDIA_TYPES = {"VIDEO", "IMAGE", "AUDIO"}
TRANSFORM_OK_TYPES = {
    "STRING", "INT", "FLOAT", "BOOL", "JSON", "SCRIPT",
    "SUBTITLE", "SEGMENTS", "TIMELINE", "ANY",
}
OPS = {"concat", "json_get", "math", "str", "upper", "lower", "len", "list"}
SRC_KEYS = {"$const", "$param", "$input", "$list"}
PERMISSION_SCOPES = {"fs", "network", "subprocess", "secret", "env"}
PERMISSION_KEYS = {
    "fs": {"read", "write"},
    "network": {"allow", "deny"},
    "subprocess": {"enabled", "argv_allowlist"},
    "secret": {"refs"},
    "env": {"vars"},
}
DEP_CHANNELS = {"pip", "bundle", "market"}

# type_id 前缀白名单：core（官方签名包）/ user（任意）+ register_prefix 登记的第三方前缀
CORE_PREFIXES = {"core", "user"}
_REGISTERED_PREFIXES: Set[str] = set()

# 顶层与端口对象允许字段（v2 严格模式 additionalProperties=false）
TOP_LEVEL_KEYS = {
    "schema_version", "engine_api", "min_engine_api", "max_engine_api", "type_id",
    "title", "category", "description", "version", "kind", "runtime",
    "entrypoint", "class_name", "inputs", "outputs", "params", "transform",
    "gpu_required", "gpu_recommended", "streaming", "permission",
    "dependencies", "ui", "signature",
}
PORT_KEYS = {
    "name", "type", "label", "required", "default", "widget", "options",
    "description", "min", "max", "step", "placeholder", "hint",
}


def register_prefix(prefix: str) -> None:
    """登记第三方 type_id 前缀（白名单扩充；幂等）。"""
    p = str(prefix).strip().lower()
    if not re.match(r"^[a-z0-9]{2,16}$", p):
        raise ValueError(f"前缀不合法（须 ^[a-z0-9]{{2,16}}$）: {p!r}")
    _REGISTERED_PREFIXES.add(p)


def prefix_whitelist() -> Set[str]:
    return CORE_PREFIXES | _REGISTERED_PREFIXES


# ---------------------------------------------------------------- 结果对象
@dataclass
class ManifestResult:
    """校验成功的结果：注入默认值后的内存 manifest（不写盘）。"""
    manifest: Dict[str, Any]
    compat: bool = False                  # 是否走了 v1 兼容通道
    warnings: List[str] = field(default_factory=list)
    signed: bool = False                  # 是否携带 signature 占位
    issues: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 版本比较
def _abi_tuple(v: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(v, str) or not ENGINE_API_RE.match(v):
        return None
    try:
        maj, mi = (int(x) for x in v.split("."))
    except (TypeError, ValueError):
        return None
    return (maj, mi)


# ---------------------------------------------------------------- v1 兼容归一化
def _normalize_v1_op(value: Any) -> Any:
    """v1 shorthand（{$concat:[…]} / {$upper:x} / op:"get" 别名）→ v2 op 表达式。"""
    if isinstance(value, dict):
        if len(value) == 1:
            key, sub = next(iter(value.items()))
            if key == "$concat" and isinstance(sub, list):
                return {"op": "concat", "sources": [_normalize_v1_op(x) for x in sub]}
            if key in ("$upper", "$lower", "$len"):
                return {"op": key[1:], "source": _normalize_v1_op(sub)}
            if key == "$get":
                return {"op": "json_get", "source": _normalize_v1_op(sub)}
        out = {}
        for k, v in value.items():
            if k == "op" and v == "get":
                out[k] = "json_get"
            else:
                out[k] = _normalize_v1_op(v)
        return out
    if isinstance(value, list):
        return [_normalize_v1_op(x) for x in value]
    return value


def _compat_normalize(m: Dict[str, Any], warnings: List[str]) -> Tuple[Dict[str, Any], bool]:
    """v1 兼容注入：只改内存副本，磁盘原样。返回 (normalized, compat)。"""
    out: Dict[str, Any] = dict(m)
    sv = out.get("schema_version")
    compat = not (isinstance(sv, int) and sv == SCHEMA_VERSION)
    if compat:
        warnings.append("v1 兼容通道：schema_version 缺失或非 2，已注入默认值（不写盘）")
        out["schema_version"] = SCHEMA_VERSION
        if not out.get("engine_api"):
            out["engine_api"] = ENGINE_API
            warnings.append("v1 兼容通道：已注入 engine_api=1.0")
        if not isinstance(out.get("permission"), dict):
            out["permission"] = {}
        for k, d in (("gpu_required", False), ("gpu_recommended", False), ("streaming", False)):
            if k not in out:
                out[k] = d
        # kind 探测
        if out.get("kind") not in KINDS:
            if str(out.get("runtime", "")).lower() == "python" or out.get("entrypoint") or out.get("class_name"):
                out["kind"] = "python"
            elif out.get("runtime", "transform") == "transform" or isinstance(out.get("transform"), dict):
                out["kind"] = "transform"
            warnings.append(f"v1 兼容通道：已按内容探测 kind={out.get('kind')!r}")
        # 元字段缺省
        if not out.get("title"):
            out["title"] = out.get("type_id", "未命名节点")
        if not out.get("category"):
            out["category"] = "用户节点"
        if "description" not in out or out.get("description") is None:
            out["description"] = ""
        # python 包端口缺失 → 补 []；transform 缺失端口在 G5 拒绝
        if out.get("kind") == "python":
            for f in ("inputs", "outputs", "params"):
                if not isinstance(out.get(f), list):
                    out[f] = []
                    warnings.append(f"v1 兼容通道：python 包 {f} 缺失，已注入 []（建议回填 spec() 端口）")
        # v1 shorthand op 归一化（transform）
        if isinstance(out.get("transform"), dict):
            out["transform"] = _normalize_v1_op(out["transform"])
        # 未知顶层字段 → 警告不拒绝（旧包可能带历史字段）
        for k in m:
            if k not in TOP_LEVEL_KEYS:
                warnings.append(f"v1 兼容通道：忽略未知字段 {k!r}")
    return out, compat


# ---------------------------------------------------------------- G 门
def _check_engine_api(m: Dict[str, Any], issues: List[str]) -> None:
    """G1/G3：engine_api 必填且兼容；min/max_engine_api 边界。"""
    ea = m.get("engine_api")
    if ea is None or not isinstance(ea, str) or not ENGINE_API_RE.match(ea):
        issues.append(f"G1: engine_api 必填且须为 主.次 版本（如 {ENGINE_API!r}）: {ea!r}")
        return
    plat = _abi_tuple(ENGINE_API)
    plugin = _abi_tuple(ea)
    if plugin and plat and plugin[0] != plat[0]:
        issues.append(f"G3: engine_api {ea!r} 与平台 {ENGINE_API!r} 主版本不兼容")
    for k in ("min_engine_api", "max_engine_api"):
        v = m.get(k)
        if v is None:
            continue
        t = _abi_tuple(v)
        if t is None:
            issues.append(f"G3: {k} 不合法（须 主.次）: {v!r}")
            continue
        if k == "min_engine_api" and t > plat:
            issues.append(f"G3: 平台 {ENGINE_API!r} 低于 min_engine_api {v!r}")
        if k == "max_engine_api" and t < plat:
            issues.append(f"G3: 平台 {ENGINE_API!r} 高于 max_engine_api {v!r}")


def _check_type_id(m: Dict[str, Any], issues: List[str], warnings: List[str],
                   compat: bool) -> None:
    """G2：type_id 格式 + 前缀白名单 + core 签名要求。"""
    tid = m.get("type_id", "")
    if not isinstance(tid, str) or not tid.strip():
        issues.append("G2: type_id 必填")
        return
    mt = TYPE_ID_RE.match(tid)
    if mt is None:
        issues.append(
            f"G2: type_id 不合法: {tid!r}（须 ^([a-z0-9]{{2,16}})/[a-z0-9][a-z0-9_.-]*$）")
        return
    prefix = mt.group(1)
    if prefix not in prefix_whitelist():
        issues.append(
            f"G2: type_id 前缀 {prefix!r} 未登记（白名单: {sorted(prefix_whitelist())}；"
            "第三方前缀需 register_prefix 登记，个人插件请用 user/）")
        return
    if prefix == "core" and not isinstance(m.get("signature"), dict):
        if compat:
            warnings.append("G2: core/ 前缀官方包未签名——v1 兼容通道放行并警告（新包必须签名）")
        else:
            issues.append("G2: core/ 前缀仅官方签名包可用（须提供 signature 占位）")


def _check_meta(m: Dict[str, Any], issues: List[str]) -> None:
    """G2：title/category/description 必填与枚举。"""
    for f in ("title", "description"):
        v = m.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            issues.append(f"G2: 必填字段缺失或为空: {f}")
    cat = m.get("category")
    if cat not in CATEGORIES:
        issues.append(f"G2: category 不在枚举内: {cat!r}（{sorted(CATEGORIES)}）")


def _check_kind(m: Dict[str, Any], issues: List[str]) -> None:
    """G4：kind/runtime/entrypoint/class_name 白名单。"""
    kind = m.get("kind")
    if kind not in KINDS:
        issues.append(f"G4: kind 必须是 transform|python，当前: {kind!r}")
        return
    runtime = m.get("runtime")
    if runtime is not None and runtime not in RUNTIMES:
        issues.append(f"G4: runtime 必须是 transform|python，当前: {runtime!r}")
    if kind == "transform" and runtime == "python":
        issues.append("G4: kind=transform 时 runtime 恒为 transform（不得声明 python）")
    if kind == "python":
        if runtime not in (None, "python"):
            issues.append("G4: kind=python 时 runtime 必须为 python")
        for f in ("entrypoint", "class_name"):
            if not isinstance(m.get(f), str) or not m[f].strip():
                issues.append(f"G4: kind=python 必填字段缺失: {f}")
        ep = str(m.get("entrypoint", ""))
        if ep and not ENTRYPOINT_RE.match(ep):
            issues.append(f"G4: entrypoint 必须是包内相对 *.py: {ep!r}")
        if ep and (ep.startswith("/") or ep.startswith("\\") or ".." in ep.split("/")):
            issues.append(f"G4: entrypoint 不得为绝对路径或含 .. : {ep!r}")
        cn = str(m.get("class_name", ""))
        if cn and not CLASS_NAME_RE.match(cn):
            issues.append(f"G4: class_name 不是合法标识符: {cn!r}")


def _check_ports(m: Dict[str, Any], issues: List[str], warnings: List[str],
                 compat: bool) -> None:
    """G5：端口声明必填 + PortSpec 合法。"""
    for f in ("inputs", "outputs", "params"):
        v = m.get(f)
        if not isinstance(v, list):
            if compat and m.get("kind") == "python":
                # compat 已注入，理论不可达；防御
                warnings.append(f"G5: python 包 {f} 缺失（兼容通道注入 []）")
                continue
            issues.append(f"G5: 端口声明缺失——'{f}' 字段必须存在（可为空数组）（R1 端口声明必填）")
            continue
        names: Set[str] = set()
        for i, p in enumerate(v):
            if not isinstance(p, dict):
                issues.append(f"G5: {f}[{i}] 必须是对象")
                continue
            for k in p:
                if k not in PORT_KEYS:
                    issues.append(f"G5: {f}[{i}] 未知端口字段 {k!r}（v2 严格模式）")
            nm = p.get("name", "")
            if not isinstance(nm, str) or not PORT_NAME_RE.match(nm):
                issues.append(f"G5: {f}[{i}] name 不合法: {nm!r}")
            if nm in names:
                issues.append(f"G5: {f} 内端口名重复: {nm!r}")
            names.add(nm)
            pt = str(p.get("type", "")).upper()
            if pt not in CORE_PORT_TYPES and ports.resolve_port_type(pt) is None:
                issues.append(f"G5: {f}[{i}] type 未注册（13 核心或已登记扩展类型之一）: {p.get('type')!r}")
            w = p.get("widget", "text")
            if w not in WIDGETS:
                issues.append(f"G5: {f}[{i}] widget 不合法: {w!r}")
            if w == "select" and not p.get("options"):
                issues.append(f"G5: {f}[{i}] widget=select 必须提供非空 options")
            if w == "number":
                d = p.get("default")
                if d is not None and not isinstance(d, (int, float)):
                    issues.append(f"G5: {f}[{i}] widget=number 时 default 必须是数字: {d!r}")
            if w in ("file", "toggle", "password") and f != "params":
                issues.append(f"G5: {f}[{i}] widget={w} 仅 params 允许")
    if isinstance(m.get("inputs"), list) and isinstance(m.get("outputs"), list):
        inames = {p.get("name") for p in m["inputs"] if isinstance(p, dict)}
        onames = {p.get("name") for p in m["outputs"] if isinstance(p, dict)}
        dup = inames & onames
        if dup:
            issues.append(f"G5: 端口名同时出现在 inputs 与 outputs: {sorted(dup)}（须改名或合规化）")


def _check_op_expr(path: str, v: Any, issues: List[str]) -> None:
    """G6 递归：op 表达式白名单 + 取值源。"""
    if isinstance(v, dict):
        if "op" in v:
            op = v.get("op")
            if op not in OPS:
                issues.append(f"G6: {path} 未知 op: {op!r}（白名单: {sorted(OPS)}）")
            for sub in (v.get("sources") or []):
                _check_op_expr(path + ".sources[]", sub, issues)
            if "source" in v:
                _check_op_expr(path + ".source", v["source"], issues)
            for sv in (v.get("values") or {}).values():
                _check_op_expr(path + ".values", sv, issues)
            for key in ("a", "b"):
                if key in v:
                    _check_op_expr(path + "." + key, v[key], issues)
        else:
            keys = [k for k in v.keys() if k.startswith("$")]
            if keys and not set(keys) <= SRC_KEYS:
                issues.append(f"G6: {path} 取值源含未知 $key: {keys}")
            for k in v:
                _check_op_expr(path + "." + k, v[k], issues)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _check_op_expr(f"{path}[{i}]", x, issues)


def _check_transform(m: Dict[str, Any], issues: List[str]) -> None:
    """G6：transform 映射 + op 白名单 + 媒体禁入。"""
    if m.get("kind") != "transform":
        return
    t = m.get("transform")
    if not isinstance(t, dict) or not t:
        issues.append("G6: kind=transform 必须提供非空 transform 映射")
        return
    onames = {p.get("name") for p in m.get("outputs") or [] if isinstance(p, dict)}
    for k, v in t.items():
        if k not in onames:
            issues.append(f"G6: transform 键 {k!r} 不在 outputs 端口名中")
        _check_op_expr("transform." + k, v, issues)
    for f in ("inputs", "outputs", "params"):
        for p in m.get(f) or []:
            if isinstance(p, dict) and str(p.get("type", "")).upper() in MEDIA_TYPES:
                issues.append(f"G6: transform 端口 {p.get('name')!r} 类型 {p['type']} 是媒体类型，禁止（§5.3）")


def _check_permission(m: Dict[str, Any], issues: List[str]) -> None:
    """G7：permission 五作用域合法。"""
    perm = m.get("permission")
    if perm is None:
        return
    if not isinstance(perm, dict):
        issues.append("G7: permission 必须是对象")
        return
    for scope in perm:
        if scope not in PERMISSION_SCOPES:
            issues.append(f"G7: 未知 permission 作用域: {scope!r}（五作用域: {sorted(PERMISSION_SCOPES)}）")
            continue
        val = perm[scope]
        if not isinstance(val, dict):
            issues.append(f"G7: permission.{scope} 必须是对象")
            continue
        for k in val:
            if k not in PERMISSION_KEYS[scope]:
                issues.append(f"G7: permission.{scope} 未知键: {k!r}")
        for k, arr in val.items():
            if k == "enabled":
                if not isinstance(arr, bool):
                    issues.append(f"G7: permission.subprocess.enabled 必须是布尔: {arr!r}")
            elif not isinstance(arr, list) or not all(isinstance(x, str) for x in arr):
                issues.append(f"G7: permission.{scope}.{k} 必须是字符串数组: {arr!r}")


def _check_dependencies(m: Dict[str, Any], issues: List[str]) -> None:
    """G8：dependencies 声明 + 通道白名单。"""
    deps = m.get("dependencies")
    if deps is None:
        return
    if not isinstance(deps, list):
        issues.append("G8: dependencies 必须是数组")
        return
    for i, d in enumerate(deps):
        if not isinstance(d, dict):
            issues.append(f"G8: dependencies[{i}] 必须是对象")
            continue
        for k in d:
            if k not in ("id", "version", "channel", "optional"):
                issues.append(f"G8: dependencies[{i}] 未知字段 {k!r}")
        did = d.get("id", "")
        if not isinstance(did, str) or not did.strip():
            issues.append(f"G8: dependencies[{i}] id 必填且非空")
        ver = d.get("version")
        if ver is None or not isinstance(ver, str) or not ver.strip():
            issues.append(f"G8: dependencies[{i}] version 必填")
        ch = d.get("channel")
        if ch not in DEP_CHANNELS:
            issues.append(f"G8: dependencies[{i}] channel 必须是 pip|bundle|market: {ch!r}")
        opt = d.get("optional")
        if opt is not None and not isinstance(opt, bool):
            issues.append(f"G8: dependencies[{i}] optional 必须是布尔")


def _check_ui(m: Dict[str, Any], issues: List[str]) -> None:
    """G8：ui 声明（UI 资源目录 ui/）。entry 必须指向包内 ui/ 子树。"""
    ui = m.get("ui")
    if ui is None:
        return
    if not isinstance(ui, dict):
        issues.append("G8: ui 必须是对象")
        return
    for k in ui:
        if k not in ("entry", "sandbox", "capabilities"):
            issues.append(f"G8: ui 未知字段 {k!r}（entry/sandbox/capabilities）")
    entry = ui.get("entry")
    if not isinstance(entry, str) or not UI_ENTRY_RE.match(entry):
        issues.append(f"G8: ui.entry 不合法: {entry!r}（须 ui/*.js|mjs|css，包内相对路径）")
    else:
        parts = entry.replace("\\", "/").split("/")
        if any(p in ("..", "") for p in parts[:-1]) or entry.startswith("/"):
            issues.append(f"G8: ui.entry 不得逃逸 ui/ 目录: {entry!r}")
    sandbox = ui.get("sandbox")
    if sandbox is not None and not isinstance(sandbox, dict):
        issues.append("G8: ui.sandbox 必须是对象")
    caps = ui.get("capabilities")
    if caps is not None and (not isinstance(caps, list) or not all(isinstance(x, str) for x in caps)):
        issues.append("G8: ui.capabilities 必须是字符串数组")


def _check_signature(m: Dict[str, Any], issues: List[str], warnings: List[str]) -> bool:
    """G10：signature 占位（结构校验；真实验签为 P3）。"""
    sig = m.get("signature")
    if sig is None:
        warnings.append("G10: 未签名——社区级安装（L2），官方商城上架需签名")
        return False
    if not isinstance(sig, dict):
        issues.append("G10: signature 必须是对象")
        return False
    for k in sig:
        if k not in ("alg", "key_id", "value", "over"):
            issues.append(f"G10: signature 未知字段 {k!r}")
    alg = sig.get("alg")
    if alg is not None and (not isinstance(alg, str) or not re.match(r"^[A-Za-z0-9_-]+$", alg)):
        issues.append(f"G10: signature.alg 不合法: {alg!r}")
    for k in ("key_id", "value", "over"):
        v = sig.get(k)
        if not isinstance(v, str) or not v.strip():
            issues.append(f"G10: signature.{k} 必填且非空")
    return True


def _check_unknown_fields(m: Dict[str, Any], issues: List[str], warnings: List[str],
                          compat: bool) -> None:
    """v2 严格模式 additionalProperties=false：未知顶层字段拒绝；compat 警告。"""
    for k in m:
        if k not in TOP_LEVEL_KEYS:
            if compat:
                warnings.append(f"忽略未知字段 {k!r}（v1 兼容）")
            else:
                issues.append(f"G2: 未知顶层字段 {k!r}（v2 严格模式 additionalProperties=false）")


# ---------------------------------------------------------------- 主入口
def validate_manifest(data: Any) -> ManifestResult:
    """校验 manifest 对象：通过 → ManifestResult（含注入后内存副本）；失败 → 抛错。"""
    issues: List[str] = []
    warnings: List[str] = []
    if not isinstance(data, dict):
        raise PluginManifestError(limits.ERR_MANIFEST, ["manifest 根节点必须是对象"])
    sv = data.get("schema_version")
    if sv is not None and not isinstance(sv, int):
        issues.append(f"G1: schema_version 必须是整数: {sv!r}")
    elif isinstance(sv, int) and sv not in (1, 2):
        issues.append(f"G1: 不支持的 schema_version: {sv!r}（当前仅支持 2；缺失= v1 兼容通道）")
    norm, compat = _compat_normalize(data, warnings)
    _check_unknown_fields(norm, issues, warnings, compat)
    _check_engine_api(norm, issues)
    _check_type_id(norm, issues, warnings, compat)
    _check_meta(norm, issues)
    if "version" in norm and not SEMVER_RE.match(str(norm.get("version", ""))):
        issues.append(f"G3: version 不是合法 semver: {norm.get('version')!r}")
    _check_kind(norm, issues)
    _check_ports(norm, issues, warnings, compat)
    _check_transform(norm, issues)
    _check_permission(norm, issues)
    _check_dependencies(norm, issues)
    _check_ui(norm, issues)
    signed = _check_signature(norm, issues, warnings)
    if issues:
        codes = []
        g1 = [i for i in issues if i.startswith("G1:")]
        if g1:
            codes.append(ERR_ENGINE_API if any("engine_api" in i for i in g1) else ERR_SCHEMA_VERSION)
        g2 = [i for i in issues if i.startswith("G2:")]
        if g2:
            if any("未知顶层字段" in i for i in g2):
                codes.append(ERR_UNKNOWN_FIELD)
            elif any("type_id" in i or "前缀" in i or "core/" in i for i in g2):
                codes.append(ERR_TYPE_ID)
            else:
                codes.append(ERR_META)
        g3 = [i for i in issues if i.startswith("G3:")]
        if g3:
            codes.append(ERR_VERSION if any("version" in i for i in g3) else ERR_ENGINE_API)
        if any(i.startswith("G4:") for i in issues):
            codes.append(ERR_KIND)
        if any(i.startswith("G5:") for i in issues):
            codes.append(ERR_PORTS)
        if any(i.startswith("G6:") for i in issues):
            codes.append(ERR_OP)
        if any(i.startswith("G7:") for i in issues):
            codes.append(ERR_PERMISSION)
        if any(i.startswith("G8: ui") for i in issues):
            codes.append(ERR_UI)
        elif any(i.startswith("G8:") for i in issues):
            codes.append(ERR_DEPENDENCIES)
        if any(i.startswith("G10:") for i in issues):
            codes.append(ERR_SIGNATURE)
        raise PluginManifestError(first_gate_code(codes), issues)
    return ManifestResult(manifest=norm, compat=compat, warnings=warnings, signed=signed)


def validate_manifest_file(pkg_dir: str) -> ManifestResult:
    """校验包目录 <pkg>/manifest.json（含文件层：entrypoint/ui 资源存在性与逃逸）。"""
    mpath = os.path.join(pkg_dir, "manifest.json")
    if not os.path.isfile(mpath):
        raise PluginManifestError(limits.ERR_MANIFEST, ["缺少 manifest.json"])
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise PluginManifestError(limits.ERR_MANIFEST, [f"manifest.json 解析失败: {e}"]) from e
    result = validate_manifest(data)
    norm = result.manifest
    base = os.path.abspath(pkg_dir)
    # python entrypoint 文件层校验：存在且在包目录内
    if norm.get("kind") == "python":
        entry = str(norm.get("entrypoint", "node.py"))
        entry_path = os.path.abspath(os.path.join(pkg_dir, entry))
        if not entry_path.startswith(base + os.sep) or not os.path.isfile(entry_path):
            raise PluginManifestError(ERR_KIND, [f"entrypoint 非法或不存在: {entry}"])
    # ui entry 文件层校验：声明必须存在于包内 ui/ 子树
    ui = norm.get("ui")
    if isinstance(ui, dict) and isinstance(ui.get("entry"), str):
        ui_path = os.path.abspath(os.path.join(pkg_dir, ui["entry"]))
        if not ui_path.startswith(base + os.sep):
            raise PluginManifestError(ERR_UI, [f"ui.entry 逃逸包目录: {ui['entry']}"])
        if not os.path.isfile(ui_path):
            result.warnings.append(f"G8: ui.entry 文件在包内不存在: {ui['entry']}")
    return result
