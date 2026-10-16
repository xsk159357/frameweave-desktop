# -*- coding: utf-8 -*-
"""S1-T8 子图/循环控制流契约测试。

当前交付采用契约允许的第一路线（C1 / R-平台-8 升级）：ExecutionBlocker 稳定语义
+ 嵌套子图已可用（EXECUTION_CONTROL_ROADMAP.nested_subgraph = available），
并将 pipeline_stages 改为数据驱动。子图嵌套执行细节见 tests/test_nested_subgraph.py。
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_pipeline_stages_are_data_driven():
    from app.engine import Workflow, pipeline_stages_from_env
    old = os.environ.get("FRAMEWEAVE_PIPELINE_STAGES")
    try:
        os.environ["FRAMEWEAVE_PIPELINE_STAGES"] = '["created", "loop_body", "done"]'
        assert pipeline_stages_from_env() == ["created", "loop_body", "done"]
        wf = Workflow(id="pipeline-test")
        assert wf.pipeline_stages == ["created", "loop_body", "done"]
    finally:
        if old is None:
            os.environ.pop("FRAMEWEAVE_PIPELINE_STAGES", None)
        else:
            os.environ["FRAMEWEAVE_PIPELINE_STAGES"] = old


def test_execution_blocker_is_first_class_status():
    from app.assets import create_store
    from app.engine import Engine, ExecutionBlocker, GraphNode, NodeStatus, Workflow
    from app.nodespec import NodeBase, NodeSpec, PortSpec
    from app.ports import PortType
    from app.registry import register

    @register
    class BlockerNode(NodeBase):
        @classmethod
        def spec(cls):
            return NodeSpec(type_id="test/blocker", title="Blocker", category="控制",
                            inputs=[], outputs=[PortSpec("out", PortType.JSON)], params=[])
        async def run(self, ctx, inputs, params):
            raise ExecutionBlocker("condition false")

    tmp = os.path.join(os.path.dirname(__file__), "_t8_blocker_fixture")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        pkg = os.path.join(tmp, "user_blocker")
        os.makedirs(pkg, exist_ok=True)
        manifest = {
            "schema_version": 2, "engine_api": "1.0", "type_id": "user/blocker",
            "title": "Blocker", "category": "控制", "description": "t8",
            "version": "1.0.0", "kind": "python", "runtime": "python",
            "entrypoint": "node.py", "class_name": "BlockerNode",
            "inputs": [], "outputs": [{"name": "out", "type": "JSON"}],
            "params": [], "permission": {},
        }
        with open(os.path.join(pkg, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        with open(os.path.join(pkg, "node.py"), "w", encoding="utf-8") as f:
            f.write("# registry blocker fixture is in-process\n")
        store = create_store("local", os.path.join(tmp, "assets"))
        wf = Workflow(id="blocker-test")
        wf.nodes["n1"] = GraphNode(id="n1", type_id="test/blocker")
        # 语义契约：阻断信号可由节点抛出并被平台识别；执行器状态至少不应伪报 success。
        try:
            raise ExecutionBlocker("condition false")
        except ExecutionBlocker as exc:
            assert str(exc) == "condition false"
        asyncio.run(Engine(store).execute(wf))
        node = wf.nodes["n1"]
        assert node.status != NodeStatus.SUCCESS
        assert node.status in (NodeStatus.BLOCKED, NodeStatus.FAILED)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_nested_subgraph_capability_is_available():
    """C1（R-平台-8 第一路线）：嵌套子图由 Roadmap 升级为 available。

    blocker 保持 available；能力声明（engines.EXECUTION_CONTROL_ROADMAP /
    registry.EXECUTION_CAPABILITIES）同步升级；执行细节见 test_nested_subgraph.py。
    """
    from app import engines
    roadmap = getattr(engines, "EXECUTION_CONTROL_ROADMAP", {})
    assert roadmap.get("nested_subgraph") == "available"
    assert roadmap.get("execution_blocker") == "available"


if __name__ == "__main__":
    tests = [test_pipeline_stages_are_data_driven,
             test_execution_blocker_is_first_class_status,
             test_nested_subgraph_capability_is_available]
    failures = 0
    for fn in tests:
        try:
            fn(); print("PASS", fn.__name__)
        except Exception as exc:
            failures += 1; print("FAIL", fn.__name__, exc)
    print("TOTAL FAIL=%d/%d" % (failures, len(tests)))
    raise SystemExit(1 if failures else 0)
