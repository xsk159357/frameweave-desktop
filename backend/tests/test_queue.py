# -*- coding: utf-8 -*-
"""S1-T1 任务队列与多工作流并发 验收测试（P0-1 / R-平台-1）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T1 acceptance）：
  1. FIFO 排队：同优先级严格按入队顺序执行；
  2. 并发上限：等待中的任务按并发上限分批启动；
  3. ≥3 个工作流可并行入队/执行（max_concurrent 默认 4 ≥ 3）；
  4. 优先级：priority 越小越先调度，同优先级保持 FIFO；
  5. 同工作流重复 run 不再取消旧任务（各自独立执行，旧任务不被 cancel）；
  6. 逐任务取消：等待中直接出队（cancelled-before-start）、运行中 cancel 传播；
  7. cancel_workflow：整工作流取消（旧 API 语义兼容）；
  8. API 级：POST /api/workflows/{id}/run 入队 + GET /api/queue 快照 +
     DELETE /api/queue/{id} 逐任务取消（R-平台-1 API）。

运行方式：
  - T9 统一 pytest 前先以独立脚本断言：`python tests/test_queue.py`（退出码 0=全绿）；
  - pytest 收集兼容：本文件顶层只做轻量 import，pytest 可直接收集 test_* 函数。
"""
import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t1-test-token")

TERMINAL = ("success", "failed", "cancelled")


# ================================================================ 单元级（独立 TaskQueueManager）
def _mk_runner(started, gate=None):
    """构造可观测 runner：记录启动顺序；gate 非空时阻塞等待放行。"""
    async def runner(task):
        started.append(task.task_id)
        if gate is not None:
            await gate.wait()
    return runner


