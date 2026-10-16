"""ComfyUI workflow JSON <-> FrameWeave workflow conversion (S3-T16)."""
from __future__ import annotations
from typing import Any, Dict
from .engine import Workflow, GraphNode
from .registry import get_spec


def comfy_to_fw(doc: Dict[str, Any], workflow_id: str = "comfy-import") -> Workflow:
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


def fw_to_comfy(wf: Workflow) -> Dict[str, Any]:
    prompt: Dict[str, Any] = {}
    for nid, node in wf.nodes.items():
        inputs = dict(node.params)
        inputs.update(node.literal_inputs or {})
        for name, upstream in node.inputs.items():
            inputs[name] = [upstream, int((node.input_slots or {}).get(name, 0))]
        prompt[str(nid)] = {"class_type": node.type_id, "inputs": inputs}
    return {"prompt": prompt, "name": wf.name, "version": "1.0"}
