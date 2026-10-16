# -*- coding: utf-8 -*-
"""S1-T2 worker 隔离默认开启 验收测试（P0-2 / R-平台-2）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T2 acceptance + verify）：
  1. 默认值断言：FW_PLUGIN_EXEC 未设置 → WorkerExecutor（outprocess）生效，
     Engine.current_execution_mode() == "worker"；inprocess 仅显式降级
     （F W_PLUGIN_EXEC=inprocess 且无 L2 插件 → InProcessExecutor）；
  2. L2 无签名包强制 worker：显式 inprocess + 存在无签名插件 → 仍装配 WorkerExecutor；
  3. R-平台-2 验收场景：默认配置下安装「请求 fs/network」的恶意测试插件，
     worker 权限门五作用域默认拒绝（节点 FAILED + PLUGIN_PERMISSION_DENIED）；
  4. 默认配置第三方插件真实在 worker 进程执行（良性 echo 节点 SUCCESS）。

运行方式：
  - T9 统一 pytest 前先以独立脚本断言：`python tests/test_t2_isolation.py`（退出码 0=全绿）；
  - pytest 收集兼容：顶层轻量 import，test_* 函数无参。
"""
import asyncio
import atexit
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t2-test-token")

_SAVED_ENV = {}
_TMP_ROOT = os.path.join(tempfile.gettempdir(), "fw_t2_nodes_%d" % int(time.time() * 1000))
os.makedirs(_TMP_ROOT, exist_ok=True)
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))


def _set_env(**kw):
    for k, v in kw.items():
        _SAVED_ENV.setdefault(k, os.environ.get(k))
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _restore_env():
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _SAVED_ENV.clear()


def _mk_pkg(nodes_root, name, type_id, node_code, manifest_permission):
    """在 nodes_root 下建一个无签名测试插件包（L2 社区级），返回包目录。"""
    pkg = os.path.join(nodes_root, name)
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 2, "engine_api": "1.0", "type_id": type_id,
            "title": name, "category": "用户节点", "description": "t2 fixture",
            "version": "1.0.0", "kind": "python", "runtime": "python",
            "entrypoint": "node.py", "class_name": "FixtureNode",
            "inputs": [], "outputs": [{"name": "out", "type": "JSON", "label": "out",
                                       "required": False}],
            "params": [{"name": "url", "type": "STRING", "required": False, "default": ""}],
            "permission": manifest_permission,
        }, f, ensure_ascii=False, indent=2)
    with open(os.path.join(pkg, "node.py"), "w", encoding="utf-8") as f:
        f.write(node_code)
    return pkg


_ECHO_CODE = (
    "from app.assets import Asset\n"
    "class FixtureNode:\n"
    "    async def run(self, ctx, inputs, params):\n"
    "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), "
    "payload={'ok': True, 'msg': params.get('msg', '')})\n"
    "        return {'out': out}\n"
)
_MAL_NET_CODE = (
    "class FixtureNode:\n"
    "    async def run(self, ctx, inputs, params):\n"
    "        ctx.http('GET', params.get('url') or 'https://example.com/exfil')\n"
    "        return {}\n"
)
_MAL_FS_CODE = (
    "class FixtureNode:\n"
    "    async def run(self, ctx, inputs, params):\n"
    "        ctx.fs_write('../escape/evil.txt', 'ZGF0YQ==')\n"
    "        return {}\n"
)


def _fresh_nodes_dir(sub):
    d = os.path.join(_TMP_ROOT, sub)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def _install(nodes_root):
    import app.declarative as decl
    decl.set_user_nodes_dir(nodes_root)
    n = decl.scan()
    assert n >= 1, "插件扫描失败: %d" % n
    return decl


def _engine_for(nodes_root, store_root):
    from app.assets import AssetStore
    from app.engine import Engine
    from app.engines import InProcessExecutor, WorkerExecutor
    store = AssetStore(store_root)
    eng = Engine(store)
    return eng, store, InProcessExecutor, WorkerExecutor


# ================================================================ 默认值断言
def test_default_worker_mode():
    """① 默认（未设 FW_PLUGIN_EXEC）→ worker（outprocess）+ 权限门模式。"""
    from app.engine import Engine
    from app.engines import WorkerExecutor
    from app.assets import AssetStore
    _set_env(FW_PLUGIN_EXEC=None)
    try:
        e = Engine(AssetStore(os.path.join(_TMP_ROOT, "store_def")))
        assert isinstance(e.executor, WorkerExecutor), type(e.executor)
        assert Engine.current_execution_mode() == "worker"
        assert e.execution_mode() == "worker"
    finally:
        _restore_env()


def test_explicit_inprocess_no_l2():
    """② 显式 FW_PLUGIN_EXEC=inprocess 且无 L2 插件 → InProcessExecutor（显式降级）。"""
    from app.engine import Engine
    from app.engines import InProcessExecutor
    from app.assets import AssetStore
    _set_env(FW_PLUGIN_EXEC="inprocess")
    try:
        e = Engine(AssetStore(os.path.join(_TMP_ROOT, "store_inp")))
        assert isinstance(e.executor, InProcessExecutor), type(e.executor)
        assert Engine.current_execution_mode() == "inprocess"
    finally:
        _restore_env()


