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

注意：本文件 import app.server 属有意为之（统一 server 初始化时机），不是副作用。
"""
from __future__ import annotations

import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# ---- 统一本地令牌（先于任何测试文件 import server） ----
UNIFIED_TOKEN = "fw-t9-test-token"
os.environ["FRAMEWEAVE_LOCAL_TOKEN"] = UNIFIED_TOKEN
os.environ.setdefault("FW_PLUGIN_EXEC", "inprocess")

# 统一导入时机：server.LOCAL_TOKEN 固定为 UNIFIED_TOKEN（防跨文件 401 漂移）
import app.server as srv  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_local_token():
    """每个测试后还原统一令牌环境变量（防 test_authz 等直接覆盖 env 污染后续）。"""
    yield
    os.environ["FRAMEWEAVE_LOCAL_TOKEN"] = UNIFIED_TOKEN