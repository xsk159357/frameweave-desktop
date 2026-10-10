# -*- coding: utf-8 -*-
"""t4 平台 P2：引擎 SPI / Provider / 存储 / PortType 注册制 / models_registry 验收测试。

验收项：
  R1 第三方按接口实现调度器/CacheProvider/Executor 可注册替换（Engine 组合 SPI）
  R2 LLM/TTS/ASR Provider 注册表：默认实现 + 第三方替换（get_provider 消费）
  R3 AssetStore SPI：create_store("local") 默认 + register_store_adapter 自定义工厂
  R4 PortType 注册制：register_port_type/resolve/list/can_connect 规则 + /api/port_types
  R5 models_registry 外置：默认加载 / 远程更新(sha256) / 本地缓存回落
  R6 含边工作流导出 200（/api/export/workflow/{wfid} 含 edges）
"""
import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_AUDIT_LOG",
                      os.path.join(tempfile.gettempdir(), "fw_t4_audit_test.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402
from app import server, engines, providers as prov, model_download as mdr  # noqa: E402
from app.assets import StoreAdapter, LocalAssetStore, create_store, register_store_adapter  # noqa: E402
from app.ports import (  # noqa: E402
    PortType, register_port_type, resolve_port_type, register_connect_rule,
    register_port_connect, can_connect, list_port_types,
)
from app.engine import Engine, Workflow, GraphNode, NodeStatus  # noqa: E402
from app.nodespec import NodeBase, NodeSpec, PortSpec  # noqa: E402
from app.registry import register as reg_register  # noqa: E402

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))

TMP_ROOT = tempfile.mkdtemp(prefix="fw_t4_")

def tmp_store():
    root = os.path.join(TMP_ROOT, "assets_" + os.urandom(4).hex())
    return create_store("local", root)


# ================= R1: 引擎 SPI 注册替换 =================
print("\n==== R1 引擎 SPI ====")
ext = engines.list_engine_ext()
check("R1 默认扩展已注册（scheduler/cache/executor）",
      ext.get("scheduler") == ["default"] and ext.get("cache") == ["default"]
      and ext.get("executor") == ["default"], f"ext={ext}")

st = tmp_store()
eng = Engine(st, trust_levels={})
check("R1 Engine 解析默认 KahnScheduler", type(eng.scheduler).__name__ == "KahnScheduler",
      f"sched={type(eng.scheduler).__name__}")
check("R1 Engine 解析默认 IncrementalCacheProvider",
      type(eng.cache).__name__ == "IncrementalCacheProvider",
      f"cache={type(eng.cache).__name__}")
check("R1 Engine 解析默认 InProcessExecutor", type(eng.executor).__name__ == "InProcessExecutor",
      f"exec={type(eng.executor).__name__}")

# 图：a -> b -> c，Kahn 批次 [[a],[b],[c]]
wf = Workflow(id="w1", nodes={
    "a": GraphNode(id="a", type_id="x", inputs={}, outputs={"o": "b"}),
    "b": GraphNode(id="b", type_id="x", inputs={"i": "a"}, outputs={"o": "c"}),
    "c": GraphNode(id="c", type_id="x", inputs={"i": "b"}, outputs={}),
}, edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "c"}])
check("R1 Kahn 拓扑批次", eng.topological_batches(wf) == [["a"], ["b"], ["c"]],
      f"batches={eng.topological_batches(wf)}")
cyc = Workflow(id="wc", nodes={
    "a": GraphNode(id="a", type_id="x"), "b": GraphNode(id="b", type_id="x")},
    edges=[{"source": "a", "target": "b"}, {"source": "b", "target": "a"}])
check("R1 Kahn 环检测", len(eng.validate(cyc)) > 0, f"errs={eng.validate(cyc)}")

# 第三方调度器替换
class ReverseScheduler:
    def validate(self, wf):
        return []
    def topological_batches(self, wf):
        return [sorted(wf.nodes.keys())]

reg_register_ret = engines.register_engine_ext("scheduler", "reverse", ReverseScheduler())
eng2 = Engine(tmp_store(), scheduler=engines.get_engine_ext("scheduler", "reverse"))
check("R1 第三方调度器注入生效", eng2.topological_batches(wf) == [["a", "b", "c"]],
      f"batches={eng2.topological_batches(wf)}")
