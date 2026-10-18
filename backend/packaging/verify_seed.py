# -*- coding: utf-8 -*-
"""内置基础节点种子验证（t6）：在模拟 frozen 目录下执行
declarative.seed_builtin_basics + declarative.scan，断言 24 个基础节点全部
可加载、scan_errors 为空，并覆盖幂等/跳过/验签拒绝路径。

模拟要素（与打包版运行环境对齐）：
  - sys.frozen = True（触发 declarative 的 %APPDATA% 数据目录分支与 seed 入口）
  - APPDATA 指向临时目录（%APPDATA%/FrameWeave/user_nodes = 用户节点目录）
  - sys._MEIPASS 指向临时镜像根（内置 builtin_basics/ 种子树，对应打包产物
    _internal/builtin_basics/）
  - 官方公钥锚：源码模式解析到 backend/app/fw_official_pubkey.json（与打包版
    _internal/app/fw_official_pubkey.json 内容一致）

用法：
  python packaging/verify_seed.py
  （本脚本自动重新生成种子树 packaging/build/builtin_basics，一次性端到端）
退出码：0 = 全部通过；非 0 = 失败（打印断言明细）。
"""
from __future__ import annotations

import json
import os
import py_compile
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
_PKG = os.path.dirname(os.path.abspath(__file__))  # backend/packaging
sys.path.insert(0, _ROOT)
sys.path.insert(0, _PKG)

import build_seed  # noqa: E402


def _compile_targets() -> None:
    """py_compile：变更文件语法门（spec 仅语法检查，不执行）。"""
    targets = [
        os.path.join(_ROOT, "app", "declarative.py"),
        os.path.join(_ROOT, "app", "server.py"),
        os.path.join(_PKG, "build_seed.py"),
        os.path.join(_PKG, "verify_seed.py"),
        os.path.join(_PKG, "frameweave-backend.spec"),
    ]
    for t in targets:
        py_compile.compile(t, doraise=True)
        print(f"[verify] py_compile OK: {os.path.basename(t)}")


def _expected_type_ids() -> set:
    return {("core/" + n[len("core_"):]) for n in build_seed.BASIC_NODE_DIRS}


