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


_KNOWN_NODE_MODULES = []

def autodiscover() -> None:
    """扫描官方/社区插件目录；核心业务节点不再从 app.nodes 内置导入。"""
    declarative.scan()
