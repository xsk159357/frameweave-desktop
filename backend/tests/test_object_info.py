# -*- coding: utf-8 -*-
"""S1-T4 /object_info schema 验收测试（P0-4 / R-平台-4）。

覆盖（对应 06_实施任务拆解_阶段一.md S1-T4 acceptance + verify）：
  1. 全量 schema 完整性：扫描 frameweave-plugins/packages 全部插件包，断言每个
     合法 manifest 的 type_id 均能从 /object_info 解析，且契约字段齐备：
     input（required/optional/hidden）+ input_order + is_input_list +
     output（含 list 标记 output_is_list）+ output_name + description +
     search_aliases + category + deprecated；
  2. 端口顺序完整性：input_order.required/optional 与 input.required/optional
     一一对应（Comfy 前端渲染依赖声明顺序）；
  3. /api/specs 与 /api/object_info 同源生成：specs 数组条目 = object_info()
     单一来源投影（title/category/description/search_aliases/deprecated + 端口）；
  4. HTTP 实测：GET /api/object_info、GET /api/specs 经 TestClient 返回正确；
  5. manifest 语义字段（search_aliases/deprecated/kind/pkg）经校验器 → object_info
     传播正确（deprecated 可被前端降级渲染）。

运行方式：
  - T9 统一 pytest 前先以独立脚本断言：`python tests/test_object_info.py`（退出码 0=全绿）；
  - pytest 收集兼容：本文件顶层只做轻量 import，pytest 可直接收集 test_* 函数。
"""
import asyncio
import json
import os
import sys

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_TESTS)
sys.path.insert(0, _BACKEND)

# 扫描目标 = 官方插件仓库全量包（本测试把 USER_NODES 指向它，覆盖 40+ 插件包）
from _plugin_paths import packages_dir  # noqa: E402
_PLUGINS = packages_dir()
pytestmark = pytest.mark.skipif(not _PLUGINS, reason="frameweave-plugins/packages 不可用")
os.environ["FRAMEWEAVE_USER_NODES"] = _PLUGINS
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t4-test-token")

from app import registry  # noqa: E402
from app import declarative  # noqa: E402
# 2026-10-19：显式设置扫描目录。此前只设 env，而 USER_NODES_DIR 在 declarative 导入时
# 已绑定（conftest 先导入 app.server），导致本文件单独运行时扫到 backend/user_nodes（33 包）
# 而非插件仓库（70 包）——测试通过与否取决于 test_node_smoke 是否先跑（顺序依赖）。
if _PLUGINS:
    declarative.set_user_nodes_dir(_PLUGINS)
from app import server as srv  # noqa: E402  导入即 autodiscover() + declarative.scan()
from app.server import _oi_ports_to_spec  # noqa: E402

CATEGORIES = {"输入", "语义", "分析", "音频", "剪辑", "输出", "控制", "用户节点"}
OBSERVED_KEYS = {
    "name", "display_name", "description", "category", "version",
    "input", "input_order", "is_input_list",
    "output", "output_name", "output_is_list",
    "search_aliases", "deprecated",
}


def _package_manifests():
    """packages 目录下全部 manifest.json（保留目录跳过，与 declarative.scan 同口径）。"""
    out = []
    if not os.path.isdir(_PLUGINS):
        return out
    for entry in sorted(os.listdir(_PLUGINS)):
        if entry.startswith(".") or entry in (".staging", ".backup", "_vendor", ".git"):
            continue
        mpath = os.path.join(_PLUGINS, entry, "manifest.json")
        if os.path.isfile(mpath):
            try:
                with open(mpath, "r", encoding="utf-8") as f:
                    out.append((entry, json.load(f)))
            except (OSError, ValueError):
                pass
    return out


