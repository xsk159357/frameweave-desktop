"""端口类型体系：定义节点输入/输出端口的数据类型。

P2（t4）注册制：13 核心类型保持既有枚举成员（全部消费方零破坏），
在此基础上支持运行时扩展：
- register_port_type(name, *, is_media, can_broadcast)  显式注册扩展类型
- resolve_port_type(value)                               枚举/扩展表解析（未知 → None）
- list_port_types()                                      全量注册表（/api/port_types 用）
- register_connect_rule(rule)                            连线裁决规则注册驱动
- can_connect(src, dst)                                  内置规则 + 注册规则裁决
"""
from __future__ import annotations
import re
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        if self in (PortType.VIDEO, PortType.AUDIO, PortType.IMAGE, PortType.TIMELINE):
            return True
        return bool(_EXT_META.get(str(self._name_), {}).get("is_media", False))

    @property
    def can_broadcast(self) -> bool:
        """该类型是否允许宽泛连线（如 ANY/JSON 可连到大部分端口）"""
        if self in (PortType.ANY, PortType.JSON):
            return True
        return bool(_EXT_META.get(str(self._name_), {}).get("can_broadcast", False))


# ---------------------------------------------------------------- 扩展注册表（P2）
_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
_EXT_TYPES: Dict[str, PortType] = {}
_EXT_META: Dict[str, Dict[str, Any]] = {}
_EXT_LOCK = threading.Lock()

# 13 核心类型（注册表种子；/api/port_types 全量展示）
_CORE_TYPES: Tuple[str, ...] = tuple(m.value for m in PortType)


def register_port_type(name: str, *, is_media: Optional[bool] = None,
                       can_broadcast: Optional[bool] = None) -> PortType:
    """显式注册扩展端口类型（幂等：同名已注册返回既有成员）。

    元数据仅在显式提供时更新（is_media/can_broadcast=None 保留既有值）；
    未注册名称不自动创建（manifest/PortSpec 引用的未知类型 → 校验拒绝，防拼写漂移）。
    """
    key = str(name).upper().strip()
    if not _TYPE_RE.match(key):
        raise ValueError(f"端口类型名不合法（须 ^[A-Z][A-Z0-9_]{{1,31}}$）: {name!r}")
    if key in {m.name for m in PortType} or key in _CORE_TYPES:
        raise ValueError(f"端口类型 {key!r} 与 13 核心类型冲突")
    with _EXT_LOCK:
        member = _EXT_TYPES.get(key)
        if member is None:
            member = str.__new__(PortType, key)  # str-Enum 必须走 str.__new__
            member._name_ = key          # type: ignore[attr-defined]
            member._value_ = key
            PortType._member_map_[key] = member          # type: ignore[attr-defined]
            PortType._value2member_map_[key] = member    # type: ignore[attr-defined]
            _EXT_TYPES[key] = member
        meta = _EXT_META.setdefault(key, {"is_media": False, "can_broadcast": False})
        if is_media is not None:
            meta["is_media"] = bool(is_media)
        if can_broadcast is not None:
            meta["can_broadcast"] = bool(can_broadcast)
        return member


def resolve_port_type(value: Any) -> Optional[PortType]:
    """解析端口类型：枚举 13 核心 → 扩展注册表；未知 → None（调用方决定兜底/拒绝）。"""
    if isinstance(value, PortType):
        return value
    key = str(value).upper().strip()
    try:
        return PortType[key]
    except KeyError:
        return _EXT_TYPES.get(key)


def list_port_types() -> List[Dict[str, Any]]:
    """全量注册表（/api/port_types 输出）。核心 13 + 扩展。"""
    out = []
    for member in PortType:
        name = member.name
        out.append({
            "name": name, "value": member.value,
            "is_media": member.is_media, "can_broadcast": member.can_broadcast,
            "core": True,
        })
    for key, member in sorted(_EXT_TYPES.items()):
        out.append({
            "name": key, "value": member.value,
            "is_media": bool(_EXT_META.get(key, {}).get("is_media", False)),
            "can_broadcast": bool(_EXT_META.get(key, {}).get("can_broadcast", False)),
            "core": False,
        })
    return out


# ---------------------------------------------------------------- 连线规则（注册驱动）
_CONNECT_RULES: List[Callable[[PortType, PortType], Optional[bool]]] = []


def register_connect_rule(rule: Callable[[PortType, PortType], Optional[bool]]) -> Callable:
    """注册连线裁决规则：rule(src, dst) -> Optional[bool]（None = 不裁决，交后续/默认拒绝）。

    内置规则（ANY/JSON/同类型）先裁决；扩展类型默认 deny，由注册规则显式放行。
    """
    _CONNECT_RULES.append(rule)
    return rule


def register_port_connect(src_name: str, dst_name: str, can: bool = True) -> None:
    """便捷注册：端口对级连线规则（如 "CUSTOM_A" 可连 "STRING"）。"""
    def _rule(src: PortType, dst: PortType) -> Optional[bool]:
        if src.name == src_name and dst.name == dst_name:
            return can
        return None
    register_connect_rule(_rule)


def can_connect(src: PortType, dst: PortType) -> bool:
    """端口连线兼容性：内置规则 + 注册规则。"""
    if src is PortType.ANY or dst is PortType.ANY or src is dst:
        return True
    if src is PortType.JSON:
        return dst in (PortType.JSON, PortType.SCRIPT, PortType.SEGMENTS, PortType.SUBTITLE, PortType.STRING)
    if dst is PortType.JSON:
        return True
    for rule in _CONNECT_RULES:
        try:
            r = rule(src, dst)
        except Exception:
            continue
        if r is not None:
            return bool(r)
    # 媒体资产窄化：VIDEO 不可直接连 IMAGE（需显式抽帧节点）；扩展类型默认 deny
    return False