async def _wait_terminal(tasks, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(t.status in TERMINAL for t in tasks):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("任务未在超时内结束: %s" %
                         [(t.task_id, t.status) for t in tasks])


def test_fifo_order():
    """① FIFO：max_concurrent=1 时严格按入队顺序执行。"""
    from app.queue import TaskQueueManager

    async def _run():
        started = []
        q = TaskQueueManager(max_concurrent=1)
        q.set_runner(_mk_runner(started))
        ts = [await q.enqueue(f"w{i}", mode="all") for i in range(3)]
        await _wait_terminal(ts)
        assert started == [t.task_id for t in ts], "FIFO 顺序错误: %s" % started
        assert all(t.status == "success" for t in ts)
        assert all(t.started_at is not None and t.finished_at is not None for t in ts)
        # 快照形态：pending 带 position
        snap = await q.snapshot()
        assert set(snap.keys()) == {"running", "pending", "max_concurrent"}
        assert q.max_concurrent == 1
        await q.shutdown()
    asyncio.run(_run())


def test_concurrency_limit():
    """② 并发上限：6 个任务在 max_concurrent=3 下分两批启动。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        started = []
        q = TaskQueueManager(max_concurrent=3)
        q.set_runner(_mk_runner(started, gate))
        ts = [await q.enqueue(f"w{i}") for i in range(6)]
        await asyncio.sleep(0.05)  # 让调度器完成首批派发
        running = [t for t in ts if t.status == "running"]
        queued = [t for t in ts if t.status == "queued"]
        assert len(running) == 3 and len(queued) == 3, \
            "并发上限失效 running=%d queued=%d" % (len(running), len(queued))
        assert len(started) == 3
        snap = await q.snapshot()
        assert len(snap["running"]) == 3 and len(snap["pending"]) == 3
        assert [p["position"] for p in snap["pending"]] == [1, 2, 3]
        gate.set()
        await _wait_terminal(ts)
        assert all(t.status == "success" for t in ts)
        await q.shutdown()
    asyncio.run(_run())


def test_three_parallel():
    """③ ≥3 个工作流并行执行：max_concurrent=4 时 3 个同时 running。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        started = []
        q = TaskQueueManager(max_concurrent=4)
        q.set_runner(_mk_runner(started, gate))
        ts = [await q.enqueue(f"wf{i}") for i in range(3)]
        await asyncio.sleep(0.05)
        assert len([t for t in ts if t.status == "running"]) == 3, \
            "应 3 个工作流并行执行: %s" % [t.status for t in ts]
        assert len(started) == 3
        gate.set()
        await _wait_terminal(ts)
        assert all(t.status == "success" for t in ts)
        await q.shutdown()
    asyncio.run(_run())


def test_priority():
    """④ 优先级：priority 越小越先调度（同优先级保持 FIFO）。"""
    from app.queue import TaskQueueManager

    async def _run():
        started = []
        q = TaskQueueManager(max_concurrent=1)
        q.set_runner(_mk_runner(started))
        t1 = await q.enqueue("wA", priority=5)
        t2 = await q.enqueue("wB", priority=1)
        t3 = await q.enqueue("wC", priority=3)
        await _wait_terminal([t1, t2, t3])
        assert started == [t2.task_id, t3.task_id, t1.task_id], \
            "优先级顺序错误: %s" % started
        # 同优先级 FIFO：再入 3 个同优先级任务
        started.clear()
        ts = [await q.enqueue(f"f{i}", priority=0) for i in range(3)]
        await _wait_terminal(ts)
        assert started == [t.task_id for t in ts], "同优先级 FIFO 错误: %s" % started
        await q.shutdown()
    asyncio.run(_run())


def test_repeat_run_no_cancel():
    """⑤ 同工作流重复 run 不再取消旧任务（R-平台-1 核心验收）。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        started = []
        q = TaskQueueManager(max_concurrent=1)
        q.set_runner(_mk_runner(started, gate))
        t1 = await q.enqueue("wfX")      # 立即开始运行（阻塞在 gate）
        t2 = await q.enqueue("wfX")      # 同工作流第二次入队
        await asyncio.sleep(0.05)
        assert t1.status == "running", "旧任务应保持运行，实际=%s" % t1.status
        assert t2.status == "queued", "第二次入队应等待，实际=%s" % t2.status
        assert t1.status != "cancelled", "重复 run 不应取消旧任务"
        gate.set()
        await _wait_terminal([t1, t2])
        assert t1.status == "success" and t2.status == "success", \
            "两个任务都应成功: %s / %s" % (t1.status, t2.status)
        assert t1.task_id != t2.task_id
        await q.shutdown()
    asyncio.run(_run())


def test_cancel_queued():
    """⑥a 逐任务取消：等待中的任务直接出队，不执行。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        started = []
        q = TaskQueueManager(max_concurrent=1)
        q.set_runner(_mk_runner(started, gate))
        t1 = await q.enqueue("w1")
        t2 = await q.enqueue("w2")
        t3 = await q.enqueue("w3")
        await asyncio.sleep(0.05)
        assert t1.status == "running" and t2.status == "queued" and t3.status == "queued"
        r = await q.cancel_task(t3.task_id)
        assert r == "cancelled" and t3.status == "cancelled" and t3.started_at is None
        snap = await q.snapshot()
        assert not any(p["task_id"] == t3.task_id for p in snap["pending"])
        gate.set()
        await _wait_terminal([t1, t2, t3])
        assert t1.status == "success" and t2.status == "success"
        assert t3.status == "cancelled"
        await q.shutdown()
    asyncio.run(_run())


def test_cancel_running():
    """⑥b 逐任务取消：运行中的任务 cancel 传播 → cancelled。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        started = []
        q = TaskQueueManager(max_concurrent=1)
        q.set_runner(_mk_runner(started, gate))
        t1 = await q.enqueue("w1")
        await asyncio.sleep(0.05)
        assert t1.status == "running"
        r = await q.cancel_task(t1.task_id)
        assert r == "cancel_requested"
        for _ in range(100):
            if t1.status in TERMINAL:
                break
            await asyncio.sleep(0.01)
        assert t1.status == "cancelled", "运行中任务取消未生效: %s" % t1.status
        assert t1.finished_at is not None
        await q.shutdown()
    asyncio.run(_run())


def test_cancel_workflow():
    """⑦ cancel_workflow：整工作流取消（排队中 + 运行中），不伤其他工作流。"""
    from app.queue import TaskQueueManager

    async def _run():
        gate = asyncio.Event()
        q = TaskQueueManager(max_concurrent=4)
        q.set_runner(_mk_runner([], gate))
        a1 = await q.enqueue("wfA")
        a2 = await q.enqueue("wfA")
        b1 = await q.enqueue("wfB")
        await asyncio.sleep(0.05)
        n = await q.cancel_workflow("wfA")
        assert n == 2, "应取消 2 个 wfA 任务，实际=%d" % n
        for _ in range(100):
            if a1.status in TERMINAL and a2.status in TERMINAL:
                break
            await asyncio.sleep(0.01)
        assert a1.status == "cancelled" and a2.status == "cancelled"
        assert b1.status == "running", "wfB 任务不应被误伤"
        gate.set()
        await _wait_terminal([b1])
        assert b1.status == "success"
        await q.shutdown()
    asyncio.run(_run())


# ================================================================ API 级（TestClient + app.server）
def _prepare_server():
    """导入 app.server，把 wfstore/store/user_nodes/队列 指到临时目录（不碰仓库数据）。"""
    import app.server as srv
    from app.assets import create_store
    from app.wfstore import WorkflowStore
    from app.queue import TaskQueueManager

    # 临时目录直接建在 gettempdir() 下（DSH 沙箱对 mkdtemp 随机子目录的子目录创建有限制，
    # 但对临时根的一级子目录可读写），测试结束由 shutil.rmtree 清理。
    tmp = os.path.join(tempfile.gettempdir(), "fw_t1_queue_api_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    nodes_dir = os.path.join(tmp, "user_nodes")
    pkg_dir = os.path.join(nodes_dir, "echo_q")
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 2, "engine_api": "1.0", "type_id": "user/echo_q",
            "title": "echo_q", "category": "用户节点", "description": "queue test echo",
            "version": "1.0.0", "kind": "python", "runtime": "python",
            "entrypoint": "node.py", "class_name": "EchoQNode",
            "inputs": [],
            "outputs": [{"name": "out", "type": "JSON", "label": "out", "required": False}],
            "params": [{"name": "sleep", "type": "FLOAT"}],
            "permission": {},
        }, f, ensure_ascii=False, indent=2)
    with open(os.path.join(pkg_dir, "node.py"), "w", encoding="utf-8") as f:
        f.write(
            "import asyncio\n"
            "from app.assets import Asset\n"
            "\n"
            "class EchoQNode:\n"
            "    async def run(self, ctx, inputs, params):\n"
            "        s = float((params or {}).get('sleep', 0.0) or 0.0)\n"
            "        if s > 0:\n"
            "            await asyncio.sleep(s)\n"
            "        store = ctx['store'] if isinstance(ctx, dict) else ctx.store\n"
            "        out = store.save_asset(\n"
            "            Asset(id='', kind='JSON', meta={}), payload={'ok': True})\n"
            "        return {'out': out}\n"
        )
    srv.declarative.set_user_nodes_dir(nodes_dir)
    n = srv.declarative.scan()
    assert n >= 1 and srv.declarative.pkg_dir("user/echo_q") is not None, \
        "echo 节点扫描失败 n=%d" % n
    srv.store = create_store("local", os.path.join(tmp, "assets"))
    srv.wfstore = WorkflowStore(os.path.join(tmp, "workflows"))
    # 每个 API 测试独立队列实例（避免跨 TestClient 事件循环复用 asyncio 原语）
    srv.task_queue = TaskQueueManager(max_concurrent=4)
    srv.task_queue.set_runner(srv._queue_run_task)
    return srv, tmp


def _api_headers(srv):
    from app.obf import x_fw_token_header
    return {x_fw_token_header(): os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "fw-t1-test-token")}


def _create_wf(c, h, name, sleep):
    r = c.post("/api/workflows", json={
        "name": name,
        "nodes": [{"id": "n1", "type": "user/echo_q", "x": 0, "y": 0,
                   "params": {"sleep": sleep}, "inputs": {}}],
        "edges": [],
    }, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_api_e2e_three_parallel():
    """⑧ API：并发提交 3 个 run → 3 个并行执行 → 全部成功（R-平台-1 实测）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        wfids = [_create_wf(c, h, f"e2e{i}", 0.4) for i in range(3)]
        runs = []
        for wid in wfids:
            r = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body.get("task_id"), body
            runs.append(body)
        # 立即快照：3 个应并行 running（max_concurrent=4 ≥ 3）
        q = c.get("/api/queue").json()
        assert len(q["running"]) == 3, "应 3 个并行运行: %s" % q
        assert q["max_concurrent"] >= 3
        assert len(q["pending"]) == 0
        # 等待全部完成
        deadline = time.time() + 20
        while time.time() < deadline:
            q = c.get("/api/queue").json()
            if not q["running"] and not q["pending"]:
                break
            time.sleep(0.05)
        assert not q["running"] and not q["pending"], "队列未清空: %s" % q
        for wid in wfids:
            wf = c.get(f"/api/workflows/{wid}").json()
            assert wf["nodes"][0]["status"] == "success", wf


def test_api_repeat_run_no_cancel():
    """⑨ API：同工作流重复 run 不取消旧任务。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        wid = _create_wf(c, h, "repeat", 0.5)
        r1 = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h).json()
        r2 = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h).json()
        assert r1["task_id"] != r2["task_id"]
        # 第二次入队后，第一次任务必须仍在运行/等待（绝不能被取消）
        q = c.get("/api/queue").json()
        ids = {t["task_id"]: t for t in q["running"] + q["pending"]}
        assert r1["task_id"] in ids, "旧任务消失（被取消？）: %s" % q
        assert ids[r1["task_id"]]["status"] in ("running", "queued"), \
            "旧任务状态异常: %s" % ids[r1["task_id"]]
        assert ids[r1["task_id"]]["status"] != "cancelled"
        # 等待两个任务都完成
        deadline = time.time() + 20
        while time.time() < deadline:
            q = c.get("/api/queue").json()
            if not q["running"] and not q["pending"]:
                break
            time.sleep(0.05)
        wf = c.get(f"/api/workflows/{wid}").json()
        node = wf["nodes"][0]
        assert node["status"] == "success", "重复 run 后节点最终应成功: %s" % wf
        assert len(node.get("run_history") or []) >= 1
        assert not any(rh.get("status") == "cancelled" for rh in (node.get("run_history") or []))


def test_api_cancel_task():
    """⑩ API：逐任务取消（DELETE /api/queue/{id}）——等待中出队 + 运行中取消。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        wid = _create_wf(c, h, "cancel", 0.6)
        # 并发上限 4：连发 5 个 run → 1 个必然 pending
        task_ids = []
        for _ in range(5):
            body = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h).json()
            assert body["ok"] is True
            task_ids.append(body["task_id"])
        q = c.get("/api/queue").json()
        assert len(q["running"]) == 4 and len(q["pending"]) == 1, \
            "期望 4 运行 + 1 等待: %s" % q
        # 取消等待中的任务
        pending_id = q["pending"][0]["task_id"]
        r = c.delete(f"/api/queue/{pending_id}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled", r.json()
        q2 = c.get("/api/queue").json()
        assert not any(p["task_id"] == pending_id for p in q2["pending"])
        # 取消一个运行中的任务
        run_id = q2["running"][0]["task_id"]
        r2 = c.delete(f"/api/queue/{run_id}", headers=h)
        assert r2.status_code == 200 and r2.json()["status"] in ("cancel_requested", "cancelled"), r2.json()
        # 未知任务 → 404
        r3 = c.delete("/api/queue/no-such-task", headers=h)
        assert r3.status_code == 404
        # 等剩余任务完成（队列最终清空）
        deadline = time.time() + 20
        while time.time() < deadline:
            q3 = c.get("/api/queue").json()
            if not q3["running"] and not q3["pending"]:
                break
            time.sleep(0.05)


def test_api_queue_snapshot_shape():
    """⑪ API：GET /api/queue 快照结构与字段（running/pending/max_concurrent）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare_server()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        wid = _create_wf(c, h, "snap", 0.2)
        body = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h).json()
        q = c.get("/api/queue").json()
        assert q["ok"] is True
        assert set(q.keys()) >= {"ok", "running", "pending", "max_concurrent"}
        all_tasks = q["running"] + q["pending"]
        assert any(t["task_id"] == body["task_id"] for t in all_tasks)
        for t in all_tasks:
            assert {"task_id", "workflow_id", "status", "priority", "mode",
                    "created_at", "started_at", "finished_at", "error"} <= set(t.keys())
        deadline = time.time() + 20
        while time.time() < deadline:
            q = c.get("/api/queue").json()
            if not q["running"] and not q["pending"]:
                break
            time.sleep(0.05)


# ================================================================ 独立脚本入口（T9 前断言）
def main():
    failures = 0
    tests = [
        test_fifo_order,
        test_concurrency_limit,
        test_three_parallel,
        test_priority,
        test_repeat_run_no_cancel,
        test_cancel_queued,
        test_cancel_running,
        test_cancel_workflow,
        test_api_e2e_three_parallel,
        test_api_repeat_run_no_cancel,
        test_api_cancel_task,
        test_api_queue_snapshot_shape,
    ]
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
    print("=" * 56)
    print("TOTAL FAIL=%d/%d" % (failures, len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
