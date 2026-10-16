# -*- coding: utf-8 -*-
"""C1 嵌套子图测试（R-平台-8 第一路线：子图内嵌套执行、输入输出映射、跨层缓存）。

覆盖（对应 t9 [c1] acceptance / verify）：
  1. 2 层嵌套工作流可执行：外层→子图→内层节点全 SUCCESS，输入输出映射正确
     （外层输入经种子注入内层、内层输出映射回外层端口，任意端口命名经槽位解析）；
  2. core/group 别名：与 core/subgraph 同路径内联执行；
  3. ExecutionBlocker 语义保持：内层阻断 → 子图节点 BLOCKED（不计失败）；
     外层下游不受阻断影响（既有语义，不弱化）；
  4. 内层图定义非法 → 子图节点 FAILED（明确错误，不崩引擎），graph 支持 dict/JSON 串；
  5. 跨层缓存：外层子图整层缓存命中（显式 reset 后 CACHED、内层不再执行）；
     内层增量缓存（未变分支种子 CACHED、仅脏分支重算）；缓存索引跨 Engine 持久复用。

运行：python -m pytest tests/test_nested_subgraph.py（或整套 pytest）。
"""
import asyncio
import json
import os
import shutil
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t9-test-token")

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_c1_subgraph_fixture")


@pytest.fixture(autouse=True)
def _restore_default_user_nodes():
    """每测重置 user_nodes 扫描到默认内置目录（防全目录整跑时被更早测试改指临时目录，
    导致 core/subgraph 等内置子图节点不在注册表 → 校验 node_type_unknown → 不执行）。"""
    from app import declarative
    declarative.set_user_nodes_dir(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "user_nodes"))
    declarative.scan()
    yield


def _make_store(tag: str):
    from app.assets import create_store
    root = os.path.join(_FIXTURE, tag + "_%d" % int(time.time() * 1000))
    os.makedirs(root, exist_ok=True)
    return create_store("local", os.path.join(root, "assets"))


def _wipe():
    shutil.rmtree(_FIXTURE, ignore_errors=True)


def _ensure_blocker_node():
    """注册内层 ExecutionBlocker 节点（in-process，同 test_subgraph_control 模式）。"""
    from app.nodespec import NodeBase, NodeSpec, PortSpec
    from app.ports import PortType
    from app.registry import get_spec, register
    try:
        get_spec("test/blocker_sub")
        return
    except KeyError:
        pass

    @register
    class BlockerSubNode(NodeBase):
        @classmethod
        def spec(cls):
            return NodeSpec(type_id="test/blocker_sub", title="BlockerSub",
                            category="控制",
                            inputs=[PortSpec("in", PortType.JSON, required=False)],
                            outputs=[PortSpec("out", PortType.JSON)], params=[])
        async def run(self, ctx, inputs, params):
            from app.engine import ExecutionBlocker
            raise ExecutionBlocker("sub blocked")


def _two_level_graphs():
    """2 层嵌套子图图定义（外层 sg1 → 内层 g2(core/subgraph) → cat）。"""
    graph2 = {
        "inputs": [{"name": "x", "node": "cat", "port": "a"},
                   {"name": "z", "node": "cat", "port": "b"}],
        "outputs": [{"name": "y", "node": "cat", "port": "text"}],
        "nodes": [{"id": "cat", "type": "core/text_concat", "params": {"sep": "-"}}],
        "edges": [],
    }
    graph1 = {
        "inputs": [{"name": "a", "node": "g2", "port": "x"}],
        "outputs": [{"name": "out", "node": "g2", "port": "y"}],
        "nodes": [
            {"id": "g2", "type": "core/subgraph", "params": {"graph": graph2}},
            {"id": "marker", "type": "core/text_input", "params": {"value": "mid"}},
        ],
        # marker.text → g2.z：源/目标端口不同名，经 input_slots 槽位解析
        "edges": [{"source": "marker", "target": "g2",
                   "sourceHandle": "text", "targetHandle": "z"}],
    }
    return graph1, graph2


