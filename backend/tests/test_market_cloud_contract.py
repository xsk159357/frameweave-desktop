# -*- coding: utf-8 -*-
"""商城下载链路静态/契约测试（t2：[auth_failed] Not Found 回归防线）。

背景：0.3.5 线上事故 `[auth_failed] Not Found` 根因是 cloud_server.py 定义了
market_download_auth / market_install_report 函数但从未注册进 ROUTES，导致
`POST /api/market/download-auth` 恒返 404 {"ok":false,"message":"Not Found"}
（无 code），客户端回退成含糊的 auth_failed。本测试锁定三层契约一致：

  1) 云端 ROUTES 必须注册 download-auth / install-report（do_POST 路由）；
  2) 本地客户端端点（/api/market/items/{id}/download-auth 等）与
     cloud_gateway 转发的云端路径、云端 ROUTES 三者互相对齐；
  3) 云端 download-auth 对免费/需登录/下架/不存在/版本过低条目返回稳定错误码，
     授权成功后 download 载荷含 url/size/sha256（客户端据此校验后进入安装）；
  4) cloud_gateway._post 对意外 4xx/5xx 补齐稳定 code=cloud_http_<status>
     （防未来路由失配再次退化成 auth_failed 类含糊错误）。

不启动 uvicorn / cloud_server 进程：第 1/2 项走静态路由表断言，第 3/4 项走
monkeypatch 的单元/HTTP mock（真实 ThreadingHTTPServer 返回 404）。
"""
from __future__ import annotations

import inspect
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import cloud_server  # backend/cloud_server.py（纯 stdlib，可安全 import）
from app import cloud_gateway as cg
import app.server as srv  # conftest 已导入；这里复用其 app 路由表


# --------------------------------------------------------------------------
# 1) 云端 ROUTES 静态契约
# --------------------------------------------------------------------------
def test_cloud_routes_registered_download_auth_and_report():
    assert callable(cloud_server.ROUTES.get("/api/market/download-auth")), \
        "cloud ROUTES 缺少 /api/market/download-auth（线上 [auth_failed] Not Found 根因）"
    assert cloud_server.ROUTES["/api/market/download-auth"] is cloud_server.market_download_auth
    assert callable(cloud_server.ROUTES.get("/api/market/install-report")), \
        "cloud ROUTES 缺少 /api/market/install-report"
    assert cloud_server.ROUTES["/api/market/install-report"] is cloud_server.market_install_report


def test_cloud_get_market_items_handled():
    # do_GET 分支需处理 /api/market/items 列表（客户端列表链路）
    src = inspect.getsource(cloud_server.H.do_GET)
    assert "path=='/api/market/items'" in src or "'/api/market/items'" in src


# --------------------------------------------------------------------------
# 2) 本地端点 ↔ 网关转发路径 ↔ 云端 ROUTES 三方对齐
# --------------------------------------------------------------------------
def test_local_client_routes_exist():
    paths = {getattr(r, "path", None) for r in srv.app.routes}
    for p in ("/api/market/items", "/api/market/items/{item_id}/download-auth",
              "/api/market/items/{item_id}/install-report", "/api/market/items/{item_id}/install",
              "/api/market/items/{item_id}/file"):
        assert p in paths, f"本地后端缺少客户端端点 {p}"


def test_gateway_cloud_paths_registered_in_cloud_routes():
    # cloud_gateway 转发的云端路径必须已注册（防“客户端→本地→云端”断链）
    src = inspect.getsource(cg.cloud_market_download_auth)
    assert "/api/market/download-auth" in src
    assert "/api/market/download-auth" in cloud_server.ROUTES
    src2 = inspect.getsource(cg.cloud_market_install_report)
    assert "/api/market/install-report" in src2
    assert "/api/market/install-report" in cloud_server.ROUTES
    # 列表路径走云端 do_GET（非 ROUTES）
    src3 = inspect.getsource(cg.cloud_market_items)
    assert "/api/market/items" in src3
    get_src = inspect.getsource(cloud_server.H.do_GET)
    assert "/api/market/items" in get_src


