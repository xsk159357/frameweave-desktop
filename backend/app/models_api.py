# -*- coding: utf-8 -*-
"""模型中心 API（/api/models*）：注册表快照 / 状态 / 一键下载 / 下载任务进度 / 引用保护删除。

2026-10-19 从 server.py 拆出（审阅 P2-9：server.py 2,403 行单文件按域拆路由）。
本模块自带进程内下载状态 `_MODEL_DOWNLOADS`，不依赖 server 的全局对象；
server.py 只做 `app.include_router(models_api.router)`，路径与响应契约完全不变。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

# ---- 模型 API（t11 + t17 / D-11 模型中心：状态快照 + 一键下载 + 引用保护删除） ----
# 进程内下载状态（POST /api/models/download 触发后记录；GET 快照并入）
_MODEL_DOWNLOADS: Dict[str, Dict[str, Any]] = {}


def _model_type(mid: str, info: Dict[str, Any]) -> str:
    """模型类型（粗分类，供列表分组；registry 无 type 字段时按 id/note 推导）。"""
    m = str(mid).lower()
    note = str(info.get("note") or "").lower()
    if "whisper" in m or "asr" in m:
        return "ASR"
    if m in ("index-tts",) or "tts" in m or "语音合成" in note:
        return "TTS"
    if "clip" in m or "dino" in m or "transnet" in m or "画面" in note or "视觉" in note:
        return "视觉"
    if "demucs" in m or "vocal" in m or "分离" in note:
        return "音频分离"
    if "llm" in m:
        return "LLM"
    return "模型"


def _models_snapshot() -> List[Dict[str, Any]]:
    """models_registry 登记模型的统一快照（/api/models 与 /api/models/status 共用）。

    字段：{id, name, type, ready, source, path, size_bytes, weight_file,
    download[], download_state, installed}。只读不写盘；未下载 → ready=false 状态而非报错。
    """
    from app.plugin import deps as _deps

    try:
        reg = _deps.load_models_registry() or {}
    except Exception:  # noqa: BLE001
        reg = {}
    out: List[Dict[str, Any]] = []
    for mid, info in (reg or {}).items():
        if not isinstance(info, dict):
            continue
        label = str(info.get("name") or info.get("label") or mid)
        try:
            r = _deps.resolve_model(str(mid), env=os.environ) or {}
        except Exception:  # noqa: BLE001
            r = {}
        rec: Dict[str, Any] = {
            "id": str(mid), "name": label, "type": _model_type(str(mid), info),
            "ready": bool(r.get("ready")), "source": str(r.get("source") or ""),
            "path": str(r.get("path") or ""), "warnings": list(r.get("warnings") or []),
            "size_bytes": None, "weight_file": None, "download_state": None,
        }
        pth = rec["path"]
        if rec["ready"] and pth:
            _p = Path(pth)
            try:
                if _p.is_dir():
                    files = [f for f in _p.rglob("*") if f.is_file()]
                    rec["size_bytes"] = sum(f.stat().st_size for f in files if f.stat().st_size is not None)
                    wts = [f for f in files if f.suffix.lower() in (".safetensors", ".bin", ".ckpt", ".pt", ".pth", ".onnx")]
                    if wts:
                        rec["weight_file"] = wts[0].name
                elif _p.is_file():
                    rec["size_bytes"] = _p.stat().st_size
                    rec["weight_file"] = _p.name
            except OSError:
                rec["size_bytes"] = None
        repo = str(info.get("repo") or "")
        rec["download"] = []
        if repo:
            rec["download"].append(f"hf download {repo} --local-dir \"{rec['path'] or '<MODELS_DIR>/' + str(mid)}\"")
        fb = str(info.get("fallback_repo") or "")
        if fb and fb != repo:
            rec["download"].append(f"下载(备选)：{fb}")
        if info.get("size_mb"):
            rec["download"].append(f"体积约 {int(info['size_mb'])} MB；放置 DATA_DIR/models/<id> 约定目录")
        st = _MODEL_DOWNLOADS.get(str(mid))
        if st:
            rec["download_state"] = dict(st)
        rec["installed"] = bool(rec["ready"])
        out.append(rec)
    out.sort(key=lambda m: (bool(m["ready"]), str(m["id"])))
    return out


@router.get("/api/models")
async def models_list():
    """模型中心列表（D-11）：{id, name, type, status, path, size_bytes, installed, download_state}。

    离线可用：未下载返回 status=not_installed（非报错）；已下载 status=installed。
    """
    snap = _models_snapshot()
    out = [{
        "id": m["id"], "name": m["name"], "type": m["type"],
        "status": "installed" if m["ready"] else "not_installed",
        "ready": m["ready"], "path": m["path"], "size_bytes": m["size_bytes"],
        "weight_file": m["weight_file"], "installed": m["installed"],
        "download_state": m["download_state"], "download": m["download"],
    } for m in snap]
    return {"ok": True, "models": out, "count": len(out),
            "registry_count": len(snap), "note": "未下载返回状态而非报错；下载经 POST /api/models/download 触发"}


@router.get("/api/models/status")
async def models_status():
    """模型中心只读快照（t11 契约保持；t17 附加 type/download_state 字段，追加不破坏）。

    与官方 core/check_models 节点同源逻辑（app.plugin.deps.resolve_model + load_models_registry），
    只读不写盘、不下载。字段：{id, name, type, ready, source, path, size_bytes, weight_file, download[], download_state}。
    """
    from app.plugin import deps as _sdeps
    snap = _models_snapshot()
    return {"ok": True, "models": snap, "count": len(snap),
            "registry_count": len((_sdeps.load_models_registry()) or {}),
            "note": "只读快照；详细自检/校验请运行 core/check_models 节点；下载经 POST /api/models/download"}


@router.post("/api/models/download")
async def models_download(body: dict):
    """按 id 触发模型下载（D-11 模型中心一键下载）。

    请求体：{id: <model_key>, prefer?: "modelscope"|"huggingface"}。
    语义：
      - 已安装 → {ok, status: "already_installed", path}（不重复下载）；
      - 下载中 → {ok, status: "downloading"}（进程内状态，GET 快照可见）；
      - 触发成功 → {ok, status: "started"}（后台线程执行，完成/失败落 download_state）；
      - 未登记 id / 下载失败 → 明确错误码（unknown_model / download_failed），不静默。
    """
    mid = str((body or {}).get("id") or (body or {}).get("model") or "").strip()
    if not mid:
        return JSONResponse({"ok": False, "code": "model_id_required",
                             "message": "缺少模型 id"}, status_code=400)
    from app.plugin import deps as _deps
    from app import model_download as _md

    r = _deps.resolve_model(mid, env=os.environ)
    if r.get("ready") and r.get("path"):
        return {"ok": True, "status": "already_installed", "model": mid, "path": r["path"]}
    if mid in _MODEL_DOWNLOADS and _MODEL_DOWNLOADS[mid].get("state") == "downloading":
        return {"ok": True, "status": "downloading", "model": mid}
    if mid not in (_deps.load_models_registry() or {}):
        return JSONResponse({"ok": False, "code": "unknown_model",
                             "message": f"模型未登记: {mid}（请先更新 models_registry）"},
                            status_code=404)
    prefer = str((body or {}).get("prefer") or "modelscope").lower()
    mdir = _deps.models_dir()
    task_id = uuid.uuid4().hex[:12]
    _MODEL_DOWNLOADS[mid] = {"task_id": task_id, "state": "downloading",
                             "started_at": time.time(), "message": ""}

    def _run():
        started = _MODEL_DOWNLOADS[mid].get("started_at")
        try:
            res = _md.download_model(mid, mdir, prefer=prefer)
            after = _deps.resolve_model(mid, env=os.environ)
            _MODEL_DOWNLOADS[mid] = {
                "task_id": task_id, "state": "done", "started_at": started,
                "finished_at": time.time(), "message": "下载完成",
                "path": str(res.get("path") or ""),
                "ready": bool(after.get("ready")),
            }
        except Exception as _e:  # noqa: BLE001
            _MODEL_DOWNLOADS[mid] = {"task_id": task_id, "state": "error",
                                     "started_at": started, "finished_at": time.time(),
                                     "message": f"{type(_e).__name__}: {_e}"}

    try:
        import asyncio as _asyncio
        _asyncio.get_running_loop().run_in_executor(None, _run)
    except RuntimeError:
        import threading
        threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "status": "started", "model": mid, "task_id": task_id,
            "message": f"开始下载 {mid}（后台执行；GET /api/models/tasks/{task_id} 实时进度）"}


@router.get("/api/models/tasks/{task_id}")
async def models_task(task_id: str):
    """模型下载任务实时进度（t23 增强，t19 契约缺口补齐）。

    查询 _MODEL_DOWNLOADS 中 task_id 对应的下载任务：
      {ok, task: {task_id, model, state(downloading|done|error), started_at, finished_at,
                  message, path, ready}}
    未找到 → 404 task_not_found（不静默）。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return JSONResponse({"ok": False, "code": "task_not_found",
                             "message": "缺少 task_id"}, status_code=404)
    for mid, rec in _MODEL_DOWNLOADS.items():
        if str(rec.get("task_id") or "") == tid:
            return {"ok": True, "task": {
                "task_id": tid, "model": mid,
                "state": str(rec.get("state") or ""),
                "started_at": rec.get("started_at"),
                "finished_at": rec.get("finished_at"),
                "message": str(rec.get("message") or ""),
                "path": str(rec.get("path") or ""),
                "ready": bool(rec.get("ready")),
            }}
    return JSONResponse({"ok": False, "code": "task_not_found",
                         "message": f"任务不存在: {task_id}"}, status_code=404)


