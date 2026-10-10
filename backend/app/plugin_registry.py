"""插件生命周期注册表（P1，t3）：plugins.json 持久化状态机 + 安装事务。

状态机（absent / active / disabled / quarantined / updating）：
- absent      记录存在但包目录缺失（外部删除/未完成安装残留）
- active      正常加载（scan 注册，节点可用）
- disabled    目录移入 .disabled/，scan 不加载（节点从画布消失，可启用恢复）
- quarantined 包目录在位但 manifest 校验失败（坏包隔离；修复后启用或卸载）
- updating    升级事务进行中；异常中断由 sync() 兜底（可加载→active，否则回滚→隔离）

安装/升级事务核（install/update 共用）：
  staging（.staging/<uuid>/unpack，五上限流式解压，唯一引用 worker/limits.py）
  → manifest v2 强校验（G1-G10，app/plugin/manifest_v2.py，t2 校验门）
  → 备份 3 份（.backup/<pkg>/b1..b3 轮转，b1 最新）
  → 原子替换（同卷 os.replace）
  → scan 验证（无 scan 错误 + type_id 已注册）
  → 失败自动回滚旧版本目录并报显式错误码（plugin_update_failed，含"已自动回滚"说明）。

持久化：plugins.json（默认 backend/data/plugins.json，FRAMEWEAVE_PLUGINS_STATE 覆盖）。
每条记录：pkg / type_id / version / state / state_reason / installed_at / updated_at /
  last_error / backups 索引（slot→version+ts，与磁盘 .backup 槽位对齐）/
  metrics（installs/updates/uninstalls/disables/enables/rollbacks/healthchecks/failures）+
  全局 ops 环形流水（100 条，metrics 接口 recent_ops）。
审计：limits.audit（plugin_install/plugin_update/plugin_disable/...，与 DATA_DIR/audit_log.jsonl 汇合）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from .worker import limits
from .plugin import manifest_v2
from .plugin.errors import PluginManifestError
from . import declarative

# ---------------------------------------------------------------- 状态机
ABSENT = "absent"
ACTIVE = "active"
DISABLED = "disabled"
QUARANTINED = "quarantined"
UPDATING = "updating"
STATES = (ABSENT, ACTIVE, DISABLED, QUARANTINED, UPDATING)

BACKUP_SLOTS = ("b1", "b2", "b3")          # b1 = 最新备份（回滚目标）
BACKUP_ROOT = ".backup"
DISABLED_ROOT = ".disabled"
_RSVD = declarative._RESERVED_DIRS          # (".staging", ".backup", "_vendor", ".git")

# ---------------------------------------------------------------- 错误码
ERR_NOT_FOUND = "plugin_not_found"         # 404
ERR_BAD_STATE = "plugin_bad_state"         # 409：状态机不允许的迁移
ERR_DIR_MISMATCH = "plugin_dir_mismatch"   # 409：更新包顶层目录与已安装不一致
ERR_NO_BACKUP = "plugin_no_backup"         # 404：无可用备份
ERR_UPDATE_FAILED = "plugin_update_failed" # 409：升级失败（已自动回滚）
ERR_ROLLBACK_FAILED = "plugin_rollback_failed"  # 409：回滚失败
ERR_ENABLE_FAILED = "plugin_enable_failed"       # 422/409：启用失败（manifest 校验或 scan 失败）
ERR_DISABLE_FAILED = "plugin_disable_failed"     # 409：禁用失败
ERR_UNINSTALL_FAILED = "plugin_uninstall_failed" # 409：卸载失败

_OP_LOG_MAX = 100


class PluginLifecycleError(Exception):
    """生命周期操作失败（显式错误码；路由异常处理器统一转为 {ok,code,message}）。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _default_state_path() -> str:
    env = os.environ.get("FRAMEWEAVE_PLUGINS_STATE")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))          # backend/app
    return os.path.join(os.path.dirname(here), "data", "plugins.json")


