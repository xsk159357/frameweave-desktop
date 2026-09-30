"""图执行引擎：DAG 校验、拓扑排序、增量缓存、状态回调、并行执行。"""
from __future__ import annotations
import asyncio
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from .registry import get_class, get_spec
from .assets import Asset, AssetStore
from .nodespec import NodeBase, NodeSpec


class NodeStatus(str, Enum):
    PENDING = "pending"      # 待运行
    QUEUED = "queued"        # 已入队
    RUNNING = "running"      # 运行中
    SUCCESS = "success"      # 成功
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消
    CACHED = "cached"        # 缓存命中（视为成功）


@dataclass
class GraphNode:
    id: str            # 节点实例 id（画布 id）
    type_id: str       # 节点类型
    x: float = 0.0
    y: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    inputs: Dict[str, str] = field(default_factory=dict)   # 端口名 -> 上游节点id
    outputs: Dict[str, str] = field(default_factory=dict)  # 端口名 -> 下游节点id
    error: str = ""
    progress: float = 0.0
    cache_key: str = ""
    asset_ids: Dict[str, str] = field(default_factory=dict)  # 端口名 -> asset id
    last_params: Dict[str, Any] = field(default_factory=dict)  # 上次运行参数快照（差异高亮）
    run_history: List[Dict[str, Any]] = field(default_factory=list)  # 运行历史（每次运行一条）
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id, "type": self.type_id, "x": self.x, "y": self.y,
            "params": self.params, "status": self.status.value,
            "inputs": self.inputs, "outputs": self.outputs,
            "error": self.error, "progress": self.progress,
            "cache_key": self.cache_key, "asset_ids": self.asset_ids,
            "last_params": self.last_params,
            "run_history": self.run_history,
        }
        return d


@dataclass
class Workflow:
    id: str
    name: str = "未命名工作流"
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[Dict[str, Any]] = field(default_factory=list)  # {source,target,sourceHandle,targetHandle}
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": self.edges,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Workflow":
        wf = cls(id=d["id"], name=d.get("name", "未命名工作流"),
                 edges=d.get("edges", []), created_at=d.get("created_at", time.time()))
        wf.updated_at = d.get("updated_at", time.time())
        for nd in d.get("nodes", []):
            n = GraphNode(
                id=nd["id"], type_id=nd["type"],
                x=nd.get("x", 0.0), y=nd.get("y", 0.0),
                params=nd.get("params", {}),
                inputs=nd.get("inputs", {}), outputs=nd.get("outputs", {}),
                status=NodeStatus(nd.get("status", "pending")),
                error=nd.get("error", ""), progress=nd.get("progress", 0.0),
                cache_key=nd.get("cache_key", ""),
                asset_ids=nd.get("asset_ids", {}),
                last_params=nd.get("last_params", {}),
                run_history=nd.get("run_history", []),
            )
            wf.nodes[nd["id"]] = n
        return wf



def _append_history(node: "GraphNode", status: str) -> None:
    """记录一次运行历史（保留最近 20 条）。"""
    node.run_history.append({
        "time": time.time(),
        "status": status,
        "params": dict(node.params),
        "cache_key": node.cache_key,
        "error": node.error,
    })
    if len(node.run_history) > 20:
        node.run_history = node.run_history[-20:]

