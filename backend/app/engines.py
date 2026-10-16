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

# S1-T8 / C1（R-平台-8 第一路线）：控制流能力契约——
# ExecutionBlocker 提供一等语义；嵌套子图由 Engine._run_nested_subgraph
# （engine.py）内联执行（子图内嵌套执行 / 输入输出映射 / 跨层缓存），
# 已从 Roadmap 升级为 available。
EXECUTION_CONTROL_ROADMAP = {
    "nested_subgraph": "available",
    "execution_blocker": "available",
    "executor_spi": "available",
}

# 子图节点类型（C1 / R-平台-8 第一路线）：引擎按类型识别并内联执行。
SUBGRAPH_TYPE_IDS = ("core/subgraph", "core/group")
# 子图图定义参数名（params["graph"]：dict 或 JSON 字符串）
SUBGRAPH_PARAM = "graph"
# 子图输入种子节点：把外层输入资产注入内层端口（引擎内部机制节点）
SUBGRAPH_SEED_TYPE = "core/asset_ref"


def is_subgraph_type(type_id: str) -> bool:
    """该类型是否为子图节点（引擎内联执行，不经常规节点 run 通道）。"""
    return type_id in SUBGRAPH_TYPE_IDS


def parse_subgraph_graph(params: Dict[str, Any]) -> Dict[str, Any]:
    """从节点 params 解析子图图定义（dict 或 JSON 字符串，两种写法兼容）。

    图定义结构（键与 Workflow/画布语义对齐）：
      inputs:  [{name, node, port}]  外层输入端口 → 内层 (node, port)（经种子注入）
      outputs: [{name, node, port}]  内层 (node, port) 输出 → 外层输出端口
      nodes:   [{id, type, params?}] 内层节点
      edges:   [{source, target, sourceHandle?, targetHandle?}] 内层连线
    解析失败抛 ValueError（由 _run_nested_subgraph 收口为节点 FAILED）。
    """
    graph = (params or {}).get(SUBGRAPH_PARAM)
    if isinstance(graph, str):
        try:
            graph = json.loads(graph)
        except (TypeError, ValueError) as e:
            raise ValueError(f"子图参数 {SUBGRAPH_PARAM!r} 不是合法 JSON 字符串: {e}") from e
    if not isinstance(graph, dict):
        raise ValueError(
            f"子图节点缺少 {SUBGRAPH_PARAM!r} 参数（须为对象或 JSON 字符串）")
    for key in ("nodes",):
        if key not in graph or not isinstance(graph[key], list):
            raise ValueError(f"子图定义缺少合法 {key!r} 列表")
    return graph

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
    local_token: Callable[[], str] = lambda: ""

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
            "node_id": self.node_id,
            "local_token": self.local_token,
            # P3 双模式 API（与 WorkerContext 同名对齐）
            "resolve_secret": self.resolve_secret,
            "http": self.http,
            "fs_read": self.fs_read,
            "fs_write": self.fs_write,
        }

    @classmethod
    def make(cls, host: Any, nid: str) -> "NodeContext":
        """从 Engine 实例构造节点上下文（与 engine.py 原 _run_one 注入语义一致）。

        P3 迁移窗口双模式 API：resolve_secret/http/fs_read/fs_write 与 worker 侧
        WorkerContext 同名对齐——官方节点改造后主进程/worker 均可运行。
        """
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
            local_token=lambda: getattr(host, "_local_token", "") or "",
        )

    # ---- P3 双模式 API（worker 侧同名；主进程侧按官方 L0 宽松实现） ----
    def resolve_secret(self, ref: str) -> str:
        """主进程侧 secret 解析（worker 内经 RPC；此处直读保险箱）。"""
        from . import secrets
        return secrets.read_secret(ref) or ""

    def http(self, method: str, url: str, headers: Optional[dict] = None,
             body: Any = None, max_resp_bytes: int = 10 * 1024 * 1024) -> Dict[str, Any]:
        """主进程侧网络代理（worker 内经 RPC + gate）。urllib 实现，超时 30s。"""
        import base64
        import urllib.request
        req = urllib.request.Request(url, data=body, method=str(method).upper(),
                                     headers=dict(headers or {}))
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(max_resp_bytes + 1)
            return {"status": r.status,
                    "body_b64": base64.b64encode(raw[:max_resp_bytes]).decode("ascii"),
                    "headers": {k: v for k, v in r.headers.items()}}

    def fs_read(self, rel: str) -> bytes:
        with open(os.path.abspath(rel), "rb") as f:
            return f.read()

    def fs_write(self, rel: str, data_b64: str) -> str:
        import base64
        fp = os.path.abspath(rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(base64.b64decode(data_b64))
        return fp


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


def asset_for_literal(host: Any, node: Any, port_name: str, val: Any) -> Asset:
    """字面量输入 → 资产（按端口类型包装，与连线输入同构；S1-T12 / R-平台-3）。

    POST /prompt 的字面量直填输入（如 core/text_length 的 text='hello world 12345'）
    必须统一落资产后再入 inputs：直接填原始值会被内置节点资产契约
    （store.read_json(asset.id)）吞掉（AttributeError 被 except 兜住 → 输出恒 0）。
    inprocess 与 worker 两个执行通道共用本函数，保证两通道语义一致。
    """
    kind = "JSON"
    try:
        spec = get_spec(node.type_id)
        for p in spec.inputs:
            if p.name == port_name:
                kind = p.type.value
                break
    except Exception:  # noqa: BLE001 —— 未知 spec 兜底 JSON
        pass
    asset = Asset(id="", kind=kind,
                  meta={"node": getattr(node, "id", "") or "",
                        "port": port_name, "literal": True})
    return host.store.save_asset(asset, payload=val)


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
        # P1 图契约校验（s2-connectivity §5）：端口存在/类型兼容。
        # 引擎级用结构性校验（不查 required 输入——部分子图运行/重跑不应被未连接
        # 的无关 required 端口阻断；mode=all 的完整校验由运行 API 严格门负责）。
        # 与环检测合并返回；执行在调度批次前被阻止，错误带稳定码与端口名。
        # C1（R-平台-8 第一路线）：子图节点端口是动态的（由 params["graph"] 声明），
        # 结构校验器无法静态枚举其端口——凡涉及子图节点的句柄/类型兼容错误在本层
        # 豁免（内层图在执行时由 _run_nested_subgraph 走同一校验器严格把关；
        # 普通节点的端口/类型错误保持硬性阻断，字节级不弱化）。
        try:
            from .graph_validator import validate_graph
            subgraph_nodes = {nid for nid, g in wf.nodes.items()
                              if is_subgraph_type(getattr(g, "type_id", "") or "")}
            for er in validate_graph(wf, check_required=False):
                code = str(er.get("code") or "")
                nid = str(er.get("node_id") or "")
                if code in ("source_handle_invalid", "target_handle_invalid") \
                        and nid in subgraph_nodes:
                    continue
                if code == "port_type_incompatible":
                    src = str(er.get("source") or "").split(".")[0]
                    tgt = str(er.get("target") or "").split(".")[0]
                    if src in subgraph_nodes or tgt in subgraph_nodes:
                        continue
                errors.append(str(er.get("message") or code))
        except Exception:  # noqa: BLE001 —— 契约校验器异常不得阻断既有环检测
            pass
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
        # S1-T12（R-平台-3）：字面量输入参与缓存键——不同字面量不得命中同一
        # 缓存（否则跨请求串味：不同输入同一资产 id、结果串到旧字面量）。
        parts.append(json.dumps(node.literal_inputs or {},
                                sort_keys=True, ensure_ascii=False, default=str))
        for port_name, up_id in node.inputs.items():
            up = wf.nodes.get(up_id)
            aid = None
            if up:
                # C1（R-平台-8 第一路线）：槽位感知——/prompt 链接与子图种子/内部边
                # 按 input_slots 解析上游真实输出端口（同名端口回落原行为，零破坏）。
                out_port = port_name
                slot = (getattr(node, "input_slots", None) or {}).get(port_name)
                if slot is not None:
                    try:
                        up_spec = get_spec(up.type_id)
                        outs = up_spec.outputs if up_spec is not None else []
                        if 0 <= int(slot) < len(outs):
                            out_port = outs[int(slot)].name
                    except Exception:  # noqa: BLE001 —— 未知 spec 兜底同名查找
                        out_port = port_name
                aid = up.asset_ids.get(out_port)
            parts.append(aid if aid else "none")
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

    async def run_node(self, host: Any, wf: Any, nid: str) -> bool:
        """单节点执行钩子（默认同进程：委托 Engine._run_one 平台兜底）。

        子类（WorkerExecutor）重写本方法以替换执行路径（如 worker 进程隔离）。
        """
        return await host._run_one(wf, nid)

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

        # S1-T12 遗留观察项修复（E1）：级联失效——预扫描用上游旧资产 id 计算键，
        # 上游参数/资产指纹变化的节点即使键变化，其下游键在预扫描阶段仍不变；
        # 这里把「将重算（PENDING）节点 → targets 内下游 SUCCESS/CACHED 节点」
        # 一并置为 PENDING，使下游随上游新资产自动重算（编辑上游后直接重跑不再陈旧）。
        host.cascade_invalidate(wf, targets)

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

            results = await asyncio.gather(*[self.run_node(host, wf, nid) for nid in run_now])
            for nid, ok in zip(to_run, results):
                pass

        host._emit({"type": "execution_done", "workflow_id": wf.id})
        return wf


# ---------------------------------------------------------------- 官方默认：WorkerExecutor（P3 进程隔离）
class WorkerExecutor(InProcessExecutor):
    """worker 进程隔离执行器（P3，t7）：节点经独立 worker 子进程 + 受限 RPC 执行。

    - 复刻 InProcessExecutor 的循环（缓存/拓扑/状态语义不变），仅替换单节点执行路径：
      run_node → WorkerPool（驻留复用 W4 + 崩溃重试 S2）→ WorkerSupervisor。
    - 失败/超时/崩溃/越权统一按 §1.4 错误码收口为节点 FAILED；worker 崩溃绝不波及主进程。
    - 启用（S1-T2/R-平台-2）：默认开启——FW_PLUGIN_EXEC 留空 = worker（权限门五作用域
      默认生效）；FW_PLUGIN_EXEC=inprocess 仅显式降级，且 L2 无签名包仍强制 worker。
    """

    def __init__(self, pool: Any = None, **kw: Any):
        self._pool = pool

    async def run_node(self, host: Any, wf: Any, nid: str) -> bool:
        from .engine import NodeStatus, _append_history  # noqa: F401
        from .worker.supervisor import (WorkerJobError, WorkerPermission,
                                        WorkerSupervisor, WorkerTimeout)
        import time as _time

        node = wf.nodes[nid]
        try:
            # C1（R-平台-8 第一路线）：子图节点是引擎平台能力（内联执行嵌套
            # 工作流），不经 worker 通道（worker 无内层引擎环境）——统一回主进程
            # _run_one 分发；内层节点随后照常按各自信任级路由（含 worker）。
            if is_subgraph_type(node.type_id):
                return await host._run_one(wf, nid)
            # 收集输入资产（语义与 Engine._run_one 一致）
            inputs: Dict[str, Any] = {}
            for port_name, up_id in node.inputs.items():
                if isinstance(up_id, str) and up_id.startswith("@"):
                    asset = host.store.get(up_id[1:])
                    if asset is None:
                        raise RuntimeError(f"素材库资产缺失: {up_id[1:]}")
                    inputs[port_name] = asset
                    continue
                up = wf.nodes.get(up_id)
                if up is None:
                    raise RuntimeError(f"输入节点不存在: {up_id}")
                aid = up.asset_ids.get(port_name)
                inputs[port_name] = host.store.get(aid) if aid else None

            # S1-T3/R-平台-3：字面量直填输入（无上游链接的输入端口）——统一按端口
            # 类型包装为资产再入 inputs，与 inprocess 通道同构（worker 侧经
            # supervisor._materialize_inputs → host_bootstrap 描述符/载荷装载）。
            for port_name, val in (node.literal_inputs or {}).items():
                if port_name not in node.inputs:
                    inputs[port_name] = asset_for_literal(host, node, port_name, val)

            node.status = NodeStatus.RUNNING
            node.started_at = _time.time()
            host._emit({"type": "node_update", "node_id": nid, "status": "running", "progress": 0.0})

            from .worker.pool import WorkerPool
            # S1-T9（t5 发现修复，R-平台-9）：池生命周期绑定 Engine（host），
            # 而不是单例 executor 实例。WorkerExecutor 在模块尾部以单例注册，
            # 若把池挂到 self._pool，跨 Engine 复用的 supervisor 会保留首次
            # host（旧 store）——后续运行输出被导入旧 store，新 store 缓存索引
            # 被 trim（execution_cached 永不触发）。host._worker_pool 保证每个
            # Engine 各持一池（同引擎内 keep_alive 复用语义不变）。
            pool = self._pool                      # 显式注入（测试/宿主覆盖）优先
            if pool is None:
                pool = getattr(host, "_worker_pool", None)
            if pool is None:
                pool = WorkerPool(host)
                try:
                    host._worker_pool = pool
                except Exception:  # noqa: BLE001 —— 只读 host 兜底：池不缓存
                    pass
            outputs = await pool.run(host, node, inputs)
            node.asset_ids = {}
            for port_name, val in outputs.items():
                if val is None:
                    continue
                node.asset_ids[port_name] = val.id
            node.status = NodeStatus.SUCCESS
            node.progress = 1.0
            node.finished_at = _time.time()
            node.last_params = dict(node.params)
            _append_history(node, "success")
            if node.cache_key:
                host.cache.store_result(node.cache_key, node)
            host._emit({"type": "node_update", "node_id": nid, "status": "success",
                        "progress": 1.0, "asset_ids": node.asset_ids})
            return True
        except asyncio.CancelledError:
            if "sup" in locals():
                try:
                    sup.cancel()
                except Exception:  # noqa: BLE001
                    pass
            node.asset_ids = {}
            node.status = NodeStatus.CANCELLED
            node.finished_at = _time.time()
            _append_history(node, "cancelled")
            host._emit({"type": "node_update", "node_id": nid, "status": "cancelled"})
            return False
        except WorkerTimeout as e:
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = str(e)
            node.finished_at = _time.time()
            _append_history(node, "failed")
            host._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        except (WorkerPermission, WorkerJobError) as e:
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = str(e)
            node.finished_at = _time.time()
            _append_history(node, "failed")
            host._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        except BaseException as e:  # noqa: BLE001 —— worker 崩溃/平台异常收口，不外传
            if "sup" in locals():
                try:
                    sup.cancel()
                except Exception:  # noqa: BLE001
                    pass
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = _time.time()
            _append_history(node, "failed")
            host._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False


# 官方默认扩展注册（cache 为工厂：Engine 实例化时传入 store/cache_file）
register_engine_ext("scheduler", "default", KahnScheduler())
register_engine_ext("cache", "default", IncrementalCacheProvider)
register_engine_ext("executor", "default", InProcessExecutor())
register_engine_ext("executor", "worker", WorkerExecutor())


# ---------------------------------------------------------------- 逐节点信任路由执行器
class TrustAwareExecutor(InProcessExecutor):
    """inprocess 显式降级 + 存在 L2 无签名包时的逐节点路由执行器（S1-T12/R-平台-2/6）。

    R-平台-2 不变式是「L2 无签名包强制 worker」（供应链投毒面不受门禁约束），
    而非「整个平台强制 worker」：出厂内置 core_* 节点（L0 签名）与官方模板
    在用户显式 FW_PLUGIN_EXEC=inprocess 时应能同进程跑通离线链（R-平台-6
    出厂语义：内置节点+官方模板默认可跑；用户显式降级不被静默忽略）。
    本执行器按节点自身信任级路由：
      - L0/L1（官方签名/审核签名） → 同进程（host._run_one）；
      - L2（无签名社区包）        → worker 进程隔离（WorkerExecutor，
        权限门五作用域生效，R-平台-2 强制语义在节点级保持）。
    """
    def __init__(self):
        self._worker: Optional["WorkerExecutor"] = None

    async def run_node(self, host: Any, wf: Any, nid: str) -> bool:
        # C1：子图节点为平台能力，无论信任级都内联执行（不落 worker）。
        if is_subgraph_type(wf.nodes[nid].type_id):
            return await host._run_one(wf, nid)
        if host.trust_level(wf.nodes[nid].type_id) == "L2":
            if self._worker is None:
                self._worker = WorkerExecutor()
            return await self._worker.run_node(host, wf, nid)
        return await host._run_one(wf, nid)


register_engine_ext("executor", "inprocess", TrustAwareExecutor())