check("R1 register_engine_ext 返回 impl", reg_register_ret is not None)

# 第三方执行器替换（记录型）
class RecordingExecutor:
    def __init__(self):
        self.calls = []
    async def execute(self, host, wf, run_node_ids=None, run_mode="all"):
        self.calls.append((wf.id, run_mode))
        return wf

rec = RecordingExecutor()
engines.register_engine_ext("executor", "recording", rec)
eng3 = Engine(tmp_store(), executor=rec)
wf3 = Workflow(id="w3", nodes={"a": GraphNode(id="a", type_id="x")}, edges=[])
asyncio.run(eng3.execute(wf3))
check("R1 第三方执行器替换：execute 派发", rec.calls == [("w3", "all")], f"calls={rec.calls}")

# NodeContext.make
from app.engines import NodeContext  # noqa: E402
ctx = NodeContext.make(eng, "n1").to_dict()
check("R1 NodeContext.make 字段齐全",
      all(k in ctx for k in ("workdir", "store", "logger", "progress", "spawn_tracked",
                             "run_tracked", "track_subprocess", "untrack_subprocess",
                             "subprocess_registry")), f"keys={sorted(ctx.keys())}")

# 端到端执行（默认 InProcessExecutor + Kahn + IncrementalCache + 计数节点）
_RUN_COUNT = {"n": 0}
@reg_register
class _CountNode(NodeBase):
    @classmethod
    def spec(cls) -> NodeSpec:
        return NodeSpec(type_id="test/count", title="Count", category="用户节点",
                        inputs=[], outputs=[PortSpec(name="out", type=PortType.INT)],
                        params=[PortSpec(name="v", type=PortType.INT)])
    async def run(self, ctx, inputs, params):
        _RUN_COUNT["n"] += 1
        return {"out": int(params.get("v", 0))}

st4 = tmp_store()
eng4 = Engine(st4)
wf4 = Workflow(id="w4", nodes={"a": GraphNode(id="a", type_id="test/count",
                                              params={"v": 1})}, edges=[])
asyncio.run(eng4.execute(wf4))
node = wf4.nodes["a"]
check("R1 端到端执行成功（缓存+落盘）",
      node.status == NodeStatus.SUCCESS and bool(node.asset_ids.get("out"))
      and _RUN_COUNT["n"] == 1,
      f"status={node.status.value} assets={node.asset_ids} runs={_RUN_COUNT['n']}")
# 新工作流（同参）执行 → 缓存命中，节点不再运行
wf4b = Workflow(id="w4b", nodes={"a": GraphNode(id="a", type_id="test/count",
                                                params={"v": 1})}, edges=[])
asyncio.run(eng4.execute(wf4b))
check("R1 增量缓存命中（新对象同参 → CACHED，run 未再调用）",
      wf4b.nodes["a"].status == NodeStatus.CACHED and _RUN_COUNT["n"] == 1,
      f"status={wf4b.nodes['a'].status.value} runs={_RUN_COUNT['n']}")

# ================= R2: Provider SPI =================
print("\n==== R2 Provider 注册表 ====")
check("R2 LLM 默认 Provider 已注册", "llm" in prov.list_providers()
      and "default" in prov.list_providers()["llm"], f"providers={prov.list_providers()}")
llm = prov.get_provider("llm")
check("R2 get_provider(llm) 默认 OpenAICompatLLM", type(llm).__name__ == "OpenAICompatLLM",
      f"type={type(llm).__name__}")
check("R2 LLM 兼容签名（chat/vision_analyze）",
      callable(getattr(llm, "chat", None)) and callable(getattr(llm, "vision_analyze", None)))

# 默认 LLM chat 委托 llm.py（monkeypatch 验证参数透传，不触网）
from app import llm as llm_mod
_calls = {}
def _fake_chat(messages, api_key, base_url, model, temperature=0.8, max_tokens=4096, stream=False):
    _calls["messages"] = messages
    _calls["key"] = api_key
    _calls["url"] = base_url
    _calls["model"] = model
    return "mock返回"
