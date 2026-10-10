"""模板注册表（M0 平台闭环）：官方内置模板源 + 已安装模板索引。

模板包结构（zip 内任意层级需包含 workflow.json）：
  {
    "tpl_id": "oneclick",           # 可选：模板 id（缺省取 zip 顶层目录名）
    "name": "一条龙 · 15分钟出片",    # 模板名
    "description": "...",           # 可选：描述
    "version": "1.0.0",             # 可选
    "kind": "workflow",             # 固定
    "tags": [...],                  # 可选
    "nodes": [{id, type, x, y, params, inputs}, ...],
    "edges": [{source, target, sourceHandle, targetHandle}, ...],
  }

索引：templates_index.json 位于模板根目录（FRAMEWEAVE_TEMPLATES 可覆盖），条目：
  {tpl_id, name, description, version, kind, tags, type_ids, node_count, installed_at, source}

- 模板可声明所需 type_id（nodes[].type 去重）；插件未装时由安装/建流链路给出
  missing_type_ids 引导（前端跳商城缺节点引导）。
- templates.py oneclick_template() 为可被索引加载的官方模板源：
  返回结构对齐 workflow.json（name/nodes/edges + tpl_id），由安装流程导入，
  不硬编码路由。
"""
from __future__ import annotations
import json
import os
import time
import zipfile
from typing import Any, Dict, List, Optional

_TEMPLATES_DIR = os.environ.get("FRAMEWEAVE_TEMPLATES") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
TEMPLATES_DIR = _TEMPLATES_DIR

INDEX_NAME = "templates_index.json"
WORKFLOW_FILE = "workflow.json"
_HISTORY_MAX = 20


# ---------------------------------------------------------------------------
# 官方内置模板源（返回结构对齐 workflow.json，可被安装流程导入）
# ---------------------------------------------------------------------------
def oneclick_template() -> Dict[str, Any]:
    """一条龙：导入视频后一键出片（MP4 + 剪映草稿 + 多格式工程）。"""
    nodes = [
        {"id": "n1", "type": "core/load_video", "x": 40, "y": 320,
         "params": {"path": ""}, "status": "pending", "inputs": {}},
        {"id": "n2", "type": "core/scene_detect", "x": 260, "y": 320,
         "params": {"sample_fps": 1, "threshold": 0.12, "min_duration": 0.6},
         "status": "pending", "inputs": {"video": "n1"}},
        {"id": "n3", "type": "core/ai_narrate", "x": 480, "y": 320,
         "params": {"mode": "上帝视角", "style": "悬疑解说", "language": "中文",
                    "api_key": "", "base_url": "https://ameaaos.com/v1", "model": "gpt-4o",
                    "word_count": 800},
         "status": "pending", "inputs": {"video": "n1", "segments": "n2"}},
        {"id": "n4", "type": "core/tts", "x": 700, "y": 160,
         "params": {"engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "+0%",
                    "output_format": "mp3"},
         "status": "pending", "inputs": {"script": "n3"}},
        {"id": "n5", "type": "core/asr", "x": 700, "y": 480,
         "params": {"model": "large-v3", "language": "zh", "device": "auto"},
         "status": "pending", "inputs": {"video": "n1"}},
        {"id": "n6", "type": "core/subtitle_compose", "x": 920, "y": 480,
         "params": {"preset": "知识科普", "animation": "fade", "karaoke": True},
         "status": "pending", "inputs": {"subtitle": "n5", "script": "n3"}},
        {"id": "n7", "type": "core/video_render", "x": 1140, "y": 160,
         "params": {"aspect": "16:9", "resolution": "1080p", "fps": 30,
                    "burn_subtitle": True, "transition": "fade", "output_name": "FrameWeave成品"},
         "status": "pending",
         "inputs": {"video": "n1", "segments": "n2", "audio": "n4", "styled_subtitle": "n6"}},
        {"id": "n8", "type": "core/draft_export", "x": 1140, "y": 480,
         "params": {"format": "all", "aspect": "16:9", "resolution": "1080p",
                    "draft_name": "FrameWeave_导出", "output_dir": ""},
         "status": "pending",
         "inputs": {"video": "n1", "segments": "n2", "audio": "n4", "styled_subtitle": "n6"}},
    ]
    edges = [
        {"source": "n1", "target": "n2", "sourceHandle": "video", "targetHandle": "video"},
        {"source": "n1", "target": "n3", "sourceHandle": "video", "targetHandle": "video"},
        {"source": "n2", "target": "n3", "sourceHandle": "segments", "targetHandle": "segments"},
        {"source": "n3", "target": "n4", "sourceHandle": "script", "targetHandle": "script"},
        {"source": "n1", "target": "n5", "sourceHandle": "video", "targetHandle": "video"},
        {"source": "n5", "target": "n6", "sourceHandle": "subtitle", "targetHandle": "subtitle"},
        {"source": "n3", "target": "n6", "sourceHandle": "script", "targetHandle": "script"},
        {"source": "n1", "target": "n7", "sourceHandle": "video", "targetHandle": "video"},
        {"source": "n2", "target": "n7", "sourceHandle": "segments", "targetHandle": "segments"},
        {"source": "n4", "target": "n7", "sourceHandle": "audio", "targetHandle": "audio"},
        {"source": "n6", "target": "n7", "sourceHandle": "styled_subtitle", "targetHandle": "styled_subtitle"},
        {"source": "n1", "target": "n8", "sourceHandle": "video", "targetHandle": "video"},
        {"source": "n2", "target": "n8", "sourceHandle": "segments", "targetHandle": "segments"},
        {"source": "n4", "target": "n8", "sourceHandle": "audio", "targetHandle": "audio"},
        {"source": "n6", "target": "n8", "sourceHandle": "styled_subtitle", "targetHandle": "styled_subtitle"},
    ]
    return {
        "tpl_id": "oneclick",
        "name": "一条龙 · 15分钟出片",
        "description": "导入视频后一键出片：场景检测 → AI文案 → 配音 → 字幕 → 渲染 + 剪映草稿导出",
        "version": "1.0.0",
        "kind": "workflow",
        "tags": ["一条龙", "官方"],
        "nodes": nodes,
        "edges": edges,
    }


