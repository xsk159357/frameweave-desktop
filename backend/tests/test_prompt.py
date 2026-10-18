# -*- coding: utf-8 -*-
"""S1-T3 标准执行协议 POST /prompt 验收测试（P0-3 / R-平台-3）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T3 acceptance/verify）：
  1. 纯执行图 {node_id: {class_type, inputs}} → 内部 Workflow 映射（链接 [up, slot]、
     字面量、params vs inputs、input_slots）；
  2. partial 目标（partial_execution_targets / partial）上游闭包子图剪枝；
  3. API：POST /prompt（含 /api/prompt 别名）→ {prompt_id, number} 排队；
     执行完成后 GET /history/{prompt_id} 返回 ComfyUI 语义 outputs（IMAGE 含 images 条目）；
  4. 校验失败：未知 class_type / 未知输入 / 链接缺失 → ComfyUI 风格 {"error", "node_errors"}；
  5. 双通道一致性：/api/specs 已注册节点 class_type 均可被 prompt 适配器接受；
  6. /api/workflows/{id}/run 保持兼容（回归）。

运行方式：
  - T9 统一 pytest 前先以独立脚本断言：`python tests/test_prompt.py`（退出码 0=全绿）；
  - pytest 收集兼容：本文件顶层仅做轻量 import，pytest 可直接收集 test_* 函数。
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t3-test-token")

# 1x1 像素 PNG（测试用 IMAGE 输出）
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

# ================================================================ 单元：prompt → Workflow 映射
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _shutdown_queue(srv):
    """同步测试收尾：取消残留调度任务（本文件不起事件循环，sched 为空时即空操作）。

    原写法 `srv.task_queue.shutdown()` 是 async，未 await 会刷 RuntimeWarning
    「coroutine was never awaited」（2026-10-19 审阅 P3-12）。
    """
    sched = getattr(getattr(srv, "task_queue", None), "_sched", None)
    if sched is not None and not sched.done():
        sched.cancel()

def _mk_manifest(pkg_dir, type_id, kind="python", entry="node.py",
                 cls="ProbeNode", inputs=None, outputs=None, params=None):
    import json as _j
    os.makedirs(pkg_dir, exist_ok=True)
    outs = outputs or []
    # 生成节点 run() 体：IMAGE 端口写 1x1 PNG 资产，其余端口写 JSON 资产
    body_lines = []
    for o in outs:
        pname = o["name"]
        if o["type"] == "IMAGE":
            body_lines.append(
                "        _png = base64.b64decode('%s')\n"
                "        _p = _os.path.join(s.files_dir, 'probe.png')\n"
                "        open(_p, 'wb').write(_png)\n"
                "        outs['%s'] = Asset(id='', kind='IMAGE', path=_p, meta={})\n"
                % (_PNG_B64, pname))
        else:
            body_lines.append(
                "        outs['%s'] = s.save_asset(Asset(id='', kind='JSON', meta={}), payload={'ok': True, 'port': '%s'})\n"
                % (pname, pname))
    body = "".join(body_lines) if body_lines else "        pass\n"
    node_src = (
        "import base64, os as _os, time\n"
        "from app.assets import Asset\n"
        "class {cls}:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        s = ctx['store'] if isinstance(ctx, dict) else ctx.store\n"
        "        outs = {{}}\n"
        "{body}"
        "        return outs\n"
    ).format(cls=cls, body=body)
    with open(os.path.join(pkg_dir, "manifest.json"), "w", encoding="utf-8") as f:
        _j.dump({
            "schema_version": 2, "engine_api": "1.0", "type_id": type_id,
            "title": type_id, "category": "用户节点", "description": "t3 probe",
            "version": "1.0.0", "kind": kind, "runtime": kind,
            "entrypoint": entry, "class_name": cls,
            "inputs": inputs or [], "outputs": outs, "params": params or [],
            "permission": {},
        }, f, ensure_ascii=False, indent=2)
    if kind == "python":
        with open(os.path.join(pkg_dir, entry), "w", encoding="utf-8") as f:
            f.write(node_src)


def _prepare():
    """隔离环境：临时 user_nodes + store + wfstore + queue（不碰仓库数据）。"""
    import app.server as srv
    from app.assets import create_store
    from app.wfstore import WorkflowStore
    from app.queue import TaskQueueManager

    tmp = os.path.join(tempfile.gettempdir(), "fw_t3_prompt_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    nodes_dir = os.path.join(tmp, "user_nodes")
    _mk_manifest(os.path.join(nodes_dir, "img_probe"), "user/img_probe",
                 inputs=[], outputs=[{"name": "image", "type": "IMAGE"}],
                 params=[])
    # 输出双端口（IMAGE 槽 0 + JSON 槽 1），便于验证链接按槽位解析
    _mk_manifest(os.path.join(nodes_dir, "dual_probe"), "user/dual_probe",
                 inputs=[], outputs=[{"name": "image", "type": "IMAGE"},
                                     {"name": "meta", "type": "JSON"}],
                 params=[])
    # 可选输入端口（partial 剪枝后未连线的上游节点也能通过图校验）
    _mk_manifest(os.path.join(nodes_dir, "echo_q"), "user/echo_q",
                 inputs=[{"name": "in", "type": "STRING", "required": False}],
                 outputs=[{"name": "out", "type": "JSON"}],
                 params=[{"name": "sleep", "type": "FLOAT"}])
    srv.declarative.set_user_nodes_dir(nodes_dir)
    n = srv.declarative.scan()
    assert n >= 3, "探针节点扫描失败 n=%d" % n
    srv.store = create_store("local", os.path.join(tmp, "assets"))
    srv.wfstore = WorkflowStore(os.path.join(tmp, "workflows"))
    srv.task_queue = TaskQueueManager(max_concurrent=4)
    srv.task_queue.set_runner(srv._queue_run_task)
    # 隔离 prompt 状态容器（避免跨测试串扰）
    srv._prompt_wfs = {}
    srv._prompt_histories = {}
    return srv, tmp


def test_mapping_links_and_literals():
    """① 纯执行图 → Workflow：链接 [up, slot] 进 inputs+input_slots；字面量分 params/literal_inputs。"""
    from app.server import _build_prompt_workflow
    srv, _ = _prepare()
    prompt = {
        "1": {"class_type": "user/echo_q",
              "inputs": {"in": ["2", 0], "sleep": 1.5}},
        "2": {"class_type": "user/dual_probe", "inputs": {}},
    }
    wf, err = _build_prompt_workflow(prompt, [])
    assert err is None, err
    assert set(wf.nodes) == {"1", "2"}
    n1 = wf.nodes["1"]
    assert n1.inputs == {"in": "2"} and n1.input_slots == {"in": 0}
    assert n1.params == {"sleep": 1.5}
    assert n1.literal_inputs == {}
    n2 = wf.nodes["2"]
    assert n2.inputs == {} and n2.params == {}
    _shutdown_queue(srv)


def test_mapping_literal_on_input_port():
    """② 字面量直填输入端口 → literal_inputs（engine 运行时无上游链接也生效）。"""
    from app.server import _build_prompt_workflow
    srv, _ = _prepare()
    prompt = {"1": {"class_type": "user/echo_q", "inputs": {"in": "hello"}}}
    wf, err = _build_prompt_workflow(prompt, [])
    assert err is None, err
    assert wf.nodes["1"].literal_inputs == {"in": "hello"}
    assert wf.nodes["1"].inputs == {}
    _shutdown_queue(srv)


def test_mapping_unknown_input_rejected():
    """③ 未知输入键 / 未注册 class_type → ComfyUI 风格错误包（不抛异常）。"""
    from app.server import _build_prompt_workflow
    srv, _ = _prepare()
    wf, err = _build_prompt_workflow(
        {"1": {"class_type": "user/echo_q", "inputs": {"bogus": 1}}}, [])
    assert wf is None and err is not None
    assert err["error"]["type"] == "invalid_prompt"
    assert "1" in err["node_errors"]
    wf2, err2 = _build_prompt_workflow(
        {"1": {"class_type": "user/not_exist", "inputs": {}}}, [])
    assert wf2 is None and err2 is not None
    assert "class_type_not_found" in [e[0] for e in err2["node_errors"]["1"]]
    _shutdown_queue(srv)


def test_partial_prune_upstream_closure():
    """④ partial 目标：仅保留目标的上游闭包子图；目标缺失 → 错误。"""
    from app.server import _build_prompt_workflow
    srv, _ = _prepare()
    prompt = {
        "1": {"class_type": "user/echo_q", "inputs": {"in": ["2", 0]}},
        "2": {"class_type": "user/echo_q", "inputs": {"in": ["3", 0]}},
        "3": {"class_type": "user/echo_q", "inputs": {}},
        "4": {"class_type": "user/echo_q", "inputs": {}},
    }
    wf, err = _build_prompt_workflow(prompt, ["1"])
    assert err is None, err
    # 目标 1 的上游闭包 = {1, 2, 3}；4 被剪掉
    assert set(wf.nodes) == {"1", "2", "3"}, set(wf.nodes)
    wf2, err2 = _build_prompt_workflow(prompt, ["9"])
    assert wf2 is None and err2 is not None
    _shutdown_queue(srv)


def test_engine_runs_prompt_workflow():
    """⑤ 引擎实测：链接按上游输出槽位解析（dual_probe[0]=IMAGE 槽 0 → echo_q.in）。"""
    from app.engine import Engine, NodeStatus
    srv, tmp = _prepare()
    from app.server import _build_prompt_workflow
    prompt = {
        "1": {"class_type": "user/echo_q", "inputs": {"in": ["2", 0]}},
        "2": {"class_type": "user/dual_probe", "inputs": {}},
    }
    wf, err = _build_prompt_workflow(prompt, [])
    assert err is None, err
    engine = Engine(srv.store, on_event=lambda ev: None)
    asyncio.run(engine.execute(wf, run_mode="all"))
    n2 = wf.nodes["2"]
    assert n2.status == NodeStatus.SUCCESS, n2.status
    assert n2.asset_ids.get("image"), "dual_probe image 输出缺失"
    n1 = wf.nodes["1"]
    assert n1.status == NodeStatus.SUCCESS, n1.status
    assert n1.asset_ids.get("out"), "echo_q 经槽位 0 链接应拿到上游 IMAGE 输出并产出 out"
    _shutdown_queue(srv)


# ================================================================ API 级（TestClient + app.server）
def _api_headers(srv):
    from app.obf import x_fw_token_header
    return {x_fw_token_header(): os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "fw-t3-test-token")}


def _wait_queue_empty(srv, c, deadline=20):
    import time as _t
    end = _t.time() + deadline
    while _t.time() < end:
        q = c.get("/api/queue", headers=_api_headers(srv)).json()
        if not q.get("running") and not q.get("pending"):
            return True
        _t.sleep(0.05)
    return False


def test_api_prompt_image_output_history():
    """⑥ API：POST /prompt（img_probe）→ prompt_id+number → 队列完成 →
       GET /history/{prompt_id} 返回 outputs.images（官方客户端完成判定依据）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        body = {"prompt": {"1": {"class_type": "user/img_probe", "inputs": {}}},
                "client_id": "1234567890"}
        r = c.post("/prompt", json=body, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("prompt_id") and "error" not in j, r.text
        pid = j["prompt_id"]
        assert isinstance(j.get("number"), int), j
        assert _wait_queue_empty(srv, c), "prompt 任务未完成"
        h2 = c.get(f"/history/{pid}", headers=h)
        assert h2.status_code == 200, h2.text
        rec = h2.json().get(pid)
        assert rec and rec["status"]["status_str"] == "success", h2.text
        out = rec["outputs"]
        assert "1" in out and out["1"].get("images"), out
        assert out["1"]["images"][0]["type"] == "output" and out["1"]["images"][0]["filename"]
    _shutdown_queue(srv)


def test_api_prompt_validation_error_shape():
    """⑦ API：未知 class_type → 200 + {"error", "node_errors"}（官方客户端依 'error' 键抛 QueueError）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        r = c.post("/prompt", json={"prompt": {"1": {"class_type": "user/nope", "inputs": {}}}},
                   headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert "error" in j and "node_errors" in j and "prompt_id" not in j, j
        assert j["node_errors"]["1"][0][0] == "class_type_not_found"
    _shutdown_queue(srv)


def test_api_prompt_alias_and_partial():
    """⑧ /api/prompt 别名 + partial 目标（partial_execution_targets 仅跑目标上游闭包）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        body = {"prompt": {
            "1": {"class_type": "user/echo_q", "inputs": {"in": ["2", 0]}},
            "2": {"class_type": "user/echo_q", "inputs": {}},
            "3": {"class_type": "user/echo_q", "inputs": {}},
        }, "partial_execution_targets": ["1"]}
        r = c.post("/api/prompt", json=body, headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j.get("prompt_id"), r.text
        pid = j["prompt_id"]
        assert _wait_queue_empty(srv, c)
        rec = c.get(f"/history/{pid}", headers=h).json().get(pid)
        assert rec and rec["status"]["status_str"] == "success"
        # 部分执行：节点 3 不在结果里（被剪枝），节点 1/2 成功
        assert "3" not in rec["outputs"], rec
    _shutdown_queue(srv)


def test_dual_channel_consistency_specs_vs_prompt():
    """⑨ 双通道一致性：/api/specs 的已注册节点 class_type 全部可被 prompt 适配器接受。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        specs = c.get("/api/specs", headers=h).json()
        assert isinstance(specs, list) and len(specs) >= 3
        from app.server import _build_prompt_workflow as _bpw
        ok = 0
        for spec in specs:
            prompt = {"n1": {"class_type": spec["type_id"], "inputs": {}}}
            wf, err = _bpw(prompt, [])
            # 空 inputs 必须通过（节点无 required 输入连线时也可执行），失败仅当未注册
            if err is not None and any(e[0] == "class_type_not_found"
                                       for es in err["node_errors"].values() for e in es):
                raise AssertionError(f"specs 注册但 prompt 拒绝: {spec['type_id']} {err}")
            ok += 1
        assert ok == len(specs), f"双通道不一致: {ok}/{len(specs)}"
    _shutdown_queue(srv)


def test_api_workflows_run_regression():
    """⑩ 回归：/api/workflows/{id}/run 保持兼容（入队 + 执行 + 节点成功）。"""
    from fastapi.testclient import TestClient
    srv, _ = _prepare()
    with TestClient(srv.app) as c:
        h = _api_headers(srv)
        r = c.post("/api/workflows", json={
            "name": "reg",
            "nodes": [{"id": "n1", "type": "user/img_probe", "x": 0, "y": 0, "params": {}, "inputs": {}}],
            "edges": [],
        }, headers=h)
        assert r.status_code == 201, r.text
        wid = r.json()["id"]
        r2 = c.post(f"/api/workflows/{wid}/run", json={"mode": "all"}, headers=h)
        assert r2.status_code == 200 and r2.json().get("task_id"), r2.text
        assert _wait_queue_empty(srv, c)
        wf = c.get(f"/api/workflows/{wid}", headers=h).json()
        assert wf["nodes"][0]["status"] == "success", wf
    _shutdown_queue(srv)


# ================================================================ 独立脚本入口（T9 前断言）
def main():
    failures = 0
    tests = [
        test_mapping_links_and_literals,
        test_mapping_literal_on_input_port,
        test_mapping_unknown_input_rejected,
        test_partial_prune_upstream_closure,
        test_engine_runs_prompt_workflow,
        test_api_prompt_image_output_history,
        test_api_prompt_validation_error_shape,
        test_api_prompt_alias_and_partial,
        test_dual_channel_consistency_specs_vs_prompt,
        test_api_workflows_run_regression,
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
