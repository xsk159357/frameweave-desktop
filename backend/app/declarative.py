"""声明式节点系统（M16）：用户无需写 Python 即可制作节点。

插件包 = <USER_NODES_DIR>/<pkg>/manifest.json（JSON 声明）。
manifest 定义：
  type_id / title / category / description / version
  kind: "transform"（白名单运算映射到输出端口）
  inputs / outputs / params: 端口与参数声明（与内置 NodeSpec 同构）
  transform: {输出端口名: op_spec}   # 仅 kind=transform

op_spec 语法（白名单，不执行任意代码）：
  {"$param": "名"} / {"$input": "端口"} / {"$const": 值}  —— 取值来源
  顶层 op: concat / json_get / math / str / list / upper / lower / len

安全模型：仅支持纯数据变换（字符串/数字/JSON），不执行用户代码。
"""
from __future__ import annotations
import json
import os
import zipfile
from typing import Any, Dict, List, Optional, Type

from .nodespec import NodeBase, NodeSpec, PortSpec
from .ports import PortType

# 用户节点目录（开发默认 backend/user_nodes；打包版由 server 启动时指向用户数据目录）
_USER_NODES_DIR = os.environ.get("FRAMEWEAVE_USER_NODES") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_nodes"
)
USER_NODES_DIR = _USER_NODES_DIR


def set_user_nodes_dir(path: str) -> None:
    """设置用户节点目录（server 启动时调用：打包版 = 用户数据目录，可写且升级保留）。"""
    global USER_NODES_DIR
    USER_NODES_DIR = path

_decl: Dict[str, Type[NodeBase]] = {}


# ---------- manifest -> NodeSpec ----------
def _port_type(s: Any) -> PortType:
    """manifest 里的端口类型字符串 -> PortType 枚举（engine 需要 .value）。"""
    if isinstance(s, PortType):
        return s
    try:
        return PortType[str(s).upper()]
    except (KeyError, AttributeError):
        return PortType.STRING


def _spec_from_manifest(m: Dict[str, Any]) -> NodeSpec:
    def ports(key: str) -> List[PortSpec]:
        out = []
        for p in m.get(key, []):
            out.append(PortSpec(
                name=p.get("name", ""),
                type=_port_type(p.get("type", "STRING")),
                label=p.get("label", ""),
                required=p.get("required", True),
                default=p.get("default"),
                widget=p.get("widget", "text"),
                options=p.get("options", []),
                description=p.get("description", ""),
            ))
        return out
    return NodeSpec(
        type_id=m.get("type_id", ""),
        title=m.get("title", m.get("type_id", "未命名节点")),
        category=m.get("category", "用户节点"),
        description=m.get("description", ""),
        inputs=ports("inputs"),
        outputs=ports("outputs"),
        params=ports("params"),
        version=m.get("version", "1.0.0"),
        gpu_required=m.get("gpu_required", False),
        gpu_recommended=m.get("gpu_recommended", False),
        streaming=m.get("streaming", False),
    )