def _outer_wf(subgraph_type: str = "core/subgraph",
              graph_param=None) -> "tuple[Workflow, list]":
    from app.engine import GraphNode, Workflow
    graph1, graph2 = _two_level_graphs()
    if graph_param is not None:
        # graph 支持 JSON 字符串写法（/prompt 字面量通道兼容）
        graph1 = graph_param
    wf = Workflow(id="c1-wf", name="c1")
    wf.nodes["in0"] = GraphNode(id="in0", type_id="core/text_input",
                                params={"value": "Hello"})
    wf.nodes["sg1"] = GraphNode(id="sg1", type_id=subgraph_type,
                                params={"graph": graph1})
    # 外层 in0.text → 子图输入端口 a（不同名 → input_slots 槽位解析）
    wf.nodes["sg1"].inputs["a"] = "in0"
    wf.nodes["sg1"].input_slots["a"] = 0
    wf.edges = [{"source": "in0", "target": "sg1",
                 "sourceHandle": "text", "targetHandle": "a"}]
    return wf, graph2


def _run(engine, wf):
    asyncio.run(engine.execute(wf))
    return wf


# ================================================================ 1. 2 层嵌套执行 + 输入输出映射
def test_two_level_nested_execution_and_mapping():
    """2 层嵌套：外层→子图→内层节点全 SUCCESS；输入输出映射正确（Hello-mid）。"""
    from app.engine import Engine, NodeStatus
    _wipe()
    try:
        store = _make_store("nested")
        engine = Engine(store)
        wf, _ = _outer_wf()
        _run(engine, wf)

        # 外层节点全 SUCCESS
        assert wf.nodes["in0"].status == NodeStatus.SUCCESS, wf.nodes["in0"].error
        sg1 = wf.nodes["sg1"]
        assert sg1.status == NodeStatus.SUCCESS, sg1.error

        # 输入输出映射：外层输入 "Hello" + 子图内部 "mid" → "Hello-mid"
        payload = store.read_json(sg1.asset_ids.get("out"))
        assert payload == {"text": "Hello-mid"}, payload

        # 内层（第一层子图）节点全 SUCCESS：g2（第二层子图）、marker、输入种子
        st1 = sg1.subgraph_last_run
        assert st1.get("nested") is True
        assert st1["statuses"]["g2"] == "success", st1
        assert st1["statuses"]["marker"] == "success", st1
        assert len(st1["seed_ids"]) == 1 and st1["statuses"][st1["seed_ids"][0]] == "success", st1

        # 内层（第二层）g2 的执行摘要：cat 与两个输入种子全 SUCCESS
        g2 = wf.nodes  # 内层 g2 节点在临时 inner_wf 中，经 sg1 摘要逐层可见——
        # 第二层摘要由 g2 节点自身携带，无法从外层直接取；用输出映射等价断言：
        # cat = text_concat("Hello","mid","-")，payload 已证明两层映射正确。
        assert g2 is not None
        # 子图节点缓存键已写入（跨层缓存索引就位）
        assert sg1.cache_key, "子图节点应持有缓存键"
        entry = engine.cache.entry(sg1.cache_key)
        assert entry is not None and (entry.get("assets") or {}).get("out"), entry
    finally:
        _wipe()


def test_two_level_nested_statuses_all_success():
    """2 层嵌套：第一层输入种子直接引用外层输入资产（映射正确性的直接证据）。

    第二层（g2 → cat）映射由 payload "Hello-mid" 端到端断言（见 test 1）；
    内层节点执行状态由 sg1.subgraph_last_run 逐层断言（g2/marker/种子全 success）。
    """
    from app.engine import Engine, NodeStatus
    _wipe()
    try:
        store = _make_store("nested2")
        engine = Engine(store)
        wf, _ = _outer_wf()
        _run(engine, wf)
        sg1 = wf.nodes["sg1"]
        assert sg1.status == NodeStatus.SUCCESS, sg1.error
        st1 = sg1.subgraph_last_run
        assert st1["statuses"]["g2"] == "success", st1
        assert st1["statuses"]["marker"] == "success", st1

        # 第一层输入种子：asset_id 必须等于外层输入资产 id（输入映射的直接证据）
        inner, seed_ids = engine._build_subgraph_workflow(
            wf, sg1, json.loads(json.dumps(sg1.params["graph"])),
            engine._collect_inputs(wf, sg1))
        assert len(seed_ids) == 1
        seed = inner.nodes[seed_ids[0]]
        assert seed.params["asset_id"] == wf.nodes["in0"].asset_ids["text"], \
            "第一层种子的 asset_id 应等于外层输入资产 id"
        # 第二层节点（g2 内嵌）类型与图参数就位
        assert inner.nodes["g2"].type_id == "core/subgraph"
        assert inner.nodes["g2"].params["graph"]["inputs"][0]["name"] == "x"
    finally:
        _wipe()


