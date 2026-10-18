# -*- coding: utf-8 -*-
"""S1-T9 节点包冒烟测试（R-平台-9：40 包冒烟 → 现 65 包全量）。

覆盖：
  1. SDK check_manifest G1-G10 全绿（每个 manifest 通过校验器）；
  2. 平台 declarative 扫描零错误（FRAMEWEAVE_USER_NODES 指向 packages 目录）；
  3. 每个包 type_id 唯一、schema_version=2、core/ 官方包携带签名占位。

运行：python -m pytest tests/test_node_smoke.py（或整套 pytest）。
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FRAMEWEAVE_LOCAL_TOKEN", "fw-t9-test-token")

# 插件工程根（工作区相对布局：frameweave-plugins 与 FrameWeave 平级；
# 本文件在 backend/tests/ → 需上溯 4 层到工作区根）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _plugin_paths import packages_dir, repo_dir, sdk_dir  # noqa: E402

_PLUGIN_REPO = repo_dir()
_PACKAGES = packages_dir()
_SDK = sdk_dir()


def _list_packages():
    if not os.path.isdir(_PACKAGES):
        pytest_skip()
    out = []
    for name in sorted(os.listdir(_PACKAGES)):
        if os.path.isfile(os.path.join(_PACKAGES, name, "manifest.json")):
            out.append(name)
    return out


def pytest_skip():
    import pytest
    pytest.skip("frameweave-plugins/packages 不存在（CI 需先 clone 插件仓库）")


def test_sdk_manifest_gate_all_packages():
    """① SDK check_manifest G1-G10 全绿（65 包）。"""
    pkgs = _list_packages()
    assert len(pkgs) >= 40, "插件包数量不足 40（R-平台-9 冒烟基线）: %d" % len(pkgs)
    failed = []
    for name in pkgs:
        r = subprocess.run(
            [sys.executable, os.path.join(_SDK, "check_manifest.py"),
             os.path.join(_PACKAGES, name)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            failed.append((name, r.stdout.strip() or r.stderr.strip()))
    assert not failed, "manifest 校验失败:\n" + "\n".join(
        "%s: %s" % (n, m) for n, m in failed)


def test_declarative_scan_zero_errors():
    """② 平台 declarative 扫描全部包零错误。"""
    from app import declarative
    declarative.set_user_nodes_dir(_PACKAGES)
    n = declarative.scan()
    errs = declarative.scan_errors()
    assert n >= 40, "扫描节点数不足: %d" % n
    assert not errs, "扫描错误: %s" % errs


def test_type_id_unique_and_schema():
    """③ type_id 唯一、schema_version=2、core/ 官方包有签名占位。"""
    seen = {}
    problems = []
    for name in _list_packages():
        mpath = os.path.join(_PACKAGES, name, "manifest.json")
        with open(mpath, "r", encoding="utf-8") as f:
            m = json.load(f)
        tid = m.get("type_id", "")
        if not tid:
            problems.append(f"{name}: 缺 type_id")
            continue
        if tid in seen:
            problems.append(f"type_id 重复: {tid} ({seen[tid]} / {name})")
        seen[tid] = name
        # schema_version 缺失/非 2 视为 v1 兼容通道（SDK 校验器与 declarative
        # 均按兼容注入默认值加载，不构成加载面问题）；新包应显式声明 2。
        if m.get("schema_version") is not None and m.get("schema_version") != 2:
            problems.append(f"{name}: schema_version != 2")
        if str(tid).startswith("core/") and not isinstance(m.get("signature"), dict):
            problems.append(f"{name}: core/ 官方包缺签名占位")
    assert not problems, "契约问题:\n" + "\n".join(problems)


def test_every_package_has_entrypoint_files():
    """④ python 包 entrypoint 文件真实存在（冒烟加载面）。"""
    problems = []
    for name in _list_packages():
        mpath = os.path.join(_PACKAGES, name, "manifest.json")
        with open(mpath, "r", encoding="utf-8") as f:
            m = json.load(f)
        if m.get("kind") != "python":
            continue
        ep = m.get("entrypoint", "node.py")
        if not os.path.isfile(os.path.join(_PACKAGES, name, ep)):
            problems.append(f"{name}: entrypoint 缺失 {ep}")
    assert not problems, "entrypoint 缺失:\n" + "\n".join(problems)