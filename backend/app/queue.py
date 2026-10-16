# -*- coding: utf-8 -*-
"""任务队列：FIFO 排队 + 优先级 + 并发上限 + 逐任务取消（S1-T1，P0-1 / R-平台-1）。

替代 `server.py:258-262` 的全局单任务表 `_RUNNING_TASKS`：
  - FIFO：同优先级严格按入队顺序调度（seq 单调递增）；
  - 优先级：priority 越小越先调度（默认 0），同优先级内保持 FIFO；
  - 并发上限：max_concurrent（默认 4，`FRAMEWEAVE_MAX_CONCURRENT` 可覆盖），
    默认 ≥3 个工作流可并行入队/执行（R-平台-1 直采）；
  - 逐任务取消：等待中的任务直接出队（cancelled-before-start），运行中的任务
    cancel 其 asyncio.Task（engine 内部 CancelledError 语义完整：回滚资产 +
    杀子进程树）；
  - 重复 run 不取消旧任务：同一工作流可多次入队，各自独立执行（各自取工作流
    最新快照，互不干扰）。

仅事件循环内使用（FastAPI 异步端点 / asyncio 测试）；进程内内存队列，
S1-T5 的 GET /api/history 可复用 `_finished` 有界历史。
"""
from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

_TERMINAL = ("success", "failed", "cancelled")


