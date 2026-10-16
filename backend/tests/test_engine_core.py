# -*- coding: utf-8 -*-
"""S1-T9 引擎核心测试（R-平台-9：拓扑 / 缓存 / 超时）。

覆盖：
  1. 拓扑：KahnScheduler 并行批次（菱形 DAG → 正确分层）、环检测（含自环）；
  2. 缓存：IncrementalCacheProvider 键稳定性（同参同键）、命中需资产在场、
     invalidate/trim 语义、落盘 load/save 幂等；
  3. 超时：node_timeout 按信任级分级（L0/L1/L2 + 渲染类 L0 1800s 上限保留）、
     声明值在信任级上限内可覆盖、setup_timeout 公式；
  4. 超时执行：inprocess 节点 run 超时 → PLUGIN_TIMEOUT 收口为 FAILED 不崩引擎；
  5. 回归（t5 发现 / captain 附加）：WorkerExecutor 单例池跨 Engine 复用不得
     带旧 host/store——连续两个不同 store 的 run，第二次输出必须落新 store、
     新 store 缓存索引不被 trim。

运行：python -m pytest tests/test_engine_core.py（或整套 pytest）。
"""
import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t9-test-token")

TYPE_ID = "user/echo_t9"

_NODE_SRC = (
    "from app.assets import Asset\n"
    "class EchoT9:\n"
    "    async def run(self, ctx, inputs, params):\n"
    "        store = ctx['store'] if isinstance(ctx, dict) else ctx.store\n"
    "        out = store.save_asset(Asset(id='', kind='JSON', meta={}), "
    "payload={'v': (params or {}).get('val')})\n"
    "        return {'out': out}\n"
)

_MANIFEST = {
    "schema_version": 2, "engine_api": "1.0", "type_id": TYPE_ID,
    "title": "echo_t9", "category": "用户节点", "description": "t9 engine test",
    "version": "1.0.0", "kind": "python", "runtime": "python",
    "entrypoint": "node.py", "class_name": "EchoT9",
    "inputs": [{"name": "in", "type": "JSON", "required": False}],
    "outputs": [{"name": "out", "type": "JSON", "required": False}],
    "params": [{"name": "val", "type": "JSON", "default": "x"}],
    "permission": {},
}


def _mk_store(tag: str):
    from app.assets import create_store
    tmp = os.path.join(tempfile.gettempdir(), "fw_t9_%s_%d" % (tag, int(time.time() * 1000)))
    os.makedirs(tmp, exist_ok=True)
    return create_store("local", os.path.join(tmp, "assets"))


def _install_test_pkg(store_root: str) -> None:
    """把 user/echo_t9 测试插件写入 user_nodes 并扫描（worker 模式必需）。"""
    from app import declarative
    nodes_dir = os.path.abspath(os.path.join(store_root, "..", "user_nodes"))
    os.makedirs(nodes_dir, exist_ok=True)
    pkg = os.path.join(nodes_dir, "test_echo")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(_MANIFEST, f, ensure_ascii=False, indent=2)
    with open(os.path.join(pkg, "node.py"), "w", encoding="utf-8") as f:
        f.write(_NODE_SRC)
    declarative.set_user_nodes_dir(nodes_dir)
    n = declarative.scan()
    assert declarative.pkg_dir(TYPE_ID) is not None, "测试节点扫描失败 n=%d" % n


def _make_wf():
    from app.engine import GraphNode, NodeStatus, Workflow
    wf = Workflow(id="echo-wf", name="x")
    wf.nodes["n1"] = GraphNode(id="n1", type_id=TYPE_ID,
                               params={"val": "x"}, status=NodeStatus.PENDING)
    return wf


# ================================================================ 拓扑
def test_topological_batches_diamond():
    """Kahn 拓扑：菱形 DAG 正确分层（第 0 层 A；第 1 层 B/C；第 2 层 D）。"""
    from app.engines import KahnScheduler
    from app.engine import GraphNode, NodeStatus, Workflow

    wf = Workflow(id="topo-diamond", name="diamond")
    for nid in ("A", "B", "C", "D"):
        wf.nodes[nid] = GraphNode(id=nid, type_id=TYPE_ID, status=NodeStatus.PENDING)
    wf.edges = [
        {"source": "A", "target": "B"},
        {"source": "A", "target": "C"},
        {"source": "B", "target": "D"},
        {"source": "C", "target": "D"},
    ]
    batches = KahnScheduler().topological_batches(wf)
    assert batches == [["A"], ["B", "C"], ["D"]], batches


