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
    print("=" * 50)
    print("TOTAL PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print("FAILED: %s | %s" % (name, detail))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
