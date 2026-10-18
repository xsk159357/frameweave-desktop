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
from . import declarative, engines


class NodeStatus(str, Enum):
    PENDING = "pending"      # 待运行
    QUEUED = "queued"        # 已入队
    RUNNING = "running"      # 运行中
    SUCCESS = "success"      # 成功
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消
    BLOCKED = "blocked"      # ExecutionBlocker：显式阻断后续执行
    CACHED = "cached"        # 缓存命中（视为成功）


class ExecutionBlocker(Exception):
    """显式控制流阻断信号（S1-T8 / R-平台-8）。"""


# 数据驱动流水线阶段：默认值仅作兼容兜底，可由环境变量覆盖。
DEFAULT_PIPELINE_STAGES = (
    "created", "scene_detected", "asr_completed", "narration_generated",
    "visual_matched", "tts_completed", "subtitles_composed", "rendered", "exported",
)


def pipeline_stages_from_env() -> List[str]:
    """读取 FRAMEWEAVE_PIPELINE_STAGES（JSON 数组或逗号分隔），不合法则默认。"""
    raw = os.environ.get("FRAMEWEAVE_PIPELINE_STAGES", "").strip()
    if not raw:
        return list(DEFAULT_PIPELINE_STAGES)
    try:
        val = json.loads(raw) if raw.startswith("[") else [x.strip() for x in raw.split(",")]
        stages = [str(x).strip() for x in val if str(x).strip()]
        return stages or list(DEFAULT_PIPELINE_STAGES)
    except (TypeError, ValueError, json.JSONDecodeError):
        return list(DEFAULT_PIPELINE_STAGES)


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
    # D-05（t9）：节点失败的结构化明细（worker 运行错误 traceback 等；用户可读
    # 主文案在 error 字段，detail 仅作排障折叠展示，默认空串）。
    error_detail: str = ""
    progress: float = 0.0
    cache_key: str = ""
    asset_ids: Dict[str, str] = field(default_factory=dict)  # 端口名 -> asset id
    last_params: Dict[str, Any] = field(default_factory=dict)  # 上次运行参数快照（差异高亮）
    run_history: List[Dict[str, Any]] = field(default_factory=list)  # 运行历史（每次运行一条）
    started_at: float = 0.0
    finished_at: float = 0.0
    # S1-T3（R-平台-3）：/prompt 纯执行图链接语义 —— 输入端口 -> 上游输出槽位（索引），
    # 原生画布链接（源/目标端口同名）不设置本字段，保持既有查找行为。
    input_slots: Dict[str, int] = field(default_factory=dict)
    # S1-T3：/prompt 字面量直填输入（输入端口无上游链接时生效）
    literal_inputs: Dict[str, Any] = field(default_factory=dict)
    # C1（R-平台-8 第一路线）：最近一次子图内层执行摘要（观测/验收用，不落盘）——
    # {inner_id, statuses:{nid:status}, seed_ids:[...]}；缓命中不更新（未真正执行）。
    subgraph_last_run: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id, "type": self.type_id, "x": self.x, "y": self.y,
            "params": self.params, "status": self.status.value,
            "inputs": self.inputs, "outputs": self.outputs,
            "error": self.error, "progress": self.progress,
            "error_detail": self.error_detail,
            "cache_key": self.cache_key, "asset_ids": self.asset_ids,
            "last_params": self.last_params,
            "run_history": self.run_history,
            "input_slots": self.input_slots,
            "literal_inputs": self.literal_inputs,
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
    # 影视流水线阶段状态：用于断点续跑、恢复入口和 UI 展示
    pipeline_stage: str = "created"
    pipeline_stage_index: int = 0
    # S1-T8：阶段定义数据驱动；默认值保持历史兼容，可由
    # FRAMEWEAVE_PIPELINE_STAGES（JSON 数组/逗号分隔）覆盖。
    pipeline_stages: List[str] = field(default_factory=pipeline_stages_from_env)
    pipeline_checkpoint: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": self.edges,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "pipeline_stage": self.pipeline_stage,
            "pipeline_stage_index": self.pipeline_stage_index,
            "pipeline_stages": list(self.pipeline_stages),
            "pipeline_checkpoint": dict(self.pipeline_checkpoint),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Workflow":
        wf = cls(id=d["id"], name=d.get("name", "未命名工作流"),
                 edges=d.get("edges", []), created_at=d.get("created_at", time.time()))
        wf.updated_at = d.get("updated_at", time.time())
        wf.pipeline_stage = d.get("pipeline_stage", "created")
        wf.pipeline_stage_index = int(d.get("pipeline_stage_index", 0) or 0)
        wf.pipeline_stages = list(d.get("pipeline_stages") or wf.pipeline_stages)
        wf.pipeline_checkpoint = dict(d.get("pipeline_checkpoint") or {})
        for nd in d.get("nodes", []):
            n = GraphNode(
                id=nd["id"], type_id=nd["type"],
                x=nd.get("x", 0.0), y=nd.get("y", 0.0),
                params=nd.get("params", {}),
                inputs=nd.get("inputs", {}), outputs=nd.get("outputs", {}),
                status=NodeStatus(nd.get("status", "pending")),
                error=nd.get("error", ""), progress=nd.get("progress", 0.0),
                error_detail=nd.get("error_detail", ""),
                cache_key=nd.get("cache_key", ""),
                asset_ids=nd.get("asset_ids", {}),
                last_params=nd.get("last_params", {}),
                run_history=nd.get("run_history", []),
                input_slots=nd.get("input_slots", {}),
                literal_inputs=nd.get("literal_inputs", {}),
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
        - executor: WorkerExecutor（S1-T2/R-平台-2 默认）——worker 进程隔离；
          FW_PLUGIN_EXEC=inprocess 仅显式降级（无 L2 包 → 纯同进程；
          有 L2 包 → TrustAwareExecutor 逐节点路由：L0/L1 同进程、L2 强制 worker）。
        """
        self.store = store
        self.on_event = on_event or (lambda ev: None)  # 事件: {"type":..., ...}
        # P2 引擎 SPI（协议见 engines.py；register_engine_ext 注册第三方实现）
        self.scheduler: engines.Scheduler = scheduler or engines.get_engine_ext("scheduler", "default")
        self.cache: engines.CacheProvider = cache or engines.get_engine_ext(
            "cache", "default", store, os.path.join(store.root, "cache_index.json"))
        self._cache_file = os.path.join(store.root, "cache_index.json")
        self._cache: Dict[str, Dict[str, Any]] = {}
        # 节点类型 -> 信任级（未登记按官方 L0 兜底；插件登记由声明/市场模块填充）
        self._trust_levels: Dict[str, str] = dict(trust_levels or {})
        if not self._trust_levels:
            self._trust_levels = self._discover_plugin_trust()
        # S1-T2（R-平台-2）：worker 隔离默认开启——FW_PLUGIN_EXEC 留空 = worker（outprocess），
        # 权限门五作用域（fs/network/subprocess/secret/env）在默认配置下生效；
        # inprocess 仅显式降级，且存在 L2 无签名包时 L2 节点强制 worker。
        # S1-T12（R-平台-2/6 复核）：L2 强制是「节点级」不变式而非「平台级」——
        # 存在 L2 包时装配 TrustAwareExecutor（L0/L1 同进程、L2 走 worker），
        # 出厂内置节点+官方模板在用户显式降级下仍可同进程跑通离线链。
        exec_name = os.environ.get("FW_PLUGIN_EXEC", "worker")
        if exec_name == "inprocess":
            # 显式降级：无 L2 无签名包 → 纯同进程（default）；存在 L2 无签名包 →
            # 逐节点路由（inprocess：L0/L1 同进程、L2 强制 worker）。
            if any(level == "L2" for level in self._trust_levels.values()):
                exec_name = "inprocess"
            else:
                exec_name = "default"
        self.executor: engines.Executor = executor or engines.get_engine_ext("executor", exec_name)
        # 执行模式（t29：worker 模式依赖检查报告）——Engine 实际装配的 executor 决定
        self._exec_mode = "worker" if isinstance(self.executor, engines.WorkerExecutor) else "inprocess"
        # 本地子服务令牌（voice_clone 等 V1 鉴权；仅单值，绝不把环境全文交给 worker）
        self._local_token: str = os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "") or ""
        # 子进程托管 registry：job_id -> [SubprocessHandle...]（§3.2 同 job 可挂多个 proc）
        self._subprocess_registry: Dict[str, List[SubprocessHandle]] = {}
        self._registry_lock = threading.Lock()
        self._load_cache()

    def _discover_plugin_trust(self) -> Dict[str, str]:
        """按已加载插件 manifest 的签名键推断信任级（R-平台-2：L2 无签名包强制 worker）。

        未显式注入 trust_levels 时的兜底填充：L0=官方锚签名 / L1=有签名 / L2=无签名。
        发现失败不阻断（未知类型沿用 L0 兜底，与 engine.trust_level 语义一致）。
        """
        out: Dict[str, str] = {}
        try:
            from . import declarative
            from .worker.supervisor import trust_of
            for spec in declarative.list_specs():
                pd = declarative.pkg_dir(spec.type_id)
                if not pd:
                    continue
                try:
                    with open(os.path.join(pd, "manifest.json"), "r", encoding="utf-8") as f:
                        out[spec.type_id] = trust_of(json.load(f))
                except (OSError, ValueError):
                    continue
        except Exception:  # noqa: BLE001 —— 发现失败不阻断（默认 L0 兜底）
            pass
        return out

    # ---- 执行模式（t29：worker 模式依赖检查报告） ----
    @staticmethod
    def current_execution_mode() -> str:
        """平台当前执行模式：'worker' | 'inprocess'（按 FW_PLUGIN_EXEC 环境开关判定）。

        S1-T2（R-平台-2）：默认 worker（outprocess，权限门五作用域生效）；
        inprocess 仅显式降级（FW_PLUGIN_EXEC=inprocess），且 L2 无签名包仍强制 worker。
        """
        return "inprocess" if os.environ.get("FW_PLUGIN_EXEC", "") == "inprocess" else "worker"

    def execution_mode(self) -> str:
        """本 Engine 实例实际装配的执行模式（executor 解析结果，权威）。"""
        return self._exec_mode

    def network_gate_report(self, type_id: str, url: str = "") -> Dict[str, Any]:
        """worker 模式依赖检查：给定节点的网络门生效情况。

        进程内模式：网络不经 PermissionGate（urllib 直连，无门控）→ 报告 mode=inprocess、
        gated=False；worker 模式：按该节点 manifest.permission.network 构建 gate 并报告
        allow/deny/通配/本地服务声明与对 url 的判定（不抛异常）。
        """
        if self._exec_mode != "worker":
            return {"mode": "inprocess", "gated": False,
                    "allow": [], "deny": [], "wildcard": False,
                    "local_services": [], "url": url, "verdict": "inprocess: 网络不经权限门"}
        try:
            from .worker.perm import PermissionGate
            pkg_dir = declarative.pkg_dir(type_id)
            if not pkg_dir:
                return {"mode": "worker", "gated": True, "error": "插件包目录未找到: %s" % type_id}
            with open(os.path.join(pkg_dir, "manifest.json"), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            gate = PermissionGate(manifest, {}, env_values={})
            report = gate.network_gate_report(url)
            report["mode"] = "worker"
            report["gated"] = True
            return report
        except Exception as e:  # noqa: BLE001
            return {"mode": "worker", "gated": True, "error": str(e)}

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

    def cascade_invalidate(self, wf: Workflow, targets: Set[str]) -> Set[str]:
        """级联失效（S1-T12 遗留观察项 / E1）：executor 预扫描后的下游缓存失效。

        背景：预扫描在节点执行前用上游**当前（旧）资产 id** 计算全部 cache key——
        编辑上游参数后直接重跑时，上游节点键变化 → PENDING（将重算），但其下游节点
        的键仍沿用上游旧资产 id → 键不变 → 保持 CACHED/SUCCESS 不重算（下游陈旧）。
        本方法把「本运行内将重算（PENDING）的节点 → 其在 targets 内的下游
        SUCCESS/CACHED 节点」一并置为 PENDING，使下游随上游新资产自动级联重算，
        不依赖显式 reset 通道。

        语义边界：只失效本次运行范围（targets）内的下游；范围外节点留待其下一次
        被纳入运行范围时由 cache key 变化自然失效。本方法只改节点状态，不动缓存
        索引条目（旧条目与参数变更路径一致，留待 trim/LRU 回收；下游重算后的新键
        天然不再命中旧条目）。幂等：已 PENDING 节点跳过。

        返回本轮被级联失效（SUCCESS/CACHED → PENDING）的节点 id 集合（不含源脏节点）。
        """
        dirty = {nid for nid, node in wf.nodes.items()
                 if nid in targets and node.status == NodeStatus.PENDING}
        invalidated: Set[str] = set()
        changed = True
        while changed:
            changed = False
            for e in wf.edges:
                s, t = e.get("source"), e.get("target")
                if s in dirty and t in targets and t in wf.nodes and t not in dirty:
                    tn = wf.nodes[t]
                    if tn.status in (NodeStatus.SUCCESS, NodeStatus.CACHED):
                        tn.status = NodeStatus.PENDING
                        tn.error = ""
                        tn.progress = 0.0
                        dirty.add(t)
                        invalidated.add(t)
                        changed = True
                        self._emit({"type": "node_update", "node_id": t,
                                    "status": "pending", "error": "", "progress": 0.0})
        return invalidated

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

        t7：执行前兜底派生跨名边 input_slots（保存/加载链之外的直达路径——
        如瞬时 /prompt 工作流——同样受益；幂等，未知类型跳过，同名回落不变）。
        """
        try:
            from . import slots  # noqa: PLC0415
            slots.derive_input_slots(wf)
        except Exception:  # noqa: BLE001 —— 派生失败不阻断执行
            pass
        return await self.executor.execute(self, wf, run_node_ids=run_node_ids, run_mode=run_mode)

    def _collect_inputs(self, wf: Workflow, node: GraphNode) -> Dict[str, Any]:
        """收集节点输入资产（常规节点与子图节点共用，语义:端口名 -> Asset|None）。

        - "@资产id" 直引素材库资产；
        - 画布链接按端口名取上游 asset；/prompt 链接按 input_slots 槽位解析输出端口；
        - 无上游链接的输入端口取 literal_inputs 字面值（按端口类型包装为资产）。
        """
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
            # S1-T3：/prompt 链接语义按上游输出槽位解析端口名；
            # 原生画布链接（未设置 input_slots）保持按同名端口查找（现状不变）。
            out_port = port_name
            slot = (node.input_slots or {}).get(port_name)
            if slot is not None:
                try:
                    up_spec = get_spec(up.type_id)
                    outs = up_spec.outputs if up_spec is not None else []
                    if 0 <= int(slot) < len(outs):
                        out_port = outs[int(slot)].name
                except Exception:
                    out_port = port_name
            aid = up.asset_ids.get(out_port)
            if aid:
                asset = self.store.get(aid)
                if asset is None:
                    raise RuntimeError(f"上游资产缺失: {aid}")
                inputs[port_name] = asset
            else:
                inputs[port_name] = None

        # S1-T3：/prompt 字面量直填输入（无上游链接的输入端口取声明字面值）。
        # S1-T12（R-平台-3）：统一按端口类型包装为资产再入 inputs——直填原始值
        # 会被内置节点资产契约（store.read_json(asset.id)）吞掉（输出恒 0）。
        for port_name, val in (node.literal_inputs or {}).items():
            if port_name not in node.inputs:
                inputs[port_name] = engines.asset_for_literal(self, node, port_name, val)
        return inputs

    @staticmethod
    def _output_slot(type_id: str, port_name: str) -> Optional[int]:
        """上游输出端口名 → 输出槽位（/prompt 槽位语义；未知类型/端口返回 None）。"""
        try:
            spec = get_spec(type_id)
            for i, p in enumerate(spec.outputs):
                if p.name == port_name:
                    return i
        except Exception:  # noqa: BLE001 —— 未知 spec 兜底 None（回落同名查找）
            pass
        return None

    def _build_subgraph_workflow(self, outer_wf: Workflow, node: GraphNode,
                                 graph: Dict[str, Any],
                                 inputs: Dict[str, Any]) -> "tuple[Workflow, List[str]]":
        """按子图图定义构造内层 Workflow（C1 / R-平台-8 第一路线）。

        - graph["nodes"] → 内层节点（id/type/params）；
        - graph["inputs"] [{name,node,port}] → 外层输入端口 name 经 core/asset_ref
          种子节点注入内层 (node, port)（种子 id 带外节点前缀防冲突）；
        - graph["edges"] [{source,target,sourceHandle?,targetHandle?}] → 内层连线 +
          node.inputs 映射；源/目标端口名不同名时经 input_slots 槽位解析
          （与 /prompt 链接语义一致），动态子图源端口回落同名查找；
        - graph["outputs"] [{name,node,port}] → 内层 (node, port) → 外层输出端口。
        图定义非法抛 ValueError（由 _run_nested_subgraph 收口为节点 FAILED）。
        返回 (inner_wf, seed_ids)。
        """
        inner = Workflow(id=f"{outer_wf.id}:{node.id}", name=f"subgraph@{node.id}")
        for nd in graph.get("nodes") or []:
            if not isinstance(nd, dict):
                raise ValueError("子图 nodes 元素必须是对象 {id, type, params?}")
            nid = str(nd.get("id") or "")
            if not nid or not str(nd.get("type") or ""):
                raise ValueError(f"子图节点缺少 id/type: {nd!r}")
            inner.nodes[nid] = GraphNode(id=nid, type_id=str(nd.get("type")),
                                         x=nd.get("x", 0.0), y=nd.get("y", 0.0),
                                         params=dict(nd.get("params") or {}))
        # 内部连线（先于种子：声明的输入映射覆盖内部连线的同名端口）
        for e in graph.get("edges") or []:
            if not isinstance(e, dict):
                raise ValueError("子图 edges 元素必须是对象")
            s, t = str(e.get("source") or ""), str(e.get("target") or "")
            if s not in inner.nodes or t not in inner.nodes:
                raise ValueError(f"子图内部边指向不存在的节点: {s} -> {t}")
            sh = str(e.get("sourceHandle") or "out")
            th = str(e.get("targetHandle") or "in")
            inner.edges.append({"source": s, "target": t,
                                "sourceHandle": sh, "targetHandle": th})
            inner.nodes[t].inputs[th] = s
            slot = self._output_slot(inner.nodes[s].type_id, sh)
            if slot is not None:
                inner.nodes[t].input_slots[th] = slot
        # 输入种子：外层端口 name → 内层 (node, port)（core/asset_ref 引用外层资产；
        # 种子输出端口恒为 "out"，经 input_slots 槽位 0 解析到目标端口）
        seed_ids: List[str] = []
        for i, inp in enumerate(graph.get("inputs") or []):
            if not isinstance(inp, dict):
                raise ValueError("子图 inputs 元素必须是对象 {name, node, port}")
            name = str(inp.get("name") or "")
            target = str(inp.get("node") or "")
            port = str(inp.get("port") or "in")
            if not (name and target):
                raise ValueError(f"子图输入映射缺少 name/node: {inp!r}")
            if target not in inner.nodes:
                raise ValueError(f"子图输入映射目标节点不存在: {target}")
            asset = inputs.get(name)
            aid = getattr(asset, "id", "") or ""
            seed_id = f"__sub_{node.id}_in{i}_{name}"
            seed = GraphNode(id=seed_id, type_id=engines.SUBGRAPH_SEED_TYPE,
                             params={"asset_id": aid})
            inner.nodes[seed_id] = seed
            seed_ids.append(seed_id)
            inner.edges.append({"source": seed_id, "target": target,
                                "sourceHandle": "out", "targetHandle": port})
            inner.nodes[target].inputs[port] = seed_id
            inner.nodes[target].input_slots[port] = 0
        return inner, seed_ids

    async def _run_nested_subgraph(self, wf: Workflow, node: GraphNode) -> bool:
        """嵌套子图执行（C1 / R-平台-8 第一路线）。

        - 输入：外层节点输入资产（_collect_inputs）→ graph.inputs 映射 → 内层种子；
        - 执行：递归 self.execute(inner_wf)（支持子图内嵌套子图，2+ 层）；
        - 输出：graph.outputs 映射内层 (node, port) 资产 → 外层输出端口；
        - 跨层缓存：外层节点走常规增量缓存（参数含 graph 定义），命中即整层跳过；
          内层节点各自走增量缓存（字面量/上游资产参与键）→ 部分重跑只重算脏分支；
        - ExecutionBlocker 语义保持：内层 BLOCKED → 本节点 BLOCKED（显式控制流，
          不计为失败）；内层 FAILED → 本节点 FAILED（真实错误优先显性）。
        错误路径收口与 _run_one 同构（CANCELLED/超时/异常不向外传播）。
        """
        job_id = self.subprocess_job_id(node.id)
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()
        self._emit({"type": "node_update", "node_id": node.id,
                    "status": "running", "progress": 0.0})
        try:
            inputs = self._collect_inputs(wf, node)
            graph = engines.parse_subgraph_graph(node.params)
            inner_wf, seed_ids = self._build_subgraph_workflow(wf, node, graph, inputs)
            # 内层图先过调度器校验（子图作者错误 → 明确 FAILED，不崩引擎、不静默吞）
            inner_errors = self.scheduler.validate(inner_wf)
            if inner_errors:
                raise RuntimeError("子图内层工作流校验失败: " + "; ".join(inner_errors[:6]))
            # 分级超时（L0 渲染类 1800s 上限保留）：内层整图执行套总超时
            declared = node.params.get("timeout_seconds")
            if self._param_is_business(node.type_id, "timeout_seconds"):
                declared = None
            run_timeout = self.node_timeout(node.type_id, declared)
            async with asyncio.timeout(run_timeout):
                await self.execute(inner_wf)

            # 输出映射：内层 (node, port) 资产 → 外层输出端口
            node.asset_ids = {}
            for out in graph.get("outputs") or []:
                if not isinstance(out, dict):
                    continue
                name = str(out.get("name") or "")
                inode = str(out.get("node") or "")
                port = str(out.get("port") or "")
                if not name or inode not in inner_wf.nodes:
                    continue
                aid = inner_wf.nodes[inode].asset_ids.get(port)
                if aid:
                    node.asset_ids[name] = aid

            # 聚合内层状态（FAILED > BLOCKED > CANCELLED > 其余）
            inner_statuses = {nid: n.status for nid, n in inner_wf.nodes.items()}
            node.subgraph_last_run = {
                "inner_id": inner_wf.id,
                "nested": True,
                "statuses": {nid: s.value for nid, s in inner_statuses.items()},
                "seed_ids": list(seed_ids),
                "asset_ids": dict(node.asset_ids),
            }
            failed = next((nid for nid, s in inner_statuses.items()
                           if s == NodeStatus.FAILED), None)
            blocked = next((nid for nid, s in inner_statuses.items()
                            if s == NodeStatus.BLOCKED), None)
            cancelled = next((nid for nid, s in inner_statuses.items()
                              if s == NodeStatus.CANCELLED), None)
            if cancelled is not None:
                node.status = NodeStatus.CANCELLED
                node.error = f"子图内层节点被取消: {cancelled}"
            elif failed is not None:
                node.status = NodeStatus.FAILED
                node.error = inner_wf.nodes[failed].error or f"子图内层节点失败: {failed}"
                node.asset_ids = {}
            elif blocked is not None:
                # ExecutionBlocker 语义保持：显式控制流，不计为插件失败
                node.status = NodeStatus.BLOCKED
                node.error = inner_wf.nodes[blocked].error or f"子图内层节点阻断: {blocked}"
                node.asset_ids = {}
            else:
                node.status = NodeStatus.SUCCESS
                node.progress = 1.0
            node.finished_at = time.time()
            node.last_params = dict(node.params)
            _append_history(node, node.status.value)
            if node.status == NodeStatus.SUCCESS and node.cache_key:
                self.cache.store_result(node.cache_key, node)
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": node.status.value, "progress": node.progress,
                        "asset_ids": node.asset_ids, "error": node.error})
            return node.status == NodeStatus.SUCCESS
        except asyncio.CancelledError:
            self.kill_tracked_tree(job_id)
            node.asset_ids = {}
            node.status = NodeStatus.CANCELLED
            node.error = "子图执行被取消"
            node.finished_at = time.time()
            _append_history(node, "cancelled")
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": "cancelled", "error": node.error})
            return False
        except ExecutionBlocker as e:
            # 防御性收口（常规路径内层已在执行器内捕获）：显式控制流 → BLOCKED
            self.kill_tracked_tree(job_id)
            node.asset_ids = {}
            node.status = NodeStatus.BLOCKED
            node.error = str(e) or "execution blocked"
            node.finished_at = time.time()
            _append_history(node, "blocked")
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": "blocked", "error": node.error})
            return False
        except asyncio.TimeoutError:
            self.kill_tracked_tree(job_id)
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"PLUGIN_TIMEOUT: 子图整图执行超时: {node.id}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": "failed", "error": node.error})
            return False
        except Exception as e:
            self.kill_tracked_tree(job_id)
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": "failed", "error": node.error})
            return False
        except BaseException as e:
            self.kill_tracked_tree(job_id)
            node.asset_ids = {}
            node.status = NodeStatus.FAILED
            node.error = f"{type(e).__name__}: {e}"
            node.finished_at = time.time()
            _append_history(node, "failed")
            self._emit({"type": "node_update", "node_id": node.id,
                        "status": "failed", "error": node.error})
            return False
        finally:
            self.kill_tracked_tree(job_id)

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
            # C1（R-平台-8 第一路线）：子图节点由引擎内联执行（嵌套执行/输入输出
            # 映射/跨层缓存），不经常规节点 run 通道（worker 亦短路回本分发）。
            if engines.is_subgraph_type(node.type_id):
                return await self._run_nested_subgraph(wf, node)
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

            # 收集输入资产（支持 "@资产id" 直接引用素材库资产；常规/子图节点共用）
            inputs = self._collect_inputs(wf, node)

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
            for spec_p in spec.outputs:
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
        except ExecutionBlocker as e:
            # S1-T8：ExecutionBlocker 是显式控制流语义，不计为插件失败。
            self.kill_tracked_tree(job_id)
            for aid in saved_ids:
                try: self.store.delete(aid)
                except Exception: pass
            node.asset_ids = {}
            node.status = NodeStatus.BLOCKED
            node.error = str(e) or "execution blocked"
            node.finished_at = time.time()
            _append_history(node, "blocked")
            self._emit({"type": "node_update", "node_id": nid, "status": "blocked",
                        "error": node.error})
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