def test_cycle_detection():
    """环检测：A→B→A 报环路；自环 A→A 报环路。"""
    from app.engines import KahnScheduler
    from app.engine import GraphNode, NodeStatus, Workflow

    wf = Workflow(id="topo-cycle", name="cycle")
    for nid in ("A", "B"):
        wf.nodes[nid] = GraphNode(id=nid, type_id=TYPE_ID, status=NodeStatus.PENDING)
    wf.edges = [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}]
    errors = KahnScheduler().validate(wf)
    assert any("环路" in e for e in errors), errors

    wf2 = Workflow(id="topo-self", name="self")
    wf2.nodes["A"] = GraphNode(id="A", type_id=TYPE_ID, status=NodeStatus.PENDING)
    wf2.edges = [{"source": "A", "target": "A"}]
    assert any("环路" in e for e in KahnScheduler().validate(wf2))


# ================================================================ 缓存
def test_cache_key_stable_and_sensitive():
    """缓存键：同参同键；参数变化键变化。"""
    from app.engines import IncrementalCacheProvider
    from app.engine import GraphNode, NodeStatus, Workflow

    store = _mk_store("cache")
    _install_test_pkg(store.root)
    prov = IncrementalCacheProvider(
        store, os.path.join(os.path.dirname(store.root), "cache_index.json"))

    wf = Workflow(id="cache-wf", name="x")
    wf.nodes["n1"] = GraphNode(id="n1", type_id=TYPE_ID, params={"val": "a"},
                               status=NodeStatus.PENDING)
    key1 = prov.cache_key(wf, wf.nodes["n1"])
    wf.nodes["n1"].params = {"val": "b"}
    key2 = prov.cache_key(wf, wf.nodes["n1"])
    assert key1 != key2, "参数变化应改变缓存键"
    wf.nodes["n1"].params = {"val": "a"}
    assert prov.cache_key(wf, wf.nodes["n1"]) == key1, "同参应同键"


def test_cache_hit_requires_assets_present():
    """缓存命中：索引存在但资产被清 → 不命中（孤儿保护）。"""
    from app.engines import IncrementalCacheProvider
    from app.assets import Asset
    from app.engine import GraphNode, NodeStatus, Workflow

    store = _mk_store("cachehit")
    _install_test_pkg(store.root)
    prov = IncrementalCacheProvider(
        store, os.path.join(os.path.dirname(store.root), "cache_index.json"))

    wf = Workflow(id="hit-wf", name="x")
    node = GraphNode(id="n1", type_id=TYPE_ID, params={}, status=NodeStatus.PENDING)
    wf.nodes["n1"] = node
    key = prov.cache_key(wf, node)
    asset = store.save_asset(Asset(id="", kind="JSON", meta={}), payload={"ok": True})
    node.asset_ids = {"out": asset.id}
    prov.store_result(key, node)
    assert prov.cache_hit(key, wf, node), "资产在场应命中"
    store.delete(asset.id)
    assert not prov.cache_hit(key, wf, node), "资产缺失不应命中"


def test_cache_invalidate_and_persist():
    """缓存：invalidate 删键；store_result 落盘后新实例 load 可复现。"""
    from app.engines import IncrementalCacheProvider
    from app.assets import Asset
    from app.engine import GraphNode, NodeStatus, Workflow

    store = _mk_store("cacheinv")
    _install_test_pkg(store.root)
    cache_file = os.path.join(os.path.dirname(store.root), "cache_index.json")
    prov = IncrementalCacheProvider(store, cache_file)

    wf = Workflow(id="inv-wf", name="x")
    node = GraphNode(id="n1", type_id=TYPE_ID, params={}, status=NodeStatus.PENDING)
    wf.nodes["n1"] = node
    key = prov.cache_key(wf, node)
    node.asset_ids = {"out": store.save_asset(
        Asset(id="", kind="JSON", meta={}), payload={"ok": 1}).id}
    prov.store_result(key, node)
    assert prov.entry(key) is not None, "store_result 后应可查到 entry"

    prov2 = IncrementalCacheProvider(store, cache_file)
    assert prov2.entry(key) is not None, "落盘后新实例应 load 到同一 entry"

    prov.invalidate(key)
    assert prov.entry(key) is None, "invalidate 后 entry 应被清除"


# ================================================================ 超时
def test_node_timeout_by_trust_level():
    """超时分级：L0 600s / L1 300s / L2 120s；渲染类 L0 提升 1800s；声明可覆盖。"""
    from app.engine import Engine

    store = _mk_store("timeout")
    engine = Engine(store)
    engine.set_trust_level("user/l0node", "L0")
    engine.set_trust_level("user/l1node", "L1")
    engine.set_trust_level("user/l2node", "L2")
    assert engine.node_timeout("user/l0node") == 600.0
    assert engine.node_timeout("user/l1node") == 300.0
    assert engine.node_timeout("user/l2node") == 120.0
    assert engine.node_timeout("core/video_render") == 1800.0
    assert engine.node_timeout("user/render_something") == 1800.0
    assert engine.node_timeout("user/l2node", declared=60.0) == 60.0
    assert engine.node_timeout("user/l2node", declared=5000.0) == 900.0
    assert engine.setup_timeout("user/l2node", 120.0) == 40.0
    assert engine.setup_timeout("user/l2node", 30.0) == 10.0