# ---------- ops 白名单执行 ----------
def _resolve(src: Any, inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    if isinstance(src, dict):
        if "$param" in src:
            return params.get(src["$param"])
        if "$input" in src:
            return inputs.get(src["$input"])
        if "$const" in src:
            return src["$const"]
        if "$list" in src:
            return [_resolve(x, inputs, params) for x in src["$list"]]
        return None
    return src


def _concat(op: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    parts = [_resolve(s, inputs, params) for s in op.get("sources", [])]
    sep = str(op.get("sep", ""))
    s = sep.join("" if p is None else str(p) for p in parts)
    s = str(op.get("prefix", "")) + s + str(op.get("suffix", ""))
    return s


def _json_get(op: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    val = _resolve(op.get("source"), inputs, params)
    path = str(op.get("path", ""))
    for key in path.split("."):
        if key == "":
            continue
        if isinstance(val, dict):
            val = val.get(key)
        elif isinstance(val, list) and key.isdigit():
            idx = int(key)
            val = val[idx] if 0 <= idx < len(val) else None
        else:
            return None
    return val


def _math(op: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    a = _resolve(op.get("a"), inputs, params)
    b = _resolve(op.get("b"), inputs, params)
    opn = str(op.get("op", "+"))
    try:
        a, b = float(a), float(b)
        if opn == "+": return a + b
        if opn == "-": return a - b
        if opn == "*": return a * b
        if opn == "/": return a / b if b != 0 else None
        if opn == "%": return a % b if b != 0 else None
        if opn == "max": return max(a, b)
        if opn == "min": return min(a, b)
    except (TypeError, ValueError):
        return None
    return None


def _strfmt(op: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
    tpl = str(op.get("template", ""))
    values = {k: "" if v is None else str(v) for k, v in (op.get("values") or {}).items()}
    resolved = {k: _resolve(v, inputs, params) if isinstance(v, dict) else v for k, v in values.items()}
    try:
        return tpl.format(**{k: "" if v is None else v for k, v in resolved.items()})
    except (KeyError, IndexError):
        return tpl


_OPS = {
    "concat": _concat,
    "json_get": _json_get,
    "math": _math,
    "str": _strfmt,
    "upper": lambda op, i, p: str(_resolve(op.get("source"), i, p) or "").upper(),
    "lower": lambda op, i, p: str(_resolve(op.get("source"), i, p) or "").lower(),
    "len": lambda op, i, p: len(_resolve(op.get("source"), i, p) or ""),
    "list": lambda op, i, p: [_resolve(x, i, p) for x in op.get("sources", [])],
    "get": _json_get,
}


def _exec_ops(manifest: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    tmap = manifest.get("transform") or {}
    for port_name, op_spec in tmap.items():
        if isinstance(op_spec, dict) and "op" in op_spec:
            fn = _OPS.get(str(op_spec.get("op")))
            if fn is None:
                out[port_name] = None
            else:
                try:
                    out[port_name] = fn(op_spec, inputs, params)
                except Exception:
                    out[port_name] = None
        else:
            out[port_name] = _resolve(op_spec, inputs, params)
    return out


# ---------- 声明式节点类 ----------
class DeclarativeNode(NodeBase):
    _manifest: Dict[str, Any] = {}

    @classmethod
    def spec(cls) -> NodeSpec:
        return _spec_from_manifest(cls._manifest)

    async def run(self, ctx: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        return _exec_ops(self._manifest, inputs, params)


def _make_class(manifest: Dict[str, Any]) -> Type[NodeBase]:
    """为每个 manifest 生成绑定类（保持 registry 的 get_class 模式）。"""
    class _N(DeclarativeNode):
        pass
    _N._manifest = manifest
    _N.__name__ = "Decl_" + manifest.get("type_id", "x").replace("/", "_").replace("-", "_")
    return _N


# ---------- 扫描 / 安装 ----------
def scan() -> int:
    """扫描 USER_NODES_DIR 下所有 manifest.json，重建注册表。返回加载数。"""
    _decl.clear()
    if not os.path.isdir(USER_NODES_DIR):
        return 0
    count = 0
    for entry in sorted(os.listdir(USER_NODES_DIR)):
        mpath = os.path.join(USER_NODES_DIR, entry, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            type_id = data.get("type_id", "")
            if not type_id:
                continue
            _decl[type_id] = _make_class(data)
            count += 1
        except Exception as e:  # noqa: BLE001
            print(f"[declarative] 插件包加载失败 {entry}: {e}")
    return count


def get_class(type_id: str) -> Optional[Type[NodeBase]]:
    return _decl.get(type_id)


def list_specs() -> List[NodeSpec]:
    return [cls.spec() for cls in _decl.values()]


def pkg_dir(type_id: str) -> Optional[str]:
    """返回包含指定 type_id 的插件包目录（导出用）。"""
    if not os.path.isdir(USER_NODES_DIR):
        return None
    for entry in sorted(os.listdir(USER_NODES_DIR)):
        mpath = os.path.join(USER_NODES_DIR, entry, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                if json.load(f).get("type_id") == type_id:
                    return os.path.join(USER_NODES_DIR, entry)
        except Exception:  # noqa: BLE001
            continue
    return None


def install_zip(zip_path: str) -> str:
    """从 zip 安装插件包（解压到 user_nodes/<pkg>/ 并重扫）。返回安装目录或抛错。
    安全：拦截路径穿越（zip-slip）——任何成员不得逃出 USER_NODES_DIR。"""
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("不是有效的 zip 插件包")
    os.makedirs(USER_NODES_DIR, exist_ok=True)
    base_abs = os.path.abspath(USER_NODES_DIR)
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        if not members:
            raise ValueError("空插件包")
        for member in members:
            # 规范化目标路径，禁止 .. 越界 / 绝对路径 / 驱动器符
            target = os.path.abspath(os.path.join(base_abs, member.replace("\\", "/")))
            if target != base_abs and not target.startswith(base_abs + os.sep):
                raise ValueError(f"插件包包含非法路径: {member}")
            if member.startswith("/") or (len(member) > 1 and member[1] == ":"):
                raise ValueError(f"插件包包含非法路径: {member}")
        top = members[0].split("/")[0]
        dest = os.path.join(USER_NODES_DIR, top)
        for member in members:
            target = os.path.join(base_abs, member.replace("\\", "/"))
            if member.endswith("/"):
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
    scan()
    return dest
