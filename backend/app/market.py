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
import re
import shutil
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PLATFORM_SHARE = 0.2
AUTHOR_SHARE = 0.8


def _ver_tuple(v) -> tuple:
    """'0.2.12' -> (0, 2, 12)；仅取能解析成整数的数字段（用于 min_client_version 比较）。"""
    parts = []
    for seg in re.split(r"[._-]", str(v or "").strip()):
        if seg.isdigit():
            parts.append(int(seg))
        else:
            break
    return tuple(parts[:7])


def client_meets_min(client_version: str, min_version: str) -> bool:
    """客户端版本是否满足最低版本要求（空 min 表示不限制）。"""
    if not min_version:
        return True
    return _ver_tuple(client_version) >= _ver_tuple(min_version) if client_version else False


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
    type_id: str = ""          # 节点 type_id（缺节点引导 / 校验用）
    sha256: str = ""           # 插件包 sha256 摘要（客户端下载后校验）
    size: int = 0              # 插件包字节数
    min_client_version: str = ""  # 最低客户端版本（空=不限制）
    status: str = "active"     # active=上架 / disabled=下架
    downloads: int = 0
    installs: int = 0          # 安装回报计数
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
                source_zip: str = "", version: str = "1.0.0", type_id: str = "",
                sha256: str = "", size: int = 0, min_client_version: str = "",
                status: str = "active") -> dict:
        """发布条目（作者上传：节点/工作流 + GitHub Release/网盘 直链登记）。"""
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind, "title": title, "description": description,
            "author": author, "price": max(0, int(price)), "official": official,
            "download_url": download_url, "version": version or "1.0.0",
            "type_id": type_id, "sha256": sha256, "size": max(0, int(size or 0)),
            "min_client_version": min_client_version, "status": status or "active",
            "downloads": 0, "installs": 0, "tags": tags or [], "created_at": time.time(),
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

    def _find_session(self, license_svc, token: str) -> Optional[dict]:
        """按 token 找本地桩会话（与 install 内联逻辑一致，避免重复）。"""
        if not token:
            return None
        for s in license_svc._sessions.values():
            if s.get("token") == token:
                return s
        return None

    def download_auth(self, item_id: str, token: str, license_svc,
                      client_version: str = "") -> dict:
        """下载授权/记录接口。

        免费插件：直接授权并返回 GitHub Release 直链（服务器绝不代理文件）；
        付费插件：校验登录 + 积分，扣价并 20/80 分成，记录下载。
        """
        it = self._items.get(item_id)
        if it is None:
            return {"ok": False, "code": "item_not_found", "message": "条目不存在"}
        if it.get("status", "active") != "active":
            return {"ok": False, "code": "item_disabled", "message": "条目已下架"}
        if not client_meets_min(client_version, it.get("min_client_version", "")):
            return {"ok": False, "code": "client_version_too_old",
                    "message": "当前客户端版本过低，需要 %s 及以上" % it.get("min_client_version", ""),
                    "min_client_version": it.get("min_client_version", "")}
        account = self._find_session(license_svc, token)
        price = int(it.get("price") or 0)
        paid = price > 0
        if paid:
            if account is None:
                return {"ok": False, "code": "login_required", "message": "请先登录后再下载付费节点"}
            if int(account.get("credits") or 0) < price:
                return {"ok": False, "code": "insufficient_credits",
                        "message": f"积分不足：需要 {price}，当前 {account.get('credits', 0)}"}
            account["credits"] = int(account.get("credits") or 0) - price
            author_email = it.get("author", "")
            for s in license_svc._sessions.values():
                if s.get("email", "").lower() == author_email.lower():
                    s["credits"] = int(s.get("credits") or 0) + int(price * AUTHOR_SHARE)
                    break
            license_svc._save()
        it["downloads"] = int(it.get("downloads") or 0) + 1
        self._append_log("download", {
            "item_id": item_id, "item_kind": it.get("kind", ""),
            "email": (account or {}).get("email", ""), "paid": paid, "price": price,
            "client_version": client_version, "ts": time.time(),
        })
        self._save()
        filename = (it.get("type_id") or item_id).replace("/", "_") or "plugin"
        return {
            "ok": True, "status": "authorized",
            "item_id": item_id, "title": it.get("title", ""),
            "version": it.get("version", "1.0.0"), "kind": it.get("kind", ""),
            "type_id": it.get("type_id", ""), "price": price,
            "download": {
                "filename": filename + ".zip",
                "size": int(it.get("size") or 0),
                "sha256": it.get("sha256", "") or "",
                "url": it.get("download_url", ""),
                "direct": True,
            },
            "direct": True,
            "min_client_version": it.get("min_client_version", ""),
            "client_version_ok": True,
        }

    def install_report(self, item_id: str, token: str = "", client_version: str = "",
                       result: str = "ok", error: str = "") -> dict:
        """安装回报接口：客户端装完/失败后回报，记录安装计数与日志。"""
        it = self._items.get(item_id)
        if it is None:
            return {"ok": False, "code": "item_not_found", "message": "条目不存在"}
        ok = (result or "ok").lower() in ("ok", "success", "installed")
        if ok:
            it["installs"] = int(it.get("installs") or 0) + 1
            self._save()
        self._append_log("install", {
            "item_id": item_id, "result": result or "ok", "error": error or "",
            "client_version": client_version, "ts": time.time(),
        })
        return {"ok": True, "item_id": item_id, "recorded": True,
                "install_count": int(it.get("installs") or 0)}

    def _append_log(self, kind: str, entry: dict) -> None:
        """追加下载/安装流水到 backend/data/market_log.json（保留操作留痕）。"""
        try:
            logp = os.path.join(self.data_dir, "market_log.json")
            rows = []
            if os.path.exists(logp):
                with open(logp, "r", encoding="utf-8") as f:
                    rows = json.load(f)
            rows.append({**entry, "kind": kind})
            rows = rows[-2000:]
            with open(logp, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
        except (OSError, json.JSONDecodeError):
            pass

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
