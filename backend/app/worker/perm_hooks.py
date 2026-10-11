"""perm_hooks.py（P3，t7 §4.3）：install/scan/run 三入口共享权限校验钩子。

- 三入口（declarative.install_zip / scan / engine._run_one）统一经本模块调用
  manifest v2 强校验（app.plugin.manifest_v2 权威）与信任级策略，杜绝校验旁路。
- check_run 返回 WorkerSupervisor 组装所需的 manifest 副本与信任级。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..plugin.manifest_v2 import validate_manifest  # noqa: F401（权威校验器，抛错型）
from . import limits


class PermissionPolicyError(Exception):
    """权限策略拒绝（带 code 与 manifest 来源）。"""

    def __init__(self, code: str, message: str, pkg: str = ""):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.pkg = pkg


def verify_install(manifest: Dict[str, Any], pkg: str = "") -> List[str]:
    """安装入口（install_zip 事务内）：强校验 + 越级声明拒绝（L2 越级需确认标记）。

    返回错误列表（空 = 通过）；抛 PermissionPolicyError 表达不可恢复拒绝。
    """
    errors: List[str] = []
    m = dict(manifest or {})
    try:
        result = validate_manifest(m)
        norm = result.manifest if result is not None else None
    except Exception as e:  # noqa: BLE001 —— 校验器已给出 G 编号错误
        raise PermissionPolicyError(limits.ERR_MANIFEST, "manifest v2 校验失败: %s" % e, pkg)
    if norm is None:
        raise PermissionPolicyError(limits.ERR_MANIFEST, "manifest v2 未通过（G1-G10）", pkg)
    # 信任级策略：L2（无签名）越级声明长任务/网络/子进程 → 需确认位
    sig = (manifest or {}).get("signature") or {}
    if not sig.get("key_id"):
        perm = (manifest or {}).get("permission") or {}
        if perm.get("subprocess") or perm.get("network") or perm.get("secret"):
            raise PermissionPolicyError(
                limits.ERR_INSTALL,
                "L2 社区插件不得声明 subprocess/network/secret 权限（越级声明拒绝）")
    return errors


def verify_scan(manifest: Dict[str, Any], pkg: str = "") -> List[str]:
    """扫描入口（scan 期）：同 install 强校验；策略漂移 → 返回错误（调用方标记禁用）。"""
    return verify_install(manifest, pkg)


def check_run(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """run 入口（engine._run_one → WorkerSupervisor）：返回信任级与门控元数据。"""
    m = dict(manifest or {})
    try:
        result = validate_manifest(m)
        norm = result.manifest if result is not None else None
    except Exception as e:  # noqa: BLE001
        raise PermissionPolicyError(limits.ERR_MANIFEST, "manifest v2 校验失败: %s" % e)
    if norm is None:
        raise PermissionPolicyError(limits.ERR_MANIFEST, "manifest v2 未通过", "")
    sig = (manifest or {}).get("signature") or {}
    kid = str(sig.get("key_id") or "")
    trust = "L0" if kid == "a8998646b062c170" else ("L1" if kid else "L2")
    return {"trust": trust, "manifest": {k: v for k, v in m.items() if k != "signature"}}
