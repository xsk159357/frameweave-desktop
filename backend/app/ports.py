"""端口类型体系：定义节点输入/输出端口的数据类型。"""
from __future__ import annotations
from enum import Enum

class PortType(str, Enum):
    VIDEO = "VIDEO"        # 视频资产（文件路径 + 元数据）
    IMAGE = "IMAGE"        # 图片/帧
    AUDIO = "AUDIO"        # 音频资产
    SCRIPT = "SCRIPT"      # 文案文本
    SUBTITLE = "SUBTITLE"  # 字幕列表
    SEGMENTS = "SEGMENTS"  # 片段列表（场景/镜头切分结果）
    TIMELINE = "TIMELINE"  # 时间线资产
    JSON = "JSON"          # 通用结构化数据
    STRING = "STRING"
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    ANY = "ANY"

    @property
    def is_media(self) -> bool:
        return self in (PortType.VIDEO, PortType.AUDIO, PortType.IMAGE, PortType.TIMELINE)

    @property
    def can_broadcast(self) -> bool:
        """该类型是否允许宽泛连线（如 ANY/JSON 可连到大部分端口）"""
        return self in (PortType.ANY, PortType.JSON)


def can_connect(src: PortType, dst: PortType) -> bool:
    """端口连线兼容性：源类型可连到目标类型（ANY 可连一切）。"""
    if src is PortType.ANY or dst is PortType.ANY or src is dst:
        return True
    if src is PortType.JSON:
        return dst in (PortType.JSON, PortType.SCRIPT, PortType.SEGMENTS, PortType.SUBTITLE, PortType.STRING)
    if dst is PortType.JSON:
        return True
    # 媒体资产窄化：VIDEO 可连到 IMAGE？否（需要显式抽帧节点）
    return False
