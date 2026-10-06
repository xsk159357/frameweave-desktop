"""节点注册中心：类型->执行类 的映射，支持插件式注册。"""
from __future__ import annotations
from typing import Dict, Type

from .nodespec import NodeBase, NodeSpec
from . import declarative

_REGISTRY: Dict[str, Type[NodeBase]] = {}


def register(cls: Type[NodeBase]) -> Type[NodeBase]:
    spec = cls.spec()
    _REGISTRY[spec.type_id] = cls
    return cls


def get_class(type_id: str) -> Type[NodeBase]:
    if type_id in _REGISTRY:
        return _REGISTRY[type_id]
    dcls = declarative.get_class(type_id)
    if dcls is not None:
        return dcls
    raise KeyError(f"节点类型未注册: {type_id}")


def list_specs() -> list[NodeSpec]:
    return [cls.spec() for cls in _REGISTRY.values()] + declarative.list_specs()


def get_spec(type_id: str) -> NodeSpec:
    return get_class(type_id).spec()


_KNOWN_NODE_MODULES = [
    "ai_narrate", "asr", "tts", "video_render",
]

def autodiscover() -> None:
    """自动导入 nodes 包下所有模块以触发 register（兼容 PyInstaller frozen 环境）。"""
    import importlib
    import pkgutil
    import app.nodes as nodes_pkg
    try:
        mods = [m.name for m in pkgutil.iter_modules(nodes_pkg.__path__)]
    except Exception:
        mods = []
    if not mods:
        # PyInstaller frozen：nodes 模块已作为 hiddenimports 打入
        import importlib.util
        mods = []
        if hasattr(nodes_pkg, "__path__"):
            # 已 import app.nodes 后其命名空间仍可能为空；直接用 spec 列表
            import app.nodes  # noqa: F401
            for n in sorted(dir(app.nodes)):
                if n.startswith("nodes_"):
                    mods.append(n)
        if not mods:
            # 显式尝试已知模块名（与 packaging spec 的 hiddenimports 对应）
            import app.nodes
            for n in sorted(dir(app.nodes)):
                # 过滤内置/包属性，找出模块对象
                obj = getattr(app.nodes, n)
                if hasattr(obj, "__file__") or isinstance(obj, type):
                    continue
                mods.append(n)
        declarative.scan()
    for m in _KNOWN_NODE_MODULES:
        try:
            importlib.import_module(f"app.nodes.{m}")
        except Exception as e:
            print(f"[nodes] 注册失败 {m}: {e}")