def test_run_timeout_isolated_to_failed():
    """超时执行：run 超过声明超时 → 节点 FAILED(PLUGIN_TIMEOUT)，引擎不崩。"""
    from app.assets import create_store
    from app.engines import get_engine_ext
    from app.engine import Engine, GraphNode, NodeStatus, Workflow
    from app.nodespec import NodeBase, NodeSpec, PortSpec
    from app.ports import PortType
    from app.registry import register, get_spec

    try:
        get_spec("user/sleepy_t9")
    except KeyError:
        @register
        class SleepyNode(NodeBase):
            @classmethod
            def spec(cls):
                return NodeSpec(type_id="user/sleepy_t9", title="sleepy", category="用户节点",
                                inputs=[], outputs=[PortSpec("out", PortType.JSON)], params=[])
            async def run(self, ctx, inputs, params):
                import asyncio
                await asyncio.sleep(5)
                return {"out": None}

    tmp = os.path.join(tempfile.gettempdir(), "fw_t9_runt_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    store = create_store("local", os.path.join(tmp, "assets"))
    # 显式注入进程内执行器：超时语义验证不依赖 worker 子进程与 L2 强制 worker 规则
    engine = Engine(store, executor=get_engine_ext("executor", "default"))
    engine.set_trust_level("user/sleepy_t9", "L2")
    wf = Workflow(id="timeout-wf", name="x")
    wf.nodes["n1"] = GraphNode(id="n1", type_id="user/sleepy_t9",
                               params={"timeout_seconds": 1.0}, status=NodeStatus.PENDING)

    async def _run():
        return await engine.execute(wf)

    t0 = time.time()
    asyncio.run(_run())
    elapsed = time.time() - t0
    assert elapsed < 4.0, "超时应快速收口（实际 %0.1fs）" % elapsed
    n = wf.nodes["n1"]
    assert n.status == NodeStatus.FAILED, n.status
    assert "超时" in n.error or "TIMEOUT" in n.error.upper(), n.error


# ================================================================ 回归（t5 发现：worker 池跨 Engine 泄漏）
def test_worker_pool_bound_to_engine_not_singleton():
    """回归（captain t9 附加 / R-平台-9）：WorkerExecutor 单例池不得跨 Engine 复用旧 store。

    场景：连续两次 run 使用**不同** AssetStore，同节点类型同参数。
    若池挂在单例 executor 上，第二次 run 复用的 supervisor 保留首次 host（旧 store），
    输出被导入旧 store → 新 store 缓存索引被 trim（execution_cached 不触发）。
    断言：
      a) 每个 Engine 的 _worker_pool 独立（首次 run 后两实例池不同对象）；
      b) 第二次 run 后，store2 的缓存索引条目仍存在且资产落在 store2（不被 trim）。
    """
    from app.engine import Engine, GraphNode, NodeStatus, Workflow

    store1 = _mk_store("pool1")
    store2 = _mk_store("pool2")
    _install_test_pkg(store1.root)
    _install_test_pkg(store2.root)

    old_exec = os.environ.get("FW_PLUGIN_EXEC")
    os.environ["FW_PLUGIN_EXEC"] = "worker"
    try:
        e1 = Engine(store1)
        e2 = Engine(store2)
        assert getattr(e1, "executor", None) is getattr(e2, "executor", None), \
            "两 Engine 应解析到同一 executor 实例（复现单例场景）"

        async def _run(engine, wf):
            await engine.execute(wf)
            return wf

        wf1 = asyncio.run(_run(e1, _make_wf()))
        assert wf1.nodes["n1"].status == NodeStatus.SUCCESS, \
            "Engine1 run 失败: %s" % wf1.nodes["n1"].error

        p1 = getattr(e1, "_worker_pool", None)
        assert p1 is not None, "Engine1 首次 run 后应建立自有 worker 池"

        wf2 = asyncio.run(_run(e2, _make_wf()))
        assert wf2.nodes["n1"].status == NodeStatus.SUCCESS, \
            "Engine2 run 失败: %s" % wf2.nodes["n1"].error
        p2 = getattr(e2, "_worker_pool", None)
        assert p2 is not None, "Engine2 应建立自有 worker 池"
        assert p1 is not p2, "回归失败：worker 池跨 Engine 共享（旧 host/store 泄漏）"

        key = e2.cache.cache_key(wf2, wf2.nodes["n1"])
        entry = e2.cache.entry(key)
        assert entry is not None, \
            "store2 缓存索引应保留第二次 run 的 entry（未被旧 store 污染 trim）"
        aid = (entry.get("assets") or {}).get("out")
        assert aid and store2.get(aid) is not None, \
            "第二次 run 的输出资产应落在 store2（而不是被导入旧 store1）"
    finally:
        if old_exec is None:
            os.environ.pop("FW_PLUGIN_EXEC", None)
        else:
            os.environ["FW_PLUGIN_EXEC"] = old_exec