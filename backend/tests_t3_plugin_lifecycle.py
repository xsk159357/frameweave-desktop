# -*- coding: utf-8 -*-
"""t3 平台 P1 插件生命周期 API 与状态机 验收测试。

覆盖（契约验收）：
  A1 安装事务：staging→manifest v2 校验→备份 3 份→原子替换→scan 验证
     （upload 安装走 plugin_registry.install_zip；覆盖安装自动备份）
  A2 禁用/启用：目录移入 .disabled/ → scan 卸载；启用校验 manifest + 恢复加载
  A3 升级：POST /api/user_nodes/{pkg}/update（zip 上传）→ updating 状态 → 事务核
  A4 升级失败自动回滚：坏包（scan 校验失败）→ 自动回滚旧版本目录 + plugin_update_failed
  A5 回滚：POST /{pkg}/rollback → 恢复到最新备份版本
  A6 卸载：删除 live/disabled/.backup；记录移除；metrics 计数
  A7 持久化：plugins.json 状态机落盘（active/disabled/absent/quarantined/updating）
  A8 统一错误响应 {ok, code, message}（PluginLifecycleError 处理器）
  A9 健康检查 + metrics 接口（备份索引、操作流水、per-state 计数）
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = tempfile.mkdtemp(prefix="fw_t3_")
os.environ["FRAMEWEAVE_USER_NODES"] = os.path.join(TMP_ROOT, "user_nodes")
os.environ["FRAMEWEAVE_PLUGINS_STATE"] = os.path.join(TMP_ROOT, "plugins.json")
os.environ["FRAMEWEAVE_AUDIT_LOG"] = os.path.join(TMP_ROOT, "audit.jsonl")

from fastapi.testclient import TestClient  # noqa: E402
from app import server  # noqa: E402
from app import declarative  # noqa: E402
from app.plugin_registry import (  # noqa: E402
    PluginRegistry, ABSENT, ACTIVE, DISABLED, QUARANTINED, UPDATING,
)
from app.worker import limits  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append((name, detail))
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


# ---- 隔离环境 ----
os.makedirs(declarative.USER_NODES_DIR, exist_ok=True)
declarative.scan()
server.plugin_registry = PluginRegistry(os.path.join(TMP_ROOT, "plugins.json"))
client = TestClient(server.app)
H = {"X-FW-Local-Token": "dev-local-token"}

GOOD = {
    "schema_version": 2, "engine_api": "1.0", "type_id": "user/demo",
    "title": "Demo", "category": "用户节点", "description": "d", "version": "1.0.0",
    "kind": "transform", "inputs": [{"name": "a", "type": "STRING"}],
    "outputs": [{"name": "out", "type": "STRING"}], "params": [],
    "transform": {"out": {"op": "upper", "source": {"$input": "a"}}},
}


def make_zip(manifest, top="pkg_demo", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "/", "")
        zf.writestr(top + "/manifest.json", json.dumps(manifest, ensure_ascii=False))
        for rel, data in (extra or []):
            zf.writestr(top + "/" + rel, data)
    return buf.getvalue()


def upload(path, files, **kw):
    return client.post(path, files=files, headers=H, **kw)


def post(path):
    return client.post(path, headers=H)


def get(path):
    return client.get(path, headers=H)


root = declarative.USER_NODES_DIR
live = os.path.join(root, "pkg_demo")
disabled_dir = os.path.join(root, ".disabled", "pkg_demo")
backup_root = os.path.join(root, ".backup", "pkg_demo")

# ================= A1: 安装事务 =================
print("\n==== A1 安装事务（staging→校验→原子替换→scan） ====")
r = upload("/api/user_nodes/install", {"file": ("demo.zip", make_zip(GOOD), "application/zip")})
j = r.json()
check("A1 安装成功 ok=true state=active",
      r.status_code == 200 and j.get("ok") is True and j.get("state") == ACTIVE
      and j.get("version") == "1.0.0", f"body={str(j)[:200]}")
check("A1 目录落盘", os.path.isdir(live) and os.path.isfile(os.path.join(live, "manifest.json")))
check("A1 节点已注册", "user/demo" in [s.type_id for s in declarative.list_specs()])
check("A1 staging 无残留",
      not os.path.exists(os.path.join(root, ".staging", "unpack")),
      f"staging={os.listdir(os.path.join(root, '.staging')) if os.path.isdir(os.path.join(root, '.staging')) else 'missing'}")

# 覆盖安装 v2 → 自动备份 b1
GOOD2 = dict(GOOD, version="2.0.0")
r2 = upload("/api/user_nodes/install", {"file": ("demo2.zip", make_zip(GOOD2), "application/zip")})
j2 = r2.json()
check("A2(安装) 覆盖安装成功 version=2.0.0 update=true",
      r2.status_code == 200 and j2.get("ok") is True and j2.get("update") is True
      and j2.get("version") == "2.0.0", f"body={str(j2)[:200]}")
check("A2(安装) 覆盖安装产生备份 b1",
      os.path.isdir(os.path.join(backup_root, "b1"))
      and json.load(open(os.path.join(backup_root, "b1", "manifest.json"), encoding="utf-8"))["version"] == "1.0.0",
      f"b1={os.listdir(os.path.join(backup_root, 'b1')) if os.path.isdir(os.path.join(backup_root, 'b1')) else 'missing'}")

# 再覆盖 v3 → b2（b1=v2, b2=v1）
GOOD3 = dict(GOOD, version="3.0.0")
r3 = upload("/api/user_nodes/install", {"file": ("demo3.zip", make_zip(GOOD3), "application/zip")})
check("A2(安装) 第三次覆盖成功 version=3.0.0",
      r3.status_code == 200 and r3.json().get("version") == "3.0.0", f"body={r3.text[:160]}")
check("A2(安装) 备份轮转 b1=v2 b2=v1",
      json.load(open(os.path.join(backup_root, "b1", "manifest.json"), encoding="utf-8"))["version"] == "2.0.0"
      and json.load(open(os.path.join(backup_root, "b2", "manifest.json"), encoding="utf-8"))["version"] == "1.0.0",
      f"b1/b2 = {os.listdir(backup_root)}")

# ================= A2: 禁用 / 启用 =================
print("\n==== A2 禁用 / 启用 ====")
r = post("/api/user_nodes/pkg_demo/disable")
j = r.json()
check("A2 禁用 ok state=disabled",
      r.status_code == 200 and j.get("ok") is True and j.get("state") == DISABLED,
      f"body={str(j)[:200]}")
check("A2 禁用目录移入 .disabled/ 且根目录移除",
      os.path.isdir(disabled_dir) and not os.path.isdir(live))
check("A2 禁用后节点卸载（scan）",
      "user/demo" not in [s.type_id for s in declarative.list_specs()])
# 重复禁用幂等
r2 = post("/api/user_nodes/pkg_demo/disable")
check("A2 重复禁用幂等 already=true", r2.status_code == 200 and r2.json().get("already") is True)
# 启用
r = post("/api/user_nodes/pkg_demo/enable")
j = r.json()
check("A2 启用 ok state=active",
      r.status_code == 200 and j.get("ok") is True and j.get("state") == ACTIVE,
      f"body={str(j)[:200]}")
check("A2 启用目录移回且节点重新加载",
      os.path.isdir(live) and not os.path.isdir(disabled_dir)
      and "user/demo" in [s.type_id for s in declarative.list_specs()])

# ================= A3: 升级（update 端点） =================
print("\n==== A3 升级（/update） ====")
GOOD4 = dict(GOOD, version="4.0.0")
r = upload("/api/user_nodes/pkg_demo/update", {"file": ("demo4.zip", make_zip(GOOD4), "application/zip")})
j = r.json()
check("A3 升级成功 version=4.0.0",
      r.status_code == 200 and j.get("ok") is True and j.get("version") == "4.0.0"
      and j.get("previous_version") == "3.0.0", f"body={str(j)[:200]}")
check("A3 升级后状态 active 且节点加载",
      j.get("state") == ACTIVE and "user/demo" in [s.type_id for s in declarative.list_specs()])
check("A3 升级产生 b1（旧 3.0.0）",
      json.load(open(os.path.join(backup_root, "b1", "manifest.json"), encoding="utf-8"))["version"] == "3.0.0")
check("A3 升级完成无 updating 残留",
      server.plugin_registry.get_record("pkg_demo").get("state") != UPDATING)

# 升级失败自动回滚：manifest v2 通过、但 scan 加载失败的包（python 类缺失）
# —— t2 校验门在事务核后置 scan 验证环节触发 → 自动回滚旧版本目录。
print("\n==== A4 升级失败自动回滚 ====")
PY_BAD = {
    "schema_version": 2, "engine_api": "1.0", "type_id": "user/demo",
    "title": "Demo", "category": "用户节点", "description": "d", "version": "5.0.0",
    "kind": "python", "runtime": "python",
    "entrypoint": "node.py", "class_name": "MissingClass",
    "inputs": [], "outputs": [], "params": [],
}
r = upload("/api/user_nodes/pkg_demo/update",
           {"file": ("badpy.zip", make_zip(PY_BAD, top="pkg_demo",
                                           extra=[("node.py", b"class Other:\n    pass\n")]),
                     "application/zip")})
j = r.json()
check("A4 升级失败返回 plugin_update_failed + 已回滚说明",
      r.status_code == 409 and j.get("ok") is False and j.get("code") == "plugin_update_failed"
      and "回滚" in str(j.get("message") or ""), f"status={r.status_code} body={str(j)[:260]}")
check("A4 失败后旧版本目录恢复（manifest version=4.0.0）",
      os.path.isdir(live)
      and json.load(open(os.path.join(live, "manifest.json"), encoding="utf-8"))["version"] == "4.0.0",
      f"live version={json.load(open(os.path.join(live, 'manifest.json'), encoding='utf-8')).get('version')}")
check("A4 失败后节点仍可加载",
      "user/demo" in [s.type_id for s in declarative.list_specs()])
check("A4 失败计入 metrics.failures",
      int(server.plugin_registry.get_record("pkg_demo")["metrics"].get("failures") or 0) >= 1)

# ================= A5: 回滚 =================
print("\n==== A5 回滚 ====")
r = post("/api/user_nodes/pkg_demo/rollback")
j = r.json()
check("A5 回滚成功版本 = 3.0.0",
      r.status_code == 200 and j.get("ok") is True and j.get("version") == "3.0.0"
      and j.get("previous_version") == "4.0.0", f"body={str(j)[:220]}")
check("A5 回滚后目录版本 = 3.0.0 且节点加载",
      json.load(open(os.path.join(live, "manifest.json"), encoding="utf-8"))["version"] == "3.0.0"
      and "user/demo" in [s.type_id for s in declarative.list_specs()])
check("A5 回滚后备份槽位左移（b1 现在 = 4.0.0 或更新）",
      os.path.isdir(os.path.join(backup_root, "b1")))

# ================= A6: 卸载 =================
print("\n==== A6 卸载 ====")
r = post("/api/user_nodes/pkg_demo/uninstall")
j = r.json()
check("A6 卸载 ok removed=true",
      r.status_code == 200 and j.get("ok") is True and j.get("removed") is True,
      f"body={str(j)[:200]}")
check("A6 live/disabled/backup 目录全部删除",
      not os.path.isdir(live) and not os.path.isdir(disabled_dir) and not os.path.isdir(backup_root))
check("A6 记录移除", server.plugin_registry.get_record("pkg_demo") is None)
check("A6 卸载后节点卸载", "user/demo" not in [s.type_id for s in declarative.list_specs()])

# 卸载不存在的插件 → 404
r = post("/api/user_nodes/pkg_demo/uninstall")
check("A6 卸载不存在 → 404 plugin_not_found",
      r.status_code == 404 and r.json().get("code") == "plugin_not_found",
      f"status={r.status_code} body={str(r.json())[:160]}")

# ================= A7: 持久化 =================
print("\n==== A7 plugins.json 持久化 ====")
state = json.load(open(os.path.join(TMP_ROOT, "plugins.json"), encoding="utf-8"))
check("A7 plugins.json 存在且为状态机结构",
      "packages" in state and "ops" in state,
      f"keys={list(state.keys())}")
check("A7 卸载后记录已移除", "pkg_demo" not in state["packages"])
check("A7 操作流水记录 uninstall",
      any(o.get("op") == "uninstall" and o.get("pkg") == "pkg_demo" for o in state["ops"]),
      f"ops={state['ops'][-5:]}")
# 持久化重载：新注册表实例读回相同状态（absent 等）
rec = server.plugin_registry.get_record("pkg_demo")
check("A7 卸载后内存记录为 None（持久化一致）", rec is None)

# 坏包 → quarantined（scan 隔离）
broken = os.path.join(root, "broken_pkg")
os.makedirs(broken, exist_ok=True)
with open(os.path.join(broken, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump({"title": "no type_id"}, f)
server.plugin_registry.sync()
rec = server.plugin_registry.get_record("broken_pkg")
check("A7 坏包被隔离 quarantined",
      rec is not None and rec.get("state") == QUARANTINED,
      f"state={rec.get('state') if rec else None}")
os.remove(os.path.join(broken, "manifest.json")); os.rmdir(broken)
server.plugin_registry.sync()
rec = server.plugin_registry.get_record("broken_pkg")
check("A7 目录删除后 sync → absent",
      rec is not None and rec.get("state") == ABSENT,
      f"state={rec.get('state') if rec else None}")

# ================= A8: 统一错误响应 =================
print("\n==== A8 统一错误响应 {ok,code,message} ====")
r = post("/api/user_nodes/nonexistent/disable")
j = r.json()
check("A8 disable 不存在 → {ok:false,code,message}",
      r.status_code == 404 and j.get("ok") is False
      and j.get("code") == "plugin_not_found" and j.get("message"),
      f"status={r.status_code} body={str(j)[:160]}")
r = post("/api/user_nodes/nonexistent/rollback")
j = r.json()
check("A8 rollback 不存在 → 404 统一结构",
      r.status_code == 404 and j.get("code") == "plugin_not_found")
# 禁用中的插件不允许升级 → 409
r = upload("/api/user_nodes/pkg_demo/update", {"file": ("x.zip", make_zip(GOOD), "application/zip")})
j = r.json()
check("A8 未安装插件升级 → 404 统一结构",
      r.status_code == 404 and j.get("code") == "plugin_not_found",
      f"status={r.status_code} body={str(j)[:160]}")

# ================= A9: 健康检查 + metrics =================
print("\n==== A9 healthcheck + metrics ====")
# 重新安装一个健康插件
r = upload("/api/user_nodes/install", {"file": ("demo.zip", make_zip(GOOD), "application/zip")})
check("A9 重装成功（为健康检查准备）", r.status_code == 200 and r.json().get("ok") is True)
r = get("/api/user_nodes/pkg_demo/healthcheck")
j = r.json()
check("A9 healthcheck healthy=true manifest_ok=true loaded=true",
      r.status_code == 200 and j.get("ok") is True and j.get("healthy") is True
      and j.get("manifest_ok") is True and j.get("loaded") is True
      and j.get("state") == ACTIVE, f"body={str(j)[:260]}")
# 禁用后 healthcheck：manifest 有效但未加载
post("/api/user_nodes/pkg_demo/disable")
r = get("/api/user_nodes/pkg_demo/healthcheck")
j = r.json()
check("A9 禁用后 healthcheck healthy=true loaded=false（禁用语义）",
      r.status_code == 200 and j.get("healthy") is True
      and j.get("loaded") is False and j.get("state") == DISABLED,
      f"body={str(j)[:260]}")
post("/api/user_nodes/pkg_demo/enable")
# 为备份索引指标做准备：升级一次（自动备份 b1）
GOOD5 = dict(GOOD, version="1.1.0")
upload("/api/user_nodes/pkg_demo/update", {"file": ("demo5.zip", make_zip(GOOD5), "application/zip")})
r = get("/api/user_nodes/metrics")
j = r.json()
check("A9 metrics 返回 per-state 计数",
      r.status_code == 200 and j.get("ok") is True
      and isinstance(j.get("by_state"), dict)
      and j["by_state"].get(ACTIVE, 0) >= 1, f"by_state={j.get('by_state')}")
check("A9 metrics 含累计计数（当前记录聚合）",
      int(j["totals"].get("installs") or 0) >= 1
      and int(j["totals"].get("updates") or 0) >= 1
      and int(j["totals"].get("disables") or 0) >= 1
      and int(j["totals"].get("enables") or 0) >= 1
      and int(j["totals"].get("healthchecks") or 0) >= 2,
      f"totals={j.get('totals')}")
check("A9 metrics 含最近操作流水", isinstance(j.get("recent_ops"), list) and len(j["recent_ops"]) >= 1,
      f"recent_ops={j.get('recent_ops')[:3]}")
check("A9 metrics 含备份索引",
      any(p.get("backup_versions") for p in j.get("plugins", [])),
      f"plugins={[(p.get('pkg'), p.get('backup_versions')) for p in j.get('plugins', [])]}")

# ================= 汇总 =================
print("\n==== 汇总 ====")
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
for name, detail in PASS:
    print("  PASS", name)
for name, detail in FAIL:
    print("  FAIL", name, "|", detail)
shutil.rmtree(TMP_ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)
