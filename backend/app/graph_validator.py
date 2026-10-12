"""Backend graph contract validation: node/handle existence, type compatibility, required ports.

实现 docs/s2-connectivity.md §5 P1：保存/运行前统一图契约校验。
- validate_graph(wf, check_required=True, skip_unknown_types=False)：运行级严格校验
  （未知节点类型也是硬错误——执行必然失败）。
- save 路径（create/update）用 check_required=False + skip_unknown_types=True：
  只校验已知类型节点之间的边结构（handle 存在/类型兼容），不阻断未安装插件引用
  （画布允许保存待安装模板），required 输入留到运行前报错（对应验收矩阵）。
- 错误统一 {code, message, node_id, handle, ...} 稳定码，不接受前端伪造或过期 edge。
"""
from __future__ import annotations
from typing import Any, Dict, List
from .registry import get_spec
from .ports import can_connect

def validate_graph(wf: Any, *, check_required: bool = True,
                   skip_unknown_types: bool = False) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    nodes = getattr(wf, 'nodes', {}) or {}
    specs = {}
    for nid, node in nodes.items():
        try:
            specs[nid] = get_spec(node.type_id)
        except Exception:
            if not skip_unknown_types:
                errors.append({'code':'node_type_unknown','node_id':nid,
                               'type_id':getattr(node,'type_id',''),
                               'message':f'节点类型未注册: {getattr(node,"type_id","")}'})
    inbound = {nid:set() for nid in nodes}
    for idx, edge in enumerate(getattr(wf, 'edges', []) or []):
        if not isinstance(edge, dict):
            errors.append({'code':'edge_invalid','edge_index':idx,
                           'message':f'edges[{idx}] 必须是对象'}); continue
        src, dst = edge.get('source'), edge.get('target')
        sh, th = edge.get('sourceHandle'), edge.get('targetHandle')
        if src not in nodes:
            errors.append({'code':'source_node_missing','edge_index':idx,'source':src,
                           'message':f'源节点不存在: {src}'}); continue
        if dst not in nodes:
            errors.append({'code':'target_node_missing','edge_index':idx,'target':dst,
                           'message':f'目标节点不存在: {dst}'}); continue
        ss, ds = specs.get(src), specs.get(dst)
        if ss is None or ds is None:
            # 一端类型未知：save 模式跳过结构校验；run 模式已在上面记录 node_type_unknown
            continue
        so = next((p for p in ss.outputs if p.name == sh), None)
        ti = next((p for p in ds.inputs if p.name == th), None)
        if so is None:
            errors.append({'code':'source_handle_invalid','edge_index':idx,'node_id':src,
                           'handle':sh,'message':f'源端口不存在: {src}.{sh}'}); continue
        if ti is None:
            errors.append({'code':'target_handle_invalid','edge_index':idx,'node_id':dst,
                           'handle':th,'message':f'目标端口不存在: {dst}.{th}'}); continue
        if not can_connect(so.type, ti.type):
            errors.append({'code':'port_type_incompatible','edge_index':idx,
                           'source':f'{src}.{sh}','target':f'{dst}.{th}',
                           'source_type':so.type.value,'target_type':ti.type.value,
                           'message':f'端口类型不兼容: {so.type.value} -> {ti.type.value}'})
        inbound.setdefault(dst,set()).add(th)
    # Explicit @asset references satisfy required media/JSON inputs.
    if check_required:
        for nid, spec in specs.items():
            node = nodes[nid]; refs = set((getattr(node, 'inputs', {}) or {}).keys())
            for p in spec.inputs:
                if p.required and p.default is None and p.name not in inbound.get(nid,set()) and not (p.name in refs and str(node.inputs.get(p.name,'')).startswith('@')):
                    errors.append({'code':'required_input_missing','node_id':nid,'handle':p.name,
                                   'message':f'必需输入未连接: {nid}.{p.name}'})
    return errors

def validate_graph_messages(wf: Any) -> List[str]:
    return [e['message'] for e in validate_graph(wf)]


def validate_graph_structural_messages(wf: Any) -> List[str]:
    """结构性图校验消息（handle 存在/类型兼容/节点存在），不检查 required 输入。

    用于引擎级调度前校验与部分子图运行（upstream/selection/downstream）：
    required 输入在 mode=all 时由运行 API 严格校验，避免误阻断合法局部重跑。
    """
    return [e['message'] for e in validate_graph(wf, check_required=False)]
