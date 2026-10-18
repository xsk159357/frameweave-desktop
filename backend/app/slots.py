"""跨名边 input_slots 运行期自动派生（t7，t8 §5 遗留根治）。

背景：画布跨名边（如 asr.subtitles→tts.segments、json_get.value→draft_export.segments）
在未手工标注 input_slots 时，engine._collect_inputs / engines.cache_key 按同名端口
回落查找，跨名时拿不到正确上游。本模块对工作流每条画布边自动推导目标端口应写入
的 input_slots 槽位（幂等），使跨名边无需手工标注即可正确解析，同时：
  - 与 t8 手工标注的槽位逐一一致（不改变已通过工作流的运行语义）；
  - 同名边同样写出槽位（运行期解析结果不变），/prompt 与 ComfyUI 转换
    （comfy_convert.fw_to_comfy 默认槽位 0）随之受益；
  - 未知类型（插件未装）跳过：运行期同名回落兜底（现状不变）。

槽位推导优先级（对每条边 source→target，sourceHandle→targetHandle）：
  1) sourceHandle 在源 spec.outputs 中存在 → 其索引（画布连线事实，精确）；
  2) 同名回落：targetHandle 在源 spec.outputs 中存在 → 其索引（现状不变）；
  3) 类型兼容/输出序推导：源 outputs 中可连目标输入端口类型者，取输出序首个；
  4) 无法确定 → None（不写槽位，运行期同名回落兜底）。
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from .registry import get_spec
from .ports import can_connect


def _output_slot_of(spec: Any, port_name: Optional[str]) -> Optional[int]:
    """上游 spec.outputs 中按端口名找槽位（未注册/不存在返回 None）。"""
    if spec is None or not port_name:
        return None
    for i, p in enumerate(spec.outputs):
        if p.name == port_name:
            return i
    return None


def _input_port_of(spec: Any, port_name: Optional[str]) -> Optional[Any]:
    """下游 spec.inputs 中按端口名找输入端口（未注册/不存在返回 None）。"""
    if spec is None or not port_name:
        return None
    for p in spec.inputs:
        if p.name == port_name:
            return p
    return None


def derive_edge_slot(src_spec: Any, dst_spec: Any,
                     source_handle: Optional[str],
                     target_handle: Optional[str]) -> Optional[int]:
    """对一条画布边推导目标端口应写入的 input_slots 槽位（见模块 docstring 优先级）。

    返回 None = 无法确定（调用方保持不写，运行期同名回落兜底）。
    """
    # 1) sourceHandle 精确（画布连线事实；跨名/同名均正确）
    slot = _output_slot_of(src_spec, source_handle)
    if slot is not None:
        return slot
    # 2) 同名回落（现状不变）
    slot = _output_slot_of(src_spec, target_handle)
    if slot is not None:
        return slot
    # 3) 类型兼容/输出序推导（sourceHandle 缺失/非法的兜底）
    if src_spec is not None and dst_spec is not None:
        tgt = _input_port_of(dst_spec, target_handle)
        if tgt is not None:
            for i, p in enumerate(src_spec.outputs):
                if can_connect(p.type, tgt.type):
                    return i
    return None


def derive_input_slots(wf: Any) -> int:
    """对工作流全部画布边自动派生 input_slots（幂等），返回写入的槽位数。

    规则：
      - 每条边 {source, target, sourceHandle, targetHandle} 推导成功后写入
        wf.nodes[target].input_slots[targetHandle] = slot；
      - **仅写入缺失槽位**：不覆盖 /prompt 链接或 t8 手工标注的既有值
        （已标注工作流派生结果与既有值一致时也不改写）；
      - 未知类型（spec 缺失）/无法推导 → 跳过（运行期同名回落兜底，现状不变）。
    """
    written = 0
    nodes = getattr(wf, "nodes", None) or {}
    for e in getattr(wf, "edges", None) or []:
        if not isinstance(e, dict):
            continue
        src = e.get("source")
        dst = e.get("target")
        sh = e.get("sourceHandle")
        th = e.get("targetHandle")
        if not src or not dst or not th:
            continue
        src_node = nodes.get(src)
        dst_node = nodes.get(dst)
        if src_node is None or dst_node is None:
            continue
        src_spec = dst_spec = None
        try:
            src_spec = get_spec(src_node.type_id)
        except Exception:  # noqa: BLE001 —— 未知类型跳过
            src_spec = None
        try:
            dst_spec = get_spec(dst_node.type_id)
        except Exception:  # noqa: BLE001
            dst_spec = None
        slot = derive_edge_slot(src_spec, dst_spec, sh, th)
        if slot is None:
            continue
        slots = dst_node.input_slots
        if th not in slots:  # 幂等：缺失才写，不覆盖既有槽位
            slots[th] = slot
            written += 1
    return written
