"""LoadVideo 节点：导入视频，解析元数据，产出 VIDEO 资产。"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
from typing import Any, Dict

from ..assets import Asset, file_fingerprint
from ..nodespec import NodeBase, NodeSpec, PortSpec, PortType
from ..registry import register


def ffprobe_meta(video_path: str) -> Dict[str, Any]:
    """用 ffprobe 提取视频元数据；找不到 ffprobe 时降级为文件信息。"""
    meta: Dict[str, Any] = {"path": video_path, "exists": os.path.exists(video_path)}
    if not meta["exists"]:
        return meta
    meta["size"] = os.path.getsize(video_path)
    meta["fingerprint"] = file_fingerprint(video_path)
    # 尝试 ffprobe
    for probe in ("ffprobe", "ffprobe.exe"):
        try:
            out = subprocess.run(
                [probe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0 and out.stdout:
                data = json.loads(out.stdout)
                fmt = data.get("format", {})
                meta["duration"] = float(fmt.get("duration", 0) or 0)
                meta["bitrate"] = fmt.get("bit_rate", "")
                meta["format_name"] = fmt.get("format_name", "")
                for st in data.get("streams", []):
                    if st.get("codec_type") == "video":
                        meta["width"] = int(st.get("width", 0) or 0)
                        meta["height"] = int(st.get("height", 0) or 0)
                        meta["fps"] = _parse_fps(st.get("avg_frame_rate", ""))
                        meta["video_codec"] = st.get("codec_name", "")
                    if st.get("codec_type") == "audio":
                        meta["audio_codec"] = st.get("codec_name", "")
            break
        except (OSError, subprocess.TimeoutExpired):
            continue
    return meta


def _parse_fps(rate: str) -> float:
    try:
        if "/" in rate:
            n, d = rate.split("/")
            return round(float(n) / float(d), 3)
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


@register
class LoadVideoNode(NodeBase):
    @classmethod
    def spec(cls) -> NodeSpec:
        return NodeSpec(
            type_id="core/load_video",
            title="导入视频",
            category="输入",
            description="导入本地视频文件并解析元数据（时长/分辨率/帧率/编码）",
            outputs=[
                PortSpec(name="video", type=PortType.VIDEO, label="视频"),
                PortSpec(name="meta", type=PortType.JSON, label="元数据"),
            ],
            params=[
                PortSpec(name="path", type=PortType.STRING, label="视频路径",
                         widget="file", description="本地视频文件路径"),
            ],
            version="1.0.0",
        )

    async def run(self, ctx, inputs, params) -> Dict[str, Any]:
        path = params.get("path", "")
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"视频文件不存在: {path}")
        meta = ffprobe_meta(path)
        # 复制到资产目录（统一管理）
        store = ctx["store"]
        dest_dir = os.path.join(store.files_dir, "videos")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(path))
        if os.path.abspath(dest) != os.path.abspath(path):
            shutil.copy2(path, dest)
        asset = Asset(id="", kind=PortType.VIDEO.value, path=dest, meta=meta,
                      fingerprint=meta.get("fingerprint", ""), node_id="")
        store.save_asset(asset)
        return {"video": asset, "meta": meta}
