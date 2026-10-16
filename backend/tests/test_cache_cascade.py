# -*- coding: utf-8 -*-
"""E1 下游缓存级联失效 测试（S1-T12 遗留观察项修复 / R-平台-3 缓存语义）。

场景（S1T12 验收观察项）：编辑上游节点参数后直接重跑，下游已缓存节点必须
自动级联失效并重算——不再沿用上游旧资产 id 命中旧缓存（下游陈旧）。

端口约定：本测试沿用画布同端口名连线约定（如 core/load_video.video →
core/scene_detect.video，缓存键按上游同名输出资产 id 解析），与 S1T12 观察
场景（minimal 模板 tpl1→tpl2）的链接语义一致。

覆盖：
  1. 链式 A→B：相同输入二次 run → 不重执行（run_history 不增）、资产 id 不变
     （缓存命中保持）；
  2. 改上游 A 参数后 run → A 重算 + B 自动级联失效重算（新资产 id，且内容反映
     新上游输出）；
  3. 三级链 A→B→C：改 A → B、C 全部级联重算（传递性）；
  4. run_mode=downstream（编辑 A 后跑下游）：A 与其下游一并重算；
  5. reset(force=true) 显式通道（engine.invalidate_cache + 节点重置）仍可用，
     且重算后同样级联下游；
  6. 范围边界：run_mode=selection 只跑 A → B（范围外）保持旧资产不动，
     留待下一次被纳入运行范围时由 cache key 变化自然失效；
  7. cascade_invalidate 单元语义：PENDING 源 → targets 内下游全部置 PENDING；
     范围外下游不受影响。

运行：python -m pytest tests/test_cache_cascade.py（或整套 pytest）。
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-e1-test-token")

TYPE_ID = "user/cascade_probe"
PORT = "sig"  # 输入/输出同名端口（画布同端口名连线约定）


def _register_probe():
    """注册测试探针节点（幂等）：输出 = {输入载荷, 参数 val}——内容依赖上游，
    资产 id 每次执行必变（save_asset 铸造新 id），可精确断言「重算发生」。"""
    from app.assets import Asset
    from app.registry import get_spec, register
    from app.nodespec import NodeBase, NodeSpec, PortSpec
    from app.ports import PortType

    try:
        get_spec(TYPE_ID)
        return
    except KeyError:
        pass

    @register
    class CascadeProbe(NodeBase):
        @classmethod
        def spec(cls):
            return NodeSpec(type_id=TYPE_ID, title="cascade probe", category="用户节点",
                            inputs=[PortSpec(PORT, PortType.JSON, required=False)],
                            outputs=[PortSpec(PORT, PortType.JSON)],
                            params=[PortSpec("val", PortType.JSON, default="x")])

        async def run(self, ctx, inputs, params):
            store = ctx["store"] if isinstance(ctx, dict) else ctx.store
            inp = None
            a = (inputs or {}).get(PORT)
            if a is not None:
                try:
                    inp = store.read_json(a.id)
                except Exception:  # noqa: BLE001 —— 载荷缺失兜底可观测标记
                    inp = {"asset": a.id}
            payload = {"in": inp, "val": (params or {}).get("val")}
            out = store.save_asset(Asset(id="", kind="JSON", meta={}), payload=payload)
            return {PORT: out}


def _mk_store(tag: str):
    from app.assets import create_store
    tmp = os.path.join(tempfile.gettempdir(), "fw_e1_%s_%d" % (tag, int(time.time() * 1000)))
    os.makedirs(tmp, exist_ok=True)
    return create_store("local", os.path.join(tmp, "assets"))


def _make_engine(store):
    """进程内执行器（显式注入）：级联语义与 worker 隔离无关，测试保持快速稳定。"""
    from app.engine import Engine
    from app.engines import get_engine_ext
    return Engine(store, executor=get_engine_ext("executor", "default"))


def _make_chain(n: int = 2):
    """链式 A→B→...→N：n1 无输入，其余 PORT 端口连前驱 PORT 输出。"""
    from app.engine import GraphNode, NodeStatus, Workflow
    wf = Workflow(id="cascade-wf", name="x")
    for i in range(1, n + 1):
        wf.nodes["n%d" % i] = GraphNode(id="n%d" % i, type_id=TYPE_ID,
                                        params={"val": "v%d" % i},
                                        status=NodeStatus.PENDING)
    for i in range(1, n):
        wf.edges.append({"source": "n%d" % i, "target": "n%d" % (i + 1),
                         "sourceHandle": PORT, "targetHandle": PORT})
        wf.nodes["n%d" % (i + 1)].inputs = {PORT: "n%d" % i}
    return wf


async def _run(engine, wf, mode="all", node_ids=None):
    await engine.execute(wf, run_node_ids=node_ids, run_mode=mode)
    return wf


# ================================================================ 核心：链式级联
def test_same_input_cache_hit_preserved():
    """① 相同输入二次 run：不重执行、资产 id 不变（缓存命中保持）。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("same")
    engine = _make_engine(store)
    wf = _make_chain(2)
    asyncio.run(_run(engine, wf))
    n1, n2 = wf.nodes["n1"], wf.nodes["n2"]
    assert n1.status == NodeStatus.SUCCESS and n2.status == NodeStatus.SUCCESS
    a1, b1 = n1.asset_ids[PORT], n2.asset_ids[PORT]
    assert store.get(a1) is not None and store.get(b1) is not None

    asyncio.run(_run(engine, wf))  # 相同输入
    assert n1.asset_ids[PORT] == a1, "相同输入应保持原资产（不重算）"
    assert n2.asset_ids[PORT] == b1, "相同输入应保持原资产（不重算）"
    assert n1.status in (NodeStatus.SUCCESS, NodeStatus.CACHED)
    assert n2.status in (NodeStatus.SUCCESS, NodeStatus.CACHED)
    assert len(n1.run_history) == 1 and len(n2.run_history) == 1, \
        "相同输入二次 run 不应产生任何执行（run_history 不增）"