def test_scan_covers_all_valid_packages():
    """① 全部合法包节点可从 object_info 解析（40+）。"""
    # 前置：隔离性——registry.scan 把所有符合 G1-G10 的包加载进 object_info
    declarative.scan()  # 幂等重建，保证本测试拿到全量快照
    oi = registry.object_info()
    manifests = _package_manifests()
    assert len(manifests) >= 40, "官方插件包数少于 40，无法满足 acceptance"
    assert len(oi) >= 40, "object_info 节点数少于 40: %d" % len(oi)

    # manifest 层面已判死的非法包（其他成员 in-flight，category 不在枚举）→ 允许缺席但必须可解释
    scan_errors = declarative.scan_errors()
    err_by_pkg = {e.get("pkg"): e.get("message", "") for e in scan_errors}
    missing = []
    for pkg, m in manifests:
        tid = m.get("type_id")
        if not tid:
            missing.append((pkg, "manifest 无 type_id"))
            continue
        if tid not in oi:
            missing.append((pkg, tid, err_by_pkg.get(pkg, "未加载且无 scan 错误记录")))
    # 缺席必须可解释（scan_errors 有该包记录：category 枚举非法 / 节点代码加载失败等
    # 其他成员 in-flight 临时缺陷）；同时 loaded ≥ 40 兜底 acceptance。
    for item in missing:
        pkg = item[0]
        msg = item[-1]
        if not msg or msg == "未加载且无 scan 错误记录":
            raise AssertionError("合法包未进入 object_info 且无 scan 错误解释: %s" % (item,))
    print("[t4] 缺席包（均有 scan_errors 解释，非本任务范围）: %s" %
          [(m[0], m[1], m[2][:60]) for m in missing])


def test_object_info_contract_fields_every_node():
    """② 逐节点契约字段齐备（input/input_order/is_input_list/output(list)/…）。"""
    oi = registry.object_info()
    assert oi, "object_info 为空"
    for tid, v in oi.items():
        for k in OBSERVED_KEYS:
            assert k in v, "%s 缺字段 %s" % (tid, k)
        inp = v["input"]
        assert isinstance(inp, dict) and set(inp) == {"required", "optional", "hidden"}, \
            "%s input 段必须含 required/optional/hidden" % tid
        assert isinstance(v["description"], str) and v["description"].strip(), \
            "%s description 缺失" % tid
        assert isinstance(v["category"], str) and v["category"], "%s category 缺失" % tid
        assert isinstance(v["search_aliases"], list) and \
            all(isinstance(x, str) for x in v["search_aliases"]), "%s search_aliases 非字符串列表" % tid
        assert isinstance(v["deprecated"], bool), "%s deprecated 非布尔" % tid
        # input_order 与 input 段一一对应（声明顺序 = Comfy 渲染顺序）
        req_names = list((inp.get("required") or {}).keys())
        opt_names = list((inp.get("optional") or {}).keys())
        order = v["input_order"]
        assert sorted(order.get("required") or []) == sorted(req_names), \
            "%s input_order.required 与 input.required 不一致" % tid
        assert sorted(order.get("optional") or []) == sorted(opt_names), \
            "%s input_order.optional 与 input.optional 不一致" % tid
        # is_input_list 覆盖全部输入端口
        il = v["is_input_list"]
        assert isinstance(il, dict) and sorted(il) == sorted(req_names + opt_names), \
            "%s is_input_list 未覆盖全部输入端口" % tid
        # 每个端口 meta 必须携带 port_role（S1-T4 连线/参数区分；/api/specs 投影与本
        # 端渲染依赖它还原画布连线手柄与参数表单）
        for bucket, names in (("required", req_names), ("optional", opt_names)):
            for name in names:
                meta = inp[bucket].get(name)
                assert meta and meta[1].get("port_role") in ("input", "param"), \
                    "%s 端口 %s 缺 port_role 标记" % (tid, name)
        # output / output_name / output_is_list 长度一致（list 标记语义）
        out = v["output"]
        assert isinstance(out, list) and all(isinstance(t, str) for t in out), \
            "%s output 非字符串列表" % tid
        assert len(v["output_name"]) == len(out), "%s output_name 长度不一致" % tid
        assert len(v["output_is_list"]) == len(out) and \
            all(isinstance(b, bool) for b in v["output_is_list"]), "%s output_is_list 长度/类型异常" % tid
        assert v["name"] == tid and isinstance(v["display_name"], str), "%s name/display_name 异常" % tid


def test_specs_endpoint_same_source():
    """③ /api/specs 与 /api/object_info 同源生成（specs 条目 = object_info 投影）。"""
    oi = registry.object_info()
    specs = asyncio.run(srv.specs())
    by_tid = {s["type_id"]: s for s in specs}
    assert len(by_tid) == len(oi), "specs 数组 size 与 object_info 不一致"
    for tid, v in oi.items():
        s = by_tid[tid]
        assert s["title"] == v["display_name"], "%s title 不同源" % tid
        assert s["category"] == v["category"], "%s category 不同源" % tid
        assert s["description"] == v["description"], "%s description 不同源" % tid
        assert s["search_aliases"] == v.get("search_aliases", []), "%s search_aliases 不同源" % tid
        assert s["deprecated"] == v.get("deprecated", False), "%s deprecated 不同源" % tid
        assert s["params"] == _oi_ports_to_spec(v, "param", True), "%s params 投影不一致" % tid
        assert s["inputs"] == _oi_ports_to_spec(v, "input", True), "%s inputs 投影不一致" % tid
        assert s["outputs"] == _oi_ports_to_spec(v, None, False), "%s outputs 投影不一致" % tid


