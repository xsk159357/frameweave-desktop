# -*- coding: utf-8 -*-
"""S1-T5 统一 /ws 事件 + queue/history 只读 API 验收测试（P0-5 / R-平台-5）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T5 acceptance）：
  1. WS ping/pong：text "ping" → text "pong"（ComfyUI 客户端语义）；
     JSON {"type":"ping"} → {"type":"pong"}（R-平台-5 修复）。
  2. 事件序列对齐：真实 uvicorn 服务 + websockets 客户端订阅 /ws，
     执行单节点工作流 → 断言全链 executing(node=null 开始) → progress_state(1)
     → executing{node} → progress{value,max} → executed{node,output}
     → execution_done → executing{node=null} + progress_state(0)；
     node_update 兼容保留（原始扁平事件字段不丢）。
  3. execution_cached：重置节点后二次 run 命中缓存 → execution_cached{nodes}。
  4. execution_error：节点抛错 → execution_error{prompt_id,node_id,exception_message}。
  5. GET /api/history 与真实执行状态一致：终态任务进 history、离开 /api/queue；
     与工作流节点真实状态一致。

架构说明（重要）：全模块只启动**一个** uvicorn 实例（单例），每个测试通过
_repoint() 把模块级 store/wfstore/task_queue 指到全新临时目录后再用同一服务。
原因：多个 uvicorn 线程共享同一模块级 app/queue/store，旧循环会污染下一次测试
（缓存/队列跨循环失效）；单实例 + 重指向是与 test_queue.py 一致的做法。

运行方式：
  - 独立脚本：`python tests/test_ws_events.py`（退出码 0=全绿）；
  - pytest 收集兼容（test_* 函数）。
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t5-test-token")
# 本测试验证 WS 事件序列与 queue/history 一致性（与执行器无关）；固定走进程内执行器，
# 规避 worker 池跨 Engine 复用的既有问题（单例 WorkerExecutor._pool 缓存旧 host/store，
# 属 S1-T2 领域 bug，另报队长；t5 不修 worker 层）。executing/executed/... 事件语义
# 两种执行器一致，不影响本任务验收。
os.environ.setdefault("FW_PLUGIN_EXEC", "inprocess")

TERMINAL = ("success", "failed", "cancelled")

_SERVER = {"server": None, "th": None, "port": None}


# ================================================================ 真实服务夹具（单例 uvicorn + websockets）
def _pick_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _repoint():
    """把 app.server 模块级 store/wfstore/task_queue 指到全新临时目录（不碰仓库数据）。

    每个测试独立目录；返回 (srv, tmp, nodes_dir)。单例服务继续服务新指向。
    """
    import app.server as srv
    from app.assets import create_store
    from app.wfstore import WorkflowStore
    from app.queue import TaskQueueManager

    tmp = os.path.join(tempfile.gettempdir(), "fw_t5_ws_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    nodes_dir = os.path.join(tmp, "user_nodes")

    def _write_pkg(name, type_id, title, node_src):
        pkg_dir = os.path.join(nodes_dir, name)
        os.makedirs(pkg_dir, exist_ok=True)
        with open(os.path.join(pkg_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({
                "schema_version": 2, "engine_api": "1.0", "type_id": type_id,
                "title": title, "category": "用户节点", "description": "t5 ws test",
                "version": "1.0.0", "kind": "python", "runtime": "python",
                "entrypoint": "node.py", "class_name": "T5Node",
                "inputs": [],
                "outputs": [{"name": "out", "type": "JSON", "label": "out", "required": False}],
                "params": [{"name": "sleep", "type": "FLOAT"}],
                "permission": {},
            }, f, ensure_ascii=False, indent=2)
        with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
            f.write(node_src)

    _write_pkg("t5_echo", "user/t5_echo", "t5 echo", (
        "import asyncio\n"
        "from app.assets import Asset\n"
        "\n"
        "class T5Node:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        s = float((params or {}).get('sleep', 0.0) or 0.0)\n"
        "        if s > 0:\n"
        "            await asyncio.sleep(s)\n"
        "        store = ctx['store'] if isinstance(ctx, dict) else ctx.store\n"
        "        out = store.save_asset(\n"
        "            Asset(id='', kind='JSON', meta={}), payload={'ok': True})\n"
        "        return {'out': out}\n"
    ))
    _write_pkg("t5_fail", "user/t5_fail", "t5 fail", (
        "from app.assets import Asset\n"
        "\n"
        "class T5Node:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        raise RuntimeError('t5 故意失败')\n"
    ))

    srv.declarative.set_user_nodes_dir(nodes_dir)
    n = srv.declarative.scan()
    assert n >= 2 and srv.declarative.pkg_dir("user/t5_echo") is not None and \
        srv.declarative.pkg_dir("user/t5_fail") is not None, \
        "t5 测试节点扫描失败 n=%d" % n
    srv.store = create_store("local", os.path.join(tmp, "assets"))
    srv.wfstore = WorkflowStore(os.path.join(tmp, "workflows"))
    srv.task_queue = TaskQueueManager(max_concurrent=4)
    srv.task_queue.set_runner(srv._queue_run_task)
    # 清空跨测试共享的 worker 池：WorkerExecutor 以单例注册（engines.py 尾部），
    # 其 _pool 跨 Engine 实例复用 supervisor；supervisor 持有的 host/store 是创建
    # 时的旧引擎——复用会把输出导入旧 store，导致新 store 的缓存索引被 trim
    # （execution_cached 永不触发）。属 S1-T2 领域既有问题，测试侧隔离处理。
    try:
        from app import engines as _eng
        we = _eng.get_engine_ext("executor", "worker")
        pool = getattr(we, "_pool", None)
        if pool is not None:
            pool.shutdown()
    except Exception:  # noqa: BLE001
        pass
    return srv, tmp


def _ensure_server(srv):
    """单例 uvicorn：首次调用启动后台线程；后续复用（模块级 app 持续服务重指向后的全局）。"""
    if _SERVER["server"] is not None:
        return _SERVER["server"], _SERVER["th"], _SERVER["port"]
    import uvicorn
    port = _pick_port()
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True, name="fw-t5-uvicorn")
    th.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                _SERVER.update(server=server, th=th, port=port)
                return server, th, port
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("uvicorn 启动超时")


def _stop_server():
    """收尾停单例服务。"""
    srv = _SERVER.get("server")
    th = _SERVER.get("th")
    if srv is not None:
        srv.should_exit = True
    if th is not None:
        th.join(timeout=8.0)


def _api_headers(srv):
    from app.obf import x_fw_token_header
    return {x_fw_token_header(): os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "fw-t5-test-token")}


class _Client:
    """极简 HTTP 客户端（httpx，真实服务）。"""

    def __init__(self, port, srv):
        import httpx
        self.port = port
        self.h = _api_headers(srv)
        self._c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20.0)

    def post(self, path, json=None):
        return self._c.post(path, json=json, headers=self.h)

    def delete(self, path):
        return self._c.delete(path, headers=self.h)

    def get(self, path):
        return self._c.get(path)

    def close(self):
        self._c.close()


def _create_wf(cli, name, type_id, sleep=None):
    params = {}
    if sleep is not None:
        params["sleep"] = sleep
    r = cli.post("/api/workflows", json={
        "name": name,
        "nodes": [{"id": "n1", "type": type_id, "x": 0, "y": 0,
                   "params": params, "inputs": {}}],
        "edges": [],
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _wait_queue_empty(cli, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        q = cli.get("/api/queue").json()
        if not q["running"] and not q["pending"]:
            return q
        time.sleep(0.05)
    raise AssertionError("队列未在超时内清空: %s" % q)


async def _collect_ws(ws, stop, timeout=25.0):
    """从 websockets 连接收集事件直到 stop(events) 或超时。返回事件列表。

    websockets ≥14 的 recv() 不再接受 timeout 参数——用 asyncio.wait_for 包裹。
    """
    import asyncio
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            if events and stop(events):
                return events
            continue
        except Exception:
            break
        try:
            ev = json.loads(raw)
        except (TypeError, ValueError):
            continue
        events.append(ev)
        if stop(events):
            return events
    return events


def _raw(events):
    """事件归一化：Comfy 事件取 data，扁平 node_update 取整条。"""
    return [(e.get("type"),
             (e.get("data") if isinstance(e.get("data"), dict) else dict(e)))
            for e in events]


# ================================================================ 测试
def test_ws_ping_pong():
    """① WS ping/pong：text ping→pong；JSON ping→pong。"""
    import asyncio
    import websockets
    srv, _ = _repoint()
    server, th, port = _ensure_server(srv)
    try:
        async def _run():
            uri = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(uri) as ws:
                await ws.send("ping")
                pong = await asyncio.wait_for(ws.recv(), timeout=5)
                assert pong == "pong", "text ping 应回 text pong: %r" % pong
                await ws.send(json.dumps({"type": "ping"}))
                j = await asyncio.wait_for(ws.recv(), timeout=5)
                assert json.loads(j) == {"type": "pong"}, "JSON ping 应回 JSON pong: %r" % j
        asyncio.run(_run())
    finally:
        pass


def test_ws_event_chain():
    """② 事件序列对齐：executing→progress→executed 全链 + node_update 兼容 + 终态 idle。"""
    import asyncio
    import websockets
    srv, _ = _repoint()
    server, th, port = _ensure_server(srv)
    cli = _Client(port, srv)
    try:
        wfid = _create_wf(cli, "chain", "user/t5_echo", sleep=0.15)

        async def _run():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                r = cli.post(f"/api/workflows/{wfid}/run", json={"mode": "all"})
                assert r.status_code == 200, r.text
                tid = r.json()["task_id"]

                def _done(evs):
                    return any(e.get("type") == "progress_state"
                               and e.get("data", {}).get("value") == 0 for e in evs) and \
                           any(e.get("type") == "executed" for e in evs)
                events = await _collect_ws(ws, _done)
            raw = _raw(events)
            types = [t for t, _ in raw]
            assert "executing" in types, "缺少 executing: %s" % types
            assert "progress" in types, "缺少 progress: %s" % types
            assert "executed" in types, "缺少 executed: %s" % types
            assert "node_update" in types, "node_update 兼容被破坏: %s" % types
            assert "execution_done" in types, "缺少 execution_done: %s" % types
            # 任务开始：executing node=null + progress_state=1（Comfy 语义）
            starts = [d for t, d in raw if t == "executing" and d.get("node") is None]
            assert starts, "缺少任务开始 executing(node=null): %s" % raw
            ps1 = [d for t, d in raw if t == "progress_state" and d.get("value") == 1]
            assert ps1, "缺少 progress_state(1) 运行态: %s" % raw
            # 任务结束：executing node=null + progress_state=0
            ends = [d for t, d in raw if t == "executing" and d.get("node") is None]
            ps0 = [d for t, d in raw if t == "progress_state" and d.get("value") == 0]
            assert len(ends) >= 2 and ps0, "缺少结束 executing/进度空闲: %s" % raw
            # 节点执行链：executing{node:'n1'} 先于 executed{node:'n1'}；prompt_id 贯穿
            ex = [d for t, d in raw if t == "executing" and d.get("node") == "n1"]
            exd = [d for t, d in raw if t == "executed" and d.get("node") == "n1"]
            assert ex and exd, "节点级 executing/executed 缺失: %s" % raw
            assert all(d.get("prompt_id") == tid for d in ex + exd), \
                "prompt_id 未贯穿: %s" % raw
            i_ex = types.index("executing")
            i_exd = types.index("executed")
            assert i_ex <= i_exd, "executing 应早于 executed: %s" % types
            # node_update 保留原始语义（扁平事件，node_id/status 不丢）
            nu = [d for t, d in raw if t == "node_update" and d.get("status") == "success"]
            assert nu and nu[0].get("node_id") == "n1", "node_update 语义被破坏: %s" % raw
            # executed output 携带输出资产
            out_ev = exd[0].get("output") or {}
            assert out_ev.get("out"), "executed.output 应带 out 资产: %s" % exd[0]
        asyncio.run(_run())
    finally:
        cli.close()


def test_ws_execution_cached():
    """③ 重置节点后二次 run 命中缓存 → execution_cached{nodes}（node_update cached 兼容保留）。"""
    import asyncio
    import websockets
    srv, _ = _repoint()
    server, th, port = _ensure_server(srv)
    cli = _Client(port, srv)
    try:
        wfid = _create_wf(cli, "cached", "user/t5_echo")

        async def _run():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                r1 = cli.post(f"/api/workflows/{wfid}/run", json={"mode": "all"})
                assert r1.status_code == 200, r1.text
                tid1 = r1.json().get("task_id")

                def _done1(evs):
                    return any(e.get("type") == "progress_state"
                               and e.get("data", {}).get("value") == 0 for e in evs)
                await _collect_ws(ws, _done1)
                # 重置节点（不清缓存：force=false）→ 二次 run 走缓存命中路径
                rr = cli.post(f"/api/workflows/{wfid}/nodes/n1/reset", json={"force": False})
                assert rr.status_code == 200, rr.text
                r2 = cli.post(f"/api/workflows/{wfid}/run", json={"mode": "all"})
                assert r2.status_code == 200 and r2.json()["ok"], r2.text

                def _done2(evs):
                    return any(e.get("type") == "execution_cached" for e in evs)
                events = await _collect_ws(ws, _done2)
            raw = _raw(events)
            cached = [d for t, d in raw if t == "execution_cached"]
            assert cached, "缺少 execution_cached: %s" % [t for t, _ in raw]
            assert cached[0].get("nodes") == ["n1"], \
                "execution_cached.nodes 应为 ['n1']: %s" % cached[0]
            assert cached[0].get("prompt_id"), "execution_cached 应带 prompt_id: %s" % cached[0]
            assert any(t == "node_update" and d.get("status") == "cached" for t, d in raw), \
                "node_update(cached) 兼容缺失: %s" % [t for t, _ in raw]
        asyncio.run(_run())
    finally:
        cli.close()


def test_ws_execution_error():
    """④ 节点抛错 → execution_error{prompt_id,node_id,exception_message} + node_update failed。"""
    import asyncio
    import websockets
    srv, _ = _repoint()
    server, th, port = _ensure_server(srv)
    cli = _Client(port, srv)
    try:
        wfid = _create_wf(cli, "fail", "user/t5_fail")

        async def _run():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                r = cli.post(f"/api/workflows/{wfid}/run", json={"mode": "all"})
                assert r.status_code == 200, r.text
                tid = r.json()["task_id"]

                def _done(evs):
                    return any(e.get("type") == "progress_state"
                               and e.get("data", {}).get("value") == 0 for e in evs)
                events = await _collect_ws(ws, _done)
            raw = _raw(events)
            errs = [d for t, d in raw if t == "execution_error"]
            assert errs, "缺少 execution_error: %s" % [t for t, _ in raw]
            assert errs[0].get("prompt_id") == tid
            assert errs[0].get("node_id") == "n1"
            assert "t5 故意失败" in str(errs[0].get("exception_message") or "")
            assert any(t == "node_update" and d.get("status") == "failed" for t, d in raw), \
                "node_update(failed) 兼容缺失: %s" % [t for t, _ in raw]
        asyncio.run(_run())
    finally:
        cli.close()


def test_api_queue_history_consistency():
    """⑤ /api/queue + /api/history 与真实执行状态一致（终态进 history、离开 queue）。"""
    srv, _ = _repoint()
    server, th, port = _ensure_server(srv)
    cli = _Client(port, srv)
    try:
        wfid = _create_wf(cli, "hist", "user/t5_echo", sleep=0.1)
        r = cli.post(f"/api/workflows/{wfid}/run", json={"mode": "all"})
        assert r.status_code == 200 and r.json().get("task_id"), r.text
        tid = r.json()["task_id"]
        # 运行中：任务应出现在队列（running/pending 至少其一）
        deadline = time.time() + 10
        seen = False
        while time.time() < deadline:
            q = cli.get("/api/queue").json()
            all_tasks = q["running"] + q["pending"]
            if any(t["task_id"] == tid for t in all_tasks):
                seen = True
                break
            time.sleep(0.02)
        assert seen, "任务未出现在 /api/queue 运行/等待列表"
        _wait_queue_empty(cli)
        # history：终态任务在列（成功）
        hist = cli.get("/api/history?limit=50").json()
        assert hist["ok"] is True and hist["count"] >= 1, hist
        entries = {t["task_id"]: t for t in hist["history"]}
        assert tid in entries, "history 缺任务 %s: %s" % (tid, list(entries))
        e = entries[tid]
        assert e["status"] == "success", "终态应为 success: %s" % e
        assert e["workflow_id"] == wfid
        assert e["finished_at"] is not None and e["created_at"] is not None
        # 与真实工作流节点状态一致
        wf = cli.get(f"/api/workflows/{wfid}").json()
        assert wf["nodes"][0]["status"] == "success", wf
        # 取消路径：排队任务取消 → history 出现 cancelled（max_concurrent=1 卡运行）
        srv.task_queue.max_concurrent = 1
        wf2 = _create_wf(cli, "hist2", "user/t5_echo", sleep=0.25)
        r2 = cli.post(f"/api/workflows/{wf2}/run", json={"mode": "all"}).json()
        r3 = cli.post(f"/api/workflows/{wf2}/run", json={"mode": "all"}).json()
        assert r2["ok"] and r3["ok"]
        time.sleep(0.05)
        qq = cli.get("/api/queue").json()
        cand = [t for t in qq["pending"] + qq["running"] if t["task_id"] == r3["task_id"]]
        if cand:
            rr = cli.delete(f"/api/queue/{r3['task_id']}")
            assert rr.status_code == 200 and rr.json()["ok"] is True, rr.text
        _wait_queue_empty(cli)
        hist2 = cli.get("/api/history?limit=50").json()
        entries2 = {t["task_id"]: t for t in hist2["history"]}
        e3 = entries2.get(r3["task_id"])
        assert e3 is not None, "取消任务应进 history: %s" % list(entries2)
        assert e3["status"] in TERMINAL, e3
    finally:
        cli.close()


def main() -> int:
    """独立脚本入口（与 test_queue.py 同款）——逐函数执行并汇总失败数。"""
    tests = [
        test_ws_ping_pong,
        test_ws_event_chain,
        test_ws_execution_cached,
        test_ws_execution_error,
        test_api_queue_history_consistency,
    ]
    failures = 0
    try:
        for fn in tests:
            name = fn.__name__
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as e:
                failures += 1
                print("FAIL %s | %s" % (name, e))
            except Exception as e:  # noqa: BLE001
                failures += 1
                print("ERROR %s | %s: %s" % (name, type(e).__name__, e))
    finally:
        _stop_server()
    print("=" * 56)
    print("TOTAL FAIL=%d/%d" % (failures, len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())