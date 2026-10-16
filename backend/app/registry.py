"""节点注册中心：类型->执行类 的映射，支持插件式注册。

S1-T4（R-平台-4）：object_info() 组装器——把 NodeSpec（registry + declarative 单一
执行来源）与 declarative.object_meta()（search_aliases/deprecated/kind/pkg）合成
ComfyUI /object_info 等价语义，前端与第三方统一从该单一来源渲染节点。
"""
from __future__ import annotations
from typing import Any, Dict, List, Type

from .nodespec import NodeBase, NodeSpec, PortSpec
from . import declarative

_REGISTRY: Dict[str, Type[NodeBase]] = {}

# S1-T8 / C1 控制流能力声明：供第三方/验收查询——
# 嵌套子图已由 Engine._run_nested_subgraph 实现（R-平台-8 第一路线），
# ExecutionBlocker 保持一等语义（BLOCKED 状态）。
EXECUTION_CAPABILITIES = {
    "execution_blocker": "available",
    "nested_subgraph": "available",
}


def execution_capabilities() -> Dict[str, str]:
    """返回当前控制流能力契约（子图嵌套与 ExecutionBlocker 均已可用）。"""
    return dict(EXECUTION_CAPABILITIES)

# /object_info 组装缺省：端口类型 → Comfy 语义元数据
#  - is_input_list：JSON/ANY 可宽播接入（广播类型），可接多值/列表输入 → True
#  - output_is_list：同语义作用于输出端口
_LIST_CAPABLE = {"JSON", "ANY"}


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


# ---------------------------------------------------------------- S1-T4 object_info
def _port_comfy(p: PortSpec, role: str) -> List[Any]:
    """PortSpec → Comfy 端口元组 [type, {label, default, widget, options, description, required, forceInput?, port_role}]。

    forceInput：无默认值的参数端口（widget=file 或 required=False 且有 default）可连线注入，
    前端据此渲染「可连接」徽标；Comfy 等价语义为 forceInput。
    port_role：保留 FW 的「连线输入（input）/ 表单参数（param）」区分——Comfy 桶不区分两者，
    但 /api/specs 投影与 web 渲染必须能还原（画布连线手柄只画 input，参数表单只画 param）。
    """
    meta: Dict[str, Any] = {
        "label": p.label or p.name,
        "default": p.default,
        "widget": p.widget or ("number" if p.type.value in ("INT", "FLOAT") else "text"),
        "options": list(p.options or []),
        "description": p.description or "",
        "required": bool(p.required),
        "forceInput": bool(not p.required or (p.default is None and p.widget == "file")),
        "port_role": role,
    }
    return [p.type.value, meta]


def _spec_to_object_info(spec: NodeSpec, meta: Dict[str, Any]) -> Dict[str, Any]:
    """单个 NodeSpec + 声明的语义元数据 → /object_info 条目（Comfy 等价语义）。"""
    required: Dict[str, List[Any]] = {}
    optional: Dict[str, List[Any]] = {}
    # 可连线输入端口（inputs）与参数端口（params）统一进 input.required/optional
    # （Comfy 语义），但每个端口 meta 携带 port_role 以便投影还原 FW 的连线/参数区分；
    # input_order 保留声明顺序（inputs 在前、params 随后），前端渲染与 Comfy 对齐。
    for p in spec.inputs:
        target = required if bool(p.required) else optional
        target[p.name] = _port_comfy(p, "input")
    for p in spec.params:
        target = required if bool(p.required) else optional
        target[p.name] = _port_comfy(p, "param")
    input_order: Dict[str, List[str]] = {
        "required": [p.name for p in list(spec.inputs) + list(spec.params) if p.required],
        "optional": [p.name for p in list(spec.inputs) + list(spec.params) if not p.required],
    }
    is_input_list: Dict[str, bool] = {
        p.name: (p.type.value in _LIST_CAPABLE) for p in list(spec.inputs) + list(spec.params)
    }
    outputs = [p.type.value for p in spec.outputs]
    return {
        "name": spec.type_id,
        "display_name": spec.title,
        "description": spec.description or spec.title,
        "category": spec.category,
        "version": spec.version,
        "input": {"required": required, "optional": optional, "hidden": {}},
        "input_order": input_order,
        "is_input_list": is_input_list,
        "output": outputs,
        "output_name": [p.name for p in spec.outputs],
        "output_is_list": [bool(p.type.value in _LIST_CAPABLE) for p in spec.outputs],
        "search_aliases": list(meta.get("search_aliases") or []),
        "deprecated": bool(meta.get("deprecated")),
        "kind": str(meta.get("kind") or ""),
        "pkg": str(meta.get("pkg") or ""),
        "gpu_required": spec.gpu_required,
        "gpu_recommended": spec.gpu_recommended,
        "streaming": spec.streaming,
    }


def object_info() -> Dict[str, Dict[str, Any]]:
    """ComfyUI /object_info 等价语义：{type_id: {input/input_order/is_input_list/output/
    output_name/output_is_list/description/search_aliases/category/deprecated/...}}。

    - 插件节点：NodeSpec 来自 declarative，元数据来自 declarative.object_meta()；
    - 内置 @register 节点：NodeSpec 来自 _REGISTRY，无 manifest → 用默认元数据补齐。
    两者合并后按 type_id 输出；前端 /api/object_info 与 /api/specs 均从此单一来源生成。
    """
    meta_index = declarative.list_object_meta()
    out: Dict[str, Dict[str, Any]] = {}
    for spec in list_specs():
        meta = meta_index.get(spec.type_id) or {}
        out[spec.type_id] = _spec_to_object_info(spec, meta)
    return out
