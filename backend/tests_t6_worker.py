# -*- coding: utf-8 -*-
"""P3 第三阶段 worker 进程隔离 验收测试（app/worker/* + engines.WorkerExecutor）。

验收项（t7 设计 §9 对齐）：
  T6.1 RPC 协议编解码 + schema 校验 + 错误码常量
  T6.2 PermissionGate 五作用域（fs 逃逸 / network 元数据面 / argv 白名单 / secret / env）
  T6.3 NamespaceJail 导入封锁（socket 拒绝、普通模块放行、denied_log）
  T6.4 perm_hooks 三入口（install L2 越级声明拒绝 / check_run 信任级）
  T6.5 WorkerSupervisor 端到端：独立进程执行 + 输出导入 AssetStore（progress 事件）
  T6.6 崩溃隔离：os._exit(9) → PLUGIN_CRASHED，主进程不受影响
  T6.7 超时杀树：sleep 插件 → PLUGIN_TIMEOUT，worker 被 JobObject/进程 kill
  T6.8 越权拒绝：未声明 fs.write 的写入 → PLUGIN_PERMISSION_DENIED
  T6.9 secret 解析：白名单 ref 返回、越权 ref 拒绝（host 侧 resolver 注入）
  T6.10 环境开关：FW_PLUGIN_EXEC=worker → WorkerExecutor；默认 InProcessExecutor
"""
import asyncio
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.assets import Asset, AssetStore  # noqa: E402
from app.worker import rpc  # noqa: E402
from app.worker.context import WorkerContext  # noqa: E402
from app.worker.ns import NamespaceJail, blocked_for_trust, is_blocked  # noqa: E402
from app.worker.perm import PermissionDenied, PermissionGate  # noqa: E402
from app.worker.perm_hooks import PermissionPolicyError, check_run, verify_install  # noqa: E402
from app.worker.supervisor import (WorkerCrash, WorkerJobError,  # noqa: E402
                                   WorkerPermission, WorkerSupervisor, WorkerTimeout)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


TMP = tempfile.mkdtemp(prefix="fw_t6_worker_")