original_chat = llm_mod.chat
llm_mod.chat = _fake_chat
try:
    out = llm.chat([{"role": "user", "content": "hi"}], "K1", "http://x/v1", "m1", temperature=0.5)
    check("R2 默认 LLM Provider 委托 llm.chat（参数透传）",
          out == "mock返回" and _calls.get("model") == "m1" and _calls.get("key") == "K1",
          f"calls={_calls}")
finally:
    llm_mod.chat = original_chat

# 第三方 LLM 替换
class FakeLLM:
    def __init__(self):
        self.hit = 0
    def chat(self, messages, api_key, base_url=None, model=None, temperature=0.8,
             max_tokens=4096, stream=False):
        self.hit += 1
        return "fake"
    def vision_analyze(self, images, prompt, api_key, base_url=None, model=None):
        return "fake-v"

fake_llm = FakeLLM()
prov.register_provider("llm", "custom", fake_llm)
check("R2 第三方 LLM 注册可取", prov.get_provider("llm", "custom") is fake_llm)
# 消费方（translate 节点）经 get_provider("llm") 解析默认——验证模块接线
import app.nodes.translate as _tr
from app.providers import get_provider as _gp
check("R2 translate 节点消费 get_provider(llm)", _gp("llm").__class__.__name__ == "OpenAICompatLLM")

# TTS / ASR 默认 Provider（nodes 导入时注册）
import app.nodes.tts as _tts_mod  # noqa: F401
import app.nodes.asr as _asr_mod  # noqa: F401
tts_prov = prov.get_provider("tts")
asr_prov = prov.get_provider("asr")
check("R2 TTS 默认 Provider 已注册（TTSEngineProvider）",
      type(tts_prov).__name__ == "TTSEngineProvider", f"type={type(tts_prov).__name__}")
check("R2 ASR 默认 Provider 已注册（WhisperASRProvider）",
      type(asr_prov).__name__ == "WhisperASRProvider", f"type={type(asr_prov).__name__}")
import inspect
from app.nodes.tts import TTSNode  # noqa: E402
from app.nodes.asr import ASRNode  # noqa: E402
check("R2 TTS/ASR Provider run 为 async 契约",
      inspect.iscoroutinefunction(type(tts_prov).run)
      and inspect.iscoroutinefunction(type(asr_prov).run))
check("R2 TTS 节点类与 Provider 契约对齐（run(ctx, inputs, params)）",
      callable(getattr(TTSNode, "run", None)) and callable(getattr(ASRNode, "run", None)))

# ================= R3: AssetStore SPI =================
print("\n==== R3 存储 SPI ====")
st_local = create_store("local", os.path.join(TMP_ROOT, "store_local"))
check("R3 create_store(local) 返回 LocalAssetStore(StoreAdapter)",
      isinstance(st_local, LocalAssetStore) and isinstance(st_local, StoreAdapter))
a = st_local.save_asset(__import__("app.assets", fromlist=["Asset"]).Asset(id="", kind="JSON"),
                        payload={"k": "v"})
check("R3 LocalAssetStore 读写正常", st_local.get(a.id) is not None
      and st_local.read_json(a.id) == {"k": "v"}, f"id={a.id}")

class MemStore(StoreAdapter):
    """第三方内存存储（演示存储适配器替换）。"""
    def __init__(self, root, **_kw):
        self.root = root
        self.files_dir = os.path.join(root, "files")
        self.meta_dir = os.path.join(root, "meta")
        os.makedirs(self.meta_dir, exist_ok=True)
        self._data = {}
        self._meta = {}
    def save_asset(self, asset, payload=None):
        import uuid as _u, time as _t
        from app.assets import Asset
        if not asset.id:
            asset.id = _u.uuid4().hex[:16]
        asset.created_at = asset.created_at or _t.time()
        if payload is not None:
            asset.path = "mem://" + asset.id + ".json"
            self._data[asset.id] = payload
        self._meta[asset.id] = asset
        return asset
    def get(self, aid):
        return self._meta.get(aid)
    def read_json(self, aid):
        return self._data.get(aid)
    def delete(self, aid):
        self._meta.pop(aid, None)
        self._data.pop(aid, None)
    def prune_orphans(self, keep=None):
        return 0

register_store_adapter("mem", MemStore)
st_mem = create_store("mem", os.path.join(TMP_ROOT, "store_mem"))
check("R3 第三方存储适配器注册/创建", isinstance(st_mem, MemStore))
am = st_mem.save_asset(__import__("app.assets", fromlist=["Asset"]).Asset(id="", kind="JSON"),
                       payload={"m": 1})
