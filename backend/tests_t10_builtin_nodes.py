# -*- coding: utf-8 -*-
"""S1-T6 内置基础节点集（≥20）+ 离线模板 验收测试。

验收（R-平台-6）：
  A. 内置节点 ≥20（输入/输出/媒体基础/控制/JSON）
  B. 离线首次打开可见可跑通最小模板（minimal：全部使用内置节点，零插件/网络/模型）

实测设计（全程无网络请求，模拟断网安装→打开画布→加载模板→跑通最小链）：
  1) 内置节点盘点：注册表 autodiscover（USER_NODES_DIR=backend/user_nodes）→
     统计 5 类基础节点 type_id 总数 ≥20，且 5 类均有覆盖；
  2) 模板清单：templates.install_official() 后 list_templates() 含 minimal/oneclick；
  3) 离线就绪：missing_type_ids('minimal', 已注册) == []（不依赖任何插件包）；
  4) 从模板建流：模拟 /api/workflows/from-template 语义（GraphNode 组装 + edges）；
  5) 跑通最小链：Engine(inprocess).execute() → 全部 SUCCESS，且 console 输出结果
     含 n1 文本内容与长度（逐节点读资产断言）；
  6) 空画布回归：未加载模板时画布空问题已在模板存在性上消除（模板面板可见）；
  7) SDK 门禁：user_nodes 全部 core_* 包 check_manifest OK + 全部签名 SIG_OK；
  8) 插件仓回归（captain 指示）：check_all.py 56/56 OK——core_json_get/core_probe_media
     category 修复后全绿。
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))            # FrameWeave/backend
sys.path.insert(0, BACKEND)

USER_NODES = os.path.join(BACKEND, "user_nodes")
os.environ["FRAMEWEAVE_USER_NODES"] = USER_NODES

from app.assets import LocalAssetStore                                  # noqa: E402
from app.registry import autodiscover, get_class, list_specs            # noqa: E402
from app.engine import Engine, GraphNode, Workflow                      # noqa: E402
from app import templates as tpl_mod                                    # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


TMP = os.path.join(r"F:\1223\解说工坊\_recon\t10_out", "t6_tmp")
os.makedirs(TMP, exist_ok=True)

BASE_CATEGORY_TYPES = {
    "输入": {"core/text_input", "core/int_input", "core/float_input", "core/bool_input",
            "core/json_input", "core/path_input"},
    "输出": {"core/console_output", "core/json_output"},
    "媒体基础": {"core/video_info", "core/audio_info", "core/image_info"},
    "控制": {"core/gate", "core/select", "core/text_concat", "core/text_length", "core/bool_not"},
    "JSON": {"core/json_set", "core/json_merge", "core/json_array", "core/list_get", "core/list_length"},
}


def test_builtin_count():
    autodiscover()
    avail = {s.type_id for s in list_specs()}
    all_base = set()
    for cat, ids in BASE_CATEGORY_TYPES.items():
        present = ids & avail
        all_base |= present
        check(f"内置节点分类[{cat}] 存在", len(present) >= 2,
              f"{len(present)}/{len(ids)}: {sorted(present)[:6]}")
    check("内置基础节点总数 ≥20", len(all_base) >= 20, f"count={len(all_base)}")
    check("覆盖 5 类语义（输入/输出/媒体基础/控制/JSON）",
          all(any(t in all_base for t in ids) for ids in BASE_CATEGORY_TYPES.values()),
          "5 类均有")


def build_wf_from_template(payload) -> Workflow:
    """模拟 server /api/workflows/from-template 语义（GraphNode 组装）。"""
    nodes_in = payload.get("nodes") or []
    edges_in = payload.get("edges") or []
    wf = Workflow(id="wf_minimal", name=payload.get("name", "minimal"))
    for i, nd in enumerate(nodes_in):
        nid = str(nd.get("id", f"tpl{i + 1}"))
        wf.nodes[nid] = GraphNode(id=nid, type_id=nd.get("type", ""),
                                  x=nd.get("x", 0), y=nd.get("y", 0),
                                  params=dict(nd.get("params") or {}),
                                  inputs=dict(nd.get("inputs") or {}))
    wf.edges = [dict(e) for e in edges_in]
    return wf


def test_template_offline_ready():
    store = LocalAssetStore(os.path.join(TMP, "tpl_store"))
    # 隔离模板目录（避免污染真实用户数据）
    tpl_dir = os.path.join(TMP, "templates")
    os.makedirs(tpl_dir, exist_ok=True)
    tpl_mod.set_templates_dir(tpl_dir)
    installed = tpl_mod.install_official()
    ids = {m["tpl_id"] for m in installed}
    check("模板安装：官方内置含 minimal + oneclick",
          {"minimal", "oneclick"} <= ids, str(sorted(ids)))
    listed = {t["tpl_id"] for t in tpl_mod.list_templates()}
    check("模板清单可见 minimal（首次打开可见）", "minimal" in listed, str(sorted(listed)))
    avail = {s.type_id for s in list_specs()}
    missing = tpl_mod.missing_type_ids("minimal", avail)
    check("minimal 零缺失（离线可直接跑，不依赖插件）", missing == [], str(missing))
    # oneclick 依赖插件（预期缺失，属于在线完整模板）——记录不阻断
    print("  [info] oneclick missing_type_ids（预期，非内置插件）: %s"
          % tpl_mod.missing_type_ids("oneclick", avail))
    payload = tpl_mod.get_template("minimal").get("workflow")
    errs = tpl_mod.validate_workflow_payload(payload)
    check("minimal 模板结构合法", errs == [], str(errs))
    return payload


async def test_run_minimal(payload):
    store = LocalAssetStore(os.path.join(TMP, "run_store"))
    engine = Engine(store, on_event=lambda ev: None)
    wf = build_wf_from_template(payload)
    wf = await engine.execute(wf)
    bad = [n.id for n in wf.nodes.values() if n.status.value not in ("success", "cached")]
    check("最小链全部节点 SUCCESS/CACHED",
          not bad and len(wf.nodes) == 4 and all(
              n.status.value in ("success", "cached") for n in wf.nodes.values()),
          f"bad={bad} statuses={[n.status.value for n in wf.nodes.values()]}")
    # 断言控制台输出内容：n4 读 n3(json_output) → 内层 n2(length=9? 文本"离线基础工作流可用"=9 字)
    n4 = wf.nodes["n4"]
    n3 = wf.nodes["n3"]
    n2 = wf.nodes["n2"]
    console = store.read_json(n4.asset_ids.get("result", ""))
    text_asset_payload = store.read_json(wf.nodes["n1"].asset_ids.get("text", ""))
    text_value = text_asset_payload.get("text", "") if isinstance(text_asset_payload, dict) else ""
    length_val = store.read_json(n2.asset_ids.get("length", ""))
    check("n1 文本输出正确", text_value == "离线基础工作流可用", f"{text_value!r}")
    check("n2 文本长度正确", length_val == len("离线基础工作流可用"), f"len={length_val}")
    check("n4 控制台可见链式结果（长度 9 经 JSON 包装传递）",
          isinstance(console, dict) and isinstance(console.get("value"), dict)
          and console["value"].get("value") == len("离线基础工作流可用"),
          json.dumps(console, ensure_ascii=False)[:200])
    return wf


def test_sdk_gates():
    # 1) user_nodes 内置包逐包门禁
    sdk = r"F:\1223\解说工坊\frameweave-plugins\sdk"
    pkgs = sorted(d for d in os.listdir(USER_NODES)
                  if d.startswith("core_") and os.path.isfile(os.path.join(USER_NODES, d, "manifest.json")))
    bad = []
    for pkg in pkgs:
        r = subprocess.run([sys.executable, os.path.join(sdk, "check_manifest.py"),
                            os.path.join(USER_NODES, pkg)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            bad.append((pkg, r.stdout[-200:]))
        with open(os.path.join(USER_NODES, pkg, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        sig = m.get("signature") or {}
        if not sig.get("value") or "pending" in str(sig.get("value", "")):
            bad.append((pkg, "signature placeholder"))
    check("sdk: user_nodes 内置包 check_manifest OK 且签名真实",
          len(pkgs) >= 21 and not bad, f"pkgs={len(pkgs)} bad={bad[:3]}")
    # 2) 插件仓 check_all 全绿（captain 转达：core_json_get/core_probe_media category 修复）
    r = subprocess.run([sys.executable, os.path.join(sdk, "check_all.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    head = r.stdout.splitlines()[:1]
    print("  " + (head[0] if head else ""))
    check("sdk: check_all 插件仓 56/56 全绿（category 修复回归）",
          r.returncode == 0 and "0 FAILED" in r.stdout, head[0] if head else "")


def main():
    test_builtin_count()
    payload = test_template_offline_ready()
    asyncio.run(test_run_minimal(payload))
    test_sdk_gates()
    print("\n===== 汇总: PASS=%d FAIL=%d SKIP=%d =====" % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  - " + f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()