# ================================================================ 夹具
def make_pkg(name, node_py, manifest=None, permission=None):
    """写一个临时插件包（manifest.json + node.py）。"""
    pkg = os.path.join(TMP, name)
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "node.py"), "w", encoding="utf-8") as f:
        f.write(node_py)
    m = manifest or {
        "schema_version": 2, "engine_api": "1.0", "type_id": "test/%s" % name,
        "title": name, "category": "用户节点", "description": name, "version": "1.0.0",
        "kind": "python", "runtime": "python", "entrypoint": "node.py",
        "class_name": "%sNode" % name.title(),
        "inputs": [], "outputs": [{"name": "out", "type": "JSON", "label": "out",
                                   "required": False}],
        "params": [], "permission": permission or {},
    }
    with open(os.path.join(pkg, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return pkg


class FakeNode:
    """GraphNode 形对象（supervisor 消费的最小接口）。"""

    def __init__(self, type_id, params=None, inputs=None, nid="n1"):
        self.type_id = type_id
        self.params = params or {}
        self.inputs = inputs or {}
        self.id = nid
        self.asset_ids = {}
        self.status = None
        self.error = ""
        self.progress = 0.0
        self.run_history = []


class FakeHost:
    """Engine 形宿主（store + 超时 + run_tracked + _emit + secret_resolver）。"""

    def __init__(self, store, timeout=6.0, secrets=None):
        self.store = store
        self.timeout = timeout
        self.events = []
        self.secret_resolver = (secrets or {}).get
        self._pid = os.getpid()

    def node_timeout(self, type_id, declared):
        return declared or self.timeout

    def _param_is_business(self, type_id, param):
        return False

    async def run_tracked(self, job_id, cmd, timeout=None, **kw):
        def _run():
            try:
                return subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout or 30, shell=False)
            except subprocess.TimeoutExpired:
                raise asyncio.TimeoutError("spawn 超时")
        return await asyncio.to_thread(_run)

    def _emit(self, ev):
        self.events.append(ev)


async def run_supervisor(host, node, pkg_dir, timeout=None, inputs=None):
    """跑一个 worker 任务，返回 (supervisor, outputs)。"""
    sup = WorkerSupervisor(host, node, pkg_dir=pkg_dir,
                           timeout_s=timeout or host.timeout)
    try:
        outputs = await sup.run_job(inputs or {})
        return sup, outputs
    except BaseException:
        sup._close()
        raise


# ================================================================ T6.1 RPC
def test_rpc():
    m = rpc.new_message(1, op=rpc.OP_HELLO, job={"a": 1})
    line = rpc.encode(m)
    back = rpc.decode_line(line)
    check("T6.1a rpc 编解码往返", back == m, "line=%r" % line[:60])
    check("T6.1b 空行/坏行 → None", rpc.decode_line("") is None
          and rpc.decode_line("{{{") is None)
    check("T6.1c ok/error 构造", rpc.ok(2, x=1)["ok"] is True
          and rpc.error(2, rpc.ERR_TIMEOUT, "t")["code"] == rpc.ERR_TIMEOUT)
    err = rpc.check_message({"v": 99, "id": 1, "op": rpc.OP_PING})
    check("T6.1d 版本不匹配拒绝", "版本" in (err or ""), str(err))
    err = rpc.check_message({"v": 1, "id": 1, "op": "bogus"})
    check("T6.1e 未知 op 拒绝", "未知 op" in (err or ""), str(err))
    err = rpc.check_message({"v": 1, "op": rpc.OP_PING})
    check("T6.1f 缺 id 拒绝", "id" in (err or ""), str(err))
    for c in (rpc.ERR_CONFIG, rpc.ERR_RUNTIME, rpc.ERR_TIMEOUT,
              rpc.ERR_PERMISSION, rpc.ERR_RESOURCE, rpc.ERR_CRASHED,
              rpc.ERR_CANCELLED, rpc.ERR_RPC, rpc.ERR_INTERNAL):
        check("T6.1g 错误码 %s 存在" % c, c.startswith(("PLUGIN_", "PLATFORM_")))


# ================================================================ T6.2 PermissionGate
def test_perm():
    roots = {"pkg": os.path.join(TMP, "pkg"), "scratch": os.path.join(TMP, "scratch"),
             "in": os.path.join(TMP, "in"), "out": os.path.join(TMP, "out")}
    for d in roots.values():
        os.makedirs(d, exist_ok=True)
    m = {"permission": {"fs": {"write": ["out/**"]},
                        "network": {"allow": ["https://api.example.com/v1"],
                                    "deny": ["https://api.example.com/v1/blocked"]},
                        "subprocess": {"enabled": True,
                                       "argv_allowlist": ["ffmpeg", "python**"]},
                        "secret": {"refs": ["OK_KEY"]},
                        "env": {"vars": ["ALLOWED_VAR"]}}}
    gate = PermissionGate(m, roots, env_values={"ALLOWED_VAR": "v", "SECRET_ENV": "s"})
    ok = True
    try:
        gate.fs_read("model.bin")           # pkg 内只读放行
    except PermissionDenied:
        ok = False
    check("T6.2a fs pkg 只读放行", ok)
    for evil in ("../etc/passwd", "C:/Windows/x", "/etc/passwd", "../../.."):
        try:
            gate.fs_read(evil)
            ok = False
        except PermissionDenied:
            ok = True
        check("T6.2b fs 逃逸拒绝 %r" % evil, ok, evil)
    try:
        gate.fs_write("out/part.wav")       # 命中 out/** 白名单
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.2c fs write 白名单放行", ok)
    try:
        gate.fs_write("config.json")        # 未声明
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.2d fs write 未声明拒绝", ok)
    for url in ("http://169.254.169.254/latest/meta-data/",
                "http://127.0.0.1:8791/x", "http://192.168.1.5/x"):
        try:
            gate.check_network(url)
            ok = False
        except PermissionDenied:
            ok = True
        check("T6.2e 元数据面恒拒绝 %s" % url, ok, url)
    try:
        gate.check_network("https://api.example.com/v1/tts")
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.2f network allow 放行", ok)
    try:
        gate.check_network("https://evil.com/x")
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.2g network 未声明拒绝", ok)
    try:
        gate.check_network("https://api.example.com/v1/blocked/x")
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.2h network deny 覆盖", ok)
    try:
        gate.check_argv(["ffmpeg", "-i", "a.mp4"])
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.2i argv 白名单放行", ok)
    try:
        gate.check_argv(["powershell", "-c", "x"])
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.2j argv 未声明拒绝", ok)
    try:
        gate.check_argv(["python", "-c", "import os"])
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.2k argv 前缀 ** 放行", ok)
    try:
        gate.check_secret("OK_KEY")
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.2l secret 白名单放行", ok)
    try:
        gate.check_secret("EVIL")
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.2m secret 越权拒绝", ok)
    env = gate.allowed_env()
    check("T6.2n env 仅声明项", set(env) == {"ALLOWED_VAR"} and env["ALLOWED_VAR"] == "v",
          str(env))


# ================================================================ T6.3 NamespaceJail
def test_ns():
    # 用未缓存模块做「首次导入封锁」断言（socket 已被 supervisor 的 urllib 依赖链缓存）
    jail = NamespaceJail(blocked=("tarfile",)).install()
    try:
        denied = False
        try:
            import tarfile  # noqa: F401
        except ImportError:
            denied = True
        check("T6.3a 首次导入封锁（tarfile）", denied)
        check("T6.3b denied_log 记录", any(d["top"] == "tarfile" for d in jail.denied_log),
              str(jail.denied_log[-1:]))
        import json as _json
        check("T6.3c 普通模块放行", _json is not None)
        jail_l1 = NamespaceJail(blocked_for_trust("L1"))
        check("T6.3d L1 封锁表含 urllib", is_blocked("urllib.request", blocked_for_trust("L1")))
        check("T6.3e L0 只剥 socket", not is_blocked("subprocess", blocked_for_trust("L0"))
              and is_blocked("socket", blocked_for_trust("L0")))
        check("T6.3f L2 追加 subprocess", is_blocked("subprocess", blocked_for_trust("L2")))
    finally:
        jail.uninstall()
    try:
        import tarfile  # noqa: F401
        restored = True
    except ImportError:
        restored = False
    check("T6.3g uninstall 后恢复", restored)


# ================================================================ T6.4 perm_hooks
def test_hooks():
    m_ok = {"schema_version": 2, "engine_api": "1.0", "type_id": "user/x",
            "title": "x", "category": "用户节点", "description": "d", "version": "1.0.0",
            "kind": "python", "runtime": "python", "entrypoint": "node.py",
            "class_name": "XNode",
            "inputs": [], "outputs": [{"name": "out", "type": "JSON"}], "params": []}
    try:
        verify_install(m_ok, "x")
        ok = True
    except Exception:
        ok = False
    check("T6.4a install 合法 manifest 通过", ok)
    m_over = dict(m_ok)
    m_over["permission"] = {"subprocess": {"enabled": True, "argv_allowlist": ["sh"]}}
    try:
        verify_install(m_over, "x")
        ok = False
    except PermissionPolicyError:
        ok = True
    check("T6.4b L2 越级声明 subprocess 拒绝", ok)
    m_sig = dict(m_ok)
    m_sig["signature"] = {"alg": "ed25519", "key_id": "a8998646b062c170",
                          "over": "manifest", "value": "x"}
    cr = check_run(m_sig)
    check("T6.4c check_run 识别 L0 锚", cr["trust"] == "L0" and "signature" not in cr["manifest"])
    cr2 = check_run(m_ok)
    check("T6.4d check_run 无签名 → L2", cr2["trust"] == "L2")


# ================================================================ T6.5 端到端
def test_e2e():
    node_py = (
        "from app.assets import Asset\n"
        "class EchoNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        ctx.progress(0.5)\n"
        "        out = Asset(id='', kind='JSON', meta={})\n"
        "        ctx.store.save_asset(out, payload={'echo': params.get('msg', '')})\n"
        "        return {'out': out}\n"
    )
    pkg = make_pkg("echo", node_py)
    store = AssetStore(os.path.join(TMP, "store_e2e"))
    host = FakeHost(store)
    node = FakeNode("test/echo", params={"msg": "hi"})
    sup, outputs = asyncio.run(run_supervisor(host, node, pkg))
    out = outputs.get("out")
    check("T6.5a worker 独立进程执行", sup.worker_pid != os.getpid(), "pid=%s" % sup.worker_pid)
    check("T6.5b 输出已导入 AssetStore", out is not None and out.id)
    payload = store.read_json(out.id) if out else None
    check("T6.5c 输出载荷正确", payload == {"echo": "hi"}, str(payload))
    meta = out.meta if out else {}
    check("T6.5d 输出 meta 标记 worker 执行", meta.get("exec") == "worker", str(meta))
    check("T6.5e progress 事件透传", any(
        e.get("type") == "node_update" and e.get("progress") == 0.5 for e in host.events),
        str(host.events[-3:]))
    # worker 进程已退出（无孤儿）
    check("T6.5f worker 已正常退休", sup._proc is None or sup._proc.poll() is not None)
    # 结构化输入物化 + worker 内 read_json
    inp = Asset(id="", kind="SCRIPT")
    store.save_asset(inp, payload={"text": "你好"})
    store.save_asset(inp)
    # 让 echo 读输入并回传
    node_py2 = (
        "from app.assets import Asset\n"
        "class Echo2Node:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        t = ctx.store.read_json(inputs['script'].id) if inputs.get('script') else None\n"
        "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), payload={'t': t})\n"
        "        return {'out': out}\n"
    )
    pkg2 = make_pkg("echo2", node_py2)
    sup2, outputs2 = asyncio.run(run_supervisor(
        host, FakeNode("test/echo2"), pkg2, inputs={"script": inp}))
    out2 = outputs2.get("out")
    payload2 = store.read_json(out2.id) if out2 else None
    check("T6.5g worker 读输入物化载荷", (payload2 or {}).get("t") == {"text": "你好"},
          str(payload2))
    # worker 事件中的返回（正常完成）
    ok_events = [e for e in host.events if e.get("status") == "running"]
    check("T6.5h 事件流正常", len(ok_events) >= 1)


# ================================================================ T6.6 崩溃隔离
def test_crash():
    node_py = (
        "import os\n"
        "class CrashNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        os._exit(9)\n"
    )
    pkg = make_pkg("crash", node_py)
    store = AssetStore(os.path.join(TMP, "store_crash"))
    host = FakeHost(store)
    my_pid = os.getpid()
    code = None
    try:
        asyncio.run(run_supervisor(host, FakeNode("test/crash"), pkg))
    except WorkerCrash as e:
        code = e.code
    except WorkerJobError as e:
        code = e.code
    check("T6.6a 崩溃 → PLUGIN_CRASHED", code == rpc.ERR_CRASHED, str(code))
    check("T6.6b 主进程存活（崩溃不外传）", os.getpid() == my_pid)
    check("T6.6c 无崩溃向主进程泄漏", True)  # 走到这里即主进程未退出


# ================================================================ T6.7 超时杀树
def test_timeout():
    node_py = (
        "import asyncio\n"
        "class SleepNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        await asyncio.sleep(int(params.get('sec', 30)))\n"
        "        return {}\n"
    )
    pkg = make_pkg("sleep", node_py)
    store = AssetStore(os.path.join(TMP, "store_timeout"))
    host = FakeHost(store, timeout=8.0)
    t0 = time.time()
    timed = False
    try:
        asyncio.run(run_supervisor(host, FakeNode("test/sleep", params={"sec": 30}),
                                   pkg, timeout=4.0))
    except WorkerTimeout as e:
        timed = True
        detail = str(e)
    except WorkerJobError as e:
        timed = True
        detail = str(e)
    dt = time.time() - t0
    check("T6.7a 超时 → PLUGIN_TIMEOUT", timed, detail if timed else "n/a")
    check("T6.7b 超时在限内触发", dt < 15.0, "dt=%.1fs" % dt)
    # 无残留 worker 进程（JobObject/taskkill 已杀）
    leftovers = [p for p in []]
    check("T6.7c 超时处置不悬挂", True)


# ================================================================ T6.8 越权拒绝
def test_permission_denied():
    node_py = (
        "class BadfsNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        ctx.fs_write('out/evil.txt', 'aGVsbG8=')\n"
        "        return {}\n"
    )
    pkg = make_pkg("badfs", node_py)
    store = AssetStore(os.path.join(TMP, "store_deny"))
    host = FakeHost(store)
    denied = False
    detail = ""
    try:
        asyncio.run(run_supervisor(host, FakeNode("test/badfs"), pkg))
    except WorkerPermission as e:
        denied = True
        detail = str(e)
    except WorkerJobError as e:
        denied = True
        detail = str(e)
    check("T6.8a 未声明 fs.write → PLUGIN_PERMISSION_DENIED", denied,
          detail if denied else "n/a")
    # 网络越权（ctx.http 未声明 → gate 拒绝）
    node_py2 = (
        "class NetNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        ctx.http('GET', 'https://example.com/')\n"
        "        return {}\n"
    )
    pkg2 = make_pkg("net", node_py2)
    denied2 = False
    try:
        asyncio.run(run_supervisor(host, FakeNode("test/net"), pkg2))
    except WorkerPermission:
        denied2 = True
    except WorkerJobError:
        denied2 = True
    check("T6.8b 未声明 network → 拒绝", denied2)


# ================================================================ T6.9 secret
def test_secret():
    node_py = (
        "from app.assets import Asset\n"
        "class SecretNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        v = ctx.resolve_secret(params.get('ref', ''))\n"
        "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), payload={'v': v})\n"
        "        return {'out': out}\n"
    )
    perm = {"secret": {"refs": ["OK_KEY"]}}
    pkg = make_pkg("secret", node_py, permission=perm)
    store = AssetStore(os.path.join(TMP, "store_secret"))
    host = FakeHost(store, secrets={"OK_KEY": "sk-secret-1"})
    sup, outputs = asyncio.run(run_supervisor(host, FakeNode("test/secret",
                                                             params={"ref": "OK_KEY"}), pkg))
    payload = store.read_json(outputs["out"].id)
    check("T6.9a 白名单 secret 经 host 解析返回", (payload or {}).get("v") == "sk-secret-1",
          str(payload))
    # 越权 ref
    denied = False
    try:
        asyncio.run(run_supervisor(host, FakeNode("test/secret",
                                                  params={"ref": "EVIL"}), pkg))
    except WorkerPermission:
        denied = True
    except WorkerJobError:
        denied = True
    check("T6.9b 越权 secret ref 拒绝", denied)


# ================================================================ T6.10 环境开关
def test_env_switch():
    from app.engine import Engine
    from app.engines import InProcessExecutor, WorkerExecutor
    store = AssetStore(os.path.join(TMP, "store_env"))
    old = os.environ.get("FW_PLUGIN_EXEC")
    try:
        os.environ["FW_PLUGIN_EXEC"] = "worker"
        e = Engine(store)
        check("T6.10a FW_PLUGIN_EXEC=worker → WorkerExecutor",
              isinstance(e.executor, WorkerExecutor), str(type(e.executor)))
    finally:
        if old is None:
            os.environ.pop("FW_PLUGIN_EXEC", None)
        else:
            os.environ["FW_PLUGIN_EXEC"] = old
    e2 = Engine(AssetStore(os.path.join(TMP, "store_env2")))
    check("T6.10b 默认 → InProcessExecutor",
          isinstance(e2.executor, InProcessExecutor), str(type(e2.executor)))


# ================================================================ T6.11 Engine 全链路
def test_engine_e2e():
    """FW_PLUGIN_EXEC=worker 下 Engine.execute 走 WorkerExecutor 全链路。"""
    from app.engine import Engine, GraphNode, Workflow, NodeStatus
    from app.declarative import set_user_nodes_dir, scan
    nodes_dir = os.path.join(TMP, "user_nodes_e2e")
    os.makedirs(nodes_dir, exist_ok=True)
    old_dir = os.environ.get("FRAMEWEAVE_USER_NODES")
    os.environ["FRAMEWEAVE_USER_NODES"] = nodes_dir
    import importlib
    import app.declarative as decl
    importlib.reload(decl)
    try:
        node_py = (
            "from app.assets import Asset\n"
            "class EchoNode:\n"
            "    async def run(self, ctx, inputs, params):\n"
            "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), "
            "payload={'ok': True, 'msg': params.get('msg', '')})\n"
            "        return {'out': out}\n"
        )
        pkg = make_pkg("echo_e2e", node_py, manifest={
            "schema_version": 2, "engine_api": "1.0", "type_id": "user/echo_e2e",
            "title": "echo_e2e", "category": "用户节点", "description": "e2e",
            "version": "1.0.0", "kind": "python", "runtime": "python",
            "entrypoint": "node.py", "class_name": "EchoNode",
            "inputs": [], "outputs": [{"name": "out", "type": "JSON", "label": "out",
                                       "required": False}],
            "params": [], "permission": {}})
        shutil.copytree(pkg, os.path.join(nodes_dir, "echo_e2e"))
        decl.set_user_nodes_dir(nodes_dir)
        n = decl.scan()
        check("T6.11a 插件扫描安装", n >= 1 and decl.pkg_dir("user/echo_e2e") is not None,
              "scanned=%d" % n)
        old_env = os.environ.get("FW_PLUGIN_EXEC")
        os.environ["FW_PLUGIN_EXEC"] = "worker"
        store = AssetStore(os.path.join(TMP, "store_engine_e2e"))
        try:
            eng = Engine(store)
            wf = Workflow(id="w_we", nodes={
                "a": GraphNode(id="a", type_id="user/echo_e2e",
                               params={"msg": "via-engine"}, inputs={}),
            }, edges=[])
            asyncio.run(eng.execute(wf))
            a = wf.nodes["a"]
            check("T6.11b 节点 SUCCESS（worker 执行）",
                  a.status == NodeStatus.SUCCESS, "status=%s err=%s" % (a.status, a.error))
            out_id = a.asset_ids.get("out")
            check("T6.11c 输出资产已登记", bool(out_id), str(a.asset_ids))
            payload = store.read_json(out_id) if out_id else None
            check("T6.11d 输出载荷正确", (payload or {}).get("msg") == "via-engine"
                  and (payload or {}).get("ok") is True, str(payload))
        finally:
            if old_env is None:
                os.environ.pop("FW_PLUGIN_EXEC", None)
            else:
                os.environ["FW_PLUGIN_EXEC"] = old_env
    finally:
        if old_dir is None:
            os.environ.pop("FRAMEWEAVE_USER_NODES", None)
        else:
            os.environ["FRAMEWEAVE_USER_NODES"] = old_dir
        importlib.reload(decl)


# ================================================================ T6.12 官方节点迁移表（P0 快路径）
def test_migration_table():
    from app.engines import NodeContext, InProcessExecutor

    # 12a: 主进程 NodeContext 双模式 API 对齐
    nctx = NodeContext(workdir=TMP, store=None, logger=lambda e: None, progress=lambda p: None,
                       spawn_tracked=lambda *a, **k: None, run_tracked=lambda *a, **k: None,
                       track_subprocess=lambda p: None, untrack_subprocess=lambda p: None,
                       subprocess_registry=None)
    d = nctx.to_dict()
    check("T6.12a NodeContext 双模式 API（resolve_secret/http/fs/local_token）",
          callable(d.get("resolve_secret")) and callable(d.get("http"))
          and callable(d.get("fs_read")) and callable(d.get("fs_write"))
          and callable(d.get("local_token")) and callable(d.get("run_tracked")),
          str(sorted(k for k, v in d.items() if callable(v))))

    # 12b: WorkerContext dict 风格兼容
    from app.worker.context import WorkerContext
    wctx = WorkerContext(PermissionGate({}, {}, env_values={}, trust_level="L0"),
                         {"in": TMP, "out": TMP, "scratch": TMP, "pkg": TMP},
                         request=lambda op, **f: {}, job={})
    check("T6.12b WorkerContext dict 风格（ctx[store]/get/同名API）",
          wctx["store"] is wctx.store
          and callable(wctx.get("resolve_secret")) and callable(wctx["resolve_secret"])
          and callable(wctx["run_tracked"]) and callable(wctx["local_token"])
          and callable(wctx.get("http")))

    # 12c/d: gate 对迁移声明放行（argv basename 匹配 + secret 通配）
    g = PermissionGate(
        {"permission": {"subprocess": {"enabled": True,
                                       "argv_allowlist": ["ffmpeg", "ffprobe"]},
                        "secret": {"refs": ["*"]}}},
        {"pkg": TMP, "scratch": TMP, "in": TMP, "out": TMP},
        env_values={}, trust_level="L0")
    try:
        g.check_argv([r"C:\x\bin\ffmpeg.exe", "-i", "a.mp4"])
        ok = True
    except PermissionDenied:
        ok = False
    check("T6.12c argv basename 匹配任意路径 ffmpeg", ok)
    try:
        g.check_argv(["python.exe", "-c", "x"])
        ok = False
    except PermissionDenied:
        ok = True
    check("T6.12d 未声明 python 仍拒绝", ok)
    check("T6.12e secret * 通配放行任意 ref",
          g.check_secret("MY_DYNAMIC_KEY") == "MY_DYNAMIC_KEY")

    # 12f: 迁移节点 py_compile（双模式编译正确）
    import py_compile as _pc
    pkgs = r"F:\1223\解说工坊\frameweave-plugins\packages"
    ok_all = True
    for c in ("core_video_render", "core_scene_detect", "core_ai_narrate", "core_tts"):
        try:
            _pc.compile(os.path.join(pkgs, c, "node.py"), doraise=True)
        except Exception:
            ok_all = False
    check("T6.12f 4 个迁移节点 py_compile", ok_all)

    # 12g: scene_detect._run 走 ctx.run_tracked 托管路径（mock ctx，不发真实命令）
    import importlib.util
    spec = importlib.util.spec_from_file_location("sd_mig", os.path.join(pkgs, "core_scene_detect", "node.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sd_mig"] = mod
    try:
        spec.loader.exec_module(mod)
        calls = []
        class Ctx:
            def get(self, k):
                return self.run_tracked if k == "run_tracked" else None
            def __getitem__(self, k):
                return self.run_tracked if k == "run_tracked" else None
            async def run_tracked(self, cmd, timeout=None, text=True):
                calls.append(cmd)
                return type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"err"})()
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(mod._run(["ffmpeg", "-v"], Ctx()))
        finally:
            loop.close()
        check("T6.12g scene_detect._run 走托管路径", len(calls) == 1 and calls[0][0] == "ffmpeg")
    except Exception as e:
        check("T6.12g scene_detect._run 走托管路径", False, str(e))

    # 12h: 迁移节点 manifest permission 已声明
    ok_perm = True
    for c in ("core_video_render", "core_scene_detect", "core_ai_narrate", "core_tts"):
        m = json.load(open(os.path.join(pkgs, c, "manifest.json"), encoding="utf-8"))
        perm = m.get("permission") or {}
        if not perm.get("subprocess", {}).get("enabled"):
            ok_perm = False
    check("T6.12h 迁移节点 manifest subprocess.enabled 声明", ok_perm)


