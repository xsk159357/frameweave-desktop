"""WorkerPool（P3，t7 §1.2 W4/S2）：worker 驻留池 + 崩溃重试。

- W4 驻留池：按 type_id 复用空闲 worker（进程内 worker 循环连跑）；全局池上限
  max_size（默认 4）；idle 超时（默认 300s）回收，避免多工作流并发叠加内存。
- S2 崩溃托管：worker 异常退出 → 从池移除 + 按 1s/2s/4s 退避重试 ≤3 次
  （幂等节点自动重试，最终失败向上抛 WorkerCrash）。
- 并发安全：同一 type_id 并行节点取不同 worker；池满且无空闲 → 新建独立 worker
  （不阻塞执行，仅不作缓存）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from .supervisor import WorkerCrash, WorkerSupervisor


class WorkerPool:
    def __init__(self, host: Any, max_size: int = 4, idle_ttl: float = 300.0,
                 retry_attempts: int = 3):
        self.host = host
        self.max_size = max(1, max_size)
        self.idle_ttl = idle_ttl
        self.retry_attempts = retry_attempts
        self._pool: Dict[str, List[Tuple[float, WorkerSupervisor]]] = {}   # type_id -> [(last_used, sup)]
        self._in_use: Dict[str, int] = {}                                   # type_id -> busy 计数

    # ------------------------------------------------------------ 池操作
    def _reap(self, now: Optional[float] = None) -> int:
        """回收 idle 超时 worker，返回回收数。"""
        now = now or time.time()
        reaped = 0
        for tid, lst in list(self._pool.items()):
            keep = []
            for last, sup in lst:
                if now - last > self.idle_ttl:
                    try:
                        sup._close()
                    except Exception:  # noqa: BLE001
                        pass
                    reaped += 1
                else:
                    keep.append((last, sup))
            if keep:
                self._pool[tid] = keep
            else:
                self._pool.pop(tid, None)
        return reaped

    def _pick_idle(self, type_id: str) -> Optional[WorkerSupervisor]:
        lst = self._pool.get(type_id) or []
        for last, sup in lst:
            if sup.alive():
                return sup
        return None

    def _mark_busy(self, type_id: str) -> None:
        self._in_use[type_id] = self._in_use.get(type_id, 0) + 1

    def _mark_done(self, type_id: str) -> None:
        self._in_use[type_id] = max(0, (self._in_use.get(type_id) or 1) - 1)

    def _cache(self, type_id: str, sup: WorkerSupervisor) -> None:
        lst = self._pool.setdefault(type_id, [])
        lst.append((time.time(), sup))

    def _drop(self, sup: WorkerSupervisor) -> None:
        for tid, lst in list(self._pool.items()):
            self._pool[tid] = [(t, s) for t, s in lst if s is not sup]
            if not self._pool[tid]:
                self._pool.pop(tid, None)

    def total_alive(self) -> int:
        return sum(1 for lst in self._pool.values() for _, s in lst if s.alive())

    # ------------------------------------------------------------ 执行入口
    async def run(self, host: Any, node: Any, inputs: Dict[str, Any],
                  pkg_dir: str = "") -> Dict[str, Any]:
        """池化执行一次节点 run（复用/新建/崩溃重试）。"""
        self._reap()
        type_id = str(node.type_id)
        attempts = self.retry_attempts
        last_err: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            if attempt > 1:
                await asyncio.sleep(min(2.0 ** (attempt - 2), 4.0))   # 1s/2s/4s 退避
            sup = None
            if self._pick_idle(type_id) is not None:
                sup = self._pick_idle(type_id)
                self._drop(sup)
            elif self.total_alive() < self.max_size:
                sup = WorkerSupervisor(host, node, pkg_dir=pkg_dir)
            else:
                sup = WorkerSupervisor(host, node, pkg_dir=pkg_dir)   # 池满：独立 worker（不缓存）
            self._mark_busy(type_id)
            try:
                outputs = await sup.run_job(inputs, keep_alive=True)
                if sup._proc is not None:                              # 仍在池上限内 → 缓存复用
                    self._cache(type_id, sup)
                return outputs
            except WorkerCrash as e:
                last_err = e
                self._mark_done(type_id)
                try:
                    sup._close()
                except Exception:  # noqa: BLE001
                    pass
                continue
            finally:
                self._mark_done(type_id)
        raise WorkerCrash("池化执行失败（重试 %d 次后）: %s" % (attempts, last_err))

    def shutdown(self) -> int:
        """关闭全部驻留 worker，返回关闭数。"""
        n = 0
        for lst in list(self._pool.values()):
            for _, sup in lst:
                try:
                    sup._close()
                except Exception:  # noqa: BLE001
                    pass
                n += 1
        self._pool.clear()
        return n
