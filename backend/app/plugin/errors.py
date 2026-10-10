"""插件 manifest 校验错误码（G1-G10 门）与异常（P1，t2）。

错误码是显式、稳定的字符串契约：scan_errors / 上传安装响应 / 商城安装响应
都原样透出，前端/集成验证据此区分失败原因（坏 manifest 显式错误码，不静默吞异常）。

PluginManifestError 继承 worker/limits.PluginInstallError：install_zip / scan
既有 `except PluginInstallError` 路径直接捕获，无需改调用方。
"""
from __future__ import annotations

from typing import List

from ..worker import limits


# ---------------------------------------------------------------- G1-G10 错误码
ERR_SCHEMA_VERSION = "manifest_schema_version"        # G1: schema_version 非法
ERR_ENGINE_API = "manifest_engine_api"                # G1/G3: engine_api 缺失/不兼容
ERR_TYPE_ID = "manifest_type_id"                      # G2: type_id 格式/前缀白名单
ERR_META = "manifest_meta"                            # G2: title/category/description 缺失或枚举外
ERR_VERSION = "manifest_version"                      # G3: version 非 semver
ERR_KIND = "manifest_kind"                            # G4: kind/runtime/entrypoint 白名单
ERR_PORTS = "manifest_ports"                          # G5: 端口声明缺失/类型/重名/widget
ERR_OP = "manifest_op"                                # G6: transform op 白名单/媒体禁入
ERR_PERMISSION = "manifest_permission"                # G7: permission 五作用域
ERR_DEPENDENCIES = "manifest_dependencies"            # G8: dependencies 声明/通道
ERR_UI = "manifest_ui"                                # G8: ui 声明/资源目录
ERR_SIGNATURE = "manifest_signature"                  # G10: signature 结构/占位
ERR_UNKNOWN_FIELD = "manifest_unknown_field"          # v2 严格模式 additionalProperties
ERR_MANIFEST_GATE = "manifest_gate"                   # 其他校验门失败（兜底）

# 门顺序（G1..G10），用于“首个失败门”决定错误码
_GATE_ORDER: List[str] = [
    ERR_SCHEMA_VERSION, ERR_ENGINE_API, ERR_META, ERR_TYPE_ID, ERR_VERSION,
    ERR_KIND, ERR_PORTS, ERR_OP, ERR_PERMISSION, ERR_DEPENDENCIES, ERR_UI,
    ERR_SIGNATURE, ERR_UNKNOWN_FIELD,
]


class PluginManifestError(limits.PluginInstallError):
    """manifest 校验失败（显式错误码 + 全部问题清单）。"""

    def __init__(self, code: str, issues: List[str]):
        self.issues = list(issues)
        summary = "; ".join(self.issues) if self.issues else "manifest 校验失败"
        super().__init__(code, summary)


def first_gate_code(codes: List[str]) -> str:
    """按 G1-G10 顺序返回最先命中的门错误码（兜底 ERR_MANIFEST_GATE）。"""
    for c in _GATE_ORDER:
        if c in codes:
            return c
    return ERR_MANIFEST_GATE