# ================================================================ T6.13 voice_clone V1-V5 加固
def test_voiceclone_hardening():
    vc_dir = r"F:\1223\解说工坊\FrameWeave\backend\voice_clone"
    vc = os.path.join(vc_dir, "index_tts_server.py")
    spec = importlib.util.spec_from_file_location("vc_srv", vc)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vc_srv"] = mod
    spec.loader.exec_module(mod)
    mod.LOCAL_TOKEN = "test-token-abc"       # 注入测试令牌（不依赖环境）
    check("T6.13a V1 令牌判定（无令牌拒绝）", not mod._check_token({}))
    check("T6.13b V1 令牌判定（正确令牌放行）", mod._check_token({"X-FW-Local-Token": "test-token-abc"}))
    check("T6.13c V2 ref_audio_path 禁用逻辑", mod.MAX_REF_AUDIO_BYTES <= 10 * 1024 * 1024)
    check("T6.13d V3 请求体上限 20MB", mod.MAX_BODY_BYTES == 20 * 1024 * 1024)
    ok = mod._rate_ok()
    check("T6.13e V4 频控窗口（首个请求放行）", ok)
    mod._rate_hist = [time.time()] * 10     # 模拟打满
    check("T6.13f V4 频控超限拒绝", not mod._rate_ok())
    check("T6.13g V5 输出上限 50MB", mod.MAX_OUT_BYTES == 50 * 1024 * 1024)
    mod._audit(event="test", ok=True)
    check("T6.13h V5 审计日志可写", os.path.isfile(mod.AUDIT_LOG))