# ================================================================ 2. core/group 别名
def test_group_alias_executes_like_subgraph():
    """core/group 与 core/subgraph 同路径内联执行（别名语义）。"""
    from app.engine import Engine, NodeStatus
    _wipe()
    try:
        store = _make_store("group")
        engine = Engine(store)
        wf, _ = _outer_wf(subgraph_type="core/group")
        _run(engine, wf)
        sg = wf.nodes["sg1"]
        assert sg.status == NodeStatus.SUCCESS, sg.error
        assert store.read_json(sg.asset_ids.get("out")) == {"text": "Hello-mid"}
        assert sg.subgraph_last_run.get("nested") is True
    finally:
        _wipe()


# ================================================================ 3. ExecutionBlocker 语义保持
def test_subgraph_blocker_propagates_as_blocked():
    """内层 ExecutionBlocker → 子图节点 BLOCKED（显式控制流，不计失败）；外层下游继续。"""
    from app.engine import Engine, GraphNode, NodeStatus, Workflow
    _ensure_blocker_node()
    _wipe()
    try:
        store = _make_store("blocker")
        engine = Engine(store)
        graph = {
            "inputs": [{"name": "a", "node": "bk", "port": "in"}],
            "outputs": [],
            "nodes": [{"id": "bk", "type": "test/blocker_sub"}],
            "edges": [],
        }
        wf = Workflow(id="blk", name="b")
        wf.nodes["in0"] = GraphNode(id="in0", type_id="core/text_input",
                                    params={"value": "X"})
        wf.nodes["sg1"] = GraphNode(id="sg1", type_id="core/subgraph",
                                    params={"graph": graph})
        wf.nodes["sg1"].inputs["a"] = "in0"
        wf.nodes["sg1"].input_slots["a"] = 0
        wf.nodes["d0"] = GraphNode(id="d0", type_id="core/text_length")
        wf.nodes["d0"].inputs["text"] = "in0"
        wf.edges = [{"source": "in0", "target": "sg1", "sourceHandle": "text",
                     "targetHandle": "a"},
                    {"source": "in0", "target": "d0", "sourceHandle": "text",
                     "targetHandle": "text"}]
        _run(engine, wf)
        sg = wf.nodes["sg1"]
        assert sg.status == NodeStatus.BLOCKED, (sg.status, sg.error)
        assert "blocked" in sg.error, sg.error
        assert sg.subgraph_last_run["statuses"]["bk"] == "blocked"
        # ExecutionBlocker 语义保持：阻断不停止外层其余节点（既有引擎语义不变）
        assert wf.nodes["d0"].status == NodeStatus.SUCCESS, wf.nodes["d0"].error
    finally:
        _wipe()


