"""引擎 SPI（P2，t4）：Scheduler / CacheProvider / Executor 三协议 + NodeContext + 扩展注册。

第三方按 Protocol 实现调度器/缓存/执行器后，经 register_engine_ext 注册即可替换
平台默认实现（Engine 通过 get_engine_ext 解析；协议实例直接复用，类/工厂按需实例化）。

官方默认实现迁入本模块（逻辑与原 engine.py 一致）：
- KahnScheduler             —— DFS 环检测 + Kahn 拓扑并行批次
- IncrementalCacheProvider  —— 增量缓存索引（sha256 缓存键 / LRU 淘汰 / 孤儿清理 / 落盘）
- InProcessExecutor        —— 同进程执行循环（批次 gather + 缓存命中 + 事件回调）

Engine（engine.py）改为组合本 SPI：scheduler 驱动拓扑、cache 驱动增量、executor 驱动循环。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, runtime_checkable

from .registry import get_spec
from .assets import Asset


# ---------------------------------------------------------------- 协议
@runtime_checkable
class Scheduler(Protocol):
    """拓扑调度器：环检测 + 并行批次。"""

    def validate(self, wf: Any) -> List[str]: ...

    def topological_batches(self, wf: Any) -> List[List[str]]: ...


@runtime_checkable
class CacheProvider(Protocol):
    """增量缓存：缓存键计算 / 命中判定 / 索引维护。"""

    def cache_key(self, wf: Any, node: Any) -> str: ...

    def cache_hit(self, cache_key: str, wf: Any, node: Any) -> bool: ...

    def touch(self, cache_key: str) -> None: ...

    def invalidate(self, cache_key: str) -> None: ...

    def store_result(self, cache_key: str, node: Any) -> None: ...

    def load(self) -> None: ...

    def save(self) -> None: ...


@runtime_checkable
class Executor(Protocol):
    """执行器：驱动整条执行循环（含缓存命中复用、状态回写、事件回调）。

    host 是 Engine 实例（scheduler/cache/_run_one/_emit 由其提供）。
    """

    async def execute(self, host: Any, wf: Any, run_node_ids: Optional[Set[str]] = None,
                      run_mode: str = "all") -> Any: ...


# ---------------------------------------------------------------- NodeContext
@dataclass
class NodeContext:
    """节点运行上下文（原 engine._run_one 的 ctx dict 的结构化形态）。

    第三方 Executor 用 NodeContext.make(engine, nid).to_dict() 构造节点可见上下文；
    字段语义与 docs（plugin-development-spec v3 §3.4）一致。
    """
    workdir: str
    store: Any
    logger: Callable[[Dict[str, Any]], None]
    progress: Callable[[float], None]
    spawn_tracked: Callable[[List[str], Any], Any]
    run_tracked: Callable[[List[str], Any], Any]
    track_subprocess: Callable[[Any], None]
    untrack_subprocess: Callable[[Any], None]
    subprocess_registry: Any
    node_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workdir": self.workdir,
            "store": self.store,
            "logger": self.logger,
            "progress": self.progress,
            "spawn_tracked": self.spawn_tracked,
            "run_tracked": self.run_tracked,
            "track_subprocess": self.track_subprocess,
            "untrack_subprocess": self.untrack_subprocess,
            "subprocess_registry": self.subprocess_registry,
        }

    @classmethod
    def make(cls, host: Any, nid: str) -> "NodeContext":
        """从 Engine 实例构造节点上下文（与 engine.py 原 _run_one 注入语义一致）。"""
        job_id = host.subprocess_job_id(nid)
        return cls(
            workdir=host.store.root,
            store=host.store,
            logger=host._emit,
            progress=lambda p: host._emit({"type": "node_update", "node_id": nid,
                                           "status": "running", "progress": p}),
            spawn_tracked=lambda cmd, **kw: host.spawn_tracked(job_id, cmd, **kw),
            run_tracked=lambda cmd, timeout=None, **kw: host.run_tracked(
                job_id, cmd, timeout=timeout, **kw),
            track_subprocess=lambda proc: host.track_subprocess(job_id, proc),
            untrack_subprocess=lambda proc: host.untrack_subprocess(job_id, proc),
            subprocess_registry=host,
            node_id=nid,
        )


# ---------------------------------------------------------------- 扩展注册表
_ENGINE_EXTS: Dict[str, Dict[str, Any]] = {
    "scheduler": {},
    "cache": {},
    "executor": {},
}


def register_engine_ext(kind: str, name: str, impl: Any) -> Any:
    """注册引擎扩展（scheduler|cache|executor）。impl 可为协议实例或工厂（类/可调用）。

    实例直接复用；类/工厂在 Engine 解析时实例化（cache 工厂收 (store, cache_file)）。
    """
    if kind not in _ENGINE_EXTS:
        raise ValueError(f"未知引擎扩展种类: {kind!r}（scheduler|cache|executor）")
    _ENGINE_EXTS[kind][name] = impl
    return impl


def get_engine_ext(kind: str, name: str = "default", *args: Any) -> Any:
    """取已注册扩展；工厂（类/可调用）按需实例化；协议实例直接复用。未注册 → KeyError。

    注意：类对象经 isinstance(cls, Protocol) 恒为 True（类本身带方法属性），
    故必须先判 type 再判协议实例，避免把工厂类当实例返回。
    """
    entry = _ENGINE_EXTS.get(kind, {}).get(name)
    if entry is None:
        raise KeyError(f"引擎扩展未注册: {kind}/{name}")
    if isinstance(entry, type):
        return entry(*args)                      # 工厂类：实例化
    if isinstance(entry, (Scheduler, CacheProvider, Executor)):
        return entry                             # 协议实例：直接复用
    if callable(entry):
        return entry(*args)                      # 其他工厂
    return entry


def list_engine_ext() -> Dict[str, List[str]]:
    return {k: sorted(v.keys()) for k, v in _ENGINE_EXTS.items()}


# ---------------------------------------------------------------- 官方默认：KahnScheduler
class KahnScheduler:
    """DFS 环检测 + Kahn 拓扑批次（逻辑迁自 Engine.validate/topological_batches）。"""

    @staticmethod
    def _adjacency(wf: Any) -> Dict[str, List[str]]:
        adj: Dict[str, List[str]] = {nid: [] for nid in wf.nodes}
        for e in wf.edges:
            s, t = e.get("source"), e.get("target")
            if s in adj and t in adj and t not in adj[s]:
                adj[s].append(t)
        return adj

    def validate(self, wf: Any) -> List[str]:
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

    def topological_batches(self, wf: Any) -> List[List[str]]:
        adj = self._adjacency(wf)
        indeg = {nid: 0 for nid in wf.nodes}
        for s in adj:
            for t in adj[s]:
                indeg[t] += 1
        batches: List[List[str]] = []
        queue = [nid for nid, d in indeg.items() if d == 0]
        queue.sort()
        while queue:
            batches.append(list(queue))
            next_q: List[str] = []
            for nid in queue:
                for nb in adj[nid]:
                    indeg[nb] -= 1
                    if indeg[nb] == 0:
                        next_q.append(nb)
            next_q.sort()
            queue = next_q
        return batches


# ---------------------------------------------------------------- 官方默认：IncrementalCacheProvider
class IncrementalCacheProvider:
    """增量缓存索引（逻辑迁自 Engine._cache_key/_cache_hit/_load/_save/_trim/invalidate）。"""

    MAX_CACHE_ENTRIES = 2000

    def __init__(self, store: Any, cache_file: Optional[str] = None):
        self.store = store
        self._cache_file = cache_file or os.path.join(store.root, "cache_index.json")
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.load()

    # ---- 键 ----
    def cache_key(self, wf: Any, node: Any) -> str:
        spec = get_spec(node.type_id)
        parts = [spec.type_id, spec.version]
        parts.append(json.dumps(node.params, sort_keys=True, ensure_ascii=False))
        for port_name, up_id in node.inputs.items():
            up = wf.nodes.get(up_id)
            if up and up.asset_ids.get(port_name):
                parts.append(up.asset_ids[port_name])
            else:
                parts.append("none")
        raw = json.dumps(parts, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    # ---- 索引维护 ----
    def load(self) -> None:
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f) or {}
        except Exception:
            self._cache = {}
        self._trim()

    def save(self) -> None:
        self._trim()
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _trim(self) -> None:
        if not self._cache:
            return
        stale = [k for k, v in self._cache.items()
                 if any(aid and self.store.get(aid) is None
                        for aid in (v.get("assets") or {}).values())]
        for k in stale:
            self._cache.pop(k, None)
        if len(self._cache) > self.MAX_CACHE_ENTRIES:
            ordered = sorted(self._cache.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in ordered[: len(self._cache) - self.MAX_CACHE_ENTRIES]:
                self._cache.pop(k, None)

    def invalidate(self, cache_key: str) -> None:
        if cache_key in self._cache:
            self._cache.pop(cache_key, None)
            self.save()

    def trim(self) -> None:
        """公开的索引维护入口（Engine._trim_cache 委托）。"""
        self._trim()

    def cache_hit(self, cache_key: str, wf: Any, node: Any) -> bool:
        entry = self._cache.get(cache_key)
        if not entry:
            return False
        assets = entry.get("assets") or {}
        for aid in assets.values():
            if not aid or self.store.get(aid) is None:
                return False
        return True

    def touch(self, cache_key: str) -> None:
        entry = self._cache.get(cache_key)
        if entry:
            entry["ts"] = time.time()

    def store_result(self, cache_key: str, node: Any) -> None:
        self._cache[cache_key] = {
            "assets": dict(node.asset_ids),
            "ts": time.time(),
            "type": node.type_id,
        }
        self.save()

    def entry(self, cache_key: str) -> Optional[Dict[str, Any]]:
        return self._cache.get(cache_key)


# ---------------------------------------------------------------- 官方默认：InProcessExecutor
class InProcessExecutor:
    """同进程执行循环（逻辑迁自 Engine.execute：目标集合/状态重置/缓存命中/批次 gather）。"""

    async def execute(self, host: Any, wf: Any, run_node_ids: Optional[Set[str]] = None,
                      run_mode: str = "all") -> Any:
        errors = host.scheduler.validate(wf)
        if errors:
            for e in errors:
                host._emit({"type": "graph_error", "message": e})
            return wf

        batches = host.scheduler.topological_batches(wf)

        targets: Set[str] = set()
        if run_mode == "all":
            targets = set(wf.nodes.keys())
        elif run_mode == "selection" and run_node_ids:
            targets = set(run_node_ids)
        elif run_mode == "upstream" and run_node_ids:
            visited: Set[str] = set()

            def up(nid: str):
                if nid in visited:
                    return
                visited.add(nid)
                for up_id in wf.nodes[nid].inputs.values():
                    up(up_id)
            for nid in run_node_ids:
                up(nid)
            targets = visited
        elif run_mode == "downstream" and run_node_ids:
            targets = set(run_node_ids)
            changed = True
            while changed:
                changed = False
                for s, t in [(e["source"], e["target"]) for e in wf.edges]:
                    if s in targets and t not in targets:
                        targets.add(t)
                        changed = True

        from .engine import NodeStatus
        for nid, node in wf.nodes.items():
            if nid in targets:
                new_key = host.cache.cache_key(wf, node)
                if node.status in (NodeStatus.SUCCESS, NodeStatus.CACHED) and node.cache_key != new_key:
                    node.status = NodeStatus.PENDING
                    node.error = ""
                    node.progress = 0.0
                elif node.status not in (NodeStatus.SUCCESS, NodeStatus.CACHED):
                    node.status = NodeStatus.PENDING
                    node.error = ""
                    node.progress = 0.0
                node.cache_key = new_key
            host._emit({"type": "node_update", "node_id": nid, "status": node.status.value,
                        "error": node.error, "progress": node.progress})

        for level in batches:
            to_run = [nid for nid in level if nid in targets and wf.nodes[nid].status == NodeStatus.PENDING]
            if not to_run:
                continue
            run_now: List[str] = []
            for nid in to_run:
                node = wf.nodes[nid]
                node.cache_key = host.cache.cache_key(wf, node)
                if host.cache.cache_hit(node.cache_key, wf, node):
                    entry = host.cache.entry(node.cache_key)
                    host.cache.touch(node.cache_key)
                    node.asset_ids = dict((entry or {}).get("assets") or {})
                    node.status = NodeStatus.CACHED
                    node.last_params = dict(node.params)
                    from .engine import _append_history
                    _append_history(node, "cached")
                    node.progress = 1.0
                    node.finished_at = time.time()
                    host._emit({"type": "node_update", "node_id": nid, "status": "cached",
                                "progress": 1.0, "asset_ids": node.asset_ids})
                else:
                    node.status = NodeStatus.QUEUED
                    host._emit({"type": "node_update", "node_id": nid, "status": "queued"})
                    run_now.append(nid)

            results = await asyncio.gather(*[host._run_one(wf, nid) for nid in run_now])
            for nid, ok in zip(to_run, results):
                pass

        host._emit({"type": "execution_done", "workflow_id": wf.id})
        return wf


# 官方默认扩展注册（cache 为工厂：Engine 实例化时传入 store/cache_file）
register_engine_ext("scheduler", "default", KahnScheduler())
register_engine_ext("cache", "default", IncrementalCacheProvider)
register_engine_ext("executor", "default", InProcessExecutor())