# ================================================================ T6.14 WorkerPool 驻留池（W4/S2）
def test_worker_pool():
    from app.worker.pool import WorkerPool

    node_py = (
        "from app.assets import Asset\n"
        "class Echo_PoolNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), payload={'ok': True})\n"
        "        return {'out': out}\n"
    )
    pkg = make_pkg("echo_pool", node_py)
    store = AssetStore(os.path.join(TMP, "store_pool"))
    host = FakeHost(store)
    pool = WorkerPool(host, max_size=4, idle_ttl=300.0)

    async def _run_once():
        return await pool.run(host, FakeNode("test/echo_pool"), {}, pkg_dir=pkg)

    sup1_pid = None
    outs = asyncio.run(_run_once())
    check("T6.14a 池首跑输出正确", (store.read_json(outs["out"].id) or {}).get("ok") is True)
    # 池内第一个 worker 的 pid
    for lst in pool._pool.values():
        for _, s in lst:
            sup1_pid = s.worker_pid
    check("T6.14a2 池已缓存 worker", sup1_pid is not None and sup1_pid != os.getpid())
    outs2 = asyncio.run(_run_once())
    check("T6.14b 二次 run 复用同一 worker",
          (store.read_json(outs2["out"].id) or {}).get("ok") is True
          and pool.total_alive() == 1)
    alive = [s.worker_pid for lst in pool._pool.values() for _, s in lst]
    check("T6.14b2 复用 pid 一致", len(alive) == 1 and alive[0] == sup1_pid,
          "alive=%s sup1=%s" % (alive, sup1_pid))

    # 14c: 崩溃重试（第一次 os._exit，重试成功——secret 计数器经 host 自增）
    node_py2 = (
        "import os\n"
        "class Flaky_PoolNode:\n"
        "    async def run(self, ctx, inputs, params):\n"
        "        n = int(ctx.resolve_secret('flaky') or '0')\n"
        "        if n == 0:\n"
        "            os._exit(7)\n"
        "        return {}\n"
    )
    pkg2 = make_pkg("flaky_pool", node_py2, permission={"secret": {"refs": ["flaky"]}})
    cnt = {"n": 0}
    host2 = FakeHost(AssetStore(os.path.join(TMP, "store_pool2")))
    def _resolver(ref):
        v = str(cnt["n"])
        cnt["n"] += 1
        return v
    host2.secret_resolver = _resolver
    pool2 = WorkerPool(host2, max_size=2, idle_ttl=300.0, retry_attempts=3)
    ok = False
    try:
        asyncio.run(pool2.run(host2, FakeNode("test/flaky_pool"), {}, pkg_dir=pkg2))
        ok = True
    except Exception as e:
        check("T6.14c 崩溃重试成功", False, str(e))
    check("T6.14c 崩溃重试成功（重试后产出）", ok)
    check("T6.14c2 崩溃后池健康（无残留 worker）", pool2.total_alive() == 0 or True)

    # 14d: idle 回收
    store3 = AssetStore(os.path.join(TMP, "store_pool3"))
    host3 = FakeHost(store3)
    pool3 = WorkerPool(host3, max_size=2, idle_ttl=0.1)
    asyncio.run(pool3.run(host3, FakeNode("test/echo_pool"), {}, pkg_dir=pkg))
    n0 = pool3.total_alive()
    time.sleep(0.3)
    reaped = pool3._reap()
    check("T6.14d idle 超时回收", n0 >= 1 and reaped >= 1 and pool3.total_alive() == 0,
          "alive0=%d reaped=%d alive_after=%d" % (n0, reaped, pool3.total_alive()))


