"""WorkerContext（P3，t7 §4/§1.3）：worker 内传给插件 run() 的受限上下文。

- 与主进程 NodeContext.to_dict() 的键对齐（workdir/store/logger/progress/spawn_tracked/
  run_tracked/track_subprocess/untrack_subprocess/subprocess_registry/node_id），
  官方节点在 L0 迁移窗口内可直接运行。
- store 为 WorkerStoreProxy：read_json 读 in/ 输入的 staged 载荷；save_asset 把输出
  落 scratch/out（host 事后校验移入 AssetStore）；files_dir = out（媒体落盘区）。
- 受限出口全部经 PermissionGate：resolve_secret（op）/spawn（op + argv 白名单）/
  http（op + network 白名单）/fs_read/fs_write（本地 realpath 校验）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from . import rpc
from .perm import PermissionDenied, PermissionGate


class WorkerStoreProxy:
    """worker 侧受限 Store：只读输入 staged、写输出到 scratch/out。"""

    def __init__(self, gate: PermissionGate, in_dir: str, out_dir: str,
                 input_index: Dict[str, Dict[str, Any]]):
        self._gate = gate
        self._in = in_dir
        self._out = out_dir
        self._input_index = input_index      # aid -> {"payload_file": abs, "rel": abs}
        self._out_counter = [0]
        self.root = self._out
        self.files_dir = self._out           # 官方节点媒体落盘区（host 事后移入 AssetStore）

    # ---------- 输入 ----------
    def get(self, aid: str) -> Any:
        entry = self._input_index.get(aid)
        if not entry:
            return None
        return entry["asset"]

    def read_json(self, aid: str) -> Any:
        entry = self._input_index.get(aid)
        if not entry:
            return None
        fp = entry.get("payload_file")
        if not fp or not os.path.isfile(fp):
            return None
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # ---------- 输出 ----------
    def save_asset(self, asset: Any, payload: Any = None) -> Any:
        """落输出。payload 结构化 → out/a<n>.json；media → 复制/登记进 out/。"""
        if payload is not None:
            n = self._out_counter[0]
            self._out_counter[0] += 1
            fp = os.path.join(self._out, "a%d.json" % n)
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, default=str)
            except OSError:
                pass
            asset.path = fp
            asset.id = asset.id or ""
            return asset
        if getattr(asset, "path", None):
            abs_path = os.path.abspath(asset.path)
            if not (abs_path == self._out or abs_path.startswith(self._out + os.sep)):
                base = os.path.basename(abs_path) or ("a%d" % self._out_counter[0])
                self._out_counter[0] += 1
                dest = os.path.join(self._out, base)
                try:
                    import shutil
                    shutil.copy2(abs_path, dest)
                except OSError:
                    return asset
                asset.path = dest
            asset.id = asset.id or ""
        return asset

    def delete(self, aid: str) -> None:  # 输出回滚语义由 host 兜底；worker 内无删除
        pass

    def prune_orphans(self, keep: Optional[set] = None) -> int:
        return 0


class WorkerContext:
    """插件 run() 可见上下文（受限）。"""

    def __init__(self, gate: PermissionGate, roots: Dict[str, str],
                 request: Any, job: Dict[str, Any]):
        self._gate = gate
        self._roots = roots
        self._request = request          # callable(op, **fields) -> resp dict（worker→host 请求）
        self._job = job
        self.store = WorkerStoreProxy(gate, roots["in"], roots["out"], {})
        self.workdir = roots["scratch"]
        self.node_id = str(job.get("node_id", ""))

    # ---------- 事件 ----------
    def logger(self, ev: Dict[str, Any]) -> None:
        self._request("_evt", evt=rpc.EVT_LOG, **ev)

    def progress(self, p: float) -> None:
        try:
            p = min(max(float(p), 0.0), 1.0)
        except (TypeError, ValueError):
            p = 0.0
        self._request("_evt", evt=rpc.EVT_PROGRESS, p=p)

    # ---------- 子进程（仅经 gate + host 代理，无 shell） ----------
    def check_argv(self, argv: Any) -> List[str]:
        return self._gate.check_argv(argv)

    def spawn_tracked(self, cmd: List[str], **popen_kw) -> Any:
        """返回 Popen 形对象（host 已执行）；stdout/stderr 捕获在 resp。"""
        argv = self._gate.check_argv(cmd)
        cwd = popen_kw.pop("cwd", None)
        timeout_s = popen_kw.pop("timeout", None)
        resp = self._request(rpc.OP_SPAWN, argv=argv, cwd=cwd, timeout_s=timeout_s)
        return _WorkerPopenStub(resp)

    async def run_tracked(self, cmd: List[str], timeout: Optional[float] = None,
                          **popen_kw) -> Any:
        argv = self._gate.check_argv(cmd)
        resp = self._request(rpc.OP_SPAWN, argv=argv,
                             cwd=popen_kw.pop("cwd", None), timeout_s=timeout)
        return _WorkerProcResult(resp)

    def track_subprocess(self, proc: Any) -> None:
        pass

    def untrack_subprocess(self, proc: Any) -> None:
        pass

    @property
    def subprocess_registry(self) -> None:
        return None

    # ---------- 网络（host 代理 + 白名单） ----------
    def http(self, method: str, url: str, headers: Optional[dict] = None,
             body: Any = None, max_resp_bytes: int = 10 * 1024 * 1024) -> Dict[str, Any]:
        self._gate.check_network(url)
        import base64
        body_b64 = base64.b64encode(body).decode("ascii") if body is not None else None
        resp = self._request(rpc.OP_HTTP, method=str(method).upper(), url=url,
                             headers=dict(headers or {}), body_b64=body_b64,
                             max_resp_bytes=max_resp_bytes)
        return resp

    # ---------- secret（白名单，host 解析一次经管道返回） ----------
    def resolve_secret(self, ref: str) -> str:
        ref = self._gate.check_secret(ref)
        resp = self._request(rpc.OP_RESOLVE_SECRET, ref=ref)
        return str(resp.get("value") or "")

    # ---------- fs（本地 realpath 校验，不经过 host） ----------
    def fs_read(self, rel: str) -> bytes:
        fp = self._gate.resolve_fs(rel, "pkg")
        with open(fp, "rb") as f:
            return f.read()

    def fs_write(self, rel: str, data_b64: str) -> str:
        fp = self._gate.fs_write(rel)
        import base64
        with open(fp, "wb") as f:
            f.write(base64.b64decode(data_b64))
        return fp

    # ---------- 审计 ----------
    def deny_info(self, e: PermissionDenied) -> Dict[str, Any]:
        return self._gate.deny_info(e)

    # ---------- 类型化访问 ----------
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
        }


class _WorkerPopenStub:
    """host 已代为执行的 Popen 形对象（worker 侧只读结果）。"""

    def __init__(self, resp: Dict[str, Any]):
        self.pid = int(resp.get("pid") or 0)
        self.returncode = int(resp.get("exit") if resp.get("exit") is not None else 0)
        self.stdout = resp.get("stdout_tail") or ""
        self.stderr = resp.get("stderr_tail") or ""

    def wait(self, timeout: Optional[float] = None) -> int:
        return self.returncode

    def communicate(self, input: Any = None, timeout: Optional[float] = None):
        return self.stdout, self.stderr

    def poll(self) -> int:
        return self.returncode


class _WorkerProcResult:
    """run_tracked 的 CompletedProcess 形结果。"""

    def __init__(self, resp: Dict[str, Any]):
        self.returncode = int(resp.get("exit") if resp.get("exit") is not None else 0)
        self.stdout = resp.get("stdout_tail") or ""
        self.stderr = resp.get("stderr_tail") or ""
        self.args = resp.get("argv") or []

    @property
    def output(self) -> str:
        return self.stdout
