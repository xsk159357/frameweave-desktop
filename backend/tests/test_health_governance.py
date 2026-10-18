# -*- coding: utf-8 -*-
"""S1-T9 健康度治理断言测试（R-工程-1：根级无散落 / 归档 manifest 齐全）。

运行：python -m pytest tests/test_health_governance.py（或整套 pytest）。

覆盖（与 _archive/2026-10-15-S1T9-健康度治理/QUARANTINE_MANIFEST.md 对账）：
  1. backend 现存 fw_* 目录 ⊆ 05-提权脚本/fw_dirs.txt 待提权清单
     （118 个 ACL 拒绝目录按 AGENTS.md §5 标记待提权，不要求物理归零）；
  2. backend 无 *.work.* 中间产物；
  3. cloud.db / pyarmor.bug.log 已移出源码树（backend 根不存在，归档区存在）；
  4. desktop 无 NVIDIA Corporation/umdlogs 异常目录、无 *.work.*；
  5. 根 release/ 仅保留 0.3.0 三件套（多版本已归档）；
  6. 归档批次结构完整：QUARANTINE_MANIFEST.md + 04-release多版本/0.1.0；
  7. classify.json 已含 cloud.db 的 archived_path（数据维护闭环）。
"""
import json
import os
import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.dirname(os.path.dirname(BACKEND))
ARCHIVE = os.path.join(WORKSPACE, "_archive", "2026-10-15-S1T9-健康度治理")
FW_LIST = os.path.join(ARCHIVE, "05-提权脚本", "fw_dirs.txt")
MANIFEST = os.path.join(ARCHIVE, "QUARANTINE_MANIFEST.md")


def test_backend_fw_dirs_subset_of_pending_list():
    """① backend 现存 fw_* 目录必须全部在待提权清单中（新增未登记目录=散落）。"""
    if not os.path.isfile(FW_LIST):
        import pytest
        pytest.skip("fw_dirs.txt 不存在（归档批次未落盘，CI 无本地批次时跳过）")
    listed = set()
    with open(FW_LIST, "r", encoding="utf-8-sig") as f:  # 文件带 UTF-8 BOM
        for line in f:
            line = line.strip()
            if line:
                listed.add(line)
    present = {d for d in os.listdir(BACKEND)
               if d.startswith("fw_") and os.path.isdir(os.path.join(BACKEND, d))}
    unknown = present - listed
    assert not unknown, "backend 存在未登记的 fw_* 目录（应归档并登记）: %s" % sorted(unknown)[:10]


def test_backend_no_work_artifacts():
    """② backend 递归无 *.work.* 中间产物。"""
    bad = []
    for root, _dirs, files in os.walk(BACKEND):
        if ".venv" in root or "node_modules" in root:
            continue
        for fn in files:
            if ".work." in fn:
                bad.append(os.path.join(root, fn))
    assert not bad, "backend 残留 *.work.* 文件: %s" % bad[:5]


def test_cloud_db_and_pyarmor_log_removed():
    """③ cloud.db / pyarmor.bug.log 已移出 backend（源码树干净）。

    2026-10-19：归档区在工作区（非仓库），CI 检出的是干净仓库、不含 _archive/。
    因此「源码树里没有」是硬断言；「归档区里有」只在本地工作区存在该批次时校验
    （否则 CI 必红——这是此前从未真正跑通过 CI 才没暴露的问题）。
    """
    assert not os.path.exists(os.path.join(BACKEND, "cloud.db"))
    assert not os.path.exists(os.path.join(BACKEND, "pyarmor.bug.log"))
    archived = os.path.join(ARCHIVE, "01-backend-散落", "cloud.db")
    if os.path.isdir(ARCHIVE):
        assert os.path.isfile(archived), "归档批次存在但 cloud.db 不在其中"
        assert os.path.isfile(os.path.join(ARCHIVE, "01-backend-散落", "pyarmor.bug.log"))


def test_desktop_no_anomalies():
    """④ desktop 无 NVIDIA 异常目录、无 *.work.*。"""
    desktop = os.path.join(BACKEND, "..", "desktop")
    assert not os.path.exists(os.path.join(desktop, "NVIDIA Corporation"))
    for root, _dirs, files in os.walk(desktop):
        if "node_modules" in root:
            continue
        for fn in files:
            assert ".work." not in fn, "desktop 残留 *.work.*: %s" % os.path.join(root, fn)


def test_root_release_only_current():
    """⑤ 根 release/ 仅保留 0.3.0 三件套（历史多版本已移入归档）。"""
    rel = os.path.join(WORKSPACE, "release")
    if not os.path.isdir(rel):
        return  # 无根 release 也视为合规（版本由仓库内管理）
    vers = [d for d in os.listdir(rel)
            if os.path.isdir(os.path.join(rel, d)) and d.startswith("0.")]
    assert vers == ["0.3.0"], "根 release/ 版本目录异常: %s" % vers
    three = os.listdir(os.path.join(rel, "0.3.0"))
    assert any(n.startswith("FrameWeave-Setup-0.3.0.exe") for n in three)
    assert any(n == "latest.yml" for n in three)


def test_archive_batch_structure_complete():
    """⑥ 归档批次 manifest 与历史版本分区齐全（R-工程-1）。

    2026-10-19：归档区在工作区、不在仓库；CI 检出的是干净仓库（无 _archive/），
    本项只在本地工作区可验，CI 上 skip。
    """
    if not os.path.isdir(ARCHIVE):
        pytest.skip("_archive/ 不在仓库内（CI 检出无工作区归档）")
    assert os.path.isfile(MANIFEST)
    assert os.path.isdir(os.path.join(ARCHIVE, "01-backend-散落"))
    assert os.path.isdir(os.path.join(ARCHIVE, "04-release多版本", "0.1.0"))
    assert os.path.isdir(os.path.join(ARCHIVE, "05-提权脚本"))
    with open(MANIFEST, "r", encoding="utf-8") as f:
        content = f.read()
    assert "S1-T9" in content and "待提权" in content


def test_classify_json_has_archived_path():
    """⑦ classify.json 数据维护闭环：cloud.db 条目含 archived_path。"""
    cpath = os.path.join(WORKSPACE, "classify.json")
    if not os.path.isfile(cpath):
        pytest.skip("classify.json 属工作区数据，不在仓库内")
    assert os.path.isfile(cpath)
    with open(cpath, "r", encoding="utf-8-sig") as f:  # 文件带 UTF-8 BOM
        data = json.load(f)
    entries = data.get("items", []) if isinstance(data, dict) else data
    hits = [e for e in entries if str(e.get("path", "")).endswith("cloud.db")]
    assert hits and any(h.get("archived_path") for h in hits), \
        "classify.json 未记录 cloud.db archived_path"
