# -*- coding: utf-8 -*-
"""ComfyUI 标准执行协议（POST /prompt）的请求模型与纯执行图映射层。

2026-10-19 从 server.py 拆出（审阅 P2-9）。本模块**无模块级可变状态**：
- PromptRequest / _prompt_partial_targets：请求归一化；
- _build_prompt_workflow：{node_id: {class_type, inputs}} → 内部 Workflow（纯函数）。
瞬时工作流表 _prompt_wfs / 历史表 _prompt_histories 仍留在 server.py（路由与队列共享）。
server.py 顶部 import 本模块，`from app.server import _build_prompt_workflow` 仍可用。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from .engine import GraphNode, Workflow
from .registry import get_class, get_spec

class PromptRequest(BaseModel):
    prompt: Dict[str, Dict[str, Any]] = {}
    client_id: str = ""
    extra_data: Optional[Dict[str, Any]] = None
    partial_execution_targets: Optional[List[str]] = None  # 官方字段
    partial: Optional[List[str]] = None                    # 兼容别名
    partial_execution: Optional[Any] = None                # 兼容 {target_nodes: [...]}


def _prompt_partial_targets(body: PromptRequest) -> List[str]:
    """归一化 partial 目标（三种写法取其一）。"""
    if body.partial_execution_targets:
        return list(body.partial_execution_targets)
    if body.partial:
        return list(body.partial)
    pe = body.partial_execution
    if isinstance(pe, dict) and isinstance(pe.get("target_nodes"), list):
        return [str(x) for x in pe["target_nodes"]]
    return []


def _build_prompt_workflow(prompt: Dict[str, Dict[str, Any]],
                           partial_targets: List[str]) -> "tuple[Optional[Workflow], Optional[dict]]":
    """纯执行图 → 内部 Workflow（映射层，R-平台-3）。

    规则：
      - inputs 值 = [up_node_id, slot]（两元数组）→ 图链接（input_slots 记录槽位，
        engine 按上游输出端口索引解析）；
      - inputs 值 = 字面量 → spec.params 进 params；spec.inputs 端口进 literal_inputs
        （无上游链接时直填）；
      - 未知输入键 / 未注册 class_type / 链接指向不存在节点 → ComfyUI 语义错误包。
      - partial_targets 非空 → 仅保留目标上游闭包子图（部分执行）。
    返回 (Workflow, None) 或 (None, ComfyUI 风格错误包)。
    """
    import uuid as _uuid
    node_errors: Dict[str, List[List[str]]] = {}
    unknown_nodes: List[str] = []
    wf = Workflow(id=_uuid.uuid4().hex[:12], name="prompt", created_at=time.time(),
                  updated_at=time.time())
    order: Dict[str, int] = {}
    for i, nid in enumerate(prompt.keys()):
        order[str(nid)] = i
        nd = prompt[nid]
        if not isinstance(nd, dict):
            node_errors[str(nid)] = [["node_not_dict", "节点定义必须是对象", ""]]
            continue
        ct = nd.get("class_type")
        if not isinstance(ct, str) or not ct:
            node_errors[str(nid)] = [["class_type_missing", "缺少 class_type", ""]]
            continue
        try:
            spec = get_spec(ct)
            cls = get_class(ct)
        except Exception:  # noqa: BLE001 —— 未注册类型 get_spec 可能抛 KeyError
            spec = None
            cls = None
        if spec is None or cls is None:
            node_errors[str(nid)] = [["class_type_not_found",
                                      f"节点类型未注册: {ct}", ct]]
            continue
        g = GraphNode(id=str(nid), type_id=ct)
        params: Dict[str, Any] = {}
        inputs: Dict[str, str] = {}
        slots: Dict[str, int] = {}
        literals: Dict[str, Any] = {}
        raw_inputs = nd.get("inputs") or {}
        if not isinstance(raw_inputs, dict):
            node_errors.setdefault(str(nid), []).append(["inputs_not_dict", "inputs 必须是对象", ""])
            continue
        pnames = {p.name for p in spec.params}
        inames = {p.name for p in spec.inputs}
        for k, v in raw_inputs.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                # 图链接 [up_node_id, output_slot]
                up = str(v[0])
                try:
                    slot = int(v[1])
                except (TypeError, ValueError):
                    node_errors.setdefault(str(nid), []).append(
                        ["link_slot_invalid", f"输入 {k!r} 链接槽位非法: {v[1]!r}", ""])
                    continue
                inputs[k] = up
                slots[k] = slot
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                if k in pnames:
                    params[k] = v
                elif k in inames:
                    literals[k] = v
                else:
                    node_errors.setdefault(str(nid), []).append(
                        ["unknown_input", f"未知输入端口: {k!r}（class_type={ct}）", ""])
                continue
            node_errors.setdefault(str(nid), []).append(
                ["unsupported_input", f"不支持的输入值类型: {k!r}={v!r}", ""])
        g.params = params
        g.inputs = inputs
        g.input_slots = slots
        g.literal_inputs = literals
        wf.nodes[str(nid)] = g
    # 链接合法性：上游节点必须存在
    for nid, g in wf.nodes.items():
        for port, up in g.inputs.items():
            if up not in wf.nodes:
                node_errors.setdefault(str(nid), []).append(
                    ["link_node_missing", f"链接指向不存在的节点: {up}", ""])
    if node_errors:
        return None, {
            "error": {"type": "invalid_prompt",
                      "message": "Prompt 图校验失败",
                      "details": "; ".join(f"{nid}: {e[1]}" for nid, es in node_errors.items() for e in es)},
            "node_errors": node_errors,
        }
    # partial 目标：保留目标的上游闭包子图（部分执行）
    if partial_targets:
        keep: Set[str] = set()
        stack = [str(t) for t in partial_targets]
        missing = [t for t in stack if t not in wf.nodes]
        if missing:
            return None, {
                "error": {"type": "invalid_prompt",
                          "message": f"partial 目标节点不存在: {missing}"},
                "node_errors": {m: [["partial_target_missing", "目标节点不存在", ""]]
                                for m in missing},
            }
        while stack:
            cur = stack.pop()
            if cur in keep:
                continue
            keep.add(cur)
            g = wf.nodes[cur]
            for up in g.inputs.values():
                if up in wf.nodes and up not in keep:
                    stack.append(up)
        wf.nodes = {nid: g for nid, g in wf.nodes.items() if nid in keep}
    return wf, None