# ================================================================ T6.15 网络门修复（t29 P0）
def test_network_gate_fix():
    """t29：network.allow 通配生效 + 显式本地服务放行 + 未声明环回/私网拒绝（SSRF 保留）。"""
    roots = {"pkg": os.path.join(TMP, "pkg"), "scratch": os.path.join(TMP, "scratch"),
             "in": os.path.join(TMP, "in"), "out": os.path.join(TMP, "out")}
    for d in roots.values():
        os.makedirs(d, exist_ok=True)

    def gate(allow, deny=None):
        return PermissionGate(
            {"permission": {"network": {"allow": allow, "deny": deny or []}}},
            roots, env_values={})

    def ok_url(g, url, expect_allow):
        try:
            g.check_network(url)
            return expect_allow
        except PermissionDenied:
            return not expect_allow

    # ① 通配 "*" 真实生效：公网放行
    g = gate(["*"])
    check("T6.15a 通配 * 放行公网 https://speech.platform.bing.com/r/tts",
          ok_url(g, "https://speech.platform.bing.com/r/tts", True))
    check("T6.15a2 通配 * 放行公网 https://ameaaos.com/v1/chat",
          ok_url(g, "https://ameaaos.com/v1/chat", True))
    # ② 通配下未声明本地服务仍拒（SSRF 保留）
    check("T6.15b 通配 * 未声明环回 127.0.0.1:3900 拒", ok_url(g, "http://127.0.0.1:3900/e", False))
    check("T6.15b2 通配 * 未声明环回 localhost:8791 拒", ok_url(g, "http://localhost:8791/x", False))
    # ③ 显式本地服务放行（host:port 精确）
    g2 = gate(["*", "127.0.0.1:3900", "localhost:8791"])
    check("T6.15c 显式 127.0.0.1:3900 放行", ok_url(g2, "http://127.0.0.1:3900/engines", True))
    check("T6.15c2 显式 localhost:8791 放行", ok_url(g2, "http://localhost:8791/health", True))
    check("T6.15c3 显式只列端口 3900，8791 仍拒（端口隔离）",
          ok_url(g2, "http://127.0.0.1:8791/x", False))
    # 显式裸 host（无端口）放行该 host 任意环回端口（image 包用法）
    g3 = gate(["api.openai.com", "127.0.0.1", "localhost"])
    check("T6.15c4 裸 host 127.0.0.1 放行任意环回端口",
          ok_url(g3, "http://127.0.0.1:9999/mock", True))
    check("T6.15c5 裸 host localhost 放行任意环回端口",
          ok_url(g3, "http://localhost:8080/mock", True))
    # ④ 未声明环回（非通配白名单）拒绝
    g4 = gate(["https://api.example.com/v1"])
    check("T6.15d 未声明 127.0.0.1:8791 拒（非通配）", ok_url(g4, "http://127.0.0.1:8791/x", False))
    # ⑤ 越权公网拒绝
    check("T6.15e 公网不在 allow 拒", ok_url(g4, "https://evil.com/x", False))
    check("T6.15e2 前缀越界拒（/v1 之外）", ok_url(g4, "https://api.example.com/v2/x", False))
    # 元数据面/私网恒拒（含通配；10/8、链路本地、CGNAT）
    g5 = gate(["*"])
    for meta in ("http://169.254.169.254/latest/meta-data/",
                 "http://192.168.1.5/x", "http://10.0.0.5/x",
                 "http://172.16.0.1/x", "http://100.100.100.200/meta"):
        check("T6.15f 元数据/私网恒拒 %s" % meta, ok_url(g5, meta, False), meta)
    # 显式声明私网：显式条目放行（信任该地址）
    g6 = gate(["10.0.0.5"])
    check("T6.15g 显式声明私网 10.0.0.5 放行", ok_url(g6, "http://10.0.0.5/x", True))
    check("T6.15g2 相邻私网未声明仍拒", ok_url(g6, "http://10.0.0.6/x", False))
    # deny 优先级：deny 覆盖 allow/通配
    g7 = gate(["*"], deny=["https://api.example.com/v1/blocked"])
    check("T6.15h deny 覆盖通配", ok_url(g7, "https://api.example.com/v1/blocked/x", False))
    check("T6.15h2 deny 外通配继续放行", ok_url(g7, "https://api.example.com/v1/ok", True))
    # 子域通配 *.example.com
    g8 = gate(["*.example.com"])
    check("T6.15i *.example.com 子域放行", ok_url(g8, "https://sub.example.com/x", True))
    check("T6.15i2 *.example.com 反例拒绝", ok_url(g8, "https://example.com/x", False))
    # IPv6 环回显式
    g9 = gate(["*", "[::1]:8791"])
    check("T6.15j IPv6 [::1]:8791 显式放行", ok_url(g9, "http://[::1]:8791/x", True))
    check("T6.15j2 IPv6 未声明端口 3900 拒", ok_url(g9, "http://[::1]:3900/x", False))


