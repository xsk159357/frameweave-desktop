"""FrameWeave 本地服务入口：FastAPI + WebSocket 事件推送。

启动: uvicorn app.server:app --port 8788
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import zipfile
import asyncio
from typing import Any, Dict, List, Optional, Set

from fastapi import UploadFile, File, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .assets import AssetStore
from .engine import Engine
from .license import LicenseService
from .registry import autodiscover, list_specs
from . import declarative
from .templates import oneclick_template
from .draft_validator import validate_jianying_draft
from .secrets import store_secret, read_secret, list_secret_names, delete_secret
from .wfstore import WorkflowStore
from .market import MarketService
from . import email_service, cloud_gateway

# 读取 backend/.env（不覆盖宿主机已有环境变量）
def _load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line: continue
                _key, _value = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _value.strip().strip("\"\'"))
_load_local_env()

def _resolve_data_dir():
    """数据目录：源码模式用 backend/data；PyInstaller 打包模式用 %APPDATA%/FrameWeave。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "FrameWeave")
    else:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(d, exist_ok=True)
    return d

DATA_DIR = _resolve_data_dir()

AUTH_REVISION = "email-code-v2"
BACKEND_PROTOCOL = 3
app = FastAPI(title="FrameWeave Local Service", version="0.2.12")

# ---- 本地 API 鉴权（H3 修复）----
# 打包版：main.cjs 生成随机令牌经 FRAMEWEAVE_LOCAL_TOKEN 注入后端与渲染进程。
# 源码/dev：回落到固定 dev-local-token（vite 前端同值）。
LOCAL_TOKEN = os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "") or "dev-local-token"


def _local_token_ok(tok: str) -> bool:
    if tok == LOCAL_TOKEN:
        return True
    # dev 联调（FRAMEWEAVE_DEV_ENDPOINTS=1）额外放行 dev 令牌
    if os.environ.get("FRAMEWEAVE_DEV_ENDPOINTS") == "1" and tok == "dev-local-token":
        return True
    return False


@app.middleware("http")
async def local_token_guard(request: Request, call_next):
    """本地 API 鉴权（纵深防御，防本机恶意网页/进程劫持本地服务）。

    保护面：
      - /api/secrets*           全部（密钥明文读写删）
      - /api/export/*           全部（导出工作流/节点包）
      - /api/assets/*           全部（媒体文件与结构化资产读取）
      - 写操作                  POST/PUT/DELETE 的
                                workflows / market / user_nodes / util / draft
    说明：
      - /api/auth/*、/api/specs、/api/templates、/ws 保持开放（登录前必须可达）。
      - 渲染进程所有请求均带 X-FW-Local-Token（打包版为随机令牌，dev 回落常量），
        因此加严不会影响前端。
    """
    path = request.url.path
    method = request.method.upper()
    if path.startswith("/api/auth") or path == "/api/health" or path.startswith("/ws"):
        return await call_next(request)
    need = False
    if path.startswith("/api/secrets") or path.startswith("/api/export") or path.startswith("/api/assets"):
        need = True
    elif method in ("POST", "PUT", "DELETE") and path.startswith(
            ("/api/workflows", "/api/market", "/api/user_nodes", "/api/util", "/api/draft")):
        need = True
    if need:
        tok = request.headers.get("X-FW-Local-Token", "")
        if not _local_token_ok(tok):
            return JSONResponse({"ok": False, "message": "本地授权令牌无效"}, status_code=401)
    return await call_next(request)


# ---- 统一异常处理（畸形容器防 500） ----
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """请求体/参数校验失败：返回规范 422（不再让畸形 body 落到 500）。"""
    return JSONResponse(
        {"ok": False, "message": "请求参数校验失败", "errors": exc.errors()[:5]},
        status_code=422,
    )


@app.exception_handler(json.JSONDecodeError)
async def _json_handler(request: Request, exc: json.JSONDecodeError):
    """请求体不是合法 JSON：规范 400。"""
    return JSONResponse({"ok": False, "message": "请求体不是合法 JSON"}, status_code=400)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    """兜底：未捕获异常返回规范 500 结构，不泄露内部细节（HTTPException 由内置 handler 优先处理）。"""
    import logging
    try:
        logging.getLogger("uvicorn.error").error(
            "unhandled: %s %s -> %s", request.method, request.url.path, repr(exc))
    except Exception:
        pass
    return JSONResponse({"ok": False, "message": "服务器内部错误"}, status_code=500)

