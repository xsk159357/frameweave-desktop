"""商城服务（M18 本地桩，与服务端商城同构；M19 云化）。

模型（对齐平台决策）：
- 条目 kind: node | workflow；official: 官方/用户；price: 积分定价（官方免费 0）
- 下载付费插件：扣 1 次永久可下；分成 20% 平台 / 80% 作者（积分进作者账号，不可提现）
- 网盘直链：发布者提供 download_url（百度/123 直链），本地桩也可内联 zip
- 缺节点引导：前端据 spec 缺失 type_id 查询商城（本服务提供）
存储：backend/data/market.json
"""
from __future__ import annotations
import hashlib
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

from .worker import limits

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

    def source_zip_path(self, item_id: str) -> Optional[str]:
        """本地桩：返回条目内联 zip 的磁盘路径（GET /api/market/items/{id}/file 用）。

        仅本地桩数据模型有的字段；云端条目没有 source_zip，永远返回 None。
        """
        it = self._items.get(item_id)
        if it is None:
            return None
        zp = it.get("source_zip") or ""
        if zp and os.path.isfile(zp):
            return zp
        return None

    def publish(self, kind: str, title: str, description: str, author: str,
                price: int, download_url: str, tags: List[str], official: bool = False,
                source_zip: str = "", version: str = "1.0.0", type_id: str = "",
                sha256: str = "", size: int = 0, min_client_version: str = "",
                status: str = "active", publisher_pubkey: str = "",
                archive_signature: str = "") -> dict:
        """发布条目（作者上传：节点/工作流 + GitHub Release/网盘 直链登记）。

        P3 签名链：publisher_pubkey = 发布者 Ed25519 公钥 base64（L1 市场信任锚，
        客户端安装时按此验签）；archive_signature = 对插件 zip 的 archive 签名（可选，
        与条目 pubkey 配套，加强分发链完整性）。
        """
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind, "title": title, "description": description,
            "author": author, "price": max(0, int(price)), "official": official,
            "download_url": download_url, "version": version or "1.0.0",
            "type_id": type_id, "sha256": sha256, "size": max(0, int(size or 0)),
            "min_client_version": min_client_version, "status": status or "active",
            "downloads": 0, "installs": 0, "tags": tags or [], "created_at": time.time(),
            "source_zip": source_zip,
            "publisher_pubkey": publisher_pubkey or "",
            "archive_signature": archive_signature or "",
        }
        self._items[item["id"]] = item
        self._save()
        return item

    def install(self, item_id: str, token: str, user_nodes_dir: str) -> dict:
        """安装条目（纯云化）：先经云端 download-auth 授权（返回直链+sha256），
        再本地下载解压安装；云端不可达/未授权 → 明确报错，绝不本地过关。

        - kind=node：解压插件包到 user_nodes/ 并重扫（declarative.install_zip）。
        - kind=workflow：校验 zip 内含 workflow.json，解压注册到模板注册表
          （templates.install_workflow_zip，templates_dir 由 server 启动时设置）。
          插件缺失引导：返回 missing_type_ids（模板声明的 type_id 未安装）。
        """
        from . import cloud_gateway
        if not cloud_gateway.cloud_enabled():
            return {"ok": False, "code": "cloud_not_configured",
                    "message": "需要云端授权：云端授权服务器未配置，请检查网络后重试"}
        auth = cloud_gateway.cloud_market_download_auth(item_id, token, "")
        if auth is None:
            return {"ok": False, "code": "cloud_unavailable",
                    "message": "无法连接云端授权服务器，请联网后重试"}
        if not auth.get("ok"):
            return auth
        dl = (auth.get("download") or {})
        dl_url = str(dl.get("url") or "")
        if not dl_url:
            return {"ok": False, "code": "download_failed", "message": "云端未返回下载直链"}
        it = self._items.get(item_id) or {
            "id": item_id, "kind": str(auth.get("kind") or dl.get("kind") or "node"),
            "title": str(auth.get("title") or dl.get("title") or item_id),
            "type_id": str(auth.get("type_id") or dl.get("type_id") or ""),
            "price": int(auth.get("price") or 0),
            "download_url": dl_url, "sha256": str(dl.get("sha256") or ""),
            "size": int(dl.get("size") or 0), "status": "active",
            "source_zip": "", "publisher_pubkey": "", "archive_signature": "",
        }
        # 拉取 zip：优先云端直链（内联 source_zip 仅本地桩遗留兼容）
        zip_path = self._fetch_zip(it, user_nodes_dir)
        if zip_path is None:
            return {"ok": False, "code": "download_failed", "message": "下载失败：云端直链不可用"}

        try:
            if it.get("kind") == "workflow":
                from . import templates
                try:
                    meta = templates.install_workflow_zip(
                        zip_path, tpl_id=(it.get("type_id") or "").strip(),
                        tags=it.get("tags") or [], source="market")
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "message": "安装失败: " + str(e)}
                # 插件缺失引导：模板所需 type_id 未安装时给出清单
                from .registry import list_specs as _list_specs
                try:
                    installed_types = sorted({s.type_id for s in _list_specs()})
                except Exception:  # noqa: BLE001
                    installed_types = []
                missing = templates.missing_type_ids(meta["tpl_id"], installed_types)
                return {"ok": True, "message": "模板安装成功，可从模板列表创建工作流",
                        "kind": "workflow", "tpl_id": meta["tpl_id"],
                        "installed_to": os.path.join(templates.TEMPLATES_DIR, meta["tpl_id"]),
                        "missing_type_ids": missing,
                        "has_missing": bool(missing)}
            # kind=node：解压安装（原逻辑）。P3 签名链：条目绑定 publisher_pubkey 时
            # 先验 zip archive 签名（若有），再传给 declarative 按该公钥验 manifest 签名。
            from . import declarative
            from . import signatures as sg
            pubkey = (it.get("publisher_pubkey") or "").strip() or None
            archive_sig = (it.get("archive_signature") or "").strip() or None
            try:
                # 归档级验签（zip 整体）：有条目 archive_signature 且可验 → 失败拒绝
                if pubkey and archive_sig:
                    if not sg.verify_archive_signature_b64(zip_path, archive_sig, pubkey):
                        return {"ok": False, "code": limits.ERR_SIGNATURE_VERIFY,
                                "message": "安装失败：插件包归档签名校验失败（zip 被篡改或签名不匹配）"}
                dest = declarative.install_zip(zip_path, trust_pubkey=pubkey,
                                               required_sig=False)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "code": "install_failed", "message": "安装失败: " + str(e)}
            # 纯云化：服务端安装成功后向云端回报 install-report（云端记录安装计数）
            try:
                from . import cloud_gateway as _cg
                if _cg.cloud_enabled():
                    _cg.cloud_market_install_report(item_id, token, "", "ok", "")
            except Exception:  # noqa: BLE001 —— 回报失败不阻断安装成功语义
                pass
            return {"ok": True, "message": "安装成功，节点已可用", "installed_to": dest,
                    "type_ids": [s.type_id for s in declarative.list_specs()]}
        finally:
            # 清理下载的临时 zip（内联 source_zip 不删）
            try:
                if zip_path != it.get("source_zip"):
                    os.remove(zip_path)
            except OSError:
                pass

    def install_report(self, item_id: str, token: str = "", client_version: str = "",
                       result: str = "ok", error: str = "") -> dict:
        """安装回报接口（纯云化）：转发云端记录；云端不可达 → 明确报错。"""
        from . import cloud_gateway
        if not cloud_gateway.cloud_enabled():
            return {"ok": False, "code": "cloud_not_configured",
                    "message": "需要云端授权：云端授权服务器未配置，请检查网络后重试"}
        resp = cloud_gateway.cloud_market_install_report(
            item_id, token, client_version, result, error)
        if resp is None:
            return {"ok": False, "code": "cloud_unavailable",
                    "message": "无法连接云端授权服务器，请联网后重试"}
        return resp

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
        """流式下载插件 zip（大插件支持 t2）。

        块读落盘（ZIP_BLOCK 流式，2GB 级包不整包载入内存）+ 体积上限
        （唯一引用 worker/limits.UPLOAD_MAX_BYTES）+ 发布表单登记 sha256 时校验摘要。
        失败清理临时文件并返回 None（调用方回滚扣费）。
        """
        if it.get("source_zip") and os.path.isfile(it["source_zip"]):
            return it["source_zip"]
        url = it.get("download_url", "")
        if not url:
            return None
        tmp = ""
        try:
            os.makedirs(user_nodes_dir, exist_ok=True)
            tmp = os.path.join(user_nodes_dir, "_dl_" + it["id"] + ".zip")
            sha_expected = (it.get("sha256") or "").strip().lower()
            sha = hashlib.sha256()
            received = 0
            req = urllib.request.Request(url, headers={"User-Agent": "frameweave/0.2.3"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(limits.ZIP_BLOCK)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > limits.UPLOAD_MAX_BYTES:
                        os.remove(tmp)
                        return None
                    sha.update(chunk)
                    f.write(chunk)
            if not zipfile.is_zipfile(tmp):
                os.remove(tmp)
                return None
            if sha_expected and sha.hexdigest() != sha_expected:
                os.remove(tmp)
                return None
            return tmp
        except Exception:  # noqa: BLE001
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return None

    def authors(self) -> List[str]:
        return sorted({it.get("author", "") for it in self._items.values() if it.get("author")})