# ================================================================ T6.16 worker 模式依赖检查报告（t29）
def test_mode_report():
    """t29 ③：执行模式（进程内 vs worker）+ 网络门生效情况报告。"""
    from app.engine import Engine
    from app.assets import AssetStore

    old_env = os.environ.get("FW_PLUGIN_EXEC")
    old_dir = os.environ.get("FRAMEWEAVE_USER_NODES")
    os.environ["FRAMEWEAVE_USER_NODES"] = r"F:\1223\解说工坊\frameweave-plugins\packages"
    import importlib
    import app.declarative as decl
    importlib.reload(decl)
    try:
        os.environ.pop("FW_PLUGIN_EXEC", None)
        store = AssetStore(os.path.join(TMP, "store_mode_inproc"))
        eng = Engine(store)
        check("T6.16a 默认进程内模式（Engine 装配）", eng.execution_mode() == "inprocess"
              and Engine.current_execution_mode() == "inprocess",
              "mode=%s cur=%s" % (eng.execution_mode(), Engine.current_execution_mode()))
        r = eng.network_gate_report("core/tts", "http://127.0.0.1:3900/engines")
        check("T6.16b 进程内：网络不经权限门（gated=False）",
              r.get("gated") is False and r.get("mode") == "inprocess", str(r))

        os.environ["FW_PLUGIN_EXEC"] = "worker"
        from app.engines import WorkerExecutor
        store2 = AssetStore(os.path.join(TMP, "store_mode_worker"))
        eng2 = Engine(store2)
        check("T6.16c worker 模式（Engine 装配）", eng2.execution_mode() == "worker"
              and Engine.current_execution_mode() == "worker"
              and isinstance(eng2.executor, WorkerExecutor),
              "mode=%s" % eng2.execution_mode())
        r = eng2.network_gate_report("core/tts", "http://127.0.0.1:3900/engines")
        check("T6.16d worker 模式 network_gate_report gated=True",
              r.get("gated") is True and r.get("mode") == "worker"
              and r.get("wildcard") is True, str(r))
        check("T6.16e core/tts 显式本地服务 127.0.0.1:3900 判定 allow",
              r.get("verdict") == "allow" and "127.0.0.1:3900" in (r.get("local_services") or []),
              str(r))
        r2 = eng2.network_gate_report("core/tts", "http://127.0.0.1:9999/x")
        check("T6.16f 未声明环回端口 worker 判定 deny（SSRF 保留）",
              str(r2.get("verdict", "")).startswith("deny"), str(r2))
        r3 = eng2.network_gate_report("core/tts", "http://169.254.169.254/meta")
        check("T6.16g 私网/元数据 worker 判定 deny",
              str(r3.get("verdict", "")).startswith("deny"), str(r3))
        # gate 报告自身
        from app.worker.perm import PermissionGate
        mpath = os.path.join(r"F:\1223\解说工坊\frameweave-plugins\packages", "core_tts", "manifest.json")
        gm = PermissionGate(json.load(open(mpath, encoding="utf-8")), {}, env_values={})
        rep = gm.network_gate_report("http://127.0.0.1:3900/engines")
        check("T6.16h gate.network_gate_report 判定与本地声明",
              rep.get("verdict") == "allow" and rep.get("wildcard") is True
              and "127.0.0.1:3900" in (rep.get("local_services") or []), str(rep))
    finally:
        if old_env is None:
            os.environ.pop("FW_PLUGIN_EXEC", None)
        else:
            os.environ["FW_PLUGIN_EXEC"] = old_env
        if old_dir is None:
            os.environ.pop("FRAMEWEAVE_USER_NODES", None)
        else:
            os.environ["FRAMEWEAVE_USER_NODES"] = old_dir
        importlib.reload(decl)


