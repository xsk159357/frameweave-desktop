# -*- coding: utf-8 -*-
"""FastAPI 请求模型（2026-10-19 从 server.py 拆出，纯声明、无副作用）。

拆分依据：docs/reports/FrameWeave_全量审阅_20261019.md §10 P2-9（server.py 2,403 行单文件）。
本模块只放 Pydantic 请求体模型，路由与业务逻辑仍在 server.py；server.py 顶部 import 本模块，
对外符号（WFCreate / LoginRequest / MarketPublish …）保持不变。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

class WFCreate(BaseModel):
    name: str = "未命名工作流"
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None

class WFUpdate(BaseModel):
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    name: Optional[str] = None

class RunRequest(BaseModel):
    mode: str = "all"
    node_ids: Optional[List[str]] = None
    priority: int = 0  # S1-T1：越小越先调度（默认 0，同优先级保持 FIFO）

class LoginRequest(BaseModel):
    email: str
    password: str
    device_id: str = ""

class EmailCodeRequest(BaseModel):
    email: str
    scene: str = "register"

class RegisterRequest(BaseModel):
    email: str
    code: str
    password: str
    device_id: str = ""

class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str

class ActivateRequest(BaseModel):
    email: str
    card: str

class MarketPublish(BaseModel):
    kind: str
    title: str
    description: str = ""
    author: str = ""
    price: int = 0
    download_url: str = ""
    tags: List[str] = []
    source_zip: str = ""  # 本地桩：内联 zip 路径（正式版无此字段）
    version: str = "1.0.0"
    type_id: str = ""
    sha256: str = ""
    size: int = 0
    min_client_version: str = ""
    status: str = "active"

class MarketInstall(BaseModel):
    token: str = ""

class MarketDownloadAuth(BaseModel):
    token: str = ""
    client_version: str = ""

class MarketInstallReport(BaseModel):
    token: str = ""
    client_version: str = ""
    result: str = "ok"
    error: str = ""
