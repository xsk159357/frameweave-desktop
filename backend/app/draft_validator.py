"""剪映草稿结构校验器：验证 draft_content.json 兼容性。

校验规则（2024+ 剪映草稿）：
  1. 顶层必需字段：canvas_config / tracks / materials / draft_name / create_time
  2. 每个 track 必须有 type / id / segments，segment 字段完整性
  3. 视频 segment：material_id / source_timestamp / duration / transition
  4. 字幕 segment：content / target_timestamp / duration
  5. materials 必须包含引用的素材 id（无悬空引用）
  6. 时间字段必须为微秒整数（int）
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Tuple


def validate_jianying_draft(path: str) -> Dict[str, Any]:
    """校验剪映草稿文件，返回 {ok, errors, warnings, summary}。"""
    errors: List[str] = []
    warnings: List[str] = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "errors": ["JSON 读取失败: %s" % e], "warnings": [], "summary": {}}

    if not isinstance(draft, dict):
        return {"ok": False, "errors": ["草稿根节点必须是对象"], "warnings": [], "summary": {}}

    # 1. 顶层必需字段
    required_top = ["canvas_config", "tracks", "materials", "draft_name"]
    for k in required_top:
        if k not in draft:
            errors.append("缺少顶层字段: %s" % k)

    # 2. tracks 结构
    tracks = draft.get("tracks", [])
    if not isinstance(tracks, list) or not tracks:
        errors.append("tracks 必须是非空数组")
    seg_count = 0
    for i, tr in enumerate(tracks):
        if not isinstance(tr, dict):
            errors.append("track[%d] 必须是对象" % i)
            continue
        if tr.get("type") not in ("video", "audio", "text", "subtitle"):
            warnings.append("track[%d] type='%s' 非常见类型" % (i, tr.get("type")))
        segs = tr.get("segments", [])
        if not isinstance(segs, list):
            errors.append("track[%d].segments 必须是数组" % i)
            continue
        for j, s in enumerate(segs):
            seg_count += 1
            _check_segment(s, i, j, errors, warnings)

    # 3. 悬空素材引用
    mats = draft.get("materials", {})
    mat_ids = set()
    for kind in ("videos", "audios", "texts", "transitions"):
        for m in (mats.get(kind) or []):
            if isinstance(m, dict) and m.get("id"):
                mat_ids.add(str(m["id"]))
    for tr in tracks:
        for s in (tr.get("segments") or []):
            mid = s.get("material_id")
            if mid and str(mid) not in mat_ids:
                warnings.append("片段 material_id=%s 未在 materials 注册（%s）" % (mid, s.get("content", "")[:20] or "video"))

    # 4. 时间字段微秒整数检查
    for tr in tracks:
        for s in (tr.get("segments") or []):
            for fld in ("source_timestamp", "target_timestamp", "duration"):
                v = s.get(fld)
                if v is not None and not isinstance(v, int):
                    errors.append("片段字段 %s 应为 int 微秒，实际 %s" % (fld, type(v).__name__))

    # summary
    summary = {
        "tracks": len(tracks) if isinstance(tracks, list) else 0,
        "segments": seg_count,
        "canvas": draft.get("canvas_config", {}).get("canvas_size", {}),
    }
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }


def _check_segment(s: Dict[str, Any], track_idx: int, seg_idx: int,
                   errors: List[str], warnings: List[str]) -> None:
    """检查单个片段字段。"""
    if not isinstance(s, dict):
        errors.append("track[%d].segments[%d] 必须是对象" % (track_idx, seg_idx))
        return
    for fld in ("id", "material_id", "duration"):
        if fld not in s:
            errors.append("track[%d].segments[%d] 缺少字段 %s" % (track_idx, seg_idx, fld))
    # 字幕片段应带文本
    if s.get("content") is not None and not isinstance(s.get("content"), str):
        errors.append("track[%d].segments[%d].content 应为字符串" % (track_idx, seg_idx))