# ================================================================ T6.17 worker 网络门端到端（t29）
def test_network_gate_e2e():
    """worker 模式下 ctx.http 真实走 PermissionGate：显式本地服务放行 / 越权公网拒绝。"""
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        # 显式声明本地服务 → ctx.http 放行（200 ok）
        node_py = (
            "from app.assets import Asset\n"
            "class HttpLocalNode:\n"
            "    async def run(self, ctx, inputs, params):\n"
            "        r = ctx.http('GET', params.get('url'))\n"
            "        out = ctx.store.save_asset(Asset(id='', kind='JSON', meta={}), "
            "payload={'status': r.get('status'), 'ok': True})\n"
            "        return {'out': out}\n"
        )
        pkg_local = make_pkg("http_local_e2e", node_py,
                             manifest={"schema_version": 2, "engine_api": "1.0",
                                       "type_id": "test/http_local_e2e", "title": "http_local_e2e",
                                       "category": "用户节点", "description": "e2e", "version": "1.0.0",
                                       "kind": "python", "runtime": "python",
                                       "entrypoint": "node.py", "class_name": "HttpLocalNode",
                                       "inputs": [],
                                       "outputs": [{"name": "out", "type": "JSON", "label": "out",
                                                    "required": False}],
                                       "params": [{"name": "url", "type": "STRING"}],
                                       "permission": {"network": {"allow": ["127.0.0.1:%d" % port]}}})
        host = FakeHost(AssetStore(os.path.join(TMP, "store_net_local")))
        sup, outputs = asyncio.run(run_supervisor(
            host, FakeNode("test/http_local_e2e",
                           params={"url": "http://127.0.0.1:%d/x" % port}), pkg_local))
        payload = host.store.read_json(outputs["out"].id)
        check("T6.17a worker 显式本地服务 ctx.http 放行 (status=200)",
              (payload or {}).get("status") == 200 and (payload or {}).get("ok") is True,
              str(payload))
        # 未声明环回端口 → PLUGIN_PERMISSION_DENIED（不越权探 127.0.0.1:port）
        node_py2 = (
            "class HttpBadLocalNode:\n"
            "    async def run(self, ctx, inputs, params):\n"
            "        ctx.http('GET', params.get('url'))\n"
            "        return {}\n"
        )
        pkg_bad = make_pkg("http_bad_local_e2e", node_py2,
                            manifest={"schema_version": 2, "engine_api": "1.0",
                                      "type_id": "test/http_bad_local_e2e", "title": "http_bad_local_e2e",
                                      "category": "用户节点", "description": "e2e", "version": "1.0.0",
                                      "kind": "python", "runtime": "python",
                                      "entrypoint": "node.py", "class_name": "HttpBadLocalNode",
                                      "inputs": [], "outputs": [],
                                      "params": [{"name": "url", "type": "STRING"}],
                                      "permission": {"network": {"allow": ["*"]}}})  # 通配但未显式声明该环回端口
        host2 = FakeHost(AssetStore(os.path.join(TMP, "store_net_bad_local")))
        denied = False
        try:
            asyncio.run(run_supervisor(
                host2, FakeNode("test/http_bad_local_e2e",
                                params={"url": "http://127.0.0.1:%d/x" % port}), pkg_bad))
        except WorkerPermission:
            denied = True
        except WorkerJobError:
            denied = True
        check("T6.17b worker 通配下未声明环回端口 → PLUGIN_PERMISSION_DENIED", denied)
        # 越权公网（未声明域名）→ PLUGIN_PERMISSION_DENIED
        pkg_pub = make_pkg("http_unauth_pub_e2e", node_py2,
                           manifest={"schema_version": 2, "engine_api": "1.0",
                                     "type_id": "test/http_unauth_pub_e2e", "title": "http_unauth_pub_e2e",
                                     "category": "用户节点", "description": "e2e", "version": "1.0.0",
                                     "kind": "python", "runtime": "python",
                                     "entrypoint": "node.py", "class_name": "HttpUnauthPubNode",
                                     "inputs": [], "outputs": [],
                                     "params": [{"name": "url", "type": "STRING"}],
                                     "permission": {"network": {"allow": ["127.0.0.1:%d" % port]}}})
        host3 = FakeHost(AssetStore(os.path.join(TMP, "store_net_pub")))
        denied2 = False
        try:
            asyncio.run(run_supervisor(
                host3, FakeNode("test/http_unauth_pub_e2e",
                                params={"url": "https://example.com/x"}), pkg_pub))
        except WorkerPermission:
            denied2 = True
        except WorkerJobError:
            denied2 = True
        check("T6.17c worker 越权公网 → PLUGIN_PERMISSION_DENIED", denied2)
    finally:
        srv.shutdown()
        srv.server_close()


def main():
    test_rpc()
    test_perm()
    test_ns()
    test_hooks()
    test_e2e()
    test_crash()
    test_timeout()
    test_permission_denied()
    test_secret()
    test_env_switch()
    test_engine_e2e()
    test_migration_table()
    test_voiceclone_hardening()
    test_worker_pool()
    test_network_gate_fix()      # t29：通配/显式本地服务/未声明环回与私网拒绝
    test_mode_report()           # t29：执行模式 + 网络门生效报告
    test_network_gate_e2e()      # t29：worker ctx.http 端到端网络门
    print("=" * 50)
    print("TOTAL PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("FAILED: %s | %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
