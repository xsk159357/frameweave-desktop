"""商城服务（M18 本地桩，与服务端商城同构；M19 云化）。

模型（对齐平台决策）：
- 条目 kind: node | workflow；official: 官方/用户；price: 积分定价（官方免费 0）
- 下载付费插件：扣 1 次永久可下；分成 20% 平台 / 80% 作者（积分进作者账号，不可提现）
- 网盘直链：发布者提供 download_url（百度/123 直链），本地桩也可内联 zip
- 缺节点引导：前端据 spec 缺失 type_id 查询商城（本服务提供）
存储：backend/data/market.json
"""
from __future__ import annotations
import json
import os
import shutil
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PLATFORM_SHARE = 0.2
AUTHOR_SHARE = 0.8


@dataclass
class MarketItem:
    id: str
    kind: str              # node | workflow
    title: str
    description: str = ""
    author: str = ""       # 作者账号（email）
    price: int = 0         # 积分（0 = 免费）
    official: bool = False
    download_url: str = ""
    version: str = "1.0.0"
    downloads: int = 0
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    source_zip: str = ""   # 本地桩：内联 zip 路径（download_url 为空时用）


class MarketService:
    """商城本地桩。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "market.json")
        self._items: Dict[str, dict] = {}
        os.makedirs(data_dir, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._items = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._items = {}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)

    def list_items(self, q: str = "", kind: str = "", official: str = "") -> List[dict]:
        out = []
        for it in self._items.values():
            if kind and it.get("kind") != kind:
                continue
            if official in ("true", "1") and not it.get("official"):
                continue
            if official in ("false", "0") and it.get("official"):
                continue
            if q:
                hay = (it.get("title", "") + it.get("description", "") + " ".join(it.get("tags", []))).lower()
                if q.lower() not in hay:
                    continue
            out.append(it)
        out.sort(key=lambda x: (-(x.get("official") or False), -x.get("downloads", 0), -x.get("created_at", 0)))
        return out

    def get(self, item_id: str) -> Optional[dict]:
        return self._items.get(item_id)

    def publish(self, kind: str, title: str, description: str, author: str,
                price: int, download_url: str, tags: List[str], official: bool = False,
                source_zip: str = "") -> dict:
        """发布条目（作者上传：节点/工作流 + 网盘直链登记）。"""
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind, "title": title, "description": description,
            "author": author, "price": max(0, int(price)), "official": official,
            "download_url": download_url, "version": "1.0.0",
            "downloads": 0, "tags": tags or [], "created_at": time.time(),
            "source_zip": source_zip,
        }
        self._items[item["id"]] = item
        self._save()
        return item

    def install(self, item_id: str, token: str, license_svc, user_nodes_dir: str) -> dict:
        """下载并安装条目（仅 node 类可安装；付费扣积分，20/80 分成进作者）。"""
        it = self._items.get(item_id)
        if it is None:
            return {"ok": False, "message": "条目不存在"}
        if it.get("kind") != "node":
            return {"ok": False, "message": "仅节点类条目可安装到本机"}
        # 账号鉴权 + 积分扣费
        account = None
        if token:
            for s in license_svc._sessions.values():
                if s.get("token") == token:
                    account = s
                    break
        price = int(it.get("price") or 0)
        if price > 0:
            if account is None:
                return {"ok": False, "message": "请先登录后再下载付费节点"}
            if int(account.get("credits") or 0) < price:
                return {"ok": False, "message": f"积分不足：需要 {price}，当前 {account.get('credits', 0)}"}
            account["credits"] = int(account.get("credits") or 0) - price
            # 20/80 分成：作者积分入账（作者可能是本地桩账号）
            author_email = it.get("author", "")
            for s in license_svc._sessions.values():
                if s.get("email", "").lower() == author_email.lower():
                    s["credits"] = int(s.get("credits") or 0) + int(price * AUTHOR_SHARE)
                    break
            license_svc._save()
        # 拉取 zip：优先 download_url，其次内联 source_zip
        zip_path = self._fetch_zip(it, user_nodes_dir)
        if zip_path is None:
            if price > 0:
                account["credits"] = int(account.get("credits") or 0) + price  # 回滚
                license_svc._save()
            return {"ok": False, "message": "下载失败：网盘直链不可用"}
        # 解压安装
        from . import declarative
        try:
            dest = declarative.install_zip(zip_path)
        except Exception as e:  # noqa: BLE001
            if price > 0:
                account["credits"] = int(account.get("credits") or 0) + price
                license_svc._save()
            try:
                if zip_path != it.get("source_zip"):
                    os.remove(zip_path)
            except OSError:
                pass
            return {"ok": False, "message": "安装失败: " + str(e)}
        finally:
            # 清理下载的临时 zip（内联 source_zip 不删）
            try:
                if zip_path != it.get("source_zip"):
                    os.remove(zip_path)
            except OSError:
                pass
        it["downloads"] = int(it.get("downloads") or 0) + 1
        self._save()
        return {"ok": True, "message": "安装成功，节点已可用", "installed_to": dest,
                "type_ids": [s.type_id for s in declarative.list_specs()]}

    def _fetch_zip(self, it: dict, user_nodes_dir: str) -> Optional[str]:
        if it.get("source_zip") and os.path.isfile(it["source_zip"]):
            return it["source_zip"]
        url = it.get("download_url", "")
        if not url:
            return None
        try:
            os.makedirs(user_nodes_dir, exist_ok=True)
            tmp = os.path.join(user_nodes_dir, "_dl_" + it["id"] + ".zip")
            req = urllib.request.Request(url, headers={"User-Agent": "frameweave/0.2.3"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            if not zipfile.is_zipfile(tmp):
                os.remove(tmp)
                return None
            return tmp
        except Exception:  # noqa: BLE001
            return None

    def authors(self) -> List[str]:
        return sorted({it.get("author", "") for it in self._items.values() if it.get("author")})
