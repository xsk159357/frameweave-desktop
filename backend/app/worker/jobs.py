"""Windows Job Object ctypes 封装（P3，t7 §2.3）+ taskkill 进程树降级。

- Windows：kernel32 CreateJobObject / AssignProcessToJobObject / TerminateJobObject /
  QueryInformationJobObject（ctypes，无第三方依赖）。JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 保证
  supervisor 关闭句柄或进程退出时整棵树随 job 终止——取消/超时/崩溃统一走 TerminateJobObject
  或句柄关闭，杜绝 ffmpeg 类孙进程孤儿。
- 非 Windows（dev/CI）：降级为 taskkill 进程树（taskkill /T /F 或 killpg），能力差异文档化。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, List, Optional

# ---------------------------------------------------------------- Windows 常量
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
_JOB_OBJECT_LIMIT_JOB_TIME = 0x4
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
_JOB_OBJECT_LIMIT_CPU_RATE = 0x4000
_JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
_JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x2

_IO_COUNTERS = [
    ("ReadOperationCount", "Q"), ("WriteOperationCount", "Q"),
    ("OtherOperationCount", "Q"), ("ReadTransferCount", "Q"),
    ("WriteTransferCount", "Q"), ("OtherTransferCount", "Q"),
]

IS_WINDOWS = os.name == "nt"


def _job_basic_info() -> Any:
    """JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION 结构（ctypes.Structure）。"""
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = _IO_COUNTERS

    class JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]
    return JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION


class JobObject:
    """Windows Job Object 句柄封装；非 Windows 降级为空实现（taskkill 进程树兜底）。"""

    def __init__(self, name: Optional[str] = None,
                 limit_job_time_ms: Optional[int] = None,
                 memory_limit_bytes: Optional[int] = None):
        self._handle: Any = None
        self._kernel32 = None
        self._terminated = False
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = self._kernel32.CreateJobObjectW(None, name or None)
            if not h:
                return
            self._handle = ctypes.c_void_p(h)
            self._apply_limits(limit_job_time_ms, memory_limit_bytes)
        except Exception:  # noqa: BLE001 —— Job Object 不可用则降级 taskkill
            self._handle = None
            self._kernel32 = None

    def _apply_limits(self, limit_job_time_ms: Optional[int],
                      memory_limit_bytes: Optional[int]) -> None:
        if self._handle is None or self._kernel32 is None:
            return
        import ctypes
        from ctypes import wintypes

        limit_flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limit_job_time_ms:
            limit_flags |= _JOB_OBJECT_LIMIT_JOB_TIME
        if memory_limit_bytes:
            limit_flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = _IO_COUNTERS

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", ctypes.c_void_p),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        # 结构布局手工对齐（BasicLimitInformation 为嵌入结构）——
        # 用字节缓冲避免平台布局差异，只写我们需要的字段。
        buf = ctypes.create_string_buffer(ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION))
        ctypes.memset(buf, 0, len(buf))

        class BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        bli = BASIC_LIMIT_INFORMATION()
        bli.LimitFlags = limit_flags
        if limit_job_time_ms:
            bli.PerJobUserTimeLimit = limit_job_time_ms * 10000   # 100ns 单位
        if memory_limit_bytes:
            pass  # ProcessMemoryLimit 在扩展结构偏移 40（x64）—— 手工写
        ctypes.memmove(buf, ctypes.byref(bli), ctypes.sizeof(bli))
        if memory_limit_bytes:
            off = ctypes.sizeof(bli) + ctypes.sizeof(IO_COUNTERS)  # ProcessMemoryLimit 偏移
            ctypes.memmove(ctypes.cast(buf, ctypes.c_char_p) + off,
                           ctypes.byref(ctypes.c_size_t(memory_limit_bytes)),
                           ctypes.sizeof(ctypes.c_size_t))
        self._kernel32.SetInformationJobObject(
            self._handle, 9, buf, len(buf))     # JobObjectExtendedLimitInformation = 9

    def assign(self, pid: int) -> bool:
        """把进程（含未来子进程）挂入 job。失败（如已入其他 job）返回 False。"""
        if self._handle is None or self._kernel32 is None:
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self._handle, pid))
        except Exception:  # noqa: BLE001
            return False

    def terminate(self) -> None:
        """TerminateJobObject：整棵进程树（含孙进程）强制终止。幂等。"""
        if self._terminated:
            return
        self._terminated = True
        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.TerminateJobObject(self._handle, 0x100)  # exit code 256
        except Exception:  # noqa: BLE001
            pass
        self.close()

    def close(self) -> None:
        """关闭句柄。KILL_ON_JOB_CLOSE 使 job 内残存进程一并终止。"""
        if self._handle is not None:
            try:
                import ctypes
                self._kernel32.CloseHandle(self._handle)
            except Exception:  # noqa: BLE001
                pass
            self._handle = None

    def active_processes(self) -> Optional[int]:
        """QueryInformationJobObject 活动进程数；不可用返回 None。"""
        if self._handle is None or self._kernel32 is None:
            return None
        try:
            import ctypes
            info_cls = _job_basic_info()
            buf = info_cls()
            if not self._kernel32.QueryInformationJobObject(
                    self._handle, 1, ctypes.byref(buf), ctypes.sizeof(buf), None):
                return None
            return int(buf.ActiveProcesses)
        except Exception:  # noqa: BLE001
            return None

    def is_usable(self) -> bool:
        return self._handle is not None


# ---------------------------------------------------------------- taskkill 进程树（全平台降级）
def taskkill_tree(pid: int) -> int:
    """taskkill /T /F 杀进程树（Windows）；POSIX 走 killpg。返回是否已尝试杀掉。"""
    if pid is None or pid <= 0:
        return 0
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=5)
        else:
            try:
                os.killpg(pid, 9)
            except (AttributeError, ProcessLookupError):
                os.kill(pid, 9)
        return 1
    except Exception:  # noqa: BLE001
        return 0


def kill_proc_tree(proc: Any, job: Optional[JobObject] = None) -> int:
    """优先 Job Object 整树终止；否则 taskkill 进程树。返回尝试数。"""
    if job is not None and job.is_usable():
        job.terminate()
        return 1
    try:
        pid = proc.pid
    except Exception:  # noqa: BLE001
        return 0
    return taskkill_tree(pid)