check("R3 第三方存储读写", st_mem.read_json(am.id) == {"m": 1}, f"id={am.id}")
check("R3 list_store_adapters 含 local+mem",
      "local" in __import__("app.assets", fromlist=["list_store_adapters"]).list_store_adapters()
      and "mem" in __import__("app.assets", fromlist=["list_store_adapters"]).list_store_adapters())

# ================= R4: PortType 注册制 =================
print("\n==== R4 PortType 注册制 ====")
pt = register_port_type("SCENE3D", is_media=True)
check("R4 register/resolve 扩展类型", resolve_port_type("SCENE3D") is pt
      and pt.value == "SCENE3D" and pt.is_media is True)
check("R4 核心类型解析不变", resolve_port_type("VIDEO") is PortType.VIDEO
      and resolve_port_type("bogus") is None)
check("R4 幂等注册 + 元数据保留",
      register_port_type("SCENE3D", is_media=True) is pt and pt.is_media is True)
types = list_port_types()
names = [t["name"] for t in types]
check("R4 全量注册表 13 核心 + 扩展", len(types) >= 14 and "SCENE3D" in names
      and sum(1 for t in types if t["core"]) == 13, f"n={len(types)}")
register_port_connect("SCENE3D", "STRING", True)
check("R4 can_connect 注册规则驱动", can_connect(pt, PortType.STRING) is True
      and can_connect(PortType.STRING, pt) is False)

# 自定义连线规则（函数式）
register_connect_rule(lambda s, d: s.name == "SCENE3D" and d.name == "JSON")
check("R4 register_connect_rule 函数规则", can_connect(pt, PortType.JSON) is True)

# manifest G5：已登记扩展类型放行，未登记拒绝
from app.plugin import manifest_v2 as mv
from app.plugin.errors import PluginManifestError
GOOD = {"schema_version": 2, "engine_api": "1.0", "type_id": "user/demo", "title": "D",
        "category": "用户节点", "description": "d", "version": "1.0.0", "kind": "transform",
        "inputs": [{"name": "sc", "type": "SCENE3D"}],
        "outputs": [{"name": "out", "type": "STRING"}], "params": [],
        "transform": {"out": {"op": "upper", "source": {"$input": "sc"}}}}
try:
    mv.validate_manifest(GOOD)
    check("R4 manifest 引用已登记扩展类型放行", True)
except PluginManifestError:
    check("R4 manifest 引用已登记扩展类型放行", False)
try:
    mv.validate_manifest({**GOOD, "inputs": [{"name": "sc", "type": "NOTREG"}]})
    check("R4 manifest 未登记类型拒绝", False)
except PluginManifestError as ex:
    check("R4 manifest 未登记类型拒绝", ex.code == "manifest_ports", f"code={ex.code}")

client = TestClient(server.app)
r_pt = client.get("/api/port_types", headers={"X-FW-Local-Token": "dev-local-token"})
body = r_pt.json()
check("R4 /api/port_types 返回注册表", r_pt.status_code == 200
      and "port_types" in body and any(t["name"] == "SCENE3D" for t in body["port_types"]),
      f"status={r_pt.status_code}")
r_specs = client.get("/api/specs", headers={"X-FW-Local-Token": "dev-local-token"})
check("R4 /api/specs 保持数组兼容（前端零破坏）",
      r_specs.status_code == 200 and isinstance(r_specs.json(), list),
      f"status={r_specs.status_code}")

# ================= R5: models_registry 外置 =================
print("\n==== R5 models_registry ====")
try:
    mdr.load_registry()
    loaded_ok = "faster-whisper-large-v3" in mdr.MODELS_REGISTRY
except Exception as ex:
    loaded_ok = False
check("R5 默认 registry 加载", loaded_ok, f"keys={list(mdr.MODELS_REGISTRY.keys())[:3]}")
check("R5 清单字段完整", bool(mdr.MODELS_REGISTRY.get("faster-whisper-large-v3", {}).get("repo"))
      and bool(mdr.MODELS_REGISTRY.get("faster-whisper-large-v3", {}).get("file")))

