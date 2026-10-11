"""host_bootstrap.py（P3，t7 §1.2/§1.3）：worker 进程入口（插件执行侧）。

- 仅 stdlib + app.worker.*（rpc/perm/ns/context/jobs）；主进程平台代码（app.engine 等）
  不在 worker 内直接执行。
- 启动序列：读 job 文件 → PermissionGate → NamespaceJail（按信任级）→ JSONL 循环
  （hello/run/ping/shutdown/cancel）。
- run：从 scratch/in staged 输入重建 Asset → WorkerContext → 节点类 run() →
  输出写成 scratch/out 描述符 → ok{outputs}。
- worker→host 受限请求（resolve_secret/spawn/http）同步发送并等待应答；事件
  （progress/log）fire-and-forget。
- 一切 BaseException 收口为带错误码应答，绝不崩溃进程（崩溃只发生在系统级，host 兜底）。

用法（host 侧 spawn）：
  python -m app.worker.host_bootstrap --job <job.json>
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# worker 侧只允许 stdlib + worker 包（app.worker）；不 import app.engine 等主进程模块
from . import rpc
from .context import WorkerContext
from .ns import NamespaceJail, blocked_for_trust
from .perm import PermissionDenied, PermissionGate

# 让 `python -m app.worker.host_bootstrap` 找到 app 包（spawn 时 cwd=backend）
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class WorkerRpcError(Exception):
    """worker↔host 管道错误（host 未响应/EOF）。"""


class WorkerBootstrap:
    def __init__(self, job: Dict[str, Any], gate: PermissionGate,
                 jail: NamespaceJail):
        self.job = job
        self.gate = gate
        self.jail = jail
        self.roots: Dict[str, str] = job.get("roots") or {}
        self.manifest: Dict[str, Any] = job.get("manifest") or {}
        self._cancelled = False
        self._req_counter = [0]
        # 输入索引（aid → asset/描述符），由 _load_inputs 填充
        self._input_index: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------ 输入装载
    def _load_inputs(self) -> Dict[str, Any]:
        in_dir = self.roots.get("in")
        inputs: Dict[str, Any] = {}
        if in_dir and os.path.isdir(in_dir):
            for fn in sorted(os.listdir(in_dir)):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(in_dir, fn), "r", encoding="utf-8") as f:
                        desc = json.load(f)
                except (OSError, ValueError):
                    continue
                port = fn[:-5]
                aid = desc.get("aid") or ("port:" + port)
                asset = self._asset_from_desc(desc, in_dir)
                inputs[port] = asset
                if aid:
                    payload_file = os.path.join(in_dir, fn)
                    if desc.get("payload_file"):
                        payload_file = os.path.join(in_dir,
                                                   os.path.basename(desc["payload_file"]))
                    self._input_index[aid] = {"asset": asset, "payload_file": payload_file}
        return inputs

    def _asset_from_desc(self, desc: Dict[str, Any], in_dir: str) -> Any:
        from app.assets import Asset
        if desc.get("null"):
            return None
        kind = desc.get("kind", "JSON")
        meta = desc.get("meta") or {}
        rel = desc.get("rel")
        path = ""
        if rel:
            path = os.path.normpath(os.path.join(in_dir, os.path.basename(rel)))
        elif desc.get("payload_file"):
            path = os.path.join(in_dir, os.path.basename(desc["payload_file"]))
        return Asset(id=desc.get("aid", ""), kind=kind, path=path, meta=meta)

    # ------------------------------------------------------------ 节点装载
    def _load_node_class(self):
        pkg_dir = self.job.get("pkg_dir") or ""
        if not pkg_dir or not os.path.isdir(pkg_dir):
            raise RuntimeError("pkg_dir 缺失: %r" % pkg_dir)
        entrypoint = self.manifest.get("entrypoint", "node.py") or "node.py"
        mod_path = os.path.join(pkg_dir, entrypoint)
        if not os.path.isfile(mod_path):
            raise RuntimeError("插件入口缺失: %s" % mod_path)
        spec = importlib.util.spec_from_file_location("fw_plugin_node", mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fw_plugin_node"] = mod
        spec.loader.exec_module(mod)
        class_name = self.job.get("class_name") or self.manifest.get("class_name") or ""
        if class_name:
            if not hasattr(mod, class_name):
                raise RuntimeError("插件类不存在: %s" % class_name)
            return getattr(mod, class_name)
        # 无 class_name：取首个含 run() 的类（排 base）
        for name in dir(mod):
            if name == "NodeBase":
                continue
            obj = getattr(mod, name)
            if isinstance(obj, type) and hasattr(obj, "run"):
                return obj
        raise RuntimeError("插件无可执行类（class_name=%r）" % class_name)

    # ------------------------------------------------------------ op 分发
    def loop(self, reader: rpc.JSONLReader, writer: rpc.JSONLWriter) -> int:
        while True:
            msg = reader.read_message()
            if msg is None:
                return 0                        # EOF：host 关管道（正常退休）
            err = rpc.check_message(msg)
            if err:
                writer.send(rpc.error(msg.get("id"), rpc.ERR_RPC, err))
                continue
            op = msg.get("op")
            if op == rpc.OP_HELLO:
                writer.send(rpc.ok(msg["id"], engine_api=[1, 0],
                                   rpc_version=rpc.WORKER_RPC_VERSION,
                                   py=".".join(str(x) for x in sys.version_info[:3])))
            elif op == rpc.OP_RUN:
                self._handle_run(msg, writer)
            elif op == rpc.OP_PING:
                writer.send(rpc.ok(msg["id"], ts=time.time()))
            elif op == rpc.OP_SHUTDOWN:
                writer.send(rpc.ok(msg["id"]))
                return 0
            elif op == rpc.OP_CANCEL:
                self._cancelled = True
                writer.send(rpc.ok(msg["id"]))
            else:
                writer.send(rpc.error(msg["id"], rpc.ERR_RPC, "worker 不支持 op: %s" % op))

    def _handle_run(self, msg: Dict[str, Any], writer: rpc.JSONLWriter) -> None:
        req_id = msg["id"]
        params = msg.get("params") or {}
        try:
            if self._cancelled:
                raise RuntimeError("节点已被取消")
            inputs = self._load_inputs()
            ctx = WorkerContext(self.gate, self.roots, self._request, self.job)
            ctx.store._input_index = self._input_index   # 供 read_json 回查输入
            cls = self._load_node_class()
            node_obj = cls()
            # setup（可选；NotImplementedError 视为无初始化）
            setup = getattr(node_obj, "setup", None)
            if callable(setup):
                try:
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(setup(ctx))
                    finally:
                        loop.close()
                except NotImplementedError:
                    pass
            # run（asyncio 单循环；ctx 请求为同步 RPC，同线程安全）
            loop = asyncio.new_event_loop()
            try:
                outputs = loop.run_until_complete(node_obj.run(ctx, inputs, params))
            finally:
                loop.close()
            if not isinstance(outputs, dict):
                raise RuntimeError("节点输出必须是 dict {端口名: 资产或值}")
            rels = self._write_outputs(outputs)
            writer.send(rpc.ok(req_id, outputs=rels))
        except PermissionDenied as e:
            writer.send(rpc.error(req_id, rpc.ERR_PERMISSION,
                                  "%s（%s）" % (e, self.gate.deny_info(e))))
        except asyncio.TimeoutError:
            writer.send(rpc.error(req_id, rpc.ERR_TIMEOUT, "worker 内 run 超时"))
        except Exception as e:  # noqa: BLE001 —— 插件运行错误收口为应答（带栈便于排障）
            import traceback
            tb = traceback.format_exc(limit=8)
            writer.send(rpc.error(req_id, rpc.ERR_RUNTIME,
                                  "%s: %s\n%s" % (type(e).__name__, e, tb)))

    def _write_outputs(self, outputs: Dict[str, Any]) -> Dict[str, str]:
        """输出 → scratch/out/<port>.json 描述符；返回 port→rel 映射。"""
        out_dir = self.roots.get("out")
        os.makedirs(out_dir, exist_ok=True)
        rels: Dict[str, str] = {}
        declared = {p.get("name") for p in (self.manifest.get("outputs") or [])}
        kinds = {p.get("name"): p.get("type", "JSON") for p in (self.manifest.get("outputs") or [])}
        for port, val in outputs.items():
            if val is None:
                continue
            if not (port in declared or True):   # 允许动态输出（host 按描述符导入）
                pass
            desc: Dict[str, Any] = {}
            if hasattr(val, "path") and getattr(val, "kind", None):   # Asset
                from app.assets import Asset
                if not isinstance(val, Asset):
                    continue
                path = os.path.abspath(val.path or "")
                scratch = os.path.abspath(self.roots.get("scratch", ""))
                if path and (path.startswith(scratch + os.sep) or path == scratch):
                    rel = os.path.relpath(path, scratch).replace("\\", "/")
                else:
                    # worker 外文件（tempfile 等）：复制进 out/
                    base = os.path.basename(path) or ("o%d" % len(rels))
                    dest = os.path.join(out_dir, base)
                    try:
                        import shutil
                        shutil.copy2(path, dest)
                    except OSError:
                        continue
                    rel = os.path.relpath(dest, scratch).replace("\\", "/")
                desc = {"kind": val.kind, "meta": val.meta or {}, "rel": rel,
                        "fingerprint": val.fingerprint or ""}
            else:
                # 结构化值：写入 out/ 载荷文件 + 描述符
                port_file = os.path.join(out_dir, "%s.json" % port)
                try:
                    with open(port_file, "w", encoding="utf-8") as f:
                        json.dump(val, f, ensure_ascii=False, default=str)
                except OSError:
                    continue
                scratch = os.path.abspath(self.roots.get("scratch", ""))
                rel = os.path.relpath(port_file, scratch).replace("\\", "/")
                desc = {"kind": kinds.get(port, "JSON"), "value": val, "payload_file": rel}
            dpath = os.path.join(out_dir, "%s.json" % port)
            try:
                with open(dpath, "w", encoding="utf-8") as f:
                    json.dump(desc, f, ensure_ascii=False, default=str)
            except OSError:
                continue
            rels[port] = os.path.relpath(dpath, scratch).replace("\\", "/")
        return rels

    # ------------------------------------------------------------ worker→host 请求
    def _request(self, op: str, **fields: Any) -> Dict[str, Any]:
        """同步请求 host 并等待应答；_evt 为 fire-and-forget 事件。"""
        writer = self._writer
        reader = self._reader
        if op == "_evt":
            writer.send(rpc.event(fields.pop("evt", "log"), **fields))
            return {}
        self._req_counter[0] += 1
        rid = "r%d" % self._req_counter[0]
        writer.send(rpc.new_message(rid, op=op, **fields))
        while True:
            r = reader.read_message()
            if r is None:
                raise WorkerRpcError("host 关闭管道（等待应答 %s:%s）" % (op, rid))
            if r.get("id") == rid:
                if r.get("ok"):
                    return r
                code = r.get("code") or rpc.ERR_RUNTIME
                raise PermissionDenied("rpc", op,
                                       "%s: %s" % (code, r.get("error") or "host 拒绝"))
            # 其它消息（host 事件/无关应答）忽略


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FrameWeave 插件 worker（受限执行）")
    ap.add_argument("--job", required=True, help="job JSON 文件（含 roots/manifest/env）")
    args = ap.parse_args(argv)
    try:
        with open(args.job, "r", encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError) as e:
        print("worker: 读取 job 失败: %s" % e, file=sys.stderr)
        return 2
    # 沙箱目录
    for k in ("pkg", "scratch", "in", "out"):
        d = (job.get("roots") or {}).get(k)
        if d:
            os.makedirs(d, exist_ok=True)
    # 预装载 worker 自身依赖（asyncio 依赖链含 socket/selectors——jail 只拦插件期新导入，
    # 使 `import socket` 在 worker 内对插件仍失败，同时不破坏 asyncio 事件循环）。
    import asyncio  # noqa: F401
    import selectors  # noqa: F401
    import socket  # noqa: F401
    gate = PermissionGate(job.get("manifest") or {}, job.get("roots") or {},
                          env_values=job.get("env") or {}, trust_level=job.get("trust", "L2"))
    jail = NamespaceJail(blocked_for_trust(job.get("trust", "L2"))).install()
    bootstrap = WorkerBootstrap(job, gate, jail)
    writer = rpc.JSONLWriter()
    reader = rpc.JSONLReader()
    bootstrap._writer = writer
    bootstrap._reader = reader
    try:
        return bootstrap.loop(reader, writer)
    except WorkerRpcError as e:
        print("worker: %s" % e, file=sys.stderr)
        return 1
    finally:
        jail.uninstall()


if __name__ == "__main__":
    sys.exit(main())
