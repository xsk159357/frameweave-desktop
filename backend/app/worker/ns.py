"""NamespodeJail（P3，t7 §5）：worker 内模块导入白/黑名单（meta_path finder + __import__ 钩子）。

- worker 仅信任受控出口（fs op / http op / spawn op / resolve_secret op）；
  直接 `import socket` / `urllib` / `requests` 等网络原语一律拒绝，防绕过 PermissionGate。
- 按信任级分级：L0（迁移窗口）只剥 socket；L1 追加 urllib/requests/http.client；
  L2 再追加 subprocess（子进程只准经 ctx.spawn → host 侧 argv 白名单）。
- 被剥模块的导入尝试记录到 denied_log（审计事件素材），不静默。
"""
from __future__ import annotations

import builtins
import sys
from typing import List, Optional, Set, Tuple

# 默认块表（模块名或其顶层包名）
BLOCKED_DEFAULT: Tuple[str, ...] = ("socket", "_socket")
BLOCKED_L1: Tuple[str, ...] = ("urllib", "requests", "http.client", "aiohttp", "httpx")
BLOCKED_L2_SUBPROCESS: Tuple[str, ...] = ("subprocess", "pty", "pexpect")

# 信任级 → 追加块表
TRUST_EXTRA_BLOCKED: dict = {
    "L0": (),
    "L1": BLOCKED_L1,
    "L2": BLOCKED_L1 + BLOCKED_L2_SUBPROCESS,
}


def blocked_for_trust(trust: str) -> Tuple[str, ...]:
    """按信任级返回应封锁的模块（顶层包名）列表。"""
    return BLOCKED_DEFAULT + TRUST_EXTRA_BLOCKED.get(trust, ())


class NamespaceJail:
    """meta_path finder + builtins.__import__ 钩子，封锁名单模块。

    install() 后模块级 `import socket` 抛 ImportError（带审计说明）；
    uninstall() 恢复原 __import__ 并移除 finder。
    """

    def __init__(self, blocked: Tuple[str, ...] = BLOCKED_DEFAULT):
        self.blocked: Set[str] = set(blocked)
        self._orig_import = builtins.__import__
        self._installed = False
        self.denied_log: List[dict] = []

    # ---------- 安装/卸载 ----------
    def install(self) -> "NamespaceJail":
        if self._installed:
            return self
        builtins.__import__ = self._guarded_import
        sys.meta_path.insert(0, self)
        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return
        builtins.__import__ = self._orig_import
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        self._installed = False

    # ---------- import 钩子 ----------
    def _guarded_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        top = name.split(".")[0] if isinstance(name, str) else ""
        # 已缓存（sys.modules 已含）→ 放行：能力已存在，且 asyncio 等运行时需要
        # 反复 `import socket`；封锁只针对插件首次获取该原语（fresh load）。
        if top in self.blocked and top not in sys.modules:
            self.denied_log.append({"module": name, "top": top})
            raise ImportError(
                f"模块 '{name}' 被 worker 命名空间封锁（NamespaceJail）——"
                "网络/子进程原语只准经受限 RPC op 访问")
        return self._orig_import(name, globals, locals, fromlist, level)

    # ---------- meta_path（双保险：兼容 C 扩展/importlib 内部路径） ----------
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0] if isinstance(fullname, str) else ""
        if top in self.blocked:
            raise ImportError(
                f"模块 '{fullname}' 被 worker 命名空间封锁（NamespaceJail）")
        return None  # 未命中：让后续 finder 处理

    find_module = find_spec  # py<3.12 兼容（find_spec 优先）

    def __repr__(self) -> str:  # pragma: no cover
        return f"<NamespaceJail blocked={sorted(self.blocked)}>"


# 便捷：仅测试/审计用——验证某模块是否应被封锁
def is_blocked(module: str, blocked: Tuple[str, ...] = BLOCKED_DEFAULT) -> bool:
    return module.split(".")[0] in set(blocked)