def test_upstream_param_change_cascade_recompute():
    """② 改上游参数 → 下游缓存自动级联失效并重算（S1-T12 观察项核心验收）。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("chain")
    engine = _make_engine(store)
    wf = _make_chain(2)
    asyncio.run(_run(engine, wf))
    n1, n2 = wf.nodes["n1"], wf.nodes["n2"]
    a1, b1 = n1.asset_ids[PORT], n2.asset_ids[PORT]

    n1.params = {"val": "v1-changed"}        # 编辑上游参数
    asyncio.run(_run(engine, wf))            # 直接重跑
    assert n1.status == NodeStatus.SUCCESS, n1.status
    assert n2.status == NodeStatus.SUCCESS, n2.status
    a2, b2 = n1.asset_ids[PORT], n2.asset_ids[PORT]
    assert a2 != a1, "上游参数变化应重算（新资产 id）"
    assert b2 != b1, "下游缓存应级联失效并重算（新资产 id）"
    # 内容一致性：下游新输出必须反映新上游输出（不是旧值）
    b2_payload = store.read_json(b2)
    a2_payload = store.read_json(a2)
    assert b2_payload["in"] == a2_payload, \
        "下游新结果应基于新上游输出: %s vs %s" % (b2_payload, a2_payload)
    assert b2_payload["val"] == "v2", b2_payload
    assert len(n2.run_history) == 2, "下游应实际重算（run_history 增加）"


def test_transitive_cascade():
    """③ 三级链 A→B→C：改 A → B、C 全部级联重算（传递性）。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("trans")
    engine = _make_engine(store)
    wf = _make_chain(3)
    asyncio.run(_run(engine, wf))
    before = {i: wf.nodes["n%d" % i].asset_ids[PORT] for i in (1, 2, 3)}

    wf.nodes["n1"].params = {"val": "v1-x"}
    asyncio.run(_run(engine, wf))
    after = {i: wf.nodes["n%d" % i].asset_ids[PORT] for i in (1, 2, 3)}
    assert all(after[i] != before[i] for i in (1, 2, 3)), \
        "三级链改上游后全链应级联重算: %s -> %s" % (before, after)
    assert all(wf.nodes["n%d" % i].status == NodeStatus.SUCCESS for i in (1, 2, 3))


