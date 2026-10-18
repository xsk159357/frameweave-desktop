# -*- coding: utf-8 -*-
"""插件仓库路径解析（本地工作区 / CI checkout 两种布局）。

本地：`F:\\1223\\解说工坊\\frameweave-plugins\\packages`（与 FrameWeave 平级）
CI  ：`<repo>/frameweave-plugins/packages`（workflow checkout `path: frameweave-plugins`）

解析顺序：
  1. `FRAMEWEAVE_PLUGINS_DIR`（CI 在 workflow 里显式设置，指向 .../packages）
  2. 与仓库平级的工作区布局（本地开发）
  3. 仓库内布局（CI checkout）

2026-10-19：此前各测试各自硬编码 `F:\\1223\\...` 或 `backend/../../`，在 CI 上要么
FileNotFound、要么扫到 0 个包（CI 从未跑通过，问题一直被掩盖）。
"""
from __future__ import annotations

import os

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_TESTS)
_REPO = os.path.dirname(_BACKEND)
_WORKSPACE = os.path.dirname(_REPO)


def repo_dir() -> str:
    """返回插件仓库根（含 packages/ 与 sdk/）；不可用返回空串。"""
    env = os.environ.get("FRAMEWEAVE_PLUGINS_DIR")
    if env and os.path.isdir(env):
        return os.path.dirname(os.path.abspath(env))
    for cand in (
        os.path.join(_WORKSPACE, "frameweave-plugins"),
        os.path.join(_REPO, "frameweave-plugins"),
    ):
        if os.path.isdir(os.path.join(cand, "packages")):
            return cand
    return ""


def packages_dir() -> str:
    root = repo_dir()
    return os.path.join(root, "packages") if root else ""


def sdk_dir() -> str:
    root = repo_dir()
    return os.path.join(root, "sdk") if root else ""


def require_packages() -> str:
    path = packages_dir()
    if not path:
        pytest.skip("frameweave-plugins/packages 不可用（未 checkout 插件仓库）", allow_module_level=True)
    return path
