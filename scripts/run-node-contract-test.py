# -*- coding: utf-8 -*-
"""运行契约测试（t9，D-01 发布门禁）：venv 后端真实执行 load_video→scene_detect。

背景：D-01（P0）core/load_video 等在 WorkerContext 下 `dict(ctx)` 崩溃（
TypeError: attribute name must be string, not 'int'），官方输入节点一运行即废。
本脚本以 30s 样例视频在 worker 进程隔离（默认 FW_PLUGIN_EXEC=worker）下跑通
load_video→scene_detect，断言两端点 SUCCESS、无 TypeError、scan_errors 空。

用法（发布门禁，backend venv）：
    FrameWeave\\backend\\.venv\\Scripts\\python.exe scripts/run-node-contract-test.py

退出码：0 = PASS（发布门禁通过）；非 0 = FAIL（阻断发布）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # FrameWeave/scripts
_FW = os.path.dirname(_HERE)                                  # FrameWeave/
_BACKEND = os.path.join(_FW, "backend")
_ROOT = os.path.dirname(_FW)                                  # 解说工坊/
sys.path.insert(0, _BACKEND)

PACKAGES = os.path.join(_ROOT, "frameweave-plugins", "packages")
BASE_NODES = os.path.join(_BACKEND, "user_nodes")
RESERVED = (".staging", ".backup", "_vendor", ".git")

# 30s 样例视频（fw-ux-env 体验环境）
SAMPLE_CANDIDATES = [
    os.path.join(_ROOT, ".tmp", "fw-ux-env", "samples", "sample_scene_30s.mp4"),
    os.path.join(_ROOT, ".tmp", "fw-ux-env", "samples", "解说素材_中文名_30秒.mp4"),
]


def _build_merged(union: str) -> int:
    """packages + 基础节点 → 临时并集 user_nodes（94 规格全量 scan 等价）。"""
    os.makedirs(union, exist_ok=True)
    for src in (PACKAGES, BASE_NODES):
        for entry in sorted(os.listdir(src)):
            if entry.startswith(".") or entry in RESERVED:
                continue
            sp = os.path.join(src, entry)
            if not os.path.isdir(sp) or not os.path.isfile(os.path.join(sp, "manifest.json")):
                continue
            dp = os.path.join(union, entry)
            if os.path.isdir(dp):
                shutil.rmtree(dp, ignore_errors=True)
            shutil.copytree(sp, dp)
    return len([d for d in os.listdir(union)
                if os.path.isdir(os.path.join(union, d))])


def _first_sample() -> str:
    for p in SAMPLE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("缺少 30s 样例视频（.tmp/fw-ux-env/samples/sample_scene_30s.mp4）")


def main() -> int:
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="fw-contract-")
    union = os.path.join(tmp, "user_nodes")
    try:
        os.environ["FRAMEWEAVE_USER_NODES"] = union
        # 保证 load_video→scene_detect 走 worker 隔离（D-01 复现环境）
        os.environ.pop("FW_PLUGIN_EXEC", None)

        from app import registry, declarative, engine  # noqa: E402
        from app.assets import create_store  # noqa: E402

        n_merged = _build_merged(union)
        registry.autodiscover()
        scan_errs = declarative.scan_errors()
        specs = {s.type_id for s in registry.list_specs()}
        print(f"[contract] 并集目录={n_merged} 规格数={len(specs)} scan_errors={len(scan_errs)}")
        if scan_errs:
            print("    SCAN_ERR:", json.dumps(scan_errs[:5], ensure_ascii=False))
            return 2
        if "core/load_video" not in specs or "core/scene_detect" not in specs:
            print("[contract] FAIL：core/load_video / core/scene_detect 未注册")
            return 2

        sample = _first_sample()
        print(f"[contract] 样例视频: {sample}（{os.path.getsize(sample)} B）")

        # 工作流：n1 load_video(path) → n2 scene_detect(video)
        # （显式构造 node.inputs/edges——from_dict 不负责从 edges 推导 inputs，
        #  真实路径由 server create/update 保存链完成）
        wf = engine.Workflow(id="contract-load-scene", name="运行契约测试")
        wf.nodes["n1"] = engine.GraphNode(
            id="n1", type_id="core/load_video",
            params={"path": sample, "copy_into_store": False,
                    "probe_timeout": 60, "trim_start": 0.0, "trim_end": 0.0})
        wf.nodes["n2"] = engine.GraphNode(id="n2", type_id="core/scene_detect", params={})
        wf.nodes["n2"].inputs["video"] = "n1"
        wf.edges = [{"source": "n1", "target": "n2",
                     "sourceHandle": "video", "targetHandle": "video"}]

        store = create_store("local", os.path.join(tmp, "assets"))
        eng = engine.Engine(store)
        print(f"[contract] 执行模式: {eng._exec_mode}（worker 隔离）")
        t1 = time.time()
        import asyncio
        asyncio.run(eng.execute(wf))
        dt = round(time.time() - t1, 1)
        print(f"[contract] 执行耗时 {dt}s")

        n1, n2 = wf.nodes["n1"], wf.nodes["n2"]
        fail = []
        if n1.status.value != "success":
            fail.append(f"n1 load_video status={n1.status.value} error={n1.error!r} detail={n1.error_detail!r}")
        elif not n1.asset_ids.get("video"):
            fail.append("n1 load_video 未产出 video 资产")
        if n2.status.value != "success":
            fail.append(f"n2 scene_detect status={n2.status.value} error={n2.error!r} detail={n2.error_detail!r}")
        elif not n2.asset_ids.get("scenes"):
            fail.append("n2 scene_detect 未产出 scenes 资产")
        for n in (n1, n2):
            if "TypeError" in (n.error or "") or "TypeError" in (n.error_detail or ""):
                fail.append(f"节点 {n.id} 仍含 TypeError（D-01 未根治）")

        if fail:
            print("[contract] FAIL:")
            for f in fail:
                print("    -", f)
            return 1

        # 运行契约 + D-01 回归双通过
        print(f"[contract] ✅ PASS：load_video→scene_detect 真实执行成功（{dt}s），"
              f"video={n1.asset_ids.get('video')[:8]}… scenes={n2.asset_ids.get('scenes')[:8]}…")
        print(f"[contract] 总耗时 {round(time.time() - t0, 1)}s")
        return 0
    finally:
        try:
            eng = None
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