class Engine:
    """执行引擎：给定 Workflow，计算 DAG，按拓扑分批并行执行。"""

    _MAX_CACHE_ENTRIES = 2000  # 缓存索引上限（超出按 ts 淘汰最旧）

    def __init__(self, store: AssetStore, on_event: Optional[Callable] = None):
        self.store = store
        self.on_event = on_event or (lambda ev: None)  # 事件: {"type":..., ...}
        self._cache_file = os.path.join(store.root, "cache_index.json")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    # ---------- 缓存索引（持久化） ----------
    def _load_cache(self) -> None:
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f) or {}
        except Exception:
            self._cache = {}
        self._trim_cache()

    def _save_cache(self) -> None:
        self._trim_cache()
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _trim_cache(self) -> None:
        """缓存索引维护（防膨胀）：
        1) 剔除引用资产已不存在的孤儿条目；
        2) 超出 _MAX_CACHE_ENTRIES 时按 ts 淘汰最旧（LRU，命中会刷新 ts）。"""
        if not self._cache:
            return
        # 孤儿：条目声明的输出资产已被删除（磁盘上不再有 meta）
        stale = [k for k, v in self._cache.items()
                 if any(aid and self.store.get(aid) is None
                        for aid in (v.get("assets") or {}).values())]
        for k in stale:
            self._cache.pop(k, None)
        # 超限：按最后使用时间 ts 升序删最旧
        if len(self._cache) > self._MAX_CACHE_ENTRIES:
            ordered = sorted(self._cache.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in ordered[: len(self._cache) - self._MAX_CACHE_ENTRIES]:
                self._cache.pop(k, None)

    def invalidate_cache(self, cache_key: str) -> None:
        """清除单个缓存键（用于强制重算）。"""
        if cache_key in self._cache:
            self._cache.pop(cache_key, None)
            self._save_cache()

    def _cache_hit(self, cache_key: str, wf: Workflow, node: GraphNode) -> bool:
        """缓存命中：索引存在且所有输出资产仍存在。"""
        entry = self._cache.get(cache_key)
        if not entry:
            return False
        assets = entry.get("assets") or {}
        for aid in assets.values():
            if not aid or self.store.get(aid) is None:
                return False
        return True

    # ---------- DAG 校验 ----------
    def _adjacency(self, wf: Workflow) -> Dict[str, List[str]]:
        adj: Dict[str, List[str]] = {nid: [] for nid in wf.nodes}
        for e in wf.edges:
            s, t = e.get("source"), e.get("target")
            if s in adj and t in adj and t not in adj[s]:
                adj[s].append(t)
        return adj

    def validate(self, wf: Workflow) -> List[str]:
        """返回环检测结果（错误信息列表）。"""
        errors: List[str] = []
        adj = self._adjacency(wf)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in wf.nodes}

        def dfs(nid: str) -> bool:
            color[nid] = GRAY
            for nb in adj.get(nid, []):
                if color[nb] == GRAY:
                    errors.append(f"检测到环路: {nid} -> {nb}")
                    return True
                if color[nb] == WHITE and dfs(nb):
                    return True
            color[nid] = BLACK
            return False

        for nid in wf.nodes:
            if color[nid] == WHITE:
                dfs(nid)
        return errors

    def topological_batches(self, wf: Workflow) -> List[List[str]]:
        """Kahn 拓扑排序，返回并行批次。"""
        adj = self._adjacency(wf)
        indeg = {nid: 0 for nid in wf.nodes}
        for s in adj:
            for t in adj[s]:
                indeg[t] += 1
        # 只计算需要运行的节点（存在有 input 依赖的）
        batches: List[List[str]] = []
        queue = [nid for nid, d in indeg.items() if d == 0]
        # 稳定排序保证确定性
        queue.sort()
        while queue:
            level = []
            for nid in queue:
                level.append(nid)
            batches.append(level)
            next_q = []
            for nid in queue:
                for nb in adj[nid]:
                    indeg[nb] -= 1
                    if indeg[nb] == 0:
                        next_q.append(nb)
            next_q.sort()
            queue = next_q
        # 断言无环（validate 已检查）
        return batches

    # ---------- 缓存键 ----------
    def _cache_key(self, wf: Workflow, node: GraphNode) -> str:
        spec = get_spec(node.type_id)
        parts = [spec.type_id, spec.version]
        # 参数序列化
        parts.append(json.dumps(node.params, sort_keys=True, ensure_ascii=False))
        # 输入资产指纹（构造 part 数组整体序列化，避免分隔符歧义碰撞）
        for port_name, up_id in node.inputs.items():
            up = wf.nodes.get(up_id)
            if up and up.asset_ids.get(port_name):
                parts.append(up.asset_ids[port_name])
            else:
                parts.append("none")
        return json.dumps(parts, ensure_ascii=False)[:512]

    # ---------- 执行 ----------
    async def execute(self, wf: Workflow, run_node_ids: Optional[Set[str]] = None,
                      run_mode: str = "all") -> Workflow:
        """执行工作流。
        run_mode: all | upstream (运行到选中节点) | selection | downstream
        """
        errors = self.validate(wf)
        if errors:
            for e in errors:
                self._emit({"type": "graph_error", "message": e})
            return wf

        batches = self.topological_batches(wf)

        # 确定本次要运行的节点集合
        targets: Set[str] = set()
        if run_mode == "all":
            targets = set(wf.nodes.keys())
        elif run_mode == "selection" and run_node_ids:
            targets = set(run_node_ids)
        elif run_mode == "upstream" and run_node_ids:
            # 运行到选中节点：选中节点及其全部上游
            visited: Set[str] = set()
            def up(nid: str):
                if nid in visited: return
                visited.add(nid)
                for up_id in wf.nodes[nid].inputs.values():
                    up(up_id)
            for nid in run_node_ids: up(nid)
            targets = visited
        elif run_mode == "downstream" and run_node_ids:
            targets = set(run_node_ids)
            changed = True
            while changed:
                changed = False
                for s, t in [(e["source"], e["target"]) for e in wf.edges]:
                    if s in targets and t not in targets:
                        targets.add(t); changed = True

        # 初始化状态：SUCCESS 节点若 cache_key 变了（参数/输入变化）则重置重算
        for nid, node in wf.nodes.items():
            if nid in targets:
                new_key = self._cache_key(wf, node)
                if node.status in (NodeStatus.SUCCESS, NodeStatus.CACHED) and node.cache_key != new_key:
                    node.status = NodeStatus.PENDING
                    node.error = ""
                    node.progress = 0.0
                elif node.status not in (NodeStatus.SUCCESS, NodeStatus.CACHED):
                    node.status = NodeStatus.PENDING
                    node.error = ""
                    node.progress = 0.0
                node.cache_key = new_key
            self._emit({"type": "node_update", "node_id": nid, "status": node.status.value,
                        "error": node.error, "progress": node.progress})

        for level in batches:
            to_run = [nid for nid in level if nid in targets and wf.nodes[nid].status == NodeStatus.PENDING]
            if not to_run:
                continue
            # 检查缓存（增量：命中直接复用资产，无需执行）
            # 注意：cache_key 必须在此时重算——上游批次刚执行完，其 asset_ids 已更新
            run_now: List[str] = []
            for nid in to_run:
                node = wf.nodes[nid]
                node.cache_key = self._cache_key(wf, node)
                if self._cache_hit(node.cache_key, wf, node):
                    entry = self._cache[node.cache_key]
                    entry["ts"] = time.time()  # LRU：命中刷新最后使用时间
                    node.asset_ids = dict(entry.get("assets") or {})
                    node.status = NodeStatus.CACHED
                    node.last_params = dict(node.params)
                    _append_history(node, "cached")
                    node.progress = 1.0
                    node.finished_at = time.time()
                    self._emit({"type": "node_update", "node_id": nid, "status": "cached",
                                "progress": 1.0, "asset_ids": node.asset_ids})
                else:
                    node.status = NodeStatus.QUEUED
                    self._emit({"type": "node_update", "node_id": nid, "status": "queued"})
                    run_now.append(nid)

            results = await asyncio.gather(*[self._run_one(wf, nid) for nid in run_now])
            for nid, ok in zip(to_run, results):
                pass  # _run_one 已更新节点状态与资产

        self._emit({"type": "execution_done", "workflow_id": wf.id})
        return wf

    async def _run_one(self, wf: Workflow, nid: str) -> bool:
        node = wf.nodes[nid]
        try:
            cls = get_class(node.type_id)
            spec = get_spec(node.type_id)
            node.status = NodeStatus.RUNNING
            node.started_at = time.time()
            self._emit({"type": "node_update", "node_id": nid, "status": "running", "progress": 0.0})

            # 收集输入资产（支持 "@资产id" 直接引用素材库资产）
            inputs: Dict[str, Any] = {}
            for port_name, up_id in node.inputs.items():
                if isinstance(up_id, str) and up_id.startswith("@"):
                    aid = up_id[1:]
                    asset = self.store.get(aid)
                    if asset is None:
                        raise RuntimeError(f"素材库资产缺失: {aid}")
                    inputs[port_name] = asset
                    continue
                up = wf.nodes.get(up_id)
                if up is None:
                    raise RuntimeError(f"输入节点不存在: {up_id}")
                aid = up.asset_ids.get(port_name)
                if aid:
                    asset = self.store.get(aid)
                    if asset is None:
                        raise RuntimeError(f"上游资产缺失: {aid}")
                    inputs[port_name] = asset
                else:
                    inputs[port_name] = None

            ctx = {
                "workdir": self.store.root,
                "store": self.store,
                "logger": self._emit,
                "progress": lambda p: self._emit({"type": "node_update", "node_id": nid,
                                                   "status": "running", "progress": p}),
            }

            node_obj: NodeBase = cls()
            # setup（一次性）
            try:
                await node_obj.setup(ctx)
            except NotImplementedError:
                pass

            outputs = await node_obj.run(ctx, inputs, node.params)
            if not isinstance(outputs, dict):
                raise RuntimeError("节点输出必须是 dict {端口名: 资产或值}")

            # 落盘输出资产
            node.asset_ids = {}
            for port_name, spec_p in zip(spec.outputs, spec.outputs):
                val = outputs.get(spec_p.name)
                if val is None:
                    continue
                if isinstance(val, Asset):
                    self.store.save_asset(val)
                    node.asset_ids[spec_p.name] = val.id
                elif isinstance(val, (dict, list, str, int, float, bool)):
                    asset = Asset(id="", kind=spec_p.type.value, meta={"node": nid, "port": spec_p.name})
                    asset = self.store.save_asset(asset, payload=val)
                    node.asset_ids[spec_p.name] = asset.id

            node.status = NodeStatus.SUCCESS
            node.progress = 1.0
            node.finished_at = time.time()
            node.last_params = dict(node.params)  # 记录参数快照（差异高亮）
            _append_history(node, "success")
            # 写入缓存索引（增量复用）
            if node.cache_key:
                self._cache[node.cache_key] = {
                    "assets": dict(node.asset_ids),
                    "ts": time.time(),
                    "type": node.type_id,
                }
                self._save_cache()
            self._emit({"type": "node_update", "node_id": nid, "status": "success",
                        "progress": 1.0, "asset_ids": node.asset_ids})
            return True
        except asyncio.CancelledError:
            node.status = NodeStatus.CANCELLED
            self._emit({"type": "node_update", "node_id": nid, "status": "cancelled"})
            return False
        except Exception as e:
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False

    def _emit(self, ev: Dict[str, Any]) -> None:
        try:
            self.on_event(ev)
        except Exception:
            pass