@router.delete("/api/models/{mid}")
async def models_delete(mid: str):
    """删除已装模型（D-11，引用保护）。

    规则：
      - 未安装 → 404 not_installed；
      - 路径不在 DATA_DIR/models 约定目录内（env/param 指向的外部路径）→ 409 protected_path；
      - 被任一工作流节点 params/literal_inputs 引用 → 409 model_in_use（列出引用）；
      - 删除成功 → {ok, status: "deleted"}。
    """
    from app.plugin import deps as _deps

    r = _deps.resolve_model(str(mid), env=os.environ)
    if not r.get("ready") or not r.get("path"):
        return JSONResponse({"ok": False, "code": "not_installed", "model": str(mid),
                             "message": "模型未安装"}, status_code=404)
    path = str(r["path"])
    mdir = os.path.abspath(_deps.models_dir())
    absp = os.path.abspath(path)
    if not (absp == mdir or absp.startswith(mdir + os.sep)):
        return JSONResponse({"ok": False, "code": "protected_path", "model": str(mid),
                             "message": "模型路径由环境变量/参数指定（非 DATA_DIR/models 约定目录），API 不删除"},
                            status_code=409)
    refs = _find_model_refs(path)
    if refs:
        return JSONResponse({"ok": False, "code": "model_in_use", "model": str(mid),
                             "refs": refs[:10], "ref_count": len(refs),
                             "message": "模型正被工作流引用，请先解除引用后再删除"},
                            status_code=409)
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)
        else:
            return JSONResponse({"ok": False, "code": "delete_failed", "model": str(mid),
                                 "message": "路径既非文件也非目录，未删除"}, status_code=409)
    except OSError as _e:  # noqa: BLE001
        return JSONResponse({"ok": False, "code": "delete_failed", "model": str(mid),
                             "message": str(_e)}, status_code=409)
    _MODEL_DOWNLOADS.pop(str(mid), None)
    return {"ok": True, "model": str(mid), "status": "deleted", "path": path}


def _find_model_refs(model_path: str, store: Any = None) -> List[Dict[str, Any]]:
    """引用保护：扫描工作流节点 params/literal_inputs 中引用模型路径的引用清单。

    store 缺省用全局 wfstore；测试可注入临时 WorkflowStore。
    """
    path = os.path.abspath(str(model_path))
    refs: List[Dict[str, Any]] = []
    st = store if store is not None else wfstore
    for wf in st.list():
        for nid, node in wf.nodes.items():
            for k, v in (node.params or {}).items():
                if isinstance(v, str) and v:
                    av = os.path.abspath(v)
                    if av == path or av.startswith(path + os.sep):
                        refs.append({"workflow": wf.id, "workflow_name": wf.name,
                                     "node": nid, "param": k, "value": v})
            for k, v in (node.literal_inputs or {}).items():
                if isinstance(v, str) and v:
                    av = os.path.abspath(v)
                    if av == path or av.startswith(path + os.sep):
                        refs.append({"workflow": wf.id, "workflow_name": wf.name,
                                     "node": nid, "param": k, "value": v})
    return refs