def test_downstream_mode_cascade():
    """④ run_mode=downstream：编辑 A 后跑 A 的下游 → A 与其下游一并重算。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("downstream")
    engine = _make_engine(store)
    wf = _make_chain(2)
    asyncio.run(_run(engine, wf))
    b1 = wf.nodes["n2"].asset_ids[PORT]

    wf.nodes["n1"].params = {"val": "v1-ds"}
    asyncio.run(_run(engine, wf, mode="downstream", node_ids={"n1"}))
    assert wf.nodes["n1"].status == NodeStatus.SUCCESS
    assert wf.nodes["n2"].status == NodeStatus.SUCCESS
    assert wf.nodes["n2"].asset_ids[PORT] != b1, \
        "downstream 模式下游应级联重算（新资产 id）"


def test_reset_force_channel_available():
    """⑤ reset(force=true) 显式通道仍可用：清节点+清命中缓存键 → 重算且级联下游。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("reset")
    engine = _make_engine(store)
    wf = _make_chain(2)
    asyncio.run(_run(engine, wf))
    n1, n2 = wf.nodes["n1"], wf.nodes["n2"]
    a1, b1 = n1.asset_ids[PORT], n2.asset_ids[PORT]

    # 等价于 POST /api/workflows/{wfid}/nodes/n1/reset?force=true：
    # 状态→pending、清空资产/缓存键 + invalidate_cache 清除命中缓存键
    key = engine.cache.cache_key(wf, n1)
    engine.invalidate_cache(key)
    n1.status = NodeStatus.PENDING
    n1.asset_ids = {}
    n1.cache_key = ""
    asyncio.run(_run(engine, wf))

    assert n1.status == NodeStatus.SUCCESS
    assert n2.status == NodeStatus.SUCCESS
    assert n1.asset_ids[PORT] != a1, "force 重算应产出新资产 id"
    assert n2.asset_ids[PORT] != b1, "reset(force=true) 后下游应级联重算"


def test_selection_scope_boundary():
    """⑥ 范围边界：selection 只跑 A → B（范围外）保持旧资产（留待下次自然失效）。"""
    from app.engine import NodeStatus
    _register_probe()
    store = _mk_store("selection")
    engine = _make_engine(store)
    wf = _make_chain(2)
    asyncio.run(_run(engine, wf))
    b1 = wf.nodes["n2"].asset_ids[PORT]

    wf.nodes["n1"].params = {"val": "v1-sel"}
    asyncio.run(_run(engine, wf, mode="selection", node_ids={"n1"}))
    assert wf.nodes["n1"].status == NodeStatus.SUCCESS
    # 范围外节点不被触碰（既不改状态也不重算）；其下一次被纳入运行范围时，
    # 上游新资产 id 会使 cache key 变化而自然失效重算。
    assert wf.nodes["n2"].status == NodeStatus.SUCCESS
    assert wf.nodes["n2"].asset_ids[PORT] == b1, \
        "selection 范围外下游不应被级联重算"


# ================================================================ 单元：cascade_invalidate
def test_cascade_invalidate_unit_semantics():
    """⑦ cascade_invalidate 单元语义：PENDING 源 → targets 内下游全置 PENDING；
    范围外下游不受影响。"""
    from app.assets import create_store
    from app.engine import Engine, GraphNode, NodeStatus, Workflow
    _register_probe()
    tmp = os.path.join(tempfile.gettempdir(), "fw_e1_unit_%d" % int(time.time() * 1000))
    os.makedirs(tmp, exist_ok=True)
    engine = Engine(create_store("local", os.path.join(tmp, "assets")))

    def _wf():
        w = Workflow(id="u", name="x")
        for i in range(1, 4):
            w.nodes["n%d" % i] = GraphNode(id="n%d" % i, type_id=TYPE_ID,
                                           status=NodeStatus.SUCCESS)
        w.edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]
        return w

    # 全范围：n1 为源脏 → n2、n3 均级联失效
    wf = _wf()
    wf.nodes["n1"].status = NodeStatus.PENDING
    inv = engine.cascade_invalidate(wf, {"n1", "n2", "n3"})
    assert inv == {"n2", "n3"}, inv
    assert wf.nodes["n2"].status == NodeStatus.PENDING
    assert wf.nodes["n3"].status == NodeStatus.PENDING

    # 范围边界：n3 不在 targets → 不受影响
    wf2 = _wf()
    wf2.nodes["n1"].status = NodeStatus.PENDING
    inv2 = engine.cascade_invalidate(wf2, {"n1", "n2"})
    assert inv2 == {"n2"}, inv2
    assert wf2.nodes["n3"].status == NodeStatus.SUCCESS

    # 无源脏：幂等无副作用
    wf3 = _wf()
    inv3 = engine.cascade_invalidate(wf3, {"n1", "n2", "n3"})
    assert inv3 == set(), inv3
    assert all(wf3.nodes["n%d" % i].status == NodeStatus.SUCCESS for i in (1, 2, 3))


# ================================================================ 独立脚本入口
def main():
    failures = 0
    tests = [
        test_same_input_cache_hit_preserved,
        test_upstream_param_change_cascade_recompute,
        test_transitive_cascade,
        test_downstream_mode_cascade,
        test_reset_force_channel_available,
        test_selection_scope_boundary,
        test_cascade_invalidate_unit_semantics,
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
