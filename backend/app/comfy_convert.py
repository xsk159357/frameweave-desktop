"""ComfyUI workflow JSON <-> FrameWeave workflow conversion (S3-T16 + D-04).

支持两种 ComfyUI 格式 → FW Workflow：
  A. /prompt 执行格式：{node_id: {class_type, inputs: {name: [up, slot] | 值}}}（comfy_to_fw）
  B. UI 保存格式（D-04，t17）：{nodes: [{id, type, widgets_values, inputs, outputs}],
     links: [[id, origin_id, origin_slot, target_id, target_slot, type] | {…}]}
     —— 供 `POST /api/workflows/from-comfy` 路由消费（GUI ComfyJSON 导入，t18）。

转换失败不静默：逐节点/逐边错误收集（unknown_type / missing_type / link_slot_invalid /
link_missing / inputs_not_list），任一错误抛 ComfyConversionError(errors)（调用方转 422）。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .engine import Workflow, GraphNode
from .registry import get_spec


class ComfyConversionError(ValueError):
    """ComfyUI JSON 转换失败：携带逐节点/逐边错误列表（D-04，绝不静默成功）。"""

    def __init__(self, errors: List[Dict[str, Any]], warnings: Optional[List[str]] = None):
        self.errors = errors
        self.warnings = list(warnings or [])
        super().__init__("; ".join(str(e.get("message") or e) for e in errors[:5]))


def comfy_to_fw(doc: Dict[str, Any], workflow_id: str = "comfy-import") -> Workflow:
    """/prompt 执行格式 → FW Workflow（既有逻辑，S3-T16）。"""
    prompt = doc.get("prompt") if isinstance(doc.get("prompt"), dict) else doc
    if not isinstance(prompt, dict):
        raise ValueError("Comfy workflow must be a prompt object")
    wf = Workflow(id=workflow_id, name=str(doc.get("name") or "Comfy 导入"))
    for nid, raw in prompt.items():
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("class_type") or raw.get("type") or "")
        if not tid:
            raise ValueError(f"node {nid} missing class_type")
        try:
            spec = get_spec(tid)
        except KeyError:
            spec = None
        params, inputs, slots, literals = {}, {}, {}, {}
        param_names = {p.name for p in spec.params} if spec else set()
        for name, val in (raw.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                inputs[name] = val[0]; slots[name] = int(val[1])
            elif name in param_names:
                params[name] = val
            else:
                literals[name] = val
        wf.nodes[str(nid)] = GraphNode(id=str(nid), type_id=tid, params=params,
                                       inputs=inputs, input_slots=slots,
                                       literal_inputs=literals)
    return wf


def _links_as_list(links: Any) -> List[Any]:
    """links 两种形态归一：[[id, origin_id, origin_slot, target_id, target_slot, type] | {…}]。"""
    if not isinstance(links, list):
        return []
    return links


def _link_tuple(link: Any) -> Dict[str, Any]:
    """link 元素 → 归一 dict（数组序或对象字段两种写法）。"""
    if isinstance(link, dict):
        return {
            "id": link.get("id"),
            "origin_id": link.get("origin_id"),
            "origin_slot": link.get("origin_slot"),
            "target_id": link.get("target_id"),
            "target_slot": link.get("target_slot"),
            "type": link.get("type", ""),
        }
    if isinstance(link, (list, tuple)) and len(link) >= 5:
        return {
            "id": link[0],
            "origin_id": link[1],
            "origin_slot": link[2],
            "target_id": link[3],
            "target_slot": link[4],
            "type": link[5] if len(link) > 5 else "",
        }
    return {}


def comfy_ui_workflow_to_fw(doc: Dict[str, Any], workflow_id: str = "comfy-import") -> Workflow:
    """ComfyUI UI 保存格式（nodes/links/widgets_values）→ FW Workflow（D-04）。

    - 节点：type（或 class_type）→ FW type_id；未注册 → unknown_type 错误；
    - widgets_values 按 spec.params 声明序逐位映射为 params（越界值告警忽略）；
    - links → edges（sourceHandle=origin outputs[origin_slot].name，
      targetHandle=target inputs[target_slot].name）+ node.inputs/input_slots；
    - 任一错误 → 抛 ComfyConversionError（绝不静默成功）。
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    nodes_in = doc.get("nodes")
    if not isinstance(nodes_in, list):
        raise ComfyConversionError([
            {"node_id": "", "code": "invalid_document",
             "message": "Comfy workflow 缺少 nodes 列表（不是 UI 保存格式）"}])
    links = _links_as_list(doc.get("links"))
    link_by_id: Dict[Any, Dict[str, Any]] = {}
    for link in links:
        t = _link_tuple(link)
        if t.get("id") is not None:
            link_by_id[str(t["id"])] = t

    # 节点元信息（id → 声明的 inputs/outputs 槽位表）
    node_meta: Dict[str, Dict[str, Any]] = {}
    for nd in nodes_in:
        if not isinstance(nd, dict):
            continue
        nid = str(nd.get("id") or "")
        if not nid:
            continue
        node_meta[nid] = {
            "inputs": nd.get("inputs") or [],
            "outputs": nd.get("outputs") or [],
        }

    wf = Workflow(id=workflow_id, name=str(doc.get("name") or "Comfy 导入"))
    for nd in nodes_in:
        if not isinstance(nd, dict):
            errors.append({"node_id": "", "code": "node_not_dict",
                           "message": "nodes 元素必须是对象"})
            continue
        nid = str(nd.get("id") or "")
        if not nid:
            errors.append({"node_id": "", "code": "missing_node_id",
                           "message": "nodes 元素缺少 id"})
            continue
        tid = str(nd.get("type") or nd.get("class_type") or "").strip()
        if not tid:
            errors.append({"node_id": nid, "code": "missing_type",
                           "message": f"节点 {nid} 缺少 type/class_type"})
            continue
        try:
            spec = get_spec(tid)
        except Exception:  # noqa: BLE001 —— 未注册类型
            errors.append({"node_id": nid, "type_id": tid, "code": "unknown_type",
                           "message": f"节点类型未注册: {tid}（node {nid}）"})
            continue
        params: Dict[str, Any] = {}
        widgets = nd.get("widgets_values")
        if isinstance(widgets, list) and spec is not None:
            pnames = [p.name for p in spec.params]
            for i, v in enumerate(widgets):
                if i < len(pnames):
                    params[pnames[i]] = v
                else:
                    warnings.append(f"node {nid}: widgets_values[{i}] 超出 params 声明序，忽略")
        wf.nodes[nid] = GraphNode(id=nid, type_id=tid,
                                  x=float(nd.get("pos") and nd["pos"][0] or 0),
                                  y=float(nd.get("pos") and nd["pos"][1] or 0),
                                  params=params)
        # 声明的输入端口：link → 上游（槽位在 links 解析时补）
        for i, inp in enumerate(node_meta.get(nid, {}).get("inputs") or []):
            if not isinstance(inp, dict):
                continue
            iname = str(inp.get("name") or "")
            link_id = inp.get("link")
            if link_id is not None and link_id not in (0, "", None):
                if str(link_id) not in link_by_id:
                    errors.append({"node_id": nid, "code": "link_missing",
                                   "message": f"节点 {nid} 输入 {iname} 引用的 link {link_id} 不存在"})
                else:
                    wf.nodes[nid].inputs[iname] = str(link_by_id[str(link_id)]["origin_id"])
        # 输出槽位名表（供边解析）
        out_names: List[str] = []
        for outp in node_meta.get(nid, {}).get("outputs") or []:
            if isinstance(outp, dict) and outp.get("name"):
                out_names.append(str(outp["name"]))
        node_meta[nid]["_out_names"] = out_names

    # links → edges（sourceHandle/targetHandle + input_slots）
    for link in links:
        t = _link_tuple(link)
        oid = str(t.get("origin_id") or "")
        tid = str(t.get("target_id") or "")
        if not oid or not tid:
            continue
        try:
            oslot = int(t.get("origin_slot"))
            tslot = int(t.get("target_slot"))
        except (TypeError, ValueError):
            errors.append({"node_id": tid, "code": "link_slot_invalid",
                           "message": f"link {t.get('id')}: origin_slot/target_slot 非整数"})
            continue
        if oid not in wf.nodes or tid not in wf.nodes:
            errors.append({"node_id": tid, "code": "link_node_missing",
                           "message": f"link {t.get('id')}: 指向不存在的节点 {oid}->{tid}"})
            continue
        onames = node_meta.get(oid, {}).get("_out_names") or []
        if not (0 <= oslot < len(onames)):
            errors.append({"node_id": oid, "code": "link_slot_invalid",
                           "message": f"节点 {oid} 输出槽位 {oslot} 越界（outputs={onames}）"})
            continue
        # 目标端口名：优先取目标节点声明的 inputs[tslot].name（无声明时回落 spec.inputs 序）
        tin_names = [inp.get("name") for inp in node_meta.get(tid, {}).get("inputs") or []
                     if isinstance(inp, dict) and inp.get("name")]
        if 0 <= tslot < len(tin_names):
            th = tin_names[tslot]
        else:
            try:
                th = get_spec(wf.nodes[tid].type_id).inputs[tslot].name
            except Exception:  # noqa: BLE001
                th = ""
        sh = onames[oslot]
        if not th:
            errors.append({"node_id": tid, "code": "link_slot_invalid",
                           "message": f"节点 {tid} 输入槽位 {tslot} 越界"})
            continue
        wf.nodes[tid].inputs[th] = oid
        wf.nodes[tid].input_slots[th] = oslot
        wf.edges.append({"source": oid, "target": tid,
                         "sourceHandle": sh, "targetHandle": th})

    if errors:
        raise ComfyConversionError(errors, warnings)
    wf._convert_warnings = warnings  # 成功路径的告警（非阻断；调用方投影响应）
    return wf


def fw_to_comfy(wf: Workflow) -> Dict[str, Any]:
    prompt: Dict[str, Any] = {}
    for nid, node in wf.nodes.items():
        inputs = dict(node.params)
        inputs.update(node.literal_inputs or {})
        for name, upstream in node.inputs.items():
            inputs[name] = [upstream, int((node.input_slots or {}).get(name, 0))]
        prompt[str(nid)] = {"class_type": node.type_id, "inputs": inputs}
    return {"prompt": prompt, "name": wf.name, "version": "1.0"}