_BUILTIN_TEMPLATES: Dict[str, Any] = {
    "oneclick": oneclick_template,
}


# ---------------------------------------------------------------------------
# 目录 / 索引管理
# ---------------------------------------------------------------------------
def set_templates_dir(path: str) -> None:
    """设置模板根目录（server 启动时调用：打包版 = 用户数据目录，可写且升级保留）。"""
    global TEMPLATES_DIR
    TEMPLATES_DIR = path
    os.makedirs(TEMPLATES_DIR, exist_ok=True)


def _index_path() -> str:
    return os.path.join(TEMPLATES_DIR, INDEX_NAME)


def _load_index() -> Dict[str, dict]:
    p = _index_path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(idx: Dict[str, dict]) -> None:
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    with open(_index_path(), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def _tpl_dir(tpl_id: str) -> str:
    return os.path.join(TEMPLATES_DIR, tpl_id)


# ---------------------------------------------------------------------------
# 校验 / 元数据
# ---------------------------------------------------------------------------
def validate_workflow_payload(payload: Any) -> List[str]:
    """校验模板 workflow.json 结构。返回错误列表（空 = 合法）。"""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["模板内容必须是 JSON 对象"]
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("模板缺少 nodes 数组")
    else:
        for i, nd in enumerate(nodes):
            if not isinstance(nd, dict) or not nd.get("id") or not nd.get("type"):
                errors.append(f"nodes[{i}] 缺少 id/type")
    edges = payload.get("edges")
    if edges is not None and not isinstance(edges, list):
        errors.append("模板 edges 必须是数组")
    else:
        for i, e in enumerate(edges or []):
            if not isinstance(e, dict):
                errors.append(f"edges[{i}] 必须是对象")
                continue
            for k in ("source", "target"):
                if not e.get(k):
                    errors.append(f"edges[{i}] 缺少 {k}")
    return errors


def _metadata_for(payload: Dict[str, Any], tpl_id: str, *, tags=None, source: str = "") -> dict:
    nodes = payload.get("nodes") or []
    type_ids = sorted({str(nd.get("type", "")) for nd in nodes if nd.get("type")})
    now = time.time()
    return {
        "tpl_id": tpl_id,
        "name": payload.get("name") or tpl_id,
        "description": payload.get("description", ""),
        "version": payload.get("version", "1.0.0"),
        "kind": payload.get("kind", "workflow"),
        "tags": list(tags or payload.get("tags") or []),
        "type_ids": type_ids,
        "node_count": len(nodes),
        "edge_count": len(payload.get("edges") or []),
        "installed_at": now,
        "source": source or "",
    }


def _write_template(tpl_id: str, payload: Dict[str, Any]) -> str:
    """将模板 payload 落盘到 templates/<tpl_id>/workflow.json。返回模板目录。"""
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    tdir = _tpl_dir(tpl_id)
    os.makedirs(tdir, exist_ok=True)
    wpath = os.path.join(tdir, WORKFLOW_FILE)
    with open(wpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return tdir


# ---------------------------------------------------------------------------
# 安装（workflow 条目安装链路骨架：商城下载 zip → 校验 -> 注册索引）
# ---------------------------------------------------------------------------
def install_workflow_payload(payload: Any, *, tpl_id: str = "", tags=None, source: str = "") -> dict:
    """安装一个 workflow.json 内容（来自官方源 / 商城 zip）。返回模板条目元数据。"""
    errors = validate_workflow_payload(payload)
    if errors:
        raise ValueError("模板内容无效: " + "; ".join(errors))
    tid = (tpl_id or (payload.get("tpl_id") or "")).strip() or "tpl_" + os.urandom(4).hex()
    _write_template(tid, payload)
    meta = _metadata_for(payload, tid, tags=tags, source=source)
    idx = _load_index()
    old = idx.get(tid)
    if old:
        meta["installed_at"] = old.get("installed_at", meta["installed_at"])
    idx[tid] = meta
    _save_index(idx)
    return meta


def install_workflow_zip(zip_path: str, *, tpl_id: str = "", tags=None, source: str = "") -> dict:
    """从 zip 安装工作流模板（zip 内任意层级需含 workflow.json）。返回条目元数据。

    安全：zip-slip 拦截——不直接落 zip 成员（只读 workflow.json 后按 tpl_id 重建目录）。
    """
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("不是有效的 zip 文件")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        wf_members = [n for n in names if n.replace("\\", "/").split("/")[-1] == WORKFLOW_FILE]
        if not wf_members:
            raise ValueError("zip 内未找到 workflow.json（不是工作流模板包）")
        top = [n for n in wf_members if "/" not in n.replace("\\", "/")]
        chosen = sorted(top or wf_members, key=len)[0]
        with zf.open(chosen) as src:
            try:
                payload = json.loads(src.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValueError("workflow.json 不是合法 JSON: " + str(e))
    if not tpl_id:
        head = chosen.replace("\\", "/").split("/")[0]
        tpl_id = (payload.get("tpl_id") or "").strip() or ("" if head == WORKFLOW_FILE else head)
    return install_workflow_payload(payload, tpl_id=tpl_id, tags=tags, source=source)


def install_official(tpl_id: str = "") -> List[dict]:
    """导入官方内置模板源（oneclick_template 等）到注册表。

    空 tpl_id = 导入全部内置模板；返回导入的条目元数据列表。
    """
    installed: List[dict] = []
    sources = {tpl_id: _BUILTIN_TEMPLATES[tpl_id]} if tpl_id else _BUILTIN_TEMPLATES
    for tid, factory in sources.items():
        try:
            meta = install_workflow_payload(factory(), tpl_id=tid, tags=["官方"], source="builtin")
            installed.append(meta)
        except ValueError as e:
            print(f"[templates] 官方模板导入失败 {tid}: {e}")
    return installed


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def list_templates() -> List[dict]:
    """模板注册表清单（按 installed_at 升序）。"""
    idx = _load_index()
    out = []
    for meta in idx.values():
        if not os.path.isfile(os.path.join(_tpl_dir(meta.get("tpl_id", "")), WORKFLOW_FILE)):
            continue  # 目录被删：跳过（不强清理索引，避免安装中并发读丢失）
        out.append(dict(meta))
    out.sort(key=lambda x: x.get("installed_at", 0))
    return out


def get_template(tpl_id: str) -> Optional[dict]:
    """按 tpl_id 取模板（含 workflow 内容）。"""
    idx = _load_index()
    meta = idx.get(tpl_id)
    if meta is None:
        return None
    wpath = os.path.join(_tpl_dir(tpl_id), WORKFLOW_FILE)
    if not os.path.isfile(wpath):
        return None
    try:
        with open(wpath, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return {**meta, "workflow": payload}


def remove(tpl_id: str) -> bool:
    """卸载模板（删目录 + 索引）。返回是否命中。"""
    idx = _load_index()
    if tpl_id not in idx:
        return False
    tdir = _tpl_dir(tpl_id)
    import shutil
    if os.path.isdir(tdir):
        shutil.rmtree(tdir, ignore_errors=True)
    idx.pop(tpl_id, None)
    _save_index(idx)
    return True


def missing_type_ids(tpl_id: str, installed_type_ids) -> List[str]:
    """模板所需但当前未安装的 type_id 列表（插件缺失引导）。"""
    tpl = get_template(tpl_id)
    if tpl is None:
        return []
    have = set(installed_type_ids or [])
    return [t for t in (tpl.get("type_ids") or []) if t not in have]
