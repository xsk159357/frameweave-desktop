"""工作流存储：JSON 文件持久化（M0 简化版，后续可换 SQLite）。

版本字段（§5.2.3 草案落地）：每工作流一份 sidecar 版本表 workflows/_versions/<wfid>.json，
保存时若内容变化则快照旧版到 workflows/<wfid>.v<v>.json（保留最近 20 版，防膨胀）。
engine.Workflow 数据类保持不动（engine.py 不在本域改动范围），版本状态由本模块托管。
"""
from __future__ import annotations
import json
import os
import re
import time
import uuid
from typing import Dict, List, Optional

from .engine import Workflow

_HISTORY_MAX = 20
_VSNP_RE = re.compile(r"\.v\d+\.json$")


class WorkflowStore:
    def __init__(self, dir_path: str):
        self.dir = dir_path
        os.makedirs(dir_path, exist_ok=True)
        os.makedirs(self._ver_dir(), exist_ok=True)

    # ---------- 版本 sidecar ----------
    def _ver_dir(self) -> str:
        return os.path.join(self.dir, "_versions")

    def _ver_path(self, wfid: str) -> str:
        return os.path.join(self._ver_dir(), wfid + ".json")

    def _load_ver(self, wfid: str) -> dict:
        p = self._ver_path(wfid)
        if not os.path.isfile(p):
            return {"v": 1, "history": []}
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"v": int(d.get("v") or 1), "history": d.get("history") or []}
        except (OSError, json.JSONDecodeError):
            return {"v": 1, "history": []}

    def _save_ver(self, wfid: str, ver: dict) -> None:
        os.makedirs(self._ver_dir(), exist_ok=True)
        with open(self._ver_path(wfid), "w", encoding="utf-8") as f:
            json.dump(ver, f, ensure_ascii=False, indent=2)

    def versions(self, wfid: str) -> List[dict]:
        """版本列表（含当前版本；快照内容见 get_version）。"""
        ver = self._load_ver(wfid)
        return [dict(h) for h in (ver.get("history") or [])]

    def get_version(self, wfid: str, v: int) -> Optional[dict]:
        """取某历史版本内容（snapshot 文件）；返回 None 表示不存在。"""
        p = os.path.join(self.dir, f"{wfid}.v{v}.json")
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    # ---------- 基础 CRUD ----------
    def _path(self, wfid: str) -> str:
        return os.path.join(self.dir, wfid + ".json")

    def create(self, name: str = "未命名工作流") -> Workflow:
        wfid = uuid.uuid4().hex[:12]
        wf = Workflow(id=wfid, name=name, created_at=time.time(), updated_at=time.time())
        self.save(wf)
        return wf

    @staticmethod
    def _content_sig(d: dict) -> str:
        """内容签名（name/nodes/edges）——仅内容变化才产生版本快照。"""
        return json.dumps({
            "name": d.get("name"),
            "nodes": d.get("nodes"),
            "edges": d.get("edges"),
        }, sort_keys=True, ensure_ascii=False)

    def save(self, wf: Workflow) -> None:
        wf.updated_at = time.time()
        p = self._path(wf.id)
        new = wf.to_dict()
        old = None
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old = json.load(f)
            except (OSError, json.JSONDecodeError):
                old = None
        if old is not None and self._content_sig(old) != self._content_sig(new):
            # 内容变化：快照旧版，推进版本号（保留最近 20 版，防膨胀）
            ver = self._load_ver(wf.id)
            history = list(ver.get("history") or [])
            snapshot_path = os.path.join(self.dir, f"{wf.id}.v{ver['v']}.json")
            try:
                with open(snapshot_path, "w", encoding="utf-8") as f:
                    json.dump(old, f, ensure_ascii=False, indent=2)
                history.append({"v": ver["v"], "saved_at": time.time(),
                                "summary": f"快照 v{ver['v']}（内容变更前）"})
                history = history[-_HISTORY_MAX:]
                self._save_ver(wf.id, {"v": ver["v"] + 1, "history": history})
            except OSError:
                pass  # 快照失败不阻断保存
        with open(p, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)

    def get(self, wfid: str) -> Optional[Workflow]:
        p = self._path(wfid)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return Workflow.from_dict(json.load(f))

    def list(self) -> List[Workflow]:
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if not fn.endswith(".json"):
                continue
            if _VSNP_RE.search(fn):
                continue  # 历史版本快照（<wfid>.v<N>.json）不入工作流清单
            wf = self.get(fn[:-5])
            if wf:
                out.append(wf)
        return out

    def delete(self, wfid: str) -> bool:
        p = self._path(wfid)
        removed = False
        if os.path.exists(p):
            os.remove(p)
            removed = True
        # 清理版本 sidecar + 快照
        try:
            vp = self._ver_path(wfid)
            if os.path.isfile(vp):
                os.remove(vp)
        except OSError:
            pass
        try:
            for fn in os.listdir(self.dir):
                if _VSNP_RE.search(fn) and fn.startswith(wfid + ".v"):
                    os.remove(os.path.join(self.dir, fn))
        except OSError:
            pass
        return removed