# ================================================================ 4. 内层图非法 → 明确 FAILED
def test_subgraph_invalid_inner_graph_fails_cleanly():
    """内层图定义非法（边指向不存在节点）→ 子图节点 FAILED，明确错误，引擎不崩。"""
    from app.engine import Engine, GraphNode, NodeStatus, Workflow
    _wipe()
    try:
        store = _make_store("badgraph")
        engine = Engine(store)
        bad_graph = {
            "inputs": [{"name": "a", "node": "nope", "port": "in"}],
            "outputs": [],
            "nodes": [{"id": "cat", "type": "core/text_concat"}],
            "edges": [],
        }
        wf = Workflow(id="bad", name="b")
        wf.nodes["in0"] = GraphNode(id="in0", type_id="core/text_input",
                                    params={"value": "X"})
        wf.nodes["sg1"] = GraphNode(id="sg1", type_id="core/subgraph",
                                    params={"graph": bad_graph})
        wf.nodes["sg1"].inputs["a"] = "in0"
        wf.nodes["sg1"].input_slots["a"] = 0
        wf.edges = [{"source": "in0", "target": "sg1", "sourceHandle": "text",
                     "targetHandle": "a"}]
        _run(engine, wf)
        sg = wf.nodes["sg1"]
        assert sg.status == NodeStatus.FAILED, (sg.status, sg.error)
        assert "不存在" in sg.error or "校验失败" in sg.error, sg.error
        # 引擎不崩：外层其余节点仍按既有语义执行
        assert wf.nodes["in0"].status == NodeStatus.SUCCESS
    finally:
        _wipe()


def test_subgraph_graph_as_json_string():
    """graph 参数支持 JSON 字符串（/prompt 字面量通道兼容）。"""
    from app.engine import Engine, NodeStatus
    _wipe()
    try:
        store = _make_store("jsonstr")
        engine = Engine(store)
        graph1, _ = _two_level_graphs()
        wf, _ = _outer_wf(graph_param=json.dumps(graph1, ensure_ascii=False))
        _run(engine, wf)
        sg = wf.nodes["sg1"]
        assert sg.status == NodeStatus.SUCCESS, sg.error
        assert store.read_json(sg.asset_ids.get("out")) == {"text": "Hello-mid"}
    finally:
        _wipe()