# --------------------------------------------------------------------------
# 3) 云端 download-auth / install-report 稳定错误码（纯函数级，monkeypatch 存储）
# --------------------------------------------------------------------------
@pytest.fixture
def cloud_store(tmp_path, monkeypatch):
    """把 cloud_server 的市场文件/DB 指向临时目录，并预置条目。"""
    mfile = tmp_path / "market_items.json"
    dbfile = tmp_path / "cloud.db"
    monkeypatch.setattr(cloud_server, "MARKET_FILE", str(mfile))
    monkeypatch.setattr(cloud_server, "DB_PATH", str(dbfile))

    def put(item: dict) -> str:
        data = {}
        if mfile.exists():
            data = json.loads(mfile.read_text(encoding="utf-8"))
        iid = str(item.get("id") or "it_" + str(len(data) + 1))
        data[iid] = {**{
            "id": iid, "kind": "node", "title": "t", "description": "", "author": "a@qq.com",
            "price": 0, "official": False, "download_url": "https://example.com/pkg.zip",
            "version": "1.0.0", "type_id": "user/t", "sha256": "0" * 64, "size": 123,
            "min_client_version": "", "status": "active", "downloads": 0, "installs": 0,
            "tags": [], "created_at": time.time(),
        }, **item}
        mfile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return iid

    put({"id": "free_1", "title": "免费节点", "download_url": "https://example.com/a.zip",
         "sha256": "ab" * 32, "size": 1024})
    put({"id": "paid_1", "title": "付费节点", "price": 100})
    put({"id": "old_1", "title": "未来版", "min_client_version": "99.0.0"})
    put({"id": "off_1", "title": "下架", "status": "disabled"})
    return {"mfile": mfile, "dbfile": dbfile, "put": put}


def test_download_auth_stable_codes(cloud_store):
    auth_free = cloud_server.market_download_auth(
        {"item_id": "free_1", "token": "", "client_version": "0.3.11"})
    assert auth_free.get("ok") is True
    assert auth_free.get("status") == "authorized"
    dl = auth_free.get("download") or {}
    assert dl.get("url", "").startswith("http")
    assert dl.get("size") == 1024
    assert dl.get("sha256") == "ab" * 32
    assert auth_free.get("direct") is True
    assert "stub_path" not in dl  # 云端直链契约：无 stub_path

    assert cloud_server.market_download_auth(
        {"item_id": "paid_1", "token": "", "client_version": "0.3.11"}) == {
        "ok": False, "code": "login_required", "message": "请先登录后再下载付费节点"}
    assert cloud_server.market_download_auth(
        {"item_id": "paid_1", "token": "no-such-token", "client_version": "0.3.11"}).get("code") == "login_required"
    assert cloud_server.market_download_auth(
        {"item_id": "old_1", "token": "", "client_version": "0.3.11"}).get("code") == "client_version_too_old"
    assert cloud_server.market_download_auth(
        {"item_id": "off_1", "token": "", "client_version": "0.3.11"}).get("code") == "item_disabled"
    assert cloud_server.market_download_auth(
        {"item_id": "nope", "token": "", "client_version": "0.3.11"}).get("code") == "item_not_found"


def test_install_report_stable_codes(cloud_store):
    rep = cloud_server.market_install_report(
        {"item_id": "free_1", "token": "", "client_version": "0.3.11", "result": "ok", "error": ""})
    assert rep.get("ok") is True and rep.get("recorded") is True
    assert cloud_server.market_install_report(
        {"item_id": "nope", "result": "ok"}).get("code") == "item_not_found"


# --------------------------------------------------------------------------
# 4) cloud_gateway._post 对意外 4xx/5xx 补齐稳定 code（真实 404 HTTP mock）
# --------------------------------------------------------------------------
def test_gateway_http_error_gets_stable_code(monkeypatch):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(n)
            body = json.dumps({"ok": False, "message": "Not Found"}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv_ = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv_.serve_forever, daemon=True)
    th.start()
    try:
        monkeypatch.setenv("FRAMEWEAVE_CLOUD_URL", f"http://127.0.0.1:{srv_.server_port}")
        resp = cg._post("/api/market/download-auth", {"item_id": "x", "token": ""}, timeout=3)
        assert resp is not None
        assert resp.get("ok") is False
        assert resp.get("code") == "cloud_http_404"   # 稳定错误码，而非无 code
        assert resp.get("message") == "Not Found"
    finally:
        srv_.shutdown()
        th.join(timeout=5)