def test_l2_unsigned_forced_worker_even_inprocess():
    """③ L2 无签名包强制 worker（节点级）：显式 inprocess + 无签名插件 →
    装配逐节点路由执行器（TrustAwareExecutor），L2 节点执行强制走 worker 进程
    （R-平台-2 不变式在节点级保持，出厂内置 L0/L1 节点仍可同进程跑离线链）。"""
    from app.engine import Workflow, GraphNode, NodeStatus
    from app.engines import TrustAwareExecutor
    nodes = _fresh_nodes_dir("nodes_l2force")
    _mk_pkg(nodes, "t2_l2_echo", "user/t2_l2_echo", _ECHO_CODE, {})
    _install(nodes)
    _set_env(FW_PLUGIN_EXEC="inprocess")
    try:
        eng, store, _InProc, _Worker = _engine_for(nodes, os.path.join(_TMP_ROOT, "store_l2force"))
        assert isinstance(eng.executor, TrustAwareExecutor), type(eng.executor)
        # 信任级识别：无签名 → L2
        assert eng.trust_level("user/t2_l2_echo") == "L2"
        # R-平台-2 不变式：显式 inprocess 下降级权限——L2 节点仍经 worker 进程执行
        wf = Workflow(id="w_l2f", nodes={
            "n1": GraphNode(id="n1", type_id="user/t2_l2_echo",
                            params={"msg": "l2-via-worker"}, inputs={}),
        }, edges=[])
        asyncio.run(eng.execute(wf))
        n1 = wf.nodes["n1"]
        assert n1.status == NodeStatus.SUCCESS, "L2 节点应经 worker 成功: %s err=%s" % (n1.status, n1.error)
        out_aid = n1.asset_ids.get("out")
        assert out_aid, n1.asset_ids
        meta = store.get(out_aid).meta
        assert meta.get("exec") == "worker", "L2 节点应经 worker 执行（meta.exec=worker）: %s" % meta
    finally:
        _restore_env()


# ================================================================ R-平台-2 验收场景
def test_r_platform2_malicious_network_rejected():
    """④ 默认配置：请求网络（未声明 permission.network）的测试插件 → worker 权限门拒绝。"""
    from app.engine import Workflow, GraphNode, NodeStatus
    nodes = _fresh_nodes_dir("nodes_malnet")
    _mk_pkg(nodes, "t2_mal_net", "user/t2_mal_net", _MAL_NET_CODE, {})
    _install(nodes)
    _set_env(FW_PLUGIN_EXEC=None)  # 默认（worker）
    try:
        eng, store, _I, _W = _engine_for(nodes, os.path.join(_TMP_ROOT, "store_malnet"))
        assert eng.execution_mode() == "worker"
        wf = Workflow(id="w_mal", nodes={
            "n1": GraphNode(id="n1", type_id="user/t2_mal_net",
                            params={"url": "https://example.com/exfil"}, inputs={}),
        }, edges=[])
        asyncio.run(eng.execute(wf))
        n1 = wf.nodes["n1"]
        assert n1.status == NodeStatus.FAILED, "恶意节点应 FAILED: %s" % n1.status
        assert "PERMISSION" in n1.error or "越权" in n1.error or "network" in n1.error.lower(), \
            "错误应含权限拒绝信息: %s" % n1.error
    finally:
        _restore_env()


def test_r_platform2_malicious_fs_rejected():
    """⑤ 默认配置：请求 fs 越权（未声明 fs 权限）的测试插件 → worker 权限门拒绝。"""
    from app.engine import Workflow, GraphNode, NodeStatus
    nodes = _fresh_nodes_dir("nodes_malfs")
    _mk_pkg(nodes, "t2_mal_fs", "user/t2_mal_fs", _MAL_FS_CODE, {})
    _install(nodes)
    _set_env(FW_PLUGIN_EXEC=None)
    try:
        eng, store, _I, _W = _engine_for(nodes, os.path.join(_TMP_ROOT, "store_malfs"))
        wf = Workflow(id="w_malfs", nodes={
            "n1": GraphNode(id="n1", type_id="user/t2_mal_fs", params={}, inputs={}),
        }, edges=[])
        asyncio.run(eng.execute(wf))
        n1 = wf.nodes["n1"]
        assert n1.status == NodeStatus.FAILED, n1.status
        assert "PERMISSION" in n1.error or "越权" in n1.error or "fs" in n1.error.lower(), n1.error
    finally:
        _restore_env()


def test_default_worker_benign_plugin_runs():
    """⑥ 默认配置第三方良性插件在 worker 进程执行并 SUCCESS（R-平台-2 直采）。"""
    from app.engine import Workflow, GraphNode, NodeStatus
    nodes = _fresh_nodes_dir("nodes_benign")
    _mk_pkg(nodes, "t2_echo_ok", "user/t2_echo_ok", _ECHO_CODE, {})
    _install(nodes)
    _set_env(FW_PLUGIN_EXEC=None)
    try:
        eng, store, _I, _W = _engine_for(nodes, os.path.join(_TMP_ROOT, "store_benign"))
        wf = Workflow(id="w_ok", nodes={
            "n1": GraphNode(id="n1", type_id="user/t2_echo_ok",
                            params={"msg": "via-default-worker"}, inputs={}),
        }, edges=[])
        asyncio.run(eng.execute(wf))
        n1 = wf.nodes["n1"]
        assert n1.status == NodeStatus.SUCCESS, "良性节点应 SUCCESS: %s err=%s" % (n1.status, n1.error)
        out_id = n1.asset_ids.get("out")
        assert out_id, n1.asset_ids
        payload = store.read_json(out_id)
        assert (payload or {}).get("msg") == "via-default-worker", payload
    finally:
        _restore_env()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        name = fn.__name__
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL %s | %s: %s" % (name, type(e).__name__, e))
    print("=" * 56)
    print("TOTAL FAIL=%d/%d" % (failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())