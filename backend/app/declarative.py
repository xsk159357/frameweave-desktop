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
安装安全（P0.4 U1-U4 + P1 t2，依据《万物插件化_改造_运行时安全隔离.md》§6.1）：
  install_zip 五上限（总量 8GB/单文件 4GB/成员 20000/压缩比 100:1/
  symlink·hardlink·加密·穿越成员拒绝），staging 临时目录 → manifest v2 强校验
  （G1-G10，app/plugin/manifest_v2.py 替换旧基础门）→ 原子 rename → 失败清理无残留；
  scan/install 对坏包显式报错误码（PluginInstallError/PluginManifestError），
  不静默吞异常。上限参数唯一引用 worker/limits.py。
"""
from __future__ import annotations
import json
import os
import shutil
import stat
import uuid
import zipfile
import importlib.util
import sys
from typing import Any, Dict, List, Optional, Type

from .nodespec import NodeBase, NodeSpec, PortSpec
from .ports import PortType
from . import ports  # 端口类型注册制（P2）：resolve_port_type / register_port_type
from .worker import limits
from .plugin import manifest_v2

# 用户节点目录（开发默认 backend/user_nodes；打包版由 server 启动时指向用户数据目录）
_USER_NODES_DIR = os.environ.get("FRAMEWEAVE_USER_NODES") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_nodes"
)
USER_NODES_DIR = _USER_NODES_DIR

# 安装/扫描保留目录：.staging（事务临时区）、.backup、_vendor、.git 等不参与加载
_RESERVED_DIRS = (".staging", ".backup", "_vendor", ".git")


def set_user_nodes_dir(path: str) -> None:
    """设置用户节点目录（server 启动时调用：打包版 = 用户数据目录，可写且升级保留）。"""
    global USER_NODES_DIR
    USER_NODES_DIR = path

_decl: Dict[str, Type[NodeBase]] = {}
_plugin_modules: Dict[str, Any] = {}
_scan_errors: List[Dict[str, Any]] = []


# ---------- manifest -> NodeSpec ----------
def _port_type(s: Any) -> PortType:
    """manifest 里的端口类型字符串 -> PortType（13 核心 + P2 扩展注册表；未知兜底 STRING）。"""
    resolved = ports.resolve_port_type(s)
    return resolved if resolved is not None else PortType.STRING


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
    """为声明式 manifest 生成节点类。"""
    class _N(DeclarativeNode):
        pass
    _N._manifest = manifest
    _N.__name__ = "Decl_" + manifest.get("type_id", "x").replace("/", "_").replace("-", "_")
    return _N


def _make_python_class(manifest: Dict[str, Any], package_dir: str) -> Type[NodeBase]:
    """加载官方/社区 Python 插件；插件仍通过 NodeBase ctx 运行，类型契约来自 manifest。"""
    entry = str(manifest.get("entrypoint", "node.py"))
    entry_path = os.path.abspath(os.path.join(package_dir, entry))
    base = os.path.abspath(package_dir)
    if not entry_path.startswith(base + os.sep) or not os.path.isfile(entry_path):
        raise ValueError("插件 entrypoint 非法或不存在")
    module_name = "frameweave_plugin_" + manifest.get("type_id", "plugin").replace("/", "_").replace("-", "_")
    spec_obj = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec_obj is None or spec_obj.loader is None:
        raise ImportError("无法加载插件 entrypoint")
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[module_name] = module
    spec_obj.loader.exec_module(module)
    impl = getattr(module, manifest.get("class_name", "PluginNode"), None)
    if impl is None:
        raise ValueError("插件 class_name 不存在: " + str(manifest.get("class_name")))
    _plugin_modules[manifest.get("type_id", module_name)] = module
    class _P(NodeBase):
        @classmethod
        def spec(cls) -> NodeSpec:
            return _spec_from_manifest(manifest)
        async def setup(self, ctx):
            obj = impl()
            self._impl_obj = obj
            if hasattr(obj, "setup"):
                result = obj.setup(ctx)
                if result is not None:
                    await result
        async def run(self, ctx, inputs, params):
            obj = getattr(self, "_impl_obj", None) or impl()
            return await obj.run(ctx, inputs, params)
    _P.__name__ = "Plugin_" + manifest.get("type_id", "x").replace("/", "_").replace("-", "_")
    return _P


# ---------- 扫描 ----------
def scan() -> int:
    """扫描 USER_NODES_DIR 下所有 manifest.json，重建注册表。返回加载数。

    P1（t2）：每个包先过 manifest v2 强校验（G1-G10，app/plugin/manifest_v2.py），
    用校验器注入默认值后的内存副本构建节点类（v1 兼容通道不写盘）。
    失败包不静默吞异常：每条错误以 {pkg, code, message} 记录（scan_errors() 可取），
    并打印日志；仅跳过 .staging/.backup/_vendor 等保留目录。
    """
    global _scan_errors
    _decl.clear()
    _scan_errors = []
    if not os.path.isdir(USER_NODES_DIR):
        return 0
    count = 0
    for entry in sorted(os.listdir(USER_NODES_DIR)):
        if entry.startswith(".") or entry in _RESERVED_DIRS:
            continue
        mpath = os.path.join(USER_NODES_DIR, entry, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = manifest_v2.validate_manifest(data)
            norm = result.manifest
            type_id = norm.get("type_id", "")
            if not type_id:
                _scan_errors.append({"pkg": entry, "code": limits.ERR_MANIFEST,
                                     "message": "manifest 缺少 type_id"})
                continue
            # kind 由校验器归一化（v1 兼容按 entrypoint/transform 探测）——
            # 分支用 kind 而非 runtime：v1 python 包可能不写 runtime 字段
            if norm.get("kind") == "python":
                _decl[type_id] = _make_python_class(norm, os.path.join(USER_NODES_DIR, entry))
            else:
                _decl[type_id] = _make_class(norm)
            for w in result.warnings:
                print(f"[declarative] {entry}: {w}")
            count += 1
        except manifest_v2.PluginManifestError as e:
            _scan_errors.append({"pkg": entry, "code": e.code, "message": e.args[0]})
            print(f"[declarative] 插件包 manifest 校验失败 {entry}: {e}")
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", limits.ERR_INSTALL)
            _scan_errors.append({"pkg": entry, "code": code, "message": str(e)})
            print(f"[declarative] 插件包加载失败 {entry}: {e}")
    return count


def scan_errors() -> List[Dict[str, Any]]:
    """最近一次 scan() 的 per-package 错误（显式错误码，不静默吞异常）。"""
    return list(_scan_errors)


def get_class(type_id: str) -> Optional[Type[NodeBase]]:
    return _decl.get(type_id)


def list_specs() -> List[NodeSpec]:
    return [cls.spec() for cls in _decl.values()]


def pkg_dir(type_id: str) -> Optional[str]:
    """返回包含指定 type_id 的插件包目录（导出用）。"""
    if not os.path.isdir(USER_NODES_DIR):
        return None
    for entry in sorted(os.listdir(USER_NODES_DIR)):
        if entry.startswith(".") or entry in _RESERVED_DIRS:
            continue
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


# ---------- 安装（五上限 + staging 原子安装） ----------
def install_zip(zip_path: str) -> str:
    """从 zip 原子安装插件包：解压到 .staging/<uuid> → manifest v2 强校验（G1-G10）
    → 原子 rename 到 user_nodes/<pkg> → 重扫。失败清理 staging 无残留，并抛带错误码的
    PluginInstallError/PluginManifestError（不静默吞异常）。

    五上限（§6.1，大插件支持 t2，参数唯一引用 worker/limits.py）：
      总量 ≤ 8GB / 单文件 ≤ 4GB / 成员数 ≤ 20000 / 压缩比 ≤ 100:1 /
      拒绝 symlink·hardlink·加密·特殊·穿越成员。解压按 ZIP_BLOCK 流式字节级累计，
      单文件 4GB 级大包无需整包载入内存。
    """
    if not os.path.isfile(zip_path):
        raise limits.PluginInstallError(limits.ERR_NOT_ZIP, "zip 文件不存在")
    if not zipfile.is_zipfile(zip_path):
        raise limits.PluginInstallError(limits.ERR_NOT_ZIP, "不是有效的 zip 插件包")
    os.makedirs(USER_NODES_DIR, exist_ok=True)
    staging_root = os.path.join(os.path.abspath(USER_NODES_DIR), ".staging")
    os.makedirs(staging_root, exist_ok=True)
    staging = os.path.join(staging_root, uuid.uuid4().hex)
    os.makedirs(staging, exist_ok=True)
    unpack = os.path.join(staging, "unpack")
    os.makedirs(unpack, exist_ok=True)
    zip_size = os.path.getsize(zip_path)
    pkg_name = os.path.basename(zip_path)
    try:
        _extract_zip_safe(zip_path, unpack, zip_size)
        pkg = _resolve_package_dir(unpack)
        _validate_package(pkg)
        dest = os.path.join(os.path.abspath(USER_NODES_DIR), os.path.basename(pkg))
        _atomic_replace(staging, pkg, dest)
        scan()
        for err in _scan_errors:
            if err.get("pkg") == os.path.basename(dest):
                _rmtree_safe(dest)  # 校验不过 → 回滚安装，无残留
                raise limits.PluginInstallError(err.get("code") or limits.ERR_INSTALL,
                                                "安装校验失败: " + str(err.get("message")))
        limits.audit("plugin_install", zip=pkg_name, pkg=os.path.basename(dest),
                     ok=True, code="ok")
        return dest
    except limits.PluginInstallError as e:
        limits.audit("plugin_install", zip=pkg_name, ok=False,
                     code=e.code, error=str(e))
        raise
    except Exception as e:  # noqa: BLE001
        limits.audit("plugin_install", zip=pkg_name, ok=False,
                     code=limits.ERR_INSTALL, error=str(e))
        raise limits.PluginInstallError(limits.ERR_INSTALL, f"安装失败: {e}") from e
    finally:
        _rmtree_safe(staging)  # 失败/成功都清理事务目录：无半解压残留


def _extract_zip_safe(zip_path: str, dest_root: str, zip_size: int) -> None:
    """解压到 dest_root，逐项实施五上限；任何越界立即抛 PluginInstallError。

    先按中央目录声明值快速预检（成员数/单文件/总量/压缩比），再实际解压时按
    字节级累计复核（防声明撒谎）。
    """
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if not infos:
            raise limits.PluginInstallError(limits.ERR_EMPTY, "空插件包")
        if len(infos) > limits.ZIP_MEMBER_COUNT:
            raise limits.PluginInstallError(
                limits.ERR_MEMBER_COUNT,
                f"成员数超限: {len(infos)} > {limits.ZIP_MEMBER_COUNT}")
        declared_total = 0
        for info in infos:
            name = info.filename
            norm = name.replace("\\", "/")
            if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
                raise limits.PluginInstallError(limits.ERR_TRAVERSAL, f"非法路径: {name}")
            target = os.path.abspath(os.path.join(dest_root, norm))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise limits.PluginInstallError(limits.ERR_TRAVERSAL, f"非法路径: {name}")
            kind = limits.special_member_kind(info)
            if kind in ("symlink", "reparse"):
                raise limits.PluginInstallError(limits.ERR_SYMLINK, f"拒绝 {kind} 成员: {name}")
            if kind == "special":
                raise limits.PluginInstallError(limits.ERR_SPECIAL, f"拒绝特殊成员: {name}")
            if limits.member_is_encrypted(info):
                raise limits.PluginInstallError(limits.ERR_ENCRYPTED, f"拒绝加密成员: {name}")
            if not info.is_dir():
                declared_total += info.file_size
                if info.file_size > limits.ZIP_SINGLE_FILE:
                    raise limits.PluginInstallError(
                        limits.ERR_FILE_SIZE,
                        f"单文件超限: {name} {info.file_size} > {limits.ZIP_SINGLE_FILE}")
        if declared_total > limits.ZIP_TOTAL_UNCOMPRESSED:
            raise limits.PluginInstallError(
                limits.ERR_TOTAL_SIZE,
                f"解压总量超限: {declared_total} > {limits.ZIP_TOTAL_UNCOMPRESSED}")
        if zip_size > 0 and declared_total > zip_size * limits.ZIP_RATIO:
            raise limits.PluginInstallError(
                limits.ERR_ZIP_BOMB,
                f"压缩比超限: {declared_total}/{zip_size} > {limits.ZIP_RATIO}:1")
        # 实际解压（realpath 复查 + 字节级累计，防符号链接/声明撒谎）
        dest_abs = os.path.abspath(dest_root)
        actual_total = 0
        for info in infos:
            norm = info.filename.replace("\\", "/")
            target = os.path.abspath(os.path.join(dest_root, norm))
            rtarget = os.path.realpath(target)
            rroot = os.path.realpath(dest_abs)
            if rtarget != rroot and not rtarget.startswith(rroot + os.sep):
                raise limits.PluginInstallError(limits.ERR_TRAVERSAL,
                                                f"路径越界: {info.filename}")
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                written = 0
                while True:
                    chunk = src.read(limits.ZIP_BLOCK)
                    if not chunk:
                        break
                    written += len(chunk)
                    actual_total += len(chunk)
                    if written > limits.ZIP_SINGLE_FILE:
                        raise limits.PluginInstallError(
                            limits.ERR_FILE_SIZE,
                            f"单文件超限(实际): {info.filename}")
                    if actual_total > limits.ZIP_TOTAL_UNCOMPRESSED:
                        raise limits.PluginInstallError(
                            limits.ERR_TOTAL_SIZE,
                            f"解压总量超限(实际): {actual_total}")
                    if zip_size > 0 and actual_total > zip_size * limits.ZIP_RATIO:
                        raise limits.PluginInstallError(
                            limits.ERR_ZIP_BOMB,
                            f"压缩比超限(实际): {actual_total}/{zip_size}")
                    dst.write(chunk)


def _resolve_package_dir(unpack: str) -> str:
    """staging 解压区必须恰好一个顶层目录（插件包 = <pkg>/manifest.json）。"""
    entries = [e for e in os.listdir(unpack)]
    dirs = [e for e in entries if os.path.isdir(os.path.join(unpack, e))]
    files = [e for e in entries if not os.path.isdir(os.path.join(unpack, e))]
    if files or len(dirs) != 1:
        raise limits.PluginInstallError(
            limits.ERR_LAYOUT,
            "插件包必须包含单一顶层目录（<pkg>/manifest.json），不得混入根级文件")
    name = dirs[0]
    if name.startswith(".") or name in _RESERVED_DIRS:
        raise limits.PluginInstallError(limits.ERR_LAYOUT, f"顶层目录名非法: {name}")
    return os.path.join(unpack, name)


def _validate_package(pkg: str) -> manifest_v2.ManifestResult:
    """P1（t2）安装门：G1-G10 强校验（app/plugin/manifest_v2.py），替换 P0.4 基础门。

    staging 事务内调用；返回含注入默认值的内存 manifest（v1 兼容通道不写盘）。
    失败抛 PluginManifestError（显式错误码，如 manifest_type_id / manifest_ports / manifest_op）。
    """
    return manifest_v2.validate_manifest_file(pkg)


def _atomic_replace(staging: str, pkg: str, dest: str) -> None:
    """原子安装：已存在目标先改名让位，再 os.replace（同卷 rename）；失败回滚。"""
    old_dir = None
    if os.path.exists(dest):
        old_dir = os.path.join(staging, "old_" + os.path.basename(dest))
        os.rename(dest, old_dir)
    try:
        os.replace(pkg, dest)
    except Exception:
        if old_dir is not None and os.path.isdir(old_dir) and not os.path.exists(dest):
            try:
                os.rename(old_dir, dest)
            except OSError:
                pass
        raise
    if old_dir is not None:
        _rmtree_safe(old_dir)


def _rmtree_safe(path: str) -> None:
    """尽力删除目录树（Windows 只读文件先清属性）；失败不抛出。"""
    if not os.path.exists(path):
        return
    def _onerror(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_onerror)