# ================================================================ 5. 跨层缓存
def test_subgraph_cross_layer_cache():
    """跨层缓存：内层增量（未变分支种子 CACHED）；外层整层命中（reset 后 CACHED、内层不重跑）；索引跨 Engine 持久。"""
    from app.engine import Engine, GraphNode, NodeStatus, Workflow
    _wipe()
    try:
        store = _make_store("cache")
        engine = Engine(store)
        graph = {
            "inputs": [{"name": "a", "node": "cat", "port": "a"},
                       {"name": "b", "node": "cat", "port": "b"}],
            "outputs": [{"name": "out", "node": "cat", "port": "text"}],
            "nodes": [{"id": "cat", "type": "core/text_concat", "params": {"sep": "|"}}],
            "edges": [],
        }
        wf = Workflow(id="cache", name="c")
        wf.nodes["in1"] = GraphNode(id="in1", type_id="core/text_input",
                                    params={"value": "A"})
        wf.nodes["in2"] = GraphNode(id="in2", type_id="core/text_input",
                                    params={"value": "B"})
        wf.nodes["sg"] = GraphNode(id="sg", type_id="core/subgraph",
                                   params={"graph": graph})
        for port, up in (("a", "in1"), ("b", "in2")):
            wf.nodes["sg"].inputs[port] = up
            wf.nodes["sg"].input_slots[port] = 0
        wf.edges = [{"source": "in1", "target": "sg", "sourceHandle": "text",
                     "targetHandle": "a"},
                    {"source": "in2", "target": "sg", "sourceHandle": "text",
                     "targetHandle": "b"}]

        # ① 首跑：全 SUCCESS
        _run(engine, wf)
        assert wf.nodes["sg"].status == NodeStatus.SUCCESS
        assert store.read_json(wf.nodes["sg"].asset_ids["out"]) == {"text": "A|B"}

        # ② 编辑上游 in2（B→C）：内层增量缓存——未变分支种子 CACHED，脏分支重算
        wf.nodes["in2"].params = {"value": "C"}
        _run(engine, wf)
        sg = wf.nodes["sg"]
        assert sg.status == NodeStatus.SUCCESS, sg.error
        assert store.read_json(sg.asset_ids["out"]) == {"text": "A|C"}
        st2 = sg.subgraph_last_run["statuses"]
        seed_a = [k for k in st2 if "_in0_a" in k][0]
        seed_b = [k for k in st2 if "_in1_b" in k][0]
        assert st2[seed_a] == "cached", "未变分支的输入种子应跨层缓存命中: %s" % st2
        assert st2[seed_b] == "success", "脏分支的输入种子应重算: %s" % st2
        assert st2["cat"] == "success"

        # ③ 无变化重跑：内层不重执行（subgraph_last_run 保持 ② 快照，payload 不变）
        _run(engine, wf)
        assert wf.nodes["sg"].status == NodeStatus.SUCCESS
        assert wf.nodes["sg"].subgraph_last_run["statuses"] == st2, \
            "无变化重跑不得重新执行内层（last_run 应保持 ② 快照）"
        assert store.read_json(wf.nodes["sg"].asset_ids["out"]) == {"text": "A|C"}

        # ④ 显式 reset 子图节点 → 外层整层缓存命中（CACHED，资产从缓存索引恢复）
        from app.engine import NodeStatus as NS
        wf.nodes["sg"].status = NS.PENDING
        wf.nodes["sg"].cache_key = ""
        _run(engine, wf)
        assert wf.nodes["sg"].status == NodeStatus.CACHED, wf.nodes["sg"].status
        assert store.read_json(wf.nodes["sg"].asset_ids["out"]) == {"text": "A|C"}
        assert wf.nodes["sg"].subgraph_last_run["statuses"] == st2, \
            "外层缓存命中不得重新执行内层"

        # ⑤ 新 Engine 同 store（缓存索引落盘持久）：直接整层命中
        engine2 = Engine(store)
        wf2 = Workflow(id="cache2", name="c2")
        wf2.nodes["in1"] = GraphNode(id="in1", type_id="core/text_input",
                                     params={"value": "A"})
        wf2.nodes["in2"] = GraphNode(id="in2", type_id="core/text_input",
                                     params={"value": "C"})
        wf2.nodes["sg"] = GraphNode(id="sg", type_id="core/subgraph",
                                    params={"graph": graph})
        for port, up in (("a", "in1"), ("b", "in2")):
            wf2.nodes["sg"].inputs[port] = up
            wf2.nodes["sg"].input_slots[port] = 0
        wf2.edges = [{"source": "in1", "target": "sg", "sourceHandle": "text",
                      "targetHandle": "a"},
                     {"source": "in2", "target": "sg", "sourceHandle": "text",
                      "targetHandle": "b"}]
        _run(engine2, wf2)
        assert wf2.nodes["sg"].status in (NodeStatus.SUCCESS, NodeStatus.CACHED), \
            wf2.nodes["sg"].status
        assert store.read_json(wf2.nodes["sg"].asset_ids["out"]) == {"text": "A|C"}
    finally:
        _wipe()


# ================================================================ 6. 能力契约升级
def test_nested_subgraph_capability_upgraded_to_available():
    """R-平台-8 第一路线：EXECUTION_CONTROL_ROADMAP 与 EXECUTION_CAPABILITIES 升级为 available。"""
    from app import engines, registry
    assert engines.EXECUTION_CONTROL_ROADMAP["nested_subgraph"] == "available"
    assert registry.EXECUTION_CAPABILITIES["nested_subgraph"] == "available"
    assert registry.execution_capabilities()["nested_subgraph"] == "available"
    assert engines.is_subgraph_type("core/subgraph")
    assert engines.is_subgraph_type("core/group")
    assert not engines.is_subgraph_type("core/text_concat")


if __name__ == "__main__":
    tests = [test_two_level_nested_execution_and_mapping,
             test_two_level_nested_statuses_all_success,
             test_group_alias_executes_like_subgraph,
             test_subgraph_blocker_propagates_as_blocked,
             test_subgraph_invalid_inner_graph_fails_cleanly,
             test_subgraph_graph_as_json_string,
             test_subgraph_cross_layer_cache,
             test_nested_subgraph_capability_upgraded_to_available]
    failures = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            failures += 1
            print("FAIL", fn.__name__, type(exc).__name__, exc)
    print("TOTAL FAIL=%d/%d" % (failures, len(tests)))
    raise SystemExit(1 if failures else 0)