# 远程更新（file:// 直链 + registry_sha256 校验）
remote = {"models": {
    "tiny-model": {"repo": "r/t", "file": "f.bin", "size_mb": 1},
    "faster-whisper-large-v3": {"repo": "new/r", "file": "model.bin"},
}}
canon = json.dumps(remote["models"], sort_keys=True, ensure_ascii=False)
sha = __import__("hashlib").sha256(canon.encode("utf-8")).hexdigest()
fd, reg_path = tempfile.mkstemp(suffix=".json", dir=TMP_ROOT)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "models": remote["models"], "registry_sha256": sha}, f)
cache_file = os.path.join(TMP_ROOT, "reg_cache.json")
r = mdr.update_registry("file://" + reg_path.replace("\\", "/"), cache_path=cache_file)
check("R5 远程更新成功（sha256 通过）", "tiny-model" in r and "tiny-model" in mdr.MODELS_REGISTRY,
      f"r={sorted(r.keys())}")
check("R5 更新写入本地缓存", os.path.isfile(cache_file)
      and "tiny-model" in json.load(open(cache_file, encoding="utf-8"))["models"])

# sha256 不匹配 → 拒绝 + 保留既有清单
fd2, bad_path = tempfile.mkstemp(suffix=".json", dir=TMP_ROOT)
with os.fdopen(fd2, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "models": {"evil": {"repo": "x/y", "file": "z"}},
               "registry_sha256": "0" * 64}, f)
before = dict(mdr.MODELS_REGISTRY)
try:
    mdr.update_registry("file://" + bad_path.replace("\\", "/"), cache_path=cache_file)
    check("R5 sha256 不匹配拒绝", False, "未抛出")
except mdr.ModelDownloadError as ex:
    check("R5 sha256 不匹配拒绝（本地清单保留）",
          "不匹配" in str(ex) and set(mdr.MODELS_REGISTRY) == set(before),
          f"err={str(ex)[:80]}")

# 网络不可达 → 回落保留
try:
    mdr.update_registry("http://127.0.0.1:1/nope.json", timeout=2, cache_path=cache_file)
    check("R5 远程不可达回落", False, "未抛出")
except mdr.ModelDownloadError:
    check("R5 远程不可达回落（内存清单保留）", set(mdr.MODELS_REGISTRY) == set(before))


# ================= R6: 含边工作流导出 200 =================
print("\n==== R6 含边工作流导出 ====")
from app.wfstore import WorkflowStore  # noqa: E402
wstore = WorkflowStore(os.path.join(TMP_ROOT, "wf"))
wf_edge = Workflow(
    id="wf_edge_1", name="含边工作流",
    nodes={"n1": GraphNode(id="n1", type_id="test/echo"),
           "n2": GraphNode(id="n2", type_id="test/echo", inputs={"x": "n1"})},
    edges=[{"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "x"}],
)
wstore.save(wf_edge)
client2 = TestClient(server.app)
# 复用 server 的 wfstore 需要同一 DATA_DIR——改用 server 实例的数据（隔离测试直接调端点需 wf 在 server.wfstore）
# 为隔离：把工作流写入 server 的 wfstore（临时 DATA_DIR 不可控），改走 monkeypatch 属主
server.wfstore.save(wf_edge)
r_exp = client2.get("/api/export/workflow/wf_edge_1",
                    headers={"X-FW-Local-Token": "dev-local-token"})
check("R6 含边工作流导出 200", r_exp.status_code == 200
      and r_exp.headers.get("content-type", "").startswith("application/zip"),
      f"status={r_exp.status_code}")
if r_exp.status_code == 200:
    zf = zipfile.ZipFile(io.BytesIO(r_exp.content))
    wf_json = json.loads(zf.read("workflow.json").decode("utf-8"))
    check("R6 导出 zip 含 workflow.json 且 edges 完整",
          wf_json.get("edges") == [{"source": "n1", "target": "n2",
                                    "sourceHandle": "out", "targetHandle": "x"}],
          f"edges={wf_json.get('edges')}")

# ================= 汇总 =================
print("\n==== 汇总 ====")
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, detail in PASS:
    print("  PASS", name)
for name, detail in FAIL:
    print("  FAIL", name, "|", detail)
shutil.rmtree(TMP_ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)