class RunTask:
    """一次工作流运行任务（入队单元）。"""

    def __init__(self, task_id: str, wfid: str, mode: str = "all",
                 node_ids: Optional[List[str]] = None, priority: int = 0):
        self.task_id = task_id
        self.wfid = wfid
        self.mode = mode or "all"
        self.node_ids = list(node_ids or [])
        self.priority = int(priority or 0)
        self.status = "queued"          # queued | running | success | failed | cancelled
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.error = ""
        self._seq = 0                   # 入队序号（FIFO 次序）
        self._atask: Optional["asyncio.Task"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workflow_id": self.wfid,
            "mode": self.mode,
            "node_ids": list(self.node_ids),
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class TaskQueueManager:
    """FIFO + 优先级 + 并发上限的任务队列（事件循环内调度）。"""

    def __init__(self, max_concurrent: int = 4, history_limit: int = 200):
        self.max_concurrent = max(1, int(max_concurrent))
        self.history_limit = max(1, int(history_limit))
        self._lock = asyncio.Lock()
        self._waiting: List[RunTask] = []        # 按 (priority, seq) 升序
        self._running: Dict[str, RunTask] = {}   # task_id -> 运行中
        self._finished: Dict[str, RunTask] = {}  # 有界结束历史（S1-T5 /api/history 可复用）
        self._seq = itertools.count(1)
        self._wake = asyncio.Event()
        self._runner: Optional[Callable[[RunTask], Awaitable]] = None
        self._sched: Optional["asyncio.Task"] = None

    # ------------------------------------------------------------ 装配
    def set_runner(self, runner: Callable[[RunTask], Awaitable]) -> None:
        """注入执行体（async (task) -> None）。"""
        self._runner = runner

    def _ensure_scheduler(self) -> None:
        """惰性启动调度器（绑定当前事件循环；跨循环时重建 wake/任务）。"""
        loop = asyncio.get_running_loop()
        if (self._sched is None or self._sched.done()
                or getattr(self._sched, "get_loop", lambda: None)() is not loop):
            self._wake = asyncio.Event()
            self._sched = asyncio.create_task(self._scheduler())

    async def shutdown(self) -> None:
        """取消全部运行中任务并停调度器（进程退出/测试收尾）。"""
        async with self._lock:
            running = list(self._running.values())
        for t in running:
            if t._atask is not None and not t._atask.done():
                t._atask.cancel()
        if self._sched is not None:
            self._sched.cancel()

    # ------------------------------------------------------------ 入队/调度
    async def enqueue(self, wfid: str, mode: str = "all",
                      node_ids: Optional[List[str]] = None,
                      priority: int = 0) -> RunTask:
        """入队（返回任务；同一工作流重复入队不取消旧任务）。"""
        task = RunTask(task_id=uuid.uuid4().hex[:12], wfid=wfid, mode=mode,
                       node_ids=node_ids, priority=priority)
        task._seq = next(self._seq)
        async with self._lock:
            self._waiting.append(task)
            self._waiting.sort(key=lambda t: (t.priority, t._seq))
        self._ensure_scheduler()
        self._wake.set()
        return task

    async def _scheduler(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await self._dispatch()

    async def _dispatch(self) -> None:
        async with self._lock:
            while self._waiting and len(self._running) < self.max_concurrent:
                task = self._waiting.pop(0)
                task.status = "running"
                task.started_at = time.time()
                self._running[task.task_id] = task
                task._atask = asyncio.create_task(self._execute(task))

    async def _execute(self, task: RunTask) -> None:
        try:
            if self._runner is not None:
                await self._runner(task)
            if task.status == "running":
                task.status = "success"
        except asyncio.CancelledError:
            task.status = "cancelled"
            task.error = task.error or "cancelled"
        except Exception as e:  # noqa: BLE001 —— 运行失败只影响本任务
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
        finally:
            task.finished_at = time.time()
            async with self._lock:
                self._running.pop(task.task_id, None)
                self._remember(task)
            self._wake.set()

    def _remember(self, task: RunTask) -> None:
        self._finished[task.task_id] = task
        if len(self._finished) > self.history_limit:
            for k in list(self._finished)[: len(self._finished) - self.history_limit]:
                self._finished.pop(k, None)

    # ------------------------------------------------------------ 取消
    async def cancel_task(self, task_id: str) -> Optional[str]:
        """逐任务取消。返回 'cancelled'（等待中出队）/ 'cancel_requested'（运行中）；
        任务不存在返回 None。"""
        async with self._lock:
            for i, t in enumerate(self._waiting):
                if t.task_id == task_id:
                    t = self._waiting.pop(i)
                    t.status = "cancelled"
                    t.finished_at = time.time()
                    t.error = "cancelled before start"
                    self._remember(t)
                    return "cancelled"
            t = self._running.get(task_id)
            if t is not None:
                if t._atask is not None and not t._atask.done():
                    t._atask.cancel()
                return "cancel_requested"
        return None

    async def cancel_workflow(self, wfid: str) -> int:
        """取消某工作流全部任务（排队中 + 运行中）。返回取消任务数。"""
        n = 0
        async with self._lock:
            kept: List[RunTask] = []
            for t in self._waiting:
                if t.wfid == wfid:
                    t.status = "cancelled"
                    t.finished_at = time.time()
                    t.error = "cancelled before start"
                    self._remember(t)
                    n += 1
                else:
                    kept.append(t)
            self._waiting = kept
            for t in list(self._running.values()):
                if t.wfid == wfid:
                    if t._atask is not None and not t._atask.done():
                        t._atask.cancel()
                    n += 1
        return n

    # ------------------------------------------------------------ 查询
    async def snapshot(self) -> Dict[str, Any]:
        """当前队列快照：running + pending（pending 含排队位置）+ 并发上限。"""
        async with self._lock:
            pending = []
            pos = 1
            for t in self._waiting:
                d = t.to_dict()
                d["position"] = pos
                pending.append(d)
                pos += 1
            running = [t.to_dict() for t in self._running.values()]
        return {"running": running, "pending": pending,
                "max_concurrent": self.max_concurrent}

    def position_of(self, task_id: str) -> Optional[int]:
        """等待队列中的位置（1 起）；运行中/不存在返回 None。"""
        for i, t in enumerate(self._waiting):
            if t.task_id == task_id:
                return i + 1
        return None

    def get_task(self, task_id: str) -> Optional[RunTask]:
        t = self._running.get(task_id)
        if t is not None:
            return t
        for t in self._waiting:
            if t.task_id == task_id:
                return t
        return self._finished.get(task_id)

    def history(self, limit: int = 200) -> List[Dict[str, Any]]:
        """结束历史快照（最新在前；有界）。**线程安全要求：须在事件循环内调用**——
        server 的 GET /api/history 端点直接使用（async 端点天然在事件循环内）。"""
        items = sorted(self._finished.values(),
                       key=lambda t: (t.finished_at or 0.0), reverse=True)
        return [t.to_dict() for t in items[:max(1, int(limit))]]

    async def finished_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        """异步结束历史（持锁读取，供 async 端点安全调用）。"""
        async with self._lock:
            items = sorted(self._finished.values(),
                           key=lambda t: (t.finished_at or 0.0), reverse=True)
            return [t.to_dict() for t in items[:max(1, int(limit))]]
