"""PluginSupervisor（P3，t7 §1.2/§1.3/§2）：host 侧 worker 进程托管。

- spawn：`python -m app.worker.host_bootstrap --job <job.json>`，stdout=JSONL 受控通道，
  stderr→logs/<job>.log；Windows 挂 Job Object（kill-on-close，超时/取消整树终止）。
- 握手：hello 60s 超时，EOF 按 1s/2s/4s 退避重试 ≤3 次。
- run 交换：host 读消息循环——事件（progress/log）转发、worker→host 受限请求
  （resolve_secret/spawn/http）host 侧执行并应答、最终 ok{outputs} 或错误码。
- 超时：整段 run 交换套 wall 超时（信任级矩阵由 Engine.node_timeout 提供）→
  JobObject 整树 kill + WorkerTimeout（PLUGIN_TIMEOUT）。
- 崩溃：worker EOF/非零退出 → WorkerCrash（PLUGIN_CRASHED）；绝不波及主进程。
- 输出导入：out 描述符 → AssetStore（结构化 payload / 媒体文件移入 files_dir）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import time
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from . import rpc
from .jobs import JobObject, kill_proc_tree
from .perm import PermissionDenied, PermissionGate


class WorkerJobError(Exception):
    """worker 任务错误（带 §1.4 错误码）。"""

    def __init__(self, code: str, detail: str):
        super().__init__("[%s] %s" % (code, detail))
        self.code = code
        self.detail = detail


class WorkerTimeout(WorkerJobError):
    def __init__(self, detail: str):
        super().__init__(rpc.ERR_TIMEOUT, detail)


class WorkerCrash(WorkerJobError):
    def __init__(self, detail: str):
        super().__init__(rpc.ERR_CRASHED, detail)


class WorkerPermission(WorkerJobError):
    def __init__(self, detail: str):
        super().__init__(rpc.ERR_PERMISSION, detail)


def trust_of(manifest: Dict[str, Any]) -> str:
    """manifest → 信任级（L0 官方锚签名 / 其余按 L1/L2 简化）。"""
    sig = (manifest or {}).get("signature") or {}
    kid = str(sig.get("key_id") or "")
    if kid:
        return "L0" if kid == "a8998646b062c170" else "L1"
    return "L2"


class WorkerSupervisor:
    """单节点 worker 托管器。host=Engine 形对象（store/node_timeout/run_tracked/_emit）。"""

    def __init__(self, host: Any, node: Any, pkg_dir: str = "",
                 timeout_s: Optional[float] = None,
                 job_id: Optional[str] = None):
        self.host = host
        self.node = node
        self.type_id = str(node.type_id)
        self.job_id = job_id or ("job_" + uuid.uuid4().hex[:12])
        self.pkg_dir = pkg_dir or self._resolve_pkg_dir()
        with open(os.path.join(self.pkg_dir, "manifest.json"), "r", encoding="utf-8") as f:
            self.manifest: Dict[str, Any] = json.load(f)
        self.trust = trust_of(self.manifest)
        declared = (node.params or {}).get("timeout_seconds")
        if getattr(host, "_param_is_business", None) and host._param_is_business(self.type_id, "timeout_seconds"):
            declared = None
        self.timeout_s = timeout_s or host.node_timeout(self.type_id, declared)
        self.gate = PermissionGate(self.manifest, {}, env_values={}, trust_level=self.trust)
        # scratch 布局
        store_root = os.path.abspath(host.store.root)
        self.scratch = os.path.join(store_root, "runtime", "work", self.job_id)
        self.roots = {
            "pkg": os.path.abspath(self.pkg_dir),
            "scratch": self.scratch,
            "in": os.path.join(self.scratch, "in"),
            "out": os.path.join(self.scratch, "out"),
        }
        self._job_file = os.path.join(self.scratch, "job.json")
        self._proc: Any = None
        self._jobobj: Optional[JobObject] = None
        self._writer: Any = None
        self._reader: Any = None
        self.worker_pid: int = 0
        # secret 解析器（host 注入；缺省回退 app.secrets——测试用 dict 替代，防污染真实保险箱）
        self.secret_resolver = getattr(host, "secret_resolver", None)

    # ------------------------------------------------------------ 目录解析
    def _resolve_pkg_dir(self) -> str:
        try:
            from .. import declarative
            d = declarative.pkg_dir(self.type_id)
            if d:
                return d
        except Exception:  # noqa: BLE001
            pass
        raise WorkerJobError(rpc.ERR_CONFIG, "无法定位插件包目录: %s" % self.type_id)

    # ------------------------------------------------------------ 任务编排
    async def run_job(self, inputs: Dict[str, Any], keep_alive: bool = False) -> Dict[str, Any]:
        """全流程：scratch → 输入物化 → spawn → 握手 → run 交换 → 输出导入。

        keep_alive=True（池化）：run 后不关闭 worker，等待池复用（worker loop 已支持连跑）。
        """
        if self._proc is None:
            self._prepare_scratch()
            self._write_job_file()
            proc, reader, writer, jobobj = self._spawn()
            self._proc, self._reader, self._writer, self._jobobj = proc, reader, writer, jobobj
            await self._handshake(proc, reader, writer)
        outputs = await self._do_run(inputs)
        if not keep_alive:
            self._close()
        return outputs

    async def _do_run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """复用已有 worker 的一次 run（物化输入 → run 交换 → 输出导入）。"""
        self._materialize_inputs(inputs)
        proc, reader, writer = self._proc, self._reader, self._writer
        rels = await self._run_exchange(proc, reader, writer)
        return self._import_outputs(rels)

    def alive(self) -> bool:
        """worker 进程仍存活且可复用。"""
        return self._proc is not None and self._proc.poll() is None

    def cancel(self) -> None:
        """协作式取消（发送 cancel；超时/强杀由 _close 的 JobObject 保证）。"""
        try:
            if self._writer is not None:
                self._writer.send(rpc.new_message("c0", op=rpc.OP_CANCEL))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ scratch/输入
    def _prepare_scratch(self) -> None:
        for d in ("in", "out"):
            os.makedirs(os.path.join(self.scratch, d), exist_ok=True)

    def _materialize_inputs(self, inputs: Dict[str, Any]) -> None:
        in_dir = self.roots["in"]
        for port, asset in (inputs or {}).items():
            if asset is None:
                with open(os.path.join(in_dir, port + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"null": True}, f)
                continue
            path = getattr(asset, "path", "") or ""
            is_structured = bool(path and path.endswith(".json"))
            if is_structured:
                payload = None
                try:
                    payload = self.host.store.read_json(asset.id)
                except Exception:  # noqa: BLE001
                    payload = None
                desc = {"aid": asset.id, "kind": asset.kind,
                        "payload_file": "in/%s.payload.json" % port}
                with open(os.path.join(in_dir, port + ".json"), "w", encoding="utf-8") as f:
                    json.dump(desc, f, ensure_ascii=False)
                with open(os.path.join(in_dir, port + ".payload.json"), "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, default=str)
            else:
                name = os.path.basename(path) or ("in_" + port)
                dest = os.path.join(in_dir, name)
                if os.path.abspath(dest) != os.path.abspath(path):
                    shutil.copy2(path, dest)
                desc = {"aid": asset.id, "kind": asset.kind,
                        "meta": getattr(asset, "meta", None) or {},
                        "rel": "in/" + name}
                with open(os.path.join(in_dir, port + ".json"), "w", encoding="utf-8") as f:
                    json.dump(desc, f, ensure_ascii=False)

    def _write_job_file(self) -> None:
        env_declared = {}
        for k in ((self.manifest.get("permission") or {}).get("env") or {}).get("vars", []):
            if k in os.environ:
                env_declared[k] = os.environ[k]
        job = {
            "job_id": self.job_id,
            "node_id": getattr(self.node, "id", "") or "",
            "type_id": self.type_id,
            "class_name": self.manifest.get("class_name", ""),
            "pkg_dir": self.pkg_dir,
            "trust": self.trust,
            "timeout_s": self.timeout_s,
            "roots": self.roots,
            "env": env_declared,
            "local_token": getattr(self.host, "_local_token", "") or "",
            "manifest": {k: v for k, v in self.manifest.items() if k != "signature"},
        }
        with open(self._job_file, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)

    # ------------------------------------------------------------ spawn
    def _spawn(self):
        backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = {"PATH": os.environ.get("PATH", ""),
               "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
               "PYTHONIOENCODING": "utf-8"}
        # 仅注入 manifest.permission.env 声明项（绝不 os.environ 全文）
        env.update(self.gate.allowed_env())
        log_path = os.path.join(self.scratch, "worker.log")
        logf = open(log_path, "w", encoding="utf-8", errors="replace")
        try:
            proc = __import__("subprocess").Popen(
                [sys.executable, "-m", "app.worker.host_bootstrap", "--job", self._job_file],
                stdin=__import__("subprocess").PIPE,
                stdout=__import__("subprocess").PIPE,
                stderr=logf, cwd=backend, env=env,
                text=True, encoding="utf-8", errors="replace")
        except Exception:
            logf.close()
            raise
        jobobj = JobObject()
        jobobj.assign(proc.pid)          # Windows：整树 kill-on-close；非 Windows 走 taskkill
        self.worker_pid = proc.pid
        reader = rpc.JSONLReader(proc.stdout)
        writer = rpc.JSONLWriter(proc.stdin)
        return proc, reader, writer, jobobj

    # ------------------------------------------------------------ 握手
    async def _handshake(self, proc, reader, writer) -> None:
        last_err = ""
        for attempt, delay in enumerate((0.0, 1.0, 2.0), 1):
            try:
                rid = "h%d" % attempt
                job = {"job_id": self.job_id, "plugin_id": self.type_id,
                       "trust": self.trust, "node_id": getattr(self.node, "id", "") or "",
                       "timeout_s": self.timeout_s, "roots": self.roots,
                       "env": self.gate.allowed_env(),
                       "manifest": {k: v for k, v in self.manifest.items() if k != "signature"}}
                writer.send(rpc.new_message(rid, op=rpc.OP_HELLO, job=job))
                resp = await self._read_until(proc, reader, rid, timeout=60.0)
                if resp.get("ok"):
                    return
                last_err = str(resp.get("error") or "hello 被拒")
            except (WorkerCrash, TimeoutError) as e:
                last_err = str(e)
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            if attempt < 3:
                await asyncio.sleep(delay)
        raise WorkerCrash("worker 握手失败（3 次退避重试后）: %s" % last_err)

    # ------------------------------------------------------------ run 交换
    async def _run_exchange(self, proc, reader, writer) -> Dict[str, str]:
        rid = "run1"
        writer.send(rpc.new_message(rid, op=rpc.OP_RUN, node_id=self.node.id,
                                    params=dict(getattr(self.node, "params", {}) or {})))
        try:
            resp = await asyncio.wait_for(
                self._read_until(proc, reader, rid, timeout=None), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self._terminate()
            raise WorkerTimeout("节点超时（%gs）: %s" % (self.timeout_s, self.type_id))
        if not resp.get("ok"):
            code = str(resp.get("code") or rpc.ERR_RUNTIME)
            detail = str(resp.get("error") or "未知错误")
            if code == rpc.ERR_PERMISSION:
                raise WorkerPermission(detail)
            raise WorkerJobError(code, detail)
        return resp.get("outputs") or {}

    async def _read_until(self, proc, reader, target_id, timeout: Optional[float]):
        """读消息循环直到 id==target_id；处理事件与 worker 受限请求。"""
        async def _read():
            line = await asyncio.to_thread(reader.read_message)
            return line
        deadline = (time.time() + timeout) if timeout else None
        while True:
            if deadline and time.time() > deadline:
                raise TimeoutError("等待 worker 应答超时")
            try:
                line = await asyncio.wait_for(_read(), 0.5 if deadline else None)
            except asyncio.TimeoutError:
                continue
            if line is None:
                raise WorkerCrash("worker 进程提前退出（EOF，见 logs/%s/worker.log）" % self.job_id)
            msg_id = line.get("id")
            if msg_id is None and "evt" in line:
                self._on_event(line)
                continue
            if "op" in line:
                await self._handle_request(line)
                continue
            if msg_id == target_id:
                return line
            # 其它应答忽略

    def _on_event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("evt")
        if kind == rpc.EVT_PROGRESS:
            try:
                p = float(ev.get("p") or 0)
                self.host._emit({"type": "node_update", "node_id": self.node.id,
                                 "status": "running", "progress": p})
            except Exception:  # noqa: BLE001
                pass
        elif kind == rpc.EVT_LOG:
            try:
                self.host._emit({"type": "node_update", "node_id": self.node.id,
                                 "log": ev.get("msg"), "level": ev.get("level", "info")})
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ worker→host 请求
    async def _handle_request(self, line: Dict[str, Any]) -> None:
        op = line.get("op")
        try:
            if op == rpc.OP_RESOLVE_SECRET:
                self._handle_secret(line)
            elif op == rpc.OP_SPAWN:
                await self._handle_spawn(line)
            elif op == rpc.OP_HTTP:
                await self._handle_http(line)
            else:
                self._respond(line["id"], rpc.ERR_RPC, "host 不支持 op: %s" % op)
        except PermissionDenied as e:
            self._respond(line["id"], rpc.ERR_PERMISSION, str(e))
        except Exception as e:  # noqa: BLE001
            self._respond(line["id"], rpc.ERR_INTERNAL, "%s: %s" % (type(e).__name__, e))

    def _respond(self, rid, code, detail) -> None:
        try:
            if code is None:
                self._writer.send(rpc.ok(rid, **detail))
            else:
                self._writer.send(rpc.error(rid, code, detail))
        except Exception:  # noqa: BLE001
            pass

    def _handle_secret(self, line) -> None:
        ref = self._gate_check_secret(line.get("ref", ""))
        if self.secret_resolver is not None:
            value = self.secret_resolver(ref)
        else:
            from .. import secrets
            value = secrets.read_secret(ref)
        if value is None:
            raise PermissionDenied("secret", ref, "密钥未存储")
        self._respond(line["id"], None, {"value": value})

    def _gate_check_secret(self, ref: str) -> str:
        return self.gate.check_secret(ref)

    async def _handle_spawn(self, line) -> None:
        argv = self.gate.check_argv(line.get("argv") or [])
        timeout_s = line.get("timeout_s")
        job_id = self.job_id
        try:
            res = await self.host.run_tracked(job_id, argv, timeout=timeout_s)
            out_tail = (res.stdout or "")[-4000:]
            err_tail = (res.stderr or "")[-4000:]
        except asyncio.TimeoutError:
            self._respond(line["id"], None, {"exit": -1, "stdout_tail": "",
                                             "stderr_tail": "spawn 超时（%ss）" % timeout_s})
            return
        except Exception as e:  # noqa: BLE001
            self._respond(line["id"], None, {"exit": -1, "stdout_tail": "",
                                             "stderr_tail": str(e)})
            return
        self._respond(line["id"], None, {"exit": res.returncode,
                                         "stdout_tail": out_tail, "stderr_tail": err_tail,
                                         "argv": argv})

    async def _handle_http(self, line) -> None:
        url = self.gate.check_network(line.get("url", ""))
        method = str(line.get("method") or "GET").upper()
        headers = dict(line.get("headers") or {})
        body = None
        if line.get("body_b64"):
            body = base64.b64decode(line["body_b64"])
        max_bytes = int(line.get("max_resp_bytes") or 10 * 1024 * 1024)

        def _do() -> tuple:
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read(max_bytes + 1)
                return r.status, raw[:max_bytes], dict(r.headers)

        status, raw, hdrs = await asyncio.to_thread(_do)
        self._respond(line["id"], None, {"status": status,
                                         "body_b64": base64.b64encode(raw).decode("ascii"),
                                         "headers": {k: v for k, v in hdrs.items()}})

    # ------------------------------------------------------------ 输出导入
    def _import_outputs(self, rels: Dict[str, str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for port, rel in rels.items():
            dpath = os.path.join(self.scratch, rel.replace("/", os.sep))
            try:
                with open(dpath, "r", encoding="utf-8") as f:
                    desc = json.load(f)
            except (OSError, ValueError):
                continue
            asset = self._import_one(port, desc)
            if asset is not None:
                out[port] = asset
        return out

    def _import_one(self, port: str, desc: Dict[str, Any]):
        from ..assets import Asset
        store = self.host.store
        kind = desc.get("kind", "JSON")
        meta = dict(desc.get("meta") or {})
        meta.update({"node": getattr(self.node, "id", "") or "", "port": port,
                     "exec": "worker", "job": self.job_id})
        rel = desc.get("rel")
        if rel:
            src = os.path.join(self.scratch, rel.replace("/", os.sep))
            if not os.path.isfile(src):
                return None
            ext = os.path.splitext(src)[1]
            dst = os.path.join(store.files_dir, uuid.uuid4().hex + ext)
            shutil.move(src, dst)
            return store.save_asset(Asset(id="", kind=kind, path=dst, meta=meta,
                                          fingerprint=desc.get("fingerprint", "")))
        pf = desc.get("payload_file")
        if pf:
            pfp = os.path.join(self.scratch, pf.replace("/", os.sep))
            try:
                with open(pfp, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, ValueError):
                return None
            return store.save_asset(Asset(id="", kind=kind, meta=meta), payload=payload)
        if "value" in desc:
            return store.save_asset(Asset(id="", kind=kind, meta=meta), payload=desc["value"])
        return None

    # ------------------------------------------------------------ 清理
    def _terminate(self) -> None:
        """JobObject 整树 kill（含孙进程）+ 进程 kill 兜底。"""
        if self._jobobj is not None:
            self._jobobj.terminate()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._writer.send(rpc.new_message("bye", op=rpc.OP_SHUTDOWN))
                try:
                    self._proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
            if self._proc.poll() is None:
                kill_proc_tree(self._proc, self._jobobj)
        elif self._jobobj is not None:
            self._jobobj.close()          # kill-on-close 兜底
        for stream in (getattr(self._writer, "_stream", None), getattr(self._reader, "_stream", None)):
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