class PluginRegistry:
    """插件生命周期注册表（持久化状态机 + 安装事务）。"""

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = state_path or _default_state_path()
        self._lock = threading.Lock()
        self._records: Dict[str, dict] = {}   # pkg 目录名 -> 记录
        self._ops: List[dict] = []            # 全局操作流水（环形）
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            self._records = dict(data.get("packages") or {})
            self._ops = list((data.get("ops") or [])[-_OP_LOG_MAX:])
        except (OSError, ValueError):
            self._records = {}
            self._ops = []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"packages": self._records, "ops": self._ops},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)      # 原子持久化（同卷）

    # ---------------- 路径工具 ----------------
    @classmethod
    def _user_nodes(cls) -> str:
        return declarative.USER_NODES_DIR

    def _live_dir(self, pkg: str) -> str:
        return os.path.join(self._user_nodes(), pkg)

    def _disabled_dir(self, pkg: str) -> str:
        return os.path.join(self._user_nodes(), DISABLED_ROOT, pkg)

    def _backup_root(self, pkg: str) -> str:
        return os.path.join(self._user_nodes(), BACKUP_ROOT, pkg)

    def _slot_dir(self, pkg: str, slot: str) -> str:
        return os.path.join(self._backup_root(pkg), slot)

    @classmethod
    def _rmtree(cls, path: str) -> None:
        declarative._rmtree_safe(path)

    @staticmethod
    def _manifest_of(pkg_dir: str) -> Optional[dict]:
        try:
            with open(os.path.join(pkg_dir, "manifest.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _type_id_at(self, pkg_dir: str) -> str:
        m = self._manifest_of(pkg_dir)
        return str((m or {}).get("type_id") or "")

    def _version_at(self, pkg_dir: str) -> str:
        m = self._manifest_of(pkg_dir)
        return str((m or {}).get("version") or "")

    # ---------------- 记录 ----------------
    @staticmethod
    def _new_record(pkg: str, type_id: str, version: str,
                    state: str = ACTIVE, reason: str = "") -> dict:
        now = time.time()
        return {
            "pkg": pkg, "type_id": type_id, "version": version,
            "state": state, "state_reason": reason,
            "installed_at": now, "updated_at": now, "last_error": "",
            "backups": [],
            "metrics": {k: 0 for k in (
                "installs", "updates", "uninstalls", "disables", "enables",
                "rollbacks", "healthchecks", "failures")},
            "last_op": "", "last_op_at": 0.0, "last_op_ok": True,
        }

    def get_record(self, pkg: str) -> Optional[dict]:
        """按 pkg 参数找记录（目录名优先，其次 type_id）。"""
        if pkg in self._records:
            return self._records[pkg]
        for rec in self._records.values():
            if rec.get("type_id") == pkg:
                return rec
        return None

    def resolve(self, pkg: str) -> dict:
        rec = self.get_record(pkg)
        if rec is None:
            raise PluginLifecycleError(ERR_NOT_FOUND, f"插件不存在: {pkg}", 404)
        return rec

    def _record_op(self, op: str, pkg: str, type_id: str, version: Any,
                   ok: bool, note: str = "") -> None:
        self._ops.append({"op": op, "pkg": pkg, "type_id": type_id or "",
                          "version": str(version or ""), "ok": bool(ok),
                          "note": note, "ts": time.time()})
        if len(self._ops) > _OP_LOG_MAX:
            del self._ops[:len(self._ops) - _OP_LOG_MAX]

    @staticmethod
    def _quarantine(rec: dict, err: dict) -> None:
        code = str(err.get("code") or limits.ERR_MANIFEST)
        msg = str(err.get("message") or "manifest 校验失败")
        rec["state"] = QUARANTINED
        rec["state_reason"] = msg
        rec["last_error"] = f"{code}: {msg}"

    def _read_backup_index(self, pkg: str) -> List[dict]:
        """按磁盘 .backup/<pkg>/b1..b3 实际内容重建索引（防漂移）。"""
        out = []
        for slot in BACKUP_SLOTS:
            d = self._slot_dir(pkg, slot)
            if os.path.isdir(d):
                out.append({"slot": slot, "version": self._version_at(d),
                            "ts": os.path.getmtime(d), "dir": d})
        return out

    # ---------------- sync：磁盘 ↔ 注册表 对齐 ----------------
    def sync(self) -> Dict[str, Any]:
        """对齐磁盘与注册表：收养新目录、缺失目录标 absent、坏包隔离、updating 兜底。"""
        with self._lock:
            declarative.scan()
            errs = {e.get("pkg"): e for e in declarative.scan_errors()}
            loaded = {s.type_id for s in declarative.list_specs()}
            root = self._user_nodes()
            present: Dict[str, str] = {}      # 根目录名 -> type_id（含 manifest 的）
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    if name.startswith(".") or name in _RSVD:
                        continue
                    mpath = os.path.join(root, name, "manifest.json")
                    if os.path.isfile(mpath):
                        m = self._manifest_of(os.path.join(root, name))
                        present[name] = str((m or {}).get("type_id") or "")
            # 1) 收养：根目录有目录但无记录（含商城 install_zip 直装、外部拷贝）
            for name, tid in present.items():
                if name in self._records:
                    continue
                rec = self._new_record(name, tid or name,
                                       self._version_at(self._live_dir(name)))
                if name in errs:
                    self._quarantine(rec, errs[name])
                else:
                    rec["state"] = ACTIVE
                self._records[name] = rec
                limits.audit("plugin_adopted", pkg=name, type_id=tid or "",
                             version=rec["version"], ok=True, code="ok")
            # 1b) 收养：.disabled 有目录但无记录
            dk_root = os.path.join(root, DISABLED_ROOT)
            if os.path.isdir(dk_root):
                for name in sorted(os.listdir(dk_root)):
                    if name.startswith(".") or name in self._records:
                        continue
                    d_dir = self._disabled_dir(name)
                    rec = self._new_record(name, self._type_id_at(d_dir) or name,
                                           self._version_at(d_dir),
                                           DISABLED, "目录位于 .disabled/")
                    self._records[name] = rec
            # 2) 记录目录缺失 → absent；disabled 目录存在 → disabled（UPDATING 交给步骤 3 兜底）
            for name, rec in list(self._records.items()):
                if rec.get("state") == UPDATING:
                    continue
                has_live = os.path.isdir(self._live_dir(name))
                has_disabled = os.path.isdir(self._disabled_dir(name))
                st = rec.get("state")
                if not has_live and not has_disabled:
                    rec["state"] = ABSENT
                    rec["state_reason"] = "包目录缺失"
                elif has_live and st == ABSENT:
                    if name in errs:
                        self._quarantine(rec, errs[name])
                    else:
                        rec["state"] = ACTIVE
                        rec["state_reason"] = ""
                elif has_live and st == ACTIVE and name in errs:
                    self._quarantine(rec, errs[name])
                elif has_disabled and st != DISABLED:
                    rec["state"] = DISABLED
                    rec["state_reason"] = "目录位于 .disabled/（外部移动）"
            # 3) updating 兜底：可加载 → active；否则回滚 b1；再否则隔离
            for name, rec in list(self._records.items()):
                if rec.get("state") != UPDATING:
                    continue
                if os.path.isdir(self._live_dir(name)):
                    if name not in errs and rec.get("type_id") in loaded:
                        rec["state"] = ACTIVE
                        rec["state_reason"] = "中断的升级已恢复为 active"
                    else:
                        restored = self._restore_newest_lockless(name)
                        if restored is None:
                            self._quarantine(rec, errs.get(name) or
                                             {"code": ERR_UPDATE_FAILED,
                                              "message": "升级中断且无法回滚"})
                        else:
                            rec["state"] = ACTIVE
                            rec["state_reason"] = "中断的升级已回滚"
                            rec["version"] = restored
                            rec["backups"] = self._read_backup_index(name)
                else:
                    self._quarantine(rec, {"code": ERR_UPDATE_FAILED,
                                           "message": "升级中断且包目录缺失"})
            self._save()
            return self._snapshot_lockless()

    # ---------------- 事务核 ----------------
    @classmethod
    def _extract_and_validate(cls, zip_path: str) -> Tuple[str, str, str, str, str]:
        """staging 解压（五上限复用 declarative._extract_zip_safe）
        → 单一顶层目录 → manifest v2 强校验（G1-G10，t2 校验门）。
        返回 (staging_unpack_pkg_dir, dir_name, type_id, version, staging_root)。
        失败抛 PluginInstallError/PluginManifestError（显式错误码）并清理 staging 无残留。
        """
        if not os.path.isfile(zip_path):
            raise limits.PluginInstallError(limits.ERR_NOT_ZIP, "zip 文件不存在")
        if not zipfile.is_zipfile(zip_path):
            raise limits.PluginInstallError(limits.ERR_NOT_ZIP, "不是有效的 zip 插件包")
        root = os.path.abspath(declarative.USER_NODES_DIR)
        os.makedirs(root, exist_ok=True)
        staging_root = os.path.join(root, ".staging")
        os.makedirs(staging_root, exist_ok=True)
        staging = os.path.join(staging_root, uuid.uuid4().hex)
        unpack = os.path.join(staging, "unpack")
        os.makedirs(unpack, exist_ok=True)
        try:
            declarative._extract_zip_safe(zip_path, unpack, os.path.getsize(zip_path))
            pkg_dir = declarative._resolve_package_dir(unpack)
            result = manifest_v2.validate_manifest_file(pkg_dir)
            norm = result.manifest
            type_id = str(norm.get("type_id") or "")
            version = str(norm.get("version") or "1.0.0")
            dir_name = os.path.basename(pkg_dir)
            return pkg_dir, dir_name, type_id, version, staging
        except Exception:
            declarative._rmtree_safe(staging)
            raise

    def _rotate_backup(self, pkg: str) -> None:
        """替换前轮转：b2→b3（丢最旧）、b1→b2、live→b1。调用后 live 路径空置。"""
        root = self._backup_root(pkg)
        os.makedirs(root, exist_ok=True)
        for i in range(len(BACKUP_SLOTS) - 1, 0, -1):       # i = 2(b2←b1), 1(b1←live)
            src = self._slot_dir(pkg, BACKUP_SLOTS[i - 1])
            dst = self._slot_dir(pkg, BACKUP_SLOTS[i])
            if os.path.exists(dst):
                self._rmtree(dst)
            if os.path.isdir(src):
                os.rename(src, dst)
        b1 = self._slot_dir(pkg, "b1")
        if os.path.exists(b1):
            self._rmtree(b1)
        os.rename(self._live_dir(pkg), b1)                  # live 必须存在
        declarative.scan()

    def _restore_newest_lockless(self, pkg: str) -> Optional[str]:
        """把 .backup/<pkg> 最新可用槽位恢复到 live，剩余槽位左移补齐。
        返回恢复版本；全部损坏/无备份返回 None。调用方保证 live 已被清空或不存在。
        """
        root = self._backup_root(pkg)
        if not os.path.isdir(root):
            return None
        live = self._live_dir(pkg)
        self._rmtree(live)
        restored: Optional[str] = None
        for slot in BACKUP_SLOTS:
            d = self._slot_dir(pkg, slot)
            if not os.path.isdir(d):
                continue
            if not os.listdir(d):
                self._rmtree(d)
                continue
            try:
                os.replace(d, live)
            except OSError:
                continue
            declarative.scan()
            errs = {e.get("pkg"): e for e in declarative.scan_errors()}
            loaded = {s.type_id for s in declarative.list_specs()}
            rec = self._records.get(pkg, {})
            if pkg not in errs and rec.get("type_id") in loaded:
                restored = self._version_at(live) or ""
                break
            self._rmtree(live)                              # 该槽位也坏 → 试更旧
        # 剩余槽位左移补齐（消费掉的槽位空出）
        remaining = [s for s in BACKUP_SLOTS
                     if os.path.isdir(self._slot_dir(pkg, s))]
        for pos, slot in enumerate(BACKUP_SLOTS[:len(remaining)]):
            if remaining[pos] == slot:
                continue
            if os.path.exists(self._slot_dir(pkg, slot)):
                self._rmtree(self._slot_dir(pkg, slot))
            try:
                os.replace(self._slot_dir(pkg, remaining[pos]),
                           self._slot_dir(pkg, slot))
            except OSError:
                pass
        return restored

    def _commit_replace(self, unpack_pkg_dir: str, pkg: str, type_id: str,
                        version: str, source: str, update: bool) -> dict:
        """事务核：备份 3 份 → 原子替换 → scan 验证 → 失败自动回滚。
        调用方已持锁。成功返回记录；失败抛 PluginLifecycleError（更新已回滚）。
        """
        live = self._live_dir(pkg)
        old_version = self._version_at(live) if update and os.path.isdir(live) else ""
        try:
            if update and os.path.isdir(live):
                self._rotate_backup(pkg)
            os.replace(unpack_pkg_dir, live)                # staging 同卷原子替换
        except OSError as e:
            if update:
                self._restore_newest_lockless(pkg)
            raise PluginLifecycleError(
                ERR_UPDATE_FAILED if update else limits.ERR_INSTALL,
                (f"原子替换失败，已自动回滚: {e}" if update else f"原子替换失败: {e}"),
                409 if update else 500)
        # scan 验证：无 scan 错误 + type_id 已注册（t2 校验门在 scan 中二次把关）
        declarative.scan()
        errs = {e.get("pkg"): e for e in declarative.scan_errors()}
        loaded = {s.type_id for s in declarative.list_specs()}
        probe = errs.get(pkg)
        if probe is not None:
            fail = f"scan 校验失败: {probe.get('message')} ({probe.get('code')})"
        elif type_id not in loaded:
            fail = f"替换后节点类型未注册: {type_id}"
        else:
            fail = None
        if fail is not None:
            rec = self._records.get(pkg)
            if update:
                restored = self._restore_newest_lockless(pkg)
                if restored is None:
                    self._rmtree(live)
                    if rec is not None:
                        self._quarantine(rec, {"code": ERR_UPDATE_FAILED,
                                               "message": fail + "，且回滚失败，已隔离"})
                        rec["metrics"]["failures"] = int(rec["metrics"].get("failures") or 0) + 1
                        rec["updated_at"] = time.time()
                        self._record_op("update", pkg, type_id, version, False, fail)
                        self._save()
                    raise PluginLifecycleError(
                        ERR_UPDATE_FAILED,
                        f"升级失败且无法回滚（已隔离）: {fail}", 409)
                if rec is not None:
                    rec["state"] = ACTIVE
                    rec["state_reason"] = ""
                    rec["version"] = restored
                    rec["last_error"] = fail
                    rec["backups"] = self._read_backup_index(pkg)
                    rec["metrics"]["failures"] = int(rec["metrics"].get("failures") or 0) + 1
                    rec["last_op"] = "update"; rec["last_op_at"] = time.time()
                    rec["last_op_ok"] = False
                    self._record_op("update", pkg, type_id, restored, False, fail)
                    limits.audit("plugin_update", pkg=pkg, type_id=type_id,
                                 version=version, ok=False, code="rollback_succeeded",
                                 error=fail, restored=restored)
                    self._save()
                raise PluginLifecycleError(
                    ERR_UPDATE_FAILED,
                    f"升级失败，已自动回滚到 {restored or old_version}: {fail}", 409)
            # 全新安装失败：清残留、审计
            self._rmtree(live)
            raise PluginLifecycleError(limits.ERR_INSTALL, fail, 422)
        # ---------------- 成功 ----------------
        rec = self._records.get(pkg)
        now = time.time()
        if rec is None:
            rec = self._new_record(pkg, type_id, version, ACTIVE)
            rec["installed_at"] = now
            rec["metrics"]["installs"] = 1
            self._records[pkg] = rec
        else:
            if not update:
                rec["type_id"] = type_id
                rec["metrics"]["installs"] = int(rec["metrics"].get("installs") or 0) + 1
            else:
                rec["metrics"]["updates"] = int(rec["metrics"].get("updates") or 0) + 1
            rec["type_id"] = type_id
            rec["version"] = version
            rec["state"] = ACTIVE
            rec["state_reason"] = ""
            rec["last_error"] = ""
            rec["updated_at"] = now
        rec["backups"] = self._read_backup_index(pkg)
        rec["last_op"] = "update" if update else "install"
        rec["last_op_at"] = now
        rec["last_op_ok"] = True
        limits.audit("plugin_update" if update else "plugin_install",
                     pkg=pkg, type_id=type_id, version=version,
                     ok=True, code="ok", source=source)
        self._record_op("update" if update else "install", pkg, type_id,
                        version, True, "")
        self._save()
        return rec

    # ---------------- 安装 / 升级 ----------------
    def install_zip(self, zip_path: str) -> Dict[str, Any]:
        """安装（全新或覆盖已存在同名包）：完整事务核（备份→替换→验证→回滚）。"""
        with self._lock:
            pkg_dir, dir_name, type_id, version, staging = self._extract_and_validate(zip_path)
            try:
                existing = self.get_record(dir_name) or (self.get_record(type_id)
                                                         if type_id else None)
                if existing is not None and existing["pkg"] != dir_name:
                    raise PluginLifecycleError(
                        ERR_DIR_MISMATCH,
                        f"安装包顶层目录 {dir_name!r} 与已安装目录 {existing['pkg']!r} 不一致", 409)
                update = existing is not None or os.path.isdir(self._live_dir(dir_name))
                if existing is None and update:
                    rec = self._new_record(dir_name, type_id,
                                           self._version_at(self._live_dir(dir_name)), ACTIVE)
                    self._records[dir_name] = rec
                rec = self._commit_replace(pkg_dir, dir_name, type_id, version,
                                           source="upload", update=update)
                return {"ok": True, "pkg": dir_name, "type_id": type_id,
                        "version": version, "state": rec["state"], "update": update,
                        "installed_to": self._live_dir(dir_name),
                        "backups": rec["backups"],
                        "nodes": [s.type_id for s in declarative.list_specs()]}
            finally:
                declarative._rmtree_safe(staging)

    def update_zip(self, pkg: str, zip_path: str) -> Dict[str, Any]:
        """升级：updating 持久化 → 事务核 → 失败自动回滚旧版本目录。"""
        with self._lock:
            rec = self.resolve(pkg)
            name = rec["pkg"]
            st = rec.get("state")
            if st in (ABSENT, UPDATING):
                raise PluginLifecycleError(ERR_BAD_STATE, f"当前状态 {st} 不允许升级", 409)
            if st == DISABLED:
                raise PluginLifecycleError(ERR_BAD_STATE, "插件已禁用，请先启用再升级", 409)
            pkg_dir, dir_name, type_id, version, staging = self._extract_and_validate(zip_path)
            try:
                if dir_name != name:
                    raise PluginLifecycleError(
                        ERR_DIR_MISMATCH,
                        f"更新包顶层目录 {dir_name!r} 与已安装目录 {name!r} 不一致", 409)
                old_tid = rec.get("type_id")
                if old_tid and type_id != old_tid:
                    raise PluginLifecycleError(
                        ERR_BAD_STATE,
                        f"更新包 type_id {type_id!r} 与已安装 {old_tid!r} 不一致", 409)
                old_version = rec.get("version")
                rec["state"] = UPDATING
                rec["updated_at"] = time.time()
                self._save()                                 # sync 可兜底恢复
                try:
                    rec2 = self._commit_replace(pkg_dir, name, type_id, version,
                                                source="upload", update=True)
                except PluginLifecycleError:
                    # 事务核已回滚并写失败态；此处仅兜底上次写入（防御性重写）
                    final = self._records.get(name)
                    if final is not None:
                        final["last_op"] = "update"
                        final["last_op_at"] = time.time()
                        final["last_op_ok"] = False
                        self._save()
                    raise
                return {"ok": True, "pkg": name, "type_id": type_id,
                        "version": version, "previous_version": old_version,
                        "state": rec2["state"], "installed_to": self._live_dir(name),
                        "backups": rec2["backups"],
                        "nodes": [s.type_id for s in declarative.list_specs()]}
            finally:
                declarative._rmtree_safe(staging)

    def rollback(self, pkg: str) -> Dict[str, Any]:
        """回滚到最新备份（b1 优先，依次尝试更旧槽位）。"""
        with self._lock:
            rec = self.resolve(pkg)
            name = rec["pkg"]
            st = rec.get("state")
            if st in (ABSENT, UPDATING):
                raise PluginLifecycleError(ERR_BAD_STATE, f"当前状态 {st} 不允许回滚", 409)
            if not any(os.path.isdir(self._slot_dir(name, s)) for s in BACKUP_SLOTS):
                raise PluginLifecycleError(ERR_NO_BACKUP, "没有可用备份（该插件从未升级过）", 404)
            old_version = rec.get("version")
            live = self._live_dir(name)
            trash = ""
            if os.path.isdir(live):
                trash = os.path.join(self._user_nodes(), ".staging",
                                     "rollback_" + uuid.uuid4().hex)
                try:
                    os.makedirs(os.path.dirname(trash), exist_ok=True)
                    os.rename(live, trash)
                except OSError as e:
                    raise PluginLifecycleError(ERR_ROLLBACK_FAILED,
                                               f"无法暂存当前版本: {e}", 409)
            restored = self._restore_newest_lockless(name)
            if restored is None:
                if trash and os.path.isdir(trash):
                    try:
                        os.rename(trash, live)
                    except OSError:
                        pass
                raise PluginLifecycleError(ERR_ROLLBACK_FAILED,
                                           "回滚失败：备份不可用或全部损坏", 409)
            if trash and os.path.isdir(trash):
                self._rmtree(trash)
            rec["version"] = restored
            rec["updated_at"] = time.time()
            rec["state"] = ACTIVE
            rec["state_reason"] = ""
            rec["last_error"] = ""
            rec["metrics"]["rollbacks"] = int(rec["metrics"].get("rollbacks") or 0) + 1
            rec["backups"] = self._read_backup_index(name)
            rec["last_op"] = "rollback"; rec["last_op_at"] = time.time()
            rec["last_op_ok"] = True
            limits.audit("plugin_rollback", pkg=name, type_id=rec.get("type_id"),
                         version=restored, previous=old_version, ok=True, code="ok")
            self._record_op("rollback", name, rec.get("type_id"), restored, True, "")
            self._save()
            return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                    "state": ACTIVE, "version": restored, "previous_version": old_version}

    # ---------------- 禁用 / 启用 ----------------
    def disable(self, pkg: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.resolve(pkg)
            name = rec["pkg"]
            st = rec.get("state")
            if st in (ABSENT, UPDATING):
                raise PluginLifecycleError(ERR_BAD_STATE, f"当前状态 {st} 不允许禁用", 409)
            if st == DISABLED:
                return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                        "state": DISABLED, "version": rec.get("version"), "already": True}
            live = self._live_dir(name)
            if not os.path.isdir(live):
                rec["state"] = DISABLED
                rec["updated_at"] = time.time()
                rec["last_op"] = "disable"; rec["last_op_at"] = time.time()
                rec["last_op_ok"] = True
                self._save()
                return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                        "state": DISABLED, "version": rec.get("version"), "already": True}
            dk_root = os.path.join(self._user_nodes(), DISABLED_ROOT)
            os.makedirs(dk_root, exist_ok=True)
            target = self._disabled_dir(name)
            if os.path.exists(target):
                self._rmtree(target)
            try:
                os.rename(live, target)
            except OSError as e:
                raise PluginLifecycleError(ERR_DISABLE_FAILED, f"禁用失败（目录移动失败）: {e}", 409)
            declarative.scan()
            loaded = {s.type_id for s in declarative.list_specs()}
            if rec.get("type_id") in loaded:
                try:
                    os.rename(target, live)
                except OSError:
                    pass
                raise PluginLifecycleError(ERR_DISABLE_FAILED, "禁用失败：节点仍被加载", 409)
            rec["state"] = DISABLED
            rec["state_reason"] = ""
            rec["updated_at"] = time.time()
            rec["metrics"]["disables"] = int(rec["metrics"].get("disables") or 0) + 1
            rec["last_op"] = "disable"; rec["last_op_at"] = time.time()
            rec["last_op_ok"] = True
            limits.audit("plugin_disable", pkg=name, type_id=rec.get("type_id"),
                         version=rec.get("version"), ok=True, code="ok")
            self._record_op("disable", name, rec.get("type_id"), rec.get("version"), True, "")
            self._save()
            return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                    "state": DISABLED, "version": rec.get("version")}

    def enable(self, pkg: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.resolve(pkg)
            name = rec["pkg"]
            st = rec.get("state")
            if st == ACTIVE:
                return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                        "state": ACTIVE, "version": rec.get("version"), "already": True}
            if st == UPDATING:
                raise PluginLifecycleError(ERR_BAD_STATE, "升级进行中，无法启用", 409)
            live = self._live_dir(name)
            d = self._disabled_dir(name)
            if st == ABSENT and not os.path.isdir(live) and not os.path.isdir(d):
                raise PluginLifecycleError(ERR_NOT_FOUND, "插件目录不存在（已卸载或缺失）", 404)
            if os.path.isdir(d) and not os.path.isdir(live):
                try:
                    os.makedirs(self._user_nodes(), exist_ok=True)
                    os.rename(d, live)
                except OSError as e:
                    raise PluginLifecycleError(ERR_ENABLE_FAILED,
                                               f"启用失败（目录移动失败）: {e}", 409)
            if not os.path.isdir(live):
                raise PluginLifecycleError(ERR_NOT_FOUND, "插件目录不存在", 404)
            # 重新校验 manifest（t2 校验门；失败 → 保持目录在位并隔离）
            try:
                manifest_v2.validate_manifest_file(live)
            except PluginManifestError as e:
                rec["state"] = QUARANTINED
                rec["state_reason"] = f"manifest 校验失败: {e.code}"
                rec["last_error"] = str(e)
                rec["updated_at"] = time.time()
                rec["last_op"] = "enable"; rec["last_op_at"] = time.time()
                rec["last_op_ok"] = False
                self._record_op("enable", name, rec.get("type_id"),
                                rec.get("version"), False, e.code)
                self._save()
                raise PluginLifecycleError(ERR_ENABLE_FAILED,
                                           f"启用失败：{e}", 422)
            declarative.scan()
            errs = {e.get("pkg"): e for e in declarative.scan_errors()}
            loaded = {s.type_id for s in declarative.list_specs()}
            if name in errs or rec.get("type_id") not in loaded:
                msg = str(errs.get(name, {}).get("message") or "节点未注册")
                rec["state"] = QUARANTINED
                rec["state_reason"] = msg
                rec["last_error"] = msg
                rec["updated_at"] = time.time()
                rec["metrics"]["failures"] = int(rec["metrics"].get("failures") or 0) + 1
                rec["last_op"] = "enable"; rec["last_op_at"] = time.time()
                rec["last_op_ok"] = False
                self._record_op("enable", name, rec.get("type_id"),
                                rec.get("version"), False, "scan_failed")
                self._save()
                raise PluginLifecycleError(ERR_ENABLE_FAILED,
                                           f"启用失败：{msg}", 409)
            rec["state"] = ACTIVE
            rec["state_reason"] = ""
            rec["last_error"] = ""
            rec["updated_at"] = time.time()
            rec["metrics"]["enables"] = int(rec["metrics"].get("enables") or 0) + 1
            rec["last_op"] = "enable"; rec["last_op_at"] = time.time()
            rec["last_op_ok"] = True
            limits.audit("plugin_enable", pkg=name, type_id=rec.get("type_id"),
                         version=rec.get("version"), ok=True, code="ok")
            self._record_op("enable", name, rec.get("type_id"), rec.get("version"), True, "")
            self._save()
            return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                    "state": ACTIVE, "version": rec.get("version")}

    # ---------------- 卸载 ----------------
    def uninstall(self, pkg: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.resolve(pkg)
            name = rec["pkg"]
            version = rec.get("version")
            gone = True
            for p in (self._live_dir(name), self._disabled_dir(name)):
                if os.path.isdir(p):
                    try:
                        self._rmtree(p)
                    except Exception:  # noqa: BLE001
                        gone = False
            self._rmtree(self._backup_root(name))
            if not gone:
                raise PluginLifecycleError(ERR_UNINSTALL_FAILED, "卸载失败：无法删除插件目录", 409)
            declarative.scan()
            self._records.pop(name, None)
            limits.audit("plugin_uninstall", pkg=name, type_id=rec.get("type_id"),
                         version=version, ok=True, code="ok")
            self._record_op("uninstall", name, rec.get("type_id"), version, True, "")
            self._save()
            return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                    "version": version, "removed": True}

    # ---------------- 健康检查 ----------------
    def healthcheck(self, pkg: str) -> Dict[str, Any]:
        with self._lock:
            rec = self.get_record(pkg)
            if rec is None:
                live = self._live_dir(pkg)
                if not os.path.isdir(live):
                    raise PluginLifecycleError(ERR_NOT_FOUND, f"插件不存在: {pkg}", 404)
                try:
                    manifest_v2.validate_manifest_file(live)
                    rec = self._new_record(pkg, self._type_id_at(live) or pkg,
                                           self._version_at(live), ACTIVE)
                except PluginManifestError as e:
                    rec = self._new_record(pkg, self._type_id_at(live) or pkg,
                                           self._version_at(live), QUARANTINED,
                                           f"manifest 校验失败: {e.code}")
                    rec["last_error"] = str(e)
                self._records[pkg] = rec
            name = rec["pkg"]
            live = self._live_dir(name)
            d_dir = self._disabled_dir(name)
            issues: List[str] = []
            manifest_ok = False
            loc = live if os.path.isdir(live) else (d_dir if os.path.isdir(d_dir) else "")
            if not loc:
                issues.append("包目录不存在")
            else:
                try:
                    manifest_v2.validate_manifest_file(loc)
                    manifest_ok = True
                except PluginManifestError as e:
                    issues.append(f"manifest 校验失败: {e.code}: {e}")
                except Exception as e:  # noqa: BLE001
                    issues.append(f"manifest 读取失败: {e}")
                if loc == d_dir:
                    issues.append("包处于禁用目录（.disabled/），启用后才会加载")
            loaded = False
            if manifest_ok and rec.get("type_id"):
                declarative.scan()
                errs = {e.get("pkg"): e for e in declarative.scan_errors()}
                loaded = (rec["type_id"] in {s.type_id for s in declarative.list_specs()}
                          and name not in errs)
            st = rec.get("state")
            if st == ACTIVE:
                healthy = manifest_ok and loaded
                if manifest_ok and not loaded:
                    issues.append("manifest 通过但节点未加载（scan 未注册）")
            elif st == DISABLED:
                healthy = manifest_ok and not loaded
                if not manifest_ok:
                    issues.append("禁用中且 manifest 无效（可能已损坏）")
            elif st == QUARANTINED:
                healthy = manifest_ok
                if manifest_ok:
                    issues.insert(0, "隔离态：manifest 现已通过，可执行启用恢复")
            elif st == UPDATING:
                healthy = False
                issues.append("升级事务进行中（或中断未恢复，重启后自动兜底）")
            else:  # absent
                healthy = False
                issues.append("包目录缺失（absent）")
            rec["metrics"]["healthchecks"] = int(rec["metrics"].get("healthchecks") or 0) + 1
            rec["last_op"] = "healthcheck"; rec["last_op_at"] = time.time()
            rec["last_op_ok"] = True
            self._save()
            return {"ok": True, "pkg": name, "type_id": rec.get("type_id"),
                    "state": st, "version": rec.get("version"),
                    "healthy": healthy, "manifest_ok": manifest_ok, "loaded": loaded,
                    "issues": issues, "last_checked": time.time()}

    # ---------------- 指标 ----------------
    def _snapshot_lockless(self) -> Dict[str, Any]:
        by_state = {s: 0 for s in STATES}
        totals = {k: 0 for k in (
            "installs", "updates", "uninstalls", "disables", "enables",
            "rollbacks", "healthchecks", "failures")}
        pkgs = []
        for name, rec in sorted(self._records.items()):
            st = rec.get("state")
            if st not in by_state:
                st = ABSENT
            by_state[st] += 1
            m = rec.get("metrics") or {}
            for k in totals:
                totals[k] += int(m.get(k) or 0)
            pkgs.append({
                "pkg": name, "type_id": rec.get("type_id"),
                "version": rec.get("version"), "state": st,
                "state_reason": rec.get("state_reason", ""),
                "installed_at": rec.get("installed_at"),
                "updated_at": rec.get("updated_at"),
                "last_error": rec.get("last_error", ""),
                "last_op": rec.get("last_op", ""),
                "last_op_at": rec.get("last_op_at"),
                "last_op_ok": rec.get("last_op_ok", True),
                "backup_count": len(rec.get("backups") or []),
                "backup_versions": [b.get("version") for b in (rec.get("backups") or [])],
                "metrics": m,
            })
        return {"ok": True, "state_path": self.state_path,
                "packages": len(self._records), "by_state": by_state,
                "totals": totals, "recent_ops": list(reversed(self._ops[-20:])),
                "plugins": pkgs}

    def metrics(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_lockless()