app.add_middleware(
    CORSMiddleware,
    # 本地服务收窄：仅开发预览源 + file:// 加载（打包版 Origin 为 null）。
    # 恶意网页(任意 http/https 源)无法跨域读取本地 API → 阻断本地服务劫持面。
    allow_origins=["http://localhost:5180", "http://127.0.0.1:5180", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 初始化 ----
autodiscover()
declarative.scan()
store = AssetStore(os.path.join(DATA_DIR, "assets"))
wfstore = WorkflowStore(os.path.join(DATA_DIR, "workflows"))
declarative.set_user_nodes_dir(os.path.join(DATA_DIR, "user_nodes"))

# 启动清理：删除未被缓存索引引用的孤儿中间资产（部分落盘失败 / 重跑覆盖残留）
try:
    _cache_path = os.path.join(store.root, "cache_index.json")
    _keep: Set[str] = set()
    if os.path.exists(_cache_path):
        with open(_cache_path, "r", encoding="utf-8") as _f:
            _idx = json.load(_f) or {}
        for _entry in _idx.values():
            _keep.update((_entry.get("assets") or {}).values())
    _n = store.prune_orphans(_keep)
    if _n:
        print(f"[prune] 启动清理孤儿资产 {_n} 个")
except Exception as _e:  # noqa: BLE001
    print("[prune] 跳过:", _e)

# 迁移 v0.2.3 及以前装进程序目录的用户节点（打包版旧位置 _internal/user_nodes）
try:
    legacy_nodes = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_nodes")
    new_nodes = declarative.USER_NODES_DIR
    if os.path.isdir(legacy_nodes) and os.path.abspath(legacy_nodes) != os.path.abspath(new_nodes)             and not os.path.isdir(new_nodes) and os.listdir(legacy_nodes):
        os.makedirs(new_nodes, exist_ok=True)
        for entry in os.listdir(legacy_nodes):
            src = os.path.join(legacy_nodes, entry)
            if os.path.isfile(src) and entry.endswith(".zip"):
                continue  # 跳过临时下载包
            dst = os.path.join(new_nodes, entry)
            if not os.path.exists(dst):
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        print(f"[migrate] 用户节点已从旧安装目录迁移到 {new_nodes}")
except Exception as e:  # noqa: BLE001
    print("[migrate] 用户节点迁移跳过:", e)
license_svc = LicenseService(DATA_DIR)
market_svc = MarketService(DATA_DIR)

# 开发端点开关：正式打包默认关闭（本地桩联调时设 FRAMEWEAVE_DEV_ENDPOINTS=1）
DEV_ENDPOINTS = os.environ.get("FRAMEWEAVE_DEV_ENDPOINTS", "") == "1"

# WebSocket 连接管理
class WSManager:
    def __init__(self):
        self.conns: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.conns.add(ws)

    def disconnect(self, ws: WebSocket):
        self.conns.discard(ws)

    async def broadcast(self, payload: Dict[str, Any]):
        if not self.conns:
            return
        dead = []
        for ws in self.conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = WSManager()

# 保持运行中任务引用，防止被垃圾回收（wfid → asyncio.Task）
_RUNNING_TASKS: dict[str, "asyncio.Task"] = {}
def _task_done(wfid: str):
    def _done(t: "asyncio.Task"):
        _RUNNING_TASKS.pop(wfid, None)
    return _done


def make_engine() -> Engine:
    async def _broadcast(evt: Dict[str, Any]):
        await manager.broadcast(evt)
    def ev(evt: Dict[str, Any]):
        # engine._emit 是同步回调，这里把事件投递回事件循环异步广播
        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(_broadcast(evt), loop=loop)
        except RuntimeError:
            pass
    return Engine(store, on_event=ev)


# ---- API 模型 ----
class WFCreate(BaseModel):
    name: str = "未命名工作流"
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None

class WFUpdate(BaseModel):
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    name: Optional[str] = None

class RunRequest(BaseModel):
    mode: str = "all"
    node_ids: Optional[List[str]] = None

class LoginRequest(BaseModel):
    email: str
    password: str
    device_id: str = ""

class EmailCodeRequest(BaseModel):
    email: str
    scene: str = "register"

class RegisterRequest(BaseModel):
    email: str
    code: str
    password: str
    device_id: str = ""

class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str

class ActivateRequest(BaseModel):
    email: str
    card: str

class MarketPublish(BaseModel):
    kind: str
    title: str
    description: str = ""
    author: str = ""
    price: int = 0
    download_url: str = ""
    tags: List[str] = []
    source_zip: str = ""  # 本地桩：内联 zip 路径（正式版无此字段）
    version: str = "1.0.0"
    type_id: str = ""
    sha256: str = ""
    size: int = 0
    min_client_version: str = ""
    status: str = "active"

class MarketInstall(BaseModel):
    token: str = ""

class MarketDownloadAuth(BaseModel):
    token: str = ""
    client_version: str = ""

class MarketInstallReport(BaseModel):
    token: str = ""
    client_version: str = ""
    result: str = "ok"
    error: str = ""


_SECRET_KEY_RE = __import__("re").compile(r"(key|secret|token|password|api)", __import__("re").IGNORECASE)


def _protect_secret_params(nodes) -> None:
    """工作流保存时把 key 类参数明文替换为 @secret: 保险箱引用（DPAPI 密文落盘，H5 修复）。

    值匹配密钥类字段名（key/secret/token/password/api）且不是 @secret: 引用时：
    存入本机保险箱（secrets.py，DPAPI 加密），params 中改为 @secret:fw_wf_<节点>_<字段>。
    加密失败时保留原文（本地非 Windows 环境降级）。
    """
    from . import secrets as _secrets
    for nd in nodes or []:
        params = nd.get("params") or {}
        nid = str(nd.get("id", "n"))
        for name, val in list(params.items()):
            if not isinstance(val, str) or not val or val.startswith("@secret:"):
                continue
            if not _SECRET_KEY_RE.search(str(name)):
                continue
            sec_name = f"fw_wf_{nid}_{name}"
            try:
                _secrets.store_secret(sec_name, val)
                params[name] = "@secret:" + sec_name
            except Exception:  # noqa: BLE001
                pass  # 非 Windows / DPAPI 不可用：保留原文（单机降级）


# ---- 工作流 API ----
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "frameweave", "version": "0.2.12", "protocol_version": BACKEND_PROTOCOL, "auth_revision": AUTH_REVISION}

@app.get("/api/system/build-info")
async def build_info():
    return {"ok": True, "service": "frameweave", "version": "0.2.10", "protocol_version": BACKEND_PROTOCOL, "auth_revision": AUTH_REVISION}

# ---- 用户节点（声明式插件） ----
@app.get("/api/user_nodes")
async def user_nodes():
    """已安装的用户节点清单。"""
    return {"nodes": [spec.type_id for spec in declarative.list_specs()], "dir": declarative.USER_NODES_DIR}

@app.post("/api/user_nodes/reload")
async def user_nodes_reload():
    """重新扫描用户节点目录（安装/删除插件包后调用）。"""
    n = declarative.scan()
    return {"ok": True, "loaded": n, "nodes": [spec.type_id for spec in declarative.list_specs()]}

MAX_UPLOAD_MB = 50

@app.post("/api/user_nodes/install")
async def user_nodes_install(file: UploadFile = File(...)):
    """上传 zip 插件包并安装（解压到 user_nodes/ + 重扫）。"""
    import tempfile
    import shutil
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        dest = declarative.install_zip(tmp.name)
        return {"ok": True, "installed_to": dest,
                "nodes": [spec.type_id for spec in declarative.list_specs()]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

@app.get("/api/specs")
async def specs():
    """节点类型清单（前端渲染节点面板）。"""
    out = []
    for spec in list_specs():
        out.append({
            "type_id": spec.type_id, "title": spec.title, "category": spec.category,
            "description": spec.description, "version": spec.version,
            "inputs": [p.__dict__ for p in spec.inputs],
            "outputs": [p.__dict__ for p in spec.outputs],
            "params": [p.__dict__ for p in spec.params],
            "gpu_required": spec.gpu_required, "gpu_recommended": spec.gpu_recommended,
            "streaming": spec.streaming,
        })
    return out

@app.get("/api/secrets")
async def list_secrets():
    """列出已存密钥名（不返回密文）。"""
    return {"names": list_secret_names()}

@app.put("/api/secrets/{name}")
async def put_secret(name: str, body: dict):
    """加密存储密钥。body: {"value": "..."}"""
    value = str((body or {}).get("value", "")).strip()
    if not value:
        raise HTTPException(400, "value 不能为空")
    store_secret(name, value)
    return {"ok": True, "name": name, "stored": True}

@app.get("/api/secrets/{name}")
async def get_secret(name: str):
    """读取明文（仅在本地回环使用）。"""
    val = read_secret(name)
    if val is None:
        raise HTTPException(404, "密钥不存在")
    return {"name": name, "value": val}

@app.delete("/api/secrets/{name}")
async def del_secret(name: str):
    return {"ok": delete_secret(name), "name": name}

@app.post("/api/draft/validate")
async def validate_draft(body: dict):
    """校验剪映草稿 JSON（body: {path} 或 {content}）。"""
    path = (body or {}).get("path")
    if path:
        if not os.path.exists(path):
            raise HTTPException(404, "草稿文件不存在: %s" % path)
        return validate_jianying_draft(path)
    content = (body or {}).get("content")
    if content is None:
        raise HTTPException(400, "需要 path 或 content")
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        return validate_jianying_draft(tmp)
    finally:
        os.remove(tmp)

@app.get("/api/templates")
async def list_templates():
    """内置工作流模板列表。"""
    return [
        {
            "id": "oneclick",
            "title": "一条龙 · 15分钟出片",
            "description": "导入视频 → 场景检测 → AI文案 → 配音 → 字幕 → 渲染+草稿导出",
            "nodes": len(oneclick_template()["nodes"]),
        },
    ]

@app.post("/api/workflows/from-template/{tpl_id}", status_code=201)
async def create_wf_from_template(tpl_id: str):
    """按模板创建工作流。"""
    if tpl_id != "oneclick":
        raise HTTPException(404, "模板不存在")
    data = oneclick_template()
    wf = wfstore.create(data["name"])
    from .engine import GraphNode
    wf.nodes = {}
    for nd in data["nodes"]:
        n = GraphNode(id=nd["id"], type_id=nd["type"],
                      x=nd.get("x", 0), y=nd.get("y", 0),
                      params=nd.get("params", {}),
                      inputs=nd.get("inputs") or {})
        wf.nodes[n.id] = n
    wf.edges = data["edges"]
    wfstore.save(wf)
    return wf.to_dict()

@app.get("/api/workflows")
async def list_wf():
    return [{"id": w.id, "name": w.name, "updated_at": w.updated_at} for w in wfstore.list()]

@app.post("/api/workflows", status_code=201)
async def create_wf(body: WFCreate):
    wf = wfstore.create(body.name)
    if body.nodes:
        _protect_secret_params(body.nodes)
        from .engine import GraphNode
        wf.nodes = {}
        for nd in body.nodes:
            n = GraphNode(id=nd["id"], type_id=nd["type"],
                          x=nd.get("x", 0), y=nd.get("y", 0),
                          params=nd.get("params", {}),
                          inputs=nd.get("inputs") or {})
            wf.nodes[n.id] = n
        if body.edges:
            wf.edges = body.edges
            for e in body.edges:
                t = wf.nodes.get(e["target"])
                if t is not None:
                    handle = e.get("targetHandle") or "in"
                    cur = t.inputs.get(handle)
                    if not (isinstance(cur, str) and cur.startswith("@")):
                        t.inputs[handle] = e["source"]
    wfstore.save(wf)
    return wf.to_dict()

@app.get("/api/workflows/{wfid}")
async def get_wf(wfid: str):
    wf = wfstore.get(wfid)
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    return wf.to_dict()

@app.put("/api/workflows/{wfid}")
async def update_wf(wfid: str, body: WFUpdate):
    wf = wfstore.get(wfid)
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    if body.name is not None:
        wf.name = body.name
    if body.nodes is not None:
        _protect_secret_params(body.nodes)
        from .engine import GraphNode
        old_by_id = {nid: n for nid, n in wf.nodes.items()}
        wf.nodes = {}
        for nd in body.nodes:
            old = old_by_id.get(nd["id"])
            n = GraphNode(id=nd["id"], type_id=nd["type"],
                          x=nd.get("x", 0), y=nd.get("y", 0),
                          params=nd.get("params", {}),
                          inputs=nd.get("inputs") or {})
            # 保留运行时状态：若 body 未带状态且旧节点存在，继承（避免 PUT 清掉执行结果/缓存）
            if old is not None:
                n.status = old.status
                n.asset_ids = dict(old.asset_ids or {})
                n.cache_key = old.cache_key
                n.error = old.error
                n.progress = old.progress
                n.started_at = old.started_at
                n.last_params = dict(old.last_params or {})
                n.run_history = list(old.run_history or [])
            wf.nodes[n.id] = n
        # 重建 inputs/outputs 映射
        for nd in body.nodes:
            nid = nd["id"]
            for p_name in ["in", "out"]:
                pass
    if body.edges is not None:
        wf.edges = body.edges
        # 根据 edges 重建每个节点的 inputs（端口名->上游节点）
        for e in body.edges:
            t = wf.nodes.get(e["target"])
            if t is not None:
                handle = e.get("targetHandle") or "in"
                # 显式 @资产引用优先，edge 连线不覆盖
                cur = t.inputs.get(handle)
                if not (isinstance(cur, str) and cur.startswith("@")):
                    t.inputs[handle] = e["source"]
    wfstore.save(wf)
    return wf.to_dict()

@app.delete("/api/workflows/{wfid}")
async def delete_wf(wfid: str):
    ok = wfstore.delete(wfid)
    if not ok:
        raise HTTPException(404, "工作流不存在")
    return {"ok": True}

@app.post("/api/workflows/{wfid}/nodes/{nid}/reset")
async def reset_node(wfid: str, nid: str, body: dict = {}):
    """重置单个节点：状态→pending，清空资产/错误。force=true 同时清除命中缓存（强制重算）。"""
    wf = wfstore.get(wfid)
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    node = wf.nodes.get(nid)
    if node is None:
        raise HTTPException(404, "节点不存在")
    from .engine import NodeStatus
    node.status = NodeStatus.PENDING
    node.asset_ids = {}
    node.cache_key = ""
    node.error = None
    node.progress = 0.0
    node.started_at = None
    force = bool((body or {}).get("force", False))
    ret = {"ok": True, "node_id": nid, "force": force}
    if force:
        engine = make_engine()
        key = engine._cache_key(wf, node)
        engine.invalidate_cache(key)
        ret["cache_invalidated"] = key[:24] + "..."
    wfstore.save(wf)
    return ret

@app.post("/api/workflows/{wfid}/run")
async def run_wf(wfid: str, body: RunRequest):
    wf = wfstore.get(wfid)
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    engine = make_engine()

    async def _run_and_save():
        try:
            await engine.execute(wf, run_node_ids=set(body.node_ids or []), run_mode=body.mode)
        finally:
            wfstore.save(wf)  # 持久化执行后的节点状态

    # 用全局引用保持 task 不被 GC；同一工作流重复运行先取消旧任务
    t = asyncio.create_task(_run_and_save())
    old = _RUNNING_TASKS.get(wfid)
    if old and not old.done():
        old.cancel()
    _RUNNING_TASKS[wfid] = t
    t.add_done_callback(_task_done(wfid))
    return {"ok": True, "message": "执行已开始"}


@app.post("/api/workflows/{wfid}/cancel")
async def cancel_wf(wfid: str):
    """取消运行中的工作流（F3：运行中可停止）。"""
    t = _RUNNING_TASKS.get(wfid)
    if t and not t.done():
        t.cancel()
        return {"ok": True, "message": "已请求取消执行"}
    return {"ok": True, "message": "当前没有正在执行的任务"}


# ---- 资产 API ----
@app.get("/api/assets/{aid}")
async def get_asset(aid: str):
    asset = store.get(aid)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    return asset.to_dict()

@app.get("/api/assets/{aid}/content")
async def get_asset_content(aid: str):
    """结构化资产内容（JSON）。媒体文件走 /api/assets/{aid}/file。"""
    asset = store.get(aid)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    payload = store.read_json(aid)
    if payload is None and asset.path:
        # 非 JSON 文件返回文件信息
        return {"path": asset.path, "kind": asset.kind, "size": asset.size}
    return {"id": aid, "kind": asset.kind, "content": payload}

@app.post("/api/util/reveal")
async def reveal_path(body: dict):
    """在系统资源管理器中定位文件（Windows explorer /select）。"""
    import subprocess
    p = (body or {}).get("path", "")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "路径不存在: %s" % p)
    subprocess.Popen(["explorer", "/select,", os.path.abspath(p)])
    return {"ok": True}

@app.get("/api/assets/{aid}/file")
async def get_asset_file(aid: str):
    from fastapi.responses import FileResponse
    asset = store.get(aid)
    if asset is None or not asset.path or not os.path.exists(asset.path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(asset.path, filename=os.path.basename(asset.path))


# ---- 授权 API（本地桩） ----
@app.post("/api/auth/email/send-code")
async def send_email_code(body: EmailCodeRequest):
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_email_code(body.email, body.scene)
        return resp or {"ok": False, "message": "云端授权服务不可用"}
    if not email_service.valid_qq_email(body.email): return {"ok": False, "code": "INVALID_EMAIL", "message": "目前仅支持 QQ 邮箱（@qq.com）"}
    if body.scene not in ("register", "reset_password"): return {"ok": False, "code": "INVALID_SCENE", "message": "验证码场景不正确"}
    ok, message = await asyncio.to_thread(email_service.issue_code, body.email, body.scene)
    return {"ok": ok, "message": message, "expires_in": 300, "retry_after": 60}

@app.post("/api/auth/register")
async def register(body: RegisterRequest):
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_register(body.email, body.code, body.password, body.device_id)
        return resp or {"ok": False, "message": "云端授权服务不可用"}
    if not email_service.valid_qq_email(body.email): return {"ok": False, "code": "INVALID_EMAIL", "message": "目前仅支持 QQ 邮箱（@qq.com）"}
    if len(body.password) < 8: return {"ok": False, "code": "WEAK_PASSWORD", "message": "密码至少 8 位"}
    verified, message = await asyncio.to_thread(email_service.verify_code, body.email, "register", body.code)
    if not verified: return {"ok": False, "message": message}
    return license_svc.register_verified(body.email, body.password, body.device_id)

@app.post("/api/auth/password/reset")
async def reset_password(body: ResetPasswordRequest):
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_reset_password(body.email, body.code, body.new_password)
        return resp or {"ok": False, "message": "云端授权服务不可用"}
    if not email_service.valid_qq_email(body.email): return {"ok": False, "code": "INVALID_EMAIL", "message": "目前仅支持 QQ 邮箱（@qq.com）"}
    if len(body.new_password) < 8: return {"ok": False, "message": "密码至少 8 位"}
    verified, message = await asyncio.to_thread(email_service.verify_code, body.email, "reset_password", body.code)
    if not verified: return {"ok": False, "message": message}
    result = license_svc.reset_password(body.email, body.new_password)
    if not result.get("ok"):
        return {"ok": True, "message": "如果该邮箱已注册，密码重置将完成"}
    return result

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if not email_service.valid_qq_email(body.email):
        return {"ok": False, "code": "INVALID_EMAIL", "message": "目前仅支持 QQ 邮箱（@qq.com）"}
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_login(body.email, body.password, body.device_id)
        return resp or {"ok": False, "message": "云端授权服务不可用"}
    return license_svc.login(body.email, body.password, body.device_id)

@app.post("/api/auth/activate")
async def activate(body: ActivateRequest):
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_activate(body.email, body.card)
        return resp or {"ok": False, "message": "云端授权服务不可用"}
    return license_svc.activate_card(body.email, body.card)

if DEV_ENDPOINTS:
    @app.post("/api/dev/issue-card")
    async def dev_issue_card(body: dict):
        """本地桩专用：模拟管理后台发行卡密（正式版在云端管理后台）。"""
        plan = body.get("plan", "month")
        days = int(body.get("days", 30))
        return {"ok": True, "card": license_svc.issue_card(plan, days), "plan": plan, "days": days}


# ---- 商城 API（M18 本地桩） ----
@app.get("/api/market/items")
async def market_items(q: str = "", kind: str = "", official: str = ""):
    # 云化时优先返回云端条目（含 GitHub Release 直链元数据），云端不可达回落本地桩。
    if cloud_gateway.cloud_enabled():
        cloud_items = await asyncio.to_thread(cloud_gateway.cloud_market_items)
        if cloud_items is not None:
            return {"items": cloud_items, "count": len(cloud_items)}
    items = market_svc.list_items(q, kind, official)
    return {"items": items, "count": len(items)}

@app.post("/api/market/items")
async def market_publish(body: MarketPublish):
    if body.kind not in ("node", "workflow"):
        raise HTTPException(400, "kind 必须是 node 或 workflow")
    if not body.title or not body.author:
        raise HTTPException(400, "标题与作者必填")
    item = market_svc.publish(body.kind, body.title, body.description, body.author,
                              body.price, body.download_url, body.tags,
                              source_zip=body.source_zip, version=body.version,
                              type_id=body.type_id, sha256=body.sha256, size=body.size,
                              min_client_version=body.min_client_version,
                              status=body.status)
    return {"ok": True, "item": item}

@app.post("/api/market/items/{item_id}/download-auth")
async def market_download_auth(item_id: str, body: MarketDownloadAuth):
    """下载授权/记录接口：返回 GitHub Release 直链（服务器不代理文件）。

    云化时转发云端授权/记账；离线/dev 回落本地桩。
    """
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_market_download_auth(item_id, body.token, body.client_version)
        return resp or {"ok": False, "code": "cloud_unavailable", "message": "云端授权服务不可用"}
    return market_svc.download_auth(item_id, body.token, license_svc, body.client_version)

@app.post("/api/market/items/{item_id}/install-report")
async def market_install_report(item_id: str, body: MarketInstallReport):
    """安装回报：客户端装完/失败后回报，云端/本地记录安装计数与流水。"""
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_market_install_report(
            item_id, body.token, body.client_version, body.result, body.error)
        return resp or {"ok": False, "code": "cloud_unavailable", "message": "云端授权服务不可用"}
    return market_svc.install_report(item_id, body.token, body.client_version, body.result, body.error)

@app.post("/api/market/items/{item_id}/install")
async def market_install(item_id: str, body: MarketInstall):
    return market_svc.install(item_id, body.token, license_svc, declarative.USER_NODES_DIR)

@app.get("/api/market/authors")
async def market_authors():
    return {"authors": market_svc.authors()}

# ---- 导出 API（M18） ----
@app.get("/api/export/node/{type_id:path}")
async def export_node(type_id: str):
    """导出声明式节点插件包（zip，可再上传网盘分享）。"""
    import io
    import shutil
    from fastapi.responses import StreamingResponse
    pkg = declarative.pkg_dir(type_id)
    if pkg is None:
        raise HTTPException(404, "节点包不存在")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        base = os.path.basename(pkg)
        for dirpath, _dirs, files in os.walk(pkg):
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = os.path.join(base, os.path.relpath(full, pkg))
                zf.write(full, rel)
    buf.seek(0)
    name = type_id.split("/")[-1]
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})

@app.get("/api/export/workflow/{wfid}")
async def export_workflow(wfid: str):
    """导出工作流（json 打包 zip，可分享/上传商城）。"""
    import io
    from fastapi.responses import StreamingResponse
    wf = wfstore.get(wfid)
    if wf is None:
        raise HTTPException(404, "工作流不存在")
    nodes = [{"id": n.id, "type": n.type_id, "x": n.x, "y": n.y,
              "params": dict(n.params or {}), "inputs": dict(n.inputs or {})}
             for n in wf.nodes.values()]
    edges = [e.__dict__ for e in (wf.edges or [])]
    payload = json.dumps({"name": wf.name, "version": "1.0", "nodes": nodes, "edges": edges},
                         ensure_ascii=False, indent=2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json", payload)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{wfid}.zip"'})


if DEV_ENDPOINTS:
    @app.post("/api/dev/add-credits")
    async def dev_add_credits(body: dict):
        """本地桩专用：给账号加积分（正式版 = 支付宝充值，M19）。"""
        email = (body.get("email") or "").strip().lower()
        for s in license_svc._sessions.values():
            if s.get("email", "").lower() == email:
                s["credits"] = int(s.get("credits") or 0) + int(body.get("amount", 0))
                license_svc._save()
                return {"ok": True, "credits": s["credits"]}
        return {"ok": False, "message": "账号不存在"}


@app.api_route("/api/auth/verify", methods=["GET", "POST"])
async def verify(token: str = "", device_id: str = "", body: dict = None):
    """订阅校验：兼容 GET(query) 与 POST(body)，契约与云端一致。"""
    body = body or {}
    if cloud_gateway.cloud_enabled():
        resp = cloud_gateway.cloud_verify(token or body.get("token", ""), device_id or body.get("device_id", ""))
        return resp or {"ok": False, "reason": "云端授权服务不可用"}
    token = token or str(body.get("token") or "")
    device_id = device_id or str(body.get("device_id") or "")
    return license_svc.verify(token, device_id)


# ---- WebSocket ----
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            # M0：客户端心跳/订阅（保持简单）
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