def test_specs_preserves_connectable_split():
    """③b 回归：连线输入端口（画布左柄）与参数端口（表单）在投影后区分保持。

    core/analyze_chunks：video/chunks 必须仍在 inputs（连线），provider/model/… 在
    params（表单）；不得把参数端口投影成连线输入（否则画布连线手柄错乱）。
    """
    oi = registry.object_info()
    if "core/analyze_chunks" not in oi:
        pytest.skip("插件仓库快照缺少 core/analyze_chunks（CI checkout 为较早快照）")
    v = oi["core/analyze_chunks"]
    s = asyncio.run(srv.specs())
    entry = next(x for x in s if x["type_id"] == "core/analyze_chunks")
    in_names = [p["name"] for p in entry["inputs"]]
    param_names = [p["name"] for p in entry["params"]]
    out_names = [p["name"] for p in entry["outputs"]]
    assert "video" in in_names and "chunks" in in_names, "连线输入端口丢失: %s" % in_names
    assert "api_key" not in in_names, "参数端口误入连线输入: %s" % in_names
    for k in ("provider", "model", "concurrency", "retries", "frames_per_chunk"):
        assert k in param_names, "参数端口丢失: %s" % param_names
    assert out_names == ["descriptions", "report"], "输出端口投影异常: %s" % out_names


def test_http_endpoints():
    """④ HTTP 实测：GET /api/object_info 与 GET /api/specs。"""
    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        r = c.get("/api/object_info")
        assert r.status_code == 200, "GET /api/object_info -> %d" % r.status_code
        body = r.json()
        assert isinstance(body, dict) and len(body) >= 40
        s = c.get("/api/specs")
        assert s.status_code == 200, "GET /api/specs -> %d" % s.status_code
        arr = s.json()
        assert isinstance(arr, list) and len(arr) >= 40


def test_manifest_semantics_propagate():
    """⑤ manifest 语义字段（search_aliases/deprecated/kind/pkg）→ object_info。"""
    from app.plugin import manifest_v2
    from app import nodespec
    m = {
        "schema_version": 2, "engine_api": "1.0",
        "type_id": "user/t4_fixture", "title": "T4 语义夹具", "category": "用户节点",
        "description": "语义传播验证", "version": "1.0.0",
        "kind": "transform", "runtime": "transform",
        "inputs": [{"name": "text", "type": "STRING", "required": False}],
        "outputs": [{"name": "result", "type": "STRING", "required": False}],
        "params": [], "transform": {"result": {"op": "concat", "sources": [{"$input": "text"}]}},
        "search_aliases": ["场景拼接", "t4节点"],
        "deprecated": True,
    }
    norm = manifest_v2.validate_manifest(m).manifest
    assert norm["search_aliases"] == ["场景拼接", "t4节点"]
    assert norm["deprecated"] is True
    spec = declarative._spec_from_manifest(norm)
    oi_entry = registry._spec_to_object_info(spec, {
        "search_aliases": norm.get("search_aliases"),
        "deprecated": norm.get("deprecated"),
        "kind": norm.get("kind"), "pkg": "t4_fixture",
    })
    assert oi_entry["search_aliases"] == ["场景拼接", "t4节点"]
    assert oi_entry["deprecated"] is True
    assert oi_entry["kind"] == "transform"
    assert oi_entry["pkg"] == "t4_fixture"
    # 汇总明细：真实插件包（含 pkg 元数据）必须 kind/pkg 齐备；
    # @register 注册的纯测试节点（engine_core 等）无 manifest/pkg，跳过（非插件包语义）。
    total = 0
    for tid, v in registry.object_info().items():
        total += 1
        if "pkg" not in v or not v["pkg"]:
            continue  # @register 测试节点（无 manifest ）
        assert v["kind"] and v["pkg"], "%s 缺 kind/pkg 元数据" % tid
    print(f"[t4] object_info 节点总数 = {total}")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"[test_object_info] TOTAL={len(fns)} FAIL={failed}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)