"""图执行引擎：DAG 校验、拓扑排序、增量缓存、状态回调、并行执行。"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from .registry import get_class, get_spec
from .assets import Asset, AssetStore
from .nodespec import NodeBase, NodeSpec
from . import engines


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

@dataclass
class SubprocessHandle:
    """托管子进程句柄（registry 条目）。"""
    proc: "subprocess.Popen"
    job_id: Optional[str] = None
    spawned_at: float = 0.0

class Engine:
    """执行引擎：给定 Workflow，计算 DAG，按拓扑分批并行执行。"""

    _MAX_CACHE_ENTRIES = 2000  # 缓存索引上限（超出按 ts 淘汰最旧）

    # ---------- 平台兜底 / 子进程托管（P0.5 加固，依据运行时安全隔离设计 §2-3） ----------
    # 节点级超时分级（L0 渲染类保留 1800s 上限；L1/L2 见设计矩阵，按声明覆盖）
    TIMEOUT_BY_TRUST: Dict[str, float] = {
        "L0": 600.0,      # 官方：渲染类经 _render_timeout() 提升至 1800s
        "L1": 300.0,      # 审核
        "L2": 120.0,      # 社区
    }
    # 节点级超时声明上限（manifest.timeout_seconds 可覆盖，但不能超过信任级上限）
    TIMEOUT_MAX_BY_TRUST: Dict[str, float] = {
        "L0": 7200.0,
        "L1": 1800.0,
        "L2": 900.0,
    }
    SETUP_TIMEOUT_DEFAULT = 120.0   # setup 上限 = min(120s, run 超时的 1/3)

    def __init__(self, store: AssetStore, on_event: Optional[Callable] = None,
                 trust_levels: Optional[Dict[str, str]] = None,
                 scheduler: Optional[engines.Scheduler] = None,
                 cache: Optional[engines.CacheProvider] = None,
                 executor: Optional[engines.Executor] = None):
        """P2（t4）SPI 组合：scheduler/cache/executor 可注入，缺省从引擎扩展注册表解析。

        - scheduler: KahnScheduler（默认）——环检测 + 拓扑批次
        - cache: IncrementalCacheProvider（默认）——增量缓存索引
        - executor: InProcessExecutor（默认）——同进程执行循环
        """
        self.store = store
        self.on_event = on_event or (lambda ev: None)  # 事件: {"type":..., ...}
        # P2 引擎 SPI（协议见 engines.py；register_engine_ext 注册第三方实现）
        self.scheduler: engines.Scheduler = scheduler or engines.get_engine_ext("scheduler", "default")
        self.cache: engines.CacheProvider = cache or engines.get_engine_ext(
            "cache", "default", store, os.path.join(store.root, "cache_index.json"))
        self.executor: engines.Executor = executor or engines.get_engine_ext("executor", "default")
        self._cache_file = os.path.join(store.root, "cache_index.json")
        self._cache: Dict[str, Dict[str, Any]] = {}
        # 节点类型 -> 信任级（未登记按官方 L0 兜底；插件登记由声明/市场模块填充）
        self._trust_levels: Dict[str, str] = dict(trust_levels or {})
        # 子进程托管 registry：job_id -> [SubprocessHandle...]（§3.2 同 job 可挂多个 proc）
        self._subprocess_registry: Dict[str, List[SubprocessHandle]] = {}
        self._registry_lock = threading.Lock()
        self._load_cache()

    # ---- 信任级 / 超时 ----
    def set_trust_level(self, type_id: str, level: str) -> None:
        """登记节点信任级（L0/L1/L2）；未知类型默认 L0。"""
        if level not in ("L0", "L1", "L2"):
            raise ValueError(f"非法信任级: {level}")
        self._trust_levels[type_id] = level

    def trust_level(self, type_id: str) -> str:
        return self._trust_levels.get(type_id, "L0")

    def node_timeout(self, type_id: str, declared: Optional[float] = None) -> float:
        """节点 run 超时（秒）。按信任级取默认；声明值在信任级上限内可覆盖。

        declared 仅当节点未把 timeout_seconds 用作业务参数时才认可（防误伤）。
        """
        lvl = self.trust_level(type_id)
        base = self.TIMEOUT_BY_TRUST.get(lvl, 600.0)
        cap = self.TIMEOUT_MAX_BY_TRUST.get(lvl, 7200.0)
        if self._is_render_node(type_id) and lvl == "L0":
            base = 1800.0  # 渲染类 L0 上限保留
        if declared is not None and declared > 0:
            return min(max(declared, 1.0), cap)
        return base

    @staticmethod
    def _param_is_business(type_id: str, name: str) -> bool:
        """timeout_seconds 是否被节点声明为业务参数（若是，不作为超时声明使用）。"""
        try:
            spec = get_spec(type_id)
        except Exception:
            return False
        return any(param.name == name for param in spec.params)

    @staticmethod
    def _is_render_node(type_id: str) -> bool:
        """渲染类判定：官方渲染/批量出片节点保留 1800s 语义（L0）。"""
        tid = type_id.lower()
        return tid in ("core/video_render", "core/batch_render") or "render" in tid

    def setup_timeout(self, type_id: str, run_timeout: float) -> float:
        """setup 超时 = min(默认 120s, run 超时的 1/3)。"""
        return min(self.SETUP_TIMEOUT_DEFAULT, max(run_timeout / 3.0, 1.0))

    # ---- tracked 子进程托管（取消/超时 kill 进程树，无孤儿） ----
    def track_subprocess(self, job_id: str, proc: subprocess.Popen) -> None:
        """把子进程登记进节点托管 registry（同一 job_id 可挂多个 proc）。"""
        with self._registry_lock:
            self._subprocess_registry.setdefault(job_id, []).append(
                SubprocessHandle(proc, job_id, time.time()))

    def untrack_subprocess(self, job_id: str, proc: subprocess.Popen) -> None:
        with self._registry_lock:
            lst = self._subprocess_registry.get(job_id)
            if lst:
                keep = [h for h in lst if h.proc is not proc]
                if keep:
                    self._subprocess_registry[job_id] = keep
                else:
                    self._subprocess_registry.pop(job_id, None)

    def tracked_procs(self, job_id: str) -> List[subprocess.Popen]:
        """当前 job 下仍存活的托管子进程（供取消/超时 kill 进程树）。"""
        with self._registry_lock:
            lst = self._subprocess_registry.get(job_id) or []
            return [h.proc for h in lst if h.poll() is None]

    def kill_tracked_tree(self, job_id: str) -> int:
        """kill 托管子进程树（Windows: taskkill /T /F；POSIX: kill 进程组），返回杀掉的进程数。

        取消/超时统一走这里：杀进程树（含孙进程），杜绝孤儿；随后强制清空 registry。
        """
        killed = 0
        with self._registry_lock:
            lst = self._subprocess_registry.pop(job_id, None) or []
        for h in lst:
            if h.proc is None or h.proc.poll() is not None:
                continue
            pid = h.proc.pid
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=5)
                else:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (AttributeError, ProcessLookupError):
                        h.proc.kill()
                killed += 1
            except Exception:
                try:
                    h.proc.kill()
                    killed += 1
                except Exception:
                    pass
        return killed

    def spawn_tracked(self, job_id: str, cmd: List[str], **popen_kw) -> "subprocess.Popen":
        """托管子进程入口（插件节点在 P0 窗口内调用）：登记 registry + POSIX 进程组。"""
        if "creationflags" not in popen_kw and os.name == "nt":
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        if "start_new_session" not in popen_kw and os.name != "nt":
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kw)
        self.track_subprocess(job_id, proc)
        return proc

    async def run_tracked(self, job_id: str, cmd: List[str], timeout: Optional[float] = None,
                          **popen_kw) -> "subprocess.CompletedProcess":
        """to_thread 托管子进程执行（同步阻塞移出事件循环，P0 快路径）。

        超时/取消时 kill 整个进程树并回收 registry；返回 CompletedProcess（含 returncode）。
        """
        proc = self.spawn_tracked(job_id, cmd, **popen_kw)
        try:
            def _wait():
                return proc.wait(timeout)
            try:
                rc = await asyncio.to_thread(_wait)
            except subprocess.TimeoutExpired:
                self.kill_tracked_tree(job_id)
                raise asyncio.TimeoutError(f"子进程超时（{timeout}s）: {' '.join(cmd[:3])}")
            if proc.stdout is not None:
                out = await asyncio.to_thread(proc.stdout.read)
            else:
                out = None
            if proc.stderr is not None:
                err = await asyncio.to_thread(proc.stderr.read)
            else:
                err = None
            return subprocess.CompletedProcess(cmd, rc, out, err)
        except asyncio.CancelledError:
            self.kill_tracked_tree(job_id)
            raise
        finally:
            self.untrack_subprocess(job_id, proc)
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass

    def subprocess_job_id(self, nid: str) -> str:
        return f"wf-{nid}"  # 每节点一个 job 命名空间（registry 键）


    # ---- 缓存：委托 IncrementalCacheProvider（P2 SPI，可注入替换） ----
    def _load_cache(self) -> None:
        self.cache.load()

    def _save_cache(self) -> None:
        self.cache.save()

    def _trim_cache(self) -> None:
        """兼容壳：缓存索引维护（孤儿剔除 + LRU 淘汰）由 CacheProvider.trim 实施。"""
        self.cache.trim()

    def invalidate_cache(self, cache_key: str) -> None:
        """清除单个缓存键（用于强制重算）。"""
        self.cache.invalidate(cache_key)

    def _cache_hit(self, cache_key: str, wf: Workflow, node: GraphNode) -> bool:
        """缓存命中：索引存在且所有输出资产仍存在。"""
        return self.cache.cache_hit(cache_key, wf, node)

    # ---------- DAG 校验（P2：委托 KahnScheduler，可注入替换） ----------
    def validate(self, wf: Workflow) -> List[str]:
        """返回环检测结果（错误信息列表）。"""
        return self.scheduler.validate(wf)

    def topological_batches(self, wf: Workflow) -> List[List[str]]:
        """Kahn 拓扑排序，返回并行批次。"""
        return self.scheduler.topological_batches(wf)

    # ---------- 缓存键（委托 CacheProvider） ----------
    def _cache_key(self, wf: Workflow, node: GraphNode) -> str:
        return self.cache.cache_key(wf, node)

    # ---------- 执行（委托 Executor；默认 InProcessExecutor） ----------
    async def execute(self, wf: Workflow, run_node_ids: Optional[Set[str]] = None,
                      run_mode: str = "all") -> Workflow:
        """执行工作流（P2：循环逻辑在 Executor SPI，可注入第三方实现替换）。
        run_mode: all | upstream (运行到选中节点) | selection | downstream
        """
        return await self.executor.execute(self, wf, run_node_ids=run_node_ids, run_mode=run_mode)

    async def _run_one(self, wf: Workflow, nid: str) -> bool:
        """单节点执行（平台兜底）：

        - 捕 BaseException：插件 SystemExit/KeyboardInterrupt/GeneratorExit 等转 FAILED，
          绝不向外传播（不杀 uvicorn/冻结事件循环）；MemoryError 等同样收口。
        - run/setup 包 asyncio.timeout 分级超时（L0 渲染类 1800s 上限保留）。
        - 取消语义完整：CancelledError 回滚资产 + 清空子进程 registry（kill 进程树）。
        - 回归不变式：缓存键、拓扑批次、事件语义与加固前一致（只新增兜底层）。
        """
        node = wf.nodes[nid]
        job_id = self.subprocess_job_id(nid)
        saved_ids: List[str] = []  # 本次运行已落盘资产（失败/取消时回滚，防孤儿残留）
        try:
            cls = get_class(node.type_id)
            spec = get_spec(node.type_id)
            # timeout_seconds 作为超时声明（manifest.timeout_seconds 的 P0 参数透传），
            # 但节点若把它声明为业务参数则忽略（防插件数据参数被误当超时）。
            declared = node.params.get("timeout_seconds")
            if self._param_is_business(node.type_id, "timeout_seconds"):
                declared = None
            run_timeout = self.node_timeout(node.type_id, declared)
            setup_timeout = self.setup_timeout(node.type_id, run_timeout)
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

            # P2：节点上下文统一由 NodeContext.make 构造（engines.py SPI，可扩展字段）
            ctx = engines.NodeContext.make(self, nid).to_dict()

            node_obj: NodeBase = cls()
            # setup（一次性）：超时上限 = min(120s, run 超时/3)；NotImplementedError 视为无初始化
            try:
                async with asyncio.timeout(setup_timeout):
                    await node_obj.setup(ctx)
            except NotImplementedError:
                pass
            except asyncio.TimeoutError:
                raise TimeoutError(f"节点 setup 超时（{setup_timeout:g}s）: {nid}")

            async with asyncio.timeout(run_timeout):
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
                    saved_ids.append(val.id)
                    node.asset_ids[spec_p.name] = val.id
                elif isinstance(val, (dict, list, str, int, float, bool)):
                    asset = Asset(id="", kind=spec_p.type.value, meta={"node": nid, "port": spec_p.name})
                    asset = self.store.save_asset(asset, payload=val)
                    saved_ids.append(asset.id)
                    node.asset_ids[spec_p.name] = asset.id

            node.status = NodeStatus.SUCCESS
            node.progress = 1.0
            node.finished_at = time.time()
            node.last_params = dict(node.params)  # 记录参数快照（差异高亮）
            _append_history(node, "success")
            # 写入缓存索引（增量复用，经 CacheProvider SPI）
            if node.cache_key:
                self.cache.store_result(node.cache_key, node)
            self._emit({"type": "node_update", "node_id": nid, "status": "success",
                        "progress": 1.0, "asset_ids": node.asset_ids})
            return True
        except asyncio.CancelledError:
            # 取消语义完整：回滚已落盘资产 + kill 托管子进程树（无孤儿）+ 节点 CANCELLED
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.CANCELLED
            node.finished_at = time.time()
            _append_history(node, "cancelled")
            self._emit({"type": "node_update", "node_id": nid, "status": "cancelled"})
            return False
        except (SystemExit, KeyboardInterrupt, GeneratorExit) as e:
            # 插件尝试退出进程/中断：隔离为节点 FAILED，绝不向外传播（不杀 uvicorn）
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"PLUGIN_RUNTIME_ERROR: 插件尝试退出进程或中断，已隔离 ({type(e).__name__})"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        except asyncio.TimeoutError:
            # 分级超时（§2.1）：run 超时在 _run_one 入口按信任级解析；L0 渲染类 1800s 上限保留。
            # 超时处置 = FAILED(PLUGIN_TIMEOUT) + 杀残留子进程树（design §2.1）。
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"PLUGIN_TIMEOUT: 节点超时（{run_timeout:g}s）: {nid}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        except Exception as e:
            # 通用运行错误（保留既有语义：error = "{Type}: {msg}"）
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        except BaseException as e:
            # 其余 BaseException（MemoryError 等）收口为 FAILED + 隔离标记（不杀 uvicorn）
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": nid, "status": "failed", "error": node.error})
            return False
        finally:
            # 兜底：无论成功/失败/取消/超时，清空节点子进程 registry（防泄漏/孤儿）
            self.kill_tracked_tree(job_id)

    def _emit(self, ev: Dict[str, Any]) -> None:
        try:
            self.on_event(ev)
        except Exception:
            pass