def _fresh_mirror(tmp: str, tag: str, seed_tree: str, tamper_pkg: str = "") -> str:
    """构造模拟 _MEIPASS 镜像：builtin_basics/ 种子树（可对某包篡改 manifest）。"""
    meipass = os.path.join(tmp, tag)
    basics = os.path.join(meipass, "builtin_basics")
    shutil.copytree(seed_tree, basics)
    if tamper_pkg:
        mpath = os.path.join(basics, tamper_pkg, "manifest.json")
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["description"] = data.get("description", "") + "（被篡改）"
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return meipass


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    _compile_targets()

    # 1) 重新生成种子树（端到端：build_seed 亦为变更文件，一并验证）
    seed_tree = os.path.join(_PKG, "build", "builtin_basics")
    build_seed.build_seed(os.path.join(build_seed._ROOT, "user_nodes"), seed_tree)
    seed_manifest = json.load(
        open(os.path.join(seed_tree, "seed_manifest.json"), encoding="utf-8"))
    assert seed_manifest.get("count") == 24, "seed_manifest count != 24"
    assert len(seed_manifest.get("packages") or []) == 24, "packages != 24"
    print("[verify] seed_manifest: count=24, anchor_key_id=",
          seed_manifest.get("anchor_key_id"), "signed=", "signature" in seed_manifest)

    tmp = tempfile.mkdtemp(prefix="fw-seed-verify-")
    try:
        # 2) frozen 模拟：在导入 declarative 之前设置
        os.environ["APPDATA"] = os.path.join(tmp, "appdata")
        os.environ.pop("FRAMEWEAVE_USER_NODES", None)
        sys.frozen = True  # type: ignore[attr-defined]
        sys._MEIPASS = _fresh_mirror(tmp, "meipass1", seed_tree)  # type: ignore[attr-defined]

        import app.declarative as declarative  # noqa: E402

        expected_user_nodes = os.path.join(tmp, "appdata", "FrameWeave", "user_nodes")
        assert declarative.USER_NODES_DIR == expected_user_nodes, (
            f"frozen 用户节点目录未命中 %APPDATA%: {declarative.USER_NODES_DIR}")
        print("[verify] frozen USER_NODES_DIR =", declarative.USER_NODES_DIR)

        # 3) 空目录首启 seed → 24
        copied = declarative.seed_builtin_basics()
        assert copied == 24, f"首启 seed 复制数 != 24: {copied}"
        print("[verify] 首启 seed 复制 24 包 OK")

        # 4) scan：24 全部可加载、scan_errors 空
        loaded = declarative.scan()
        errors = declarative.scan_errors()
        assert loaded == 24, f"scan 加载数 != 24: {loaded}"
        assert errors == [], f"scan_errors 非空: {errors}"
        tids = {s.type_id for s in declarative.list_specs()}
        assert tids == _expected_type_ids(), (
            f"type_id 集合不符: 缺={_expected_type_ids() - tids} 多={tids - _expected_type_ids()}")
        print("[verify] scan: 24/24 加载、scan_errors 空、type_id 集合精确匹配 OK")

        # 5) 幂等：再次 seed → 0（已有节点，不重复）
        assert declarative.seed_builtin_basics() == 0, "重复 seed 应返回 0"
        print("[verify] 幂等：已 seed 后再次调用返回 0 OK")

        # 6) 非空跳过：伪造用户自装包 → 不覆盖
        user_pkg = os.path.join(expected_user_nodes, "user_dummy_test")
        os.makedirs(user_pkg, exist_ok=True)
        with open(os.path.join(user_pkg, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": 2, "engine_api": "1.0", "kind": "transform",
                       "type_id": "user/dummy_test", "title": "t", "category": "控制",
                       "description": "t", "version": "1.0.0", "inputs": [], "outputs": [],
                       "params": [], "permission": {}, "transform": {}}, f)
        assert declarative.seed_builtin_basics() == 0, "非空目录应跳过 seed"
        # 24 个种子包一个都不能被动过
        for n in build_seed.BASIC_NODE_DIRS:
            assert os.path.isdir(os.path.join(expected_user_nodes, n)), f"种子包缺失: {n}"
        print("[verify] 非空跳过：用户自装包存在时不覆盖/不删除种子 OK")

        # 7) 验签拒绝：篡改种子 manifest → 该包不复制
        sys._MEIPASS = _fresh_mirror(tmp, "meipass2", seed_tree, tamper_pkg="core_text_input")  # type: ignore[attr-defined]
        os.environ["APPDATA"] = os.path.join(tmp, "appdata2")
        # declarative.USER_NODES_DIR 是模块常量；用 env 覆盖模拟第二个"用户"实例
        os.environ["FRAMEWEAVE_USER_NODES"] = os.path.join(tmp, "appdata2", "user_nodes")
        saved_dir = declarative.USER_NODES_DIR
        declarative.set_user_nodes_dir(os.path.join(tmp, "appdata2", "user_nodes"))
        copied2 = declarative.seed_builtin_basics()
        assert copied2 == 23, f"篡改包应被拒绝复制（期望 23，实际 {copied2}）"
        assert not os.path.isdir(
            os.path.join(declarative.USER_NODES_DIR, "core_text_input")), \
            "被篡改的 core_text_input 不应被复制"
        print("[verify] 验签拒绝：篡改 manifest 的种子包不被复制（23/24）OK")
        declarative.set_user_nodes_dir(saved_dir)

        # 8) 源码模式跳过：frozen=False → seed 直接返回 0
        sys.frozen = False  # type: ignore[attr-defined]
        assert declarative.seed_builtin_basics() == 0, "非 frozen 模式应跳过 seed"
        print("[verify] 源码模式（非 frozen）seed 返回 0 OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("[verify] ✅ 全部通过：py_compile + 模拟 frozen 下 24 基础节点可加载、"
          "scan_errors 空、幂等/跳过/验签拒绝均符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
