"""工作流存储：JSON 文件持久化（M0 简化版，后续可换 SQLite）。"""
from __future__ import annotations
import json
import os
import time
import uuid
from typing import Dict, List, Optional

from .engine import Workflow


class WorkflowStore:
    def __init__(self, dir_path: str):
        self.dir = dir_path
        os.makedirs(dir_path, exist_ok=True)

    def _path(self, wfid: str) -> str:
        return os.path.join(self.dir, wfid + ".json")

    def create(self, name: str = "未命名工作流") -> Workflow:
        wfid = uuid.uuid4().hex[:12]
        wf = Workflow(id=wfid, name=name, created_at=time.time(), updated_at=time.time())
        self.save(wf)
        return wf

    def save(self, wf: Workflow) -> None:
        wf.updated_at = time.time()
        with open(self._path(wf.id), "w", encoding="utf-8") as f:
            json.dump(wf.to_dict(), f, ensure_ascii=False, indent=2)

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
            wf = self.get(fn[:-5])
            if wf:
                out.append(wf)
        return out

    def delete(self, wfid: str) -> bool:
        p = self._path(wfid)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
