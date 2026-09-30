"""节点契约：每个节点的声明（NodeSpec）与执行回调（NodeBase）。"""
from __future__ import annotations
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .ports import PortType


@dataclass
class PortSpec:
    name: str
    type: PortType
    label: str = ""
    required: bool = True
    default: Any = None
    # 参数表单元数据（前端渲染用）
    widget: str = "text"  # text|number|select|file|toggle
    options: List[str] = field(default_factory=list)
    description: str = ""

    def resolve(self) -> Any:
        return self.default


@dataclass
class NodeSpec:
    """节点类型声明（注册中心索引）。"""
    type_id: str            # 全局唯一类型名，如 "core/load_video"
    title: str
    category: str           # 输入/分析/语义/音频/剪辑/输出/控制
    description: str = ""
    inputs: List[PortSpec] = field(default_factory=list)
    outputs: List[PortSpec] = field(default_factory=list)
    params: List[PortSpec] = field(default_factory=list)
    version: str = "1.0.0"
    gpu_required: bool = False
    gpu_recommended: bool = False
    streaming: bool = False   # 是否流式产出

    def port_types(self) -> Dict[str, PortType]:
        return {p.name: p.type for p in self.outputs}


class NodeBase:
    """节点执行基类。子类实现 run(ctx, inputs, params) -> outputs dict。"""

    @classmethod
    def spec(cls) -> NodeSpec:
        raise NotImplementedError

    async def run(self, ctx: Dict[str, Any], inputs: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        """执行节点。ctx 包含: workdir, cache_dir, logger, cancel_event, progress(回调)"""
        raise NotImplementedError

    async def setup(self, ctx: Dict[str, Any]) -> None:
        """一次性初始化（如加载模型）；失败时节点进入 failed。"""
    # 若未覆盖则视为无初始化
    async def _setup_default(self, ctx: Dict[str, Any]) -> None:
        pass

    def _run_setup(self):
        async def _s(ctx):
            try:
                await self.setup(ctx)
            except NotImplementedError:
                pass
        return _s
