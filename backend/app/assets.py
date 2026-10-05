"""资产库：中间产物落盘 + 元数据 + 指纹（供增量缓存）。"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class Asset:
    id: str
    kind: str            # VIDEO/AUDIO/IMAGE/SCRIPT/SEGMENTS/TIMELINE/JSON...
    path: str = ""       # 本地文件路径（媒体类）或空
    meta: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""  # 内容指纹（缓存键组成部分）
    node_id: str = ""    # 产出节点
    created_at: float = 0.0
    size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class AssetStore:
    def __init__(self, root_dir: str):
        self.root = root_dir
        self.files_dir = os.path.join(root_dir, "files")
        self.meta_dir = os.path.join(root_dir, "meta")
        os.makedirs(self.files_dir, exist_ok=True)
        os.makedirs(self.meta_dir, exist_ok=True)
        self._cache: Dict[str, Asset] = {}

    def _meta_path(self, aid: str) -> str:
        return os.path.join(self.meta_dir, aid + ".json")

    def save_asset(self, asset: Asset, payload: Any = None) -> Asset:
        """落盘资产。payload 为结构化数据时写入 json；媒体文件由调用方写 path。"""
        if not asset.id:
            asset.id = uuid.uuid4().hex[:16]
        if not asset.created_at:
            asset.created_at = time.time()
        if asset.path and os.path.exists(asset.path):
            asset.size = os.path.getsize(asset.path)
        if payload is not None and not asset.path:
            # 结构化资产存 json
            p = os.path.join(self.files_dir, asset.id + ".json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            asset.path = p
            asset.size = os.path.getsize(p)
        with open(self._meta_path(asset.id), "w", encoding="utf-8") as f:
            json.dump(asset.to_dict(), f, ensure_ascii=False, default=str)
        self._cache[asset.id] = asset
        return asset

    def get(self, aid: str) -> Optional[Asset]:
        if aid in self._cache:
            return self._cache[aid]
        mp = self._meta_path(aid)
        if not os.path.exists(mp):
            return None
        with open(mp, "r", encoding="utf-8") as f:
            data = json.load(f)
        asset = Asset(**data)
        self._cache[aid] = asset
        return asset

    def read_json(self, aid: str) -> Any:
        asset = self.get(aid)
        if asset is None or not asset.path:
            return None
        with open(asset.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _remove_files(self, aid: str, meta_path: str) -> None:
        """删除资产文件与元数据（容忍损坏/缺失，不依赖 get 解析）。"""
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            p = data.get("path", "") if isinstance(data, dict) else ""
        except Exception:
            p = ""
        if p and os.path.exists(p):
            try: os.remove(p)
            except OSError: pass
        try: os.remove(meta_path)
        except OSError: pass
        self._cache.pop(aid, None)

    def delete(self, aid: str) -> None:
        mp = self._meta_path(aid)
        if os.path.exists(mp):
            self._remove_files(aid, mp)

    def prune_orphans(self, keep: Optional[set] = None) -> int:
        """删除未被引用的孤儿资产（元数据 + 文件），返回删除数量。

        中间资产唯一的持久引用是引擎缓存索引（cache_index.json）；
        部分落盘失败/重跑覆盖留下的无引用资产文件即孤儿，统一在此清理。
        单个元数据损坏不阻止其余清理（delete 不依赖 get 解析）。
        """
        keep = keep or set()
        removed = 0
        for fname in os.listdir(self.meta_dir):
            if not fname.endswith(".json"):
                continue
            aid = fname[:-5]
            if aid in keep:
                continue
            self._remove_files(aid, self._meta_path(aid))
            removed += 1
        return removed


def file_fingerprint(path: str, chunk: int = 1 << 20) -> str:
    """文件内容指纹（前 8MB + 长度）。"""
    h = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block or size > 8 * (1 << 20):
                    break
                h.update(block)
                size += len(block)
    except OSError:
        pass
    h.update(str(os.path.getsize(path)).encode())
    return h.hexdigest()[:32]
