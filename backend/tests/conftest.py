# -*- coding: utf-8 -*-
"""S1-T9 统一 pytest 会话级夹具（backend/tests/conftest.py）。

职责：
  1. 把 backend/ 加入 sys.path（各测试文件自身也 insert，这里兜底保证 pytest
     直接收集时的 import 一致性）；
  2. **统一本地令牌**：app.server.LOCAL_TOKEN 在模块导入时固定（obf.local_token_env
     读取 FRAMEWEAVE_LOCAL_TOKEN），而各 API 测试文件用不同的 setdefault 值且
     test_authz 会直接覆盖 env。若由"第一个碰 server 的文件"决定 LOCAL_TOKEN，
     后续文件请求头将与 server 固定值不匹配 → 401（全量运行实测）。因此这里在
     收集期**主动以统一 token 导入 app.server**，并用 autouse fixture 在每个
     测试后把 FRAMEWEAVE_LOCAL_TOKEN 还原为统一值。
  3. 测试默认执行模式：FW_PLUGIN_EXEC 默认 inprocess（worker 需要子进程 spawn，
     CI/沙箱不稳定；worker 语义由 test_t2_isolation 显式覆盖）。
  4. **临时目录固定到会话目录（2026-10-19 卫生治理）**：测试统一用
     `os.path.join(tempfile.gettempdir(), "fw_xxx_<ts>")` 建工作目录且无 teardown。
     当进程环境缺 TEMP/TMP/TMPDIR 时，`tempfile._candidate_tempdir_list()` 会回落到
     **当前工作目录**（= backend/），每跑一轮就在源码树落一批 fw_* 目录，
     直接把 test_health_governance 打红（历史实测 1,232 个）。这里在任何 tempfile
     使用之前把三个环境变量与 tempfile.tempdir 一起钉到独立会话目录，
     并在 sessionfinish 整目录删除。

注意：本文件 import app.server 属有意为之（统一 server 初始化时机），不是副作用。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# ---- 临时目录钉死（必须先于任何 tempfile 调用与 app.server 导入） ----
def _system_temp_base() -> str:
    """取真实系统临时根目录（不依赖 TEMP/TMP 是否已设置）。"""
    for key in ("TEMP", "TMP", "TMPDIR"):
        val = os.environ.get(key)
        if val and os.path.isdir(val):
            return val
    if os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")
        if os.path.isdir(cand):
            return cand
    return "/tmp" if os.path.isdir("/tmp") else BACKEND


SESSION_TMP = tempfile.mkdtemp(prefix="fw_pytest_session_", dir=_system_temp_base())
for _k in ("TMPDIR", "TEMP", "TMP"):
    os.environ[_k] = SESSION_TMP
tempfile.tempdir = SESSION_TMP  # 覆盖 pytest 自身可能已缓存的取值


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """会话结束清理临时目录（防止跨轮累积）。"""
    shutil.rmtree(SESSION_TMP, ignore_errors=True)


# ---- 代理环境归一（2026-10-19 实测） ----
# httpx 0.28.1 在 Python 3.14 下解析 NO_PROXY 的**方括号 IPv6 条目**（如 `[::1]`）会抛
# `httpx.InvalidURL: Invalid port: ':1]'`，导致任何 `httpx.Client(base_url=...)` 直接崩，
# 表现为 test_ws_events 等用真实 uvicorn 的用例整片失败（CI 的 NO_PROXY 无该条目，故只在
# 本机复现）。这里把方括号形式归一为裸 IPv6 地址，语义等价（NO_PROXY 两种写法都指 ::1）。
def _normalize_no_proxy() -> None:
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key)
        if not val:
            continue
        parts = []
        for item in val.split(","):
            item = item.strip()
            if item.startswith("[") and item.endswith("]"):
                item = item[1:-1]
            if item:
                parts.append(item)
        os.environ[key] = ",".join(parts)


_normalize_no_proxy()


# ---- 统一本地令牌（先于任何测试文件 import server） ----
UNIFIED_TOKEN = "fw-t9-test-token"
os.environ["FRAMEWEAVE_LOCAL_TOKEN"] = UNIFIED_TOKEN
os.environ.setdefault("FW_PLUGIN_EXEC", "inprocess")

# 统一导入时机：server.LOCAL_TOKEN 固定为 UNIFIED_TOKEN（防跨文件 401 漂移）
import app.server as srv  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _restore_local_token():
    """每个测试后还原统一令牌环境变量（防 test_authz 等直接覆盖 env 污染后续）。"""
    yield
    os.environ["FRAMEWEAVE_LOCAL_TOKEN"] = UNIFIED_TOKEN
