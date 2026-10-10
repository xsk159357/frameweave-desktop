"""Provider SPI（P2，t4）：LLM / TTS / ASR 三协议 + 注册表。

第三方按 Protocol 实现后 register_provider(kind, name, impl) 注册，
get_provider(kind[, name]) 取用（默认名 "default"）；节点/服务消费侧统一经
get_provider 解析，替换默认实现无需改消费方。

默认实现注册：
- llm: OpenAICompatLLM（包装 app/llm.py，OpenAI 兼容协议）——本模块导入即注册；
- tts: nodes/tts.py 导入时注册 TTSEngineProvider（edge/manual/indextts/cloud 分发）；
- asr: nodes/asr.py 导入时注册 WhisperASRProvider（faster-whisper 管线）。
providers.py 顶层不 import nodes/*（避免循环依赖）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------- 协议
@runtime_checkable
class LLMProvider(Protocol):
    """大模型 Provider：OpenAI 兼容 chat + vision 分析。"""

    def chat(self, messages: List[Dict[str, Any]], api_key: str,
             base_url: Optional[str] = None, model: Optional[str] = None,
             temperature: float = 0.8, max_tokens: int = 4096,
             stream: bool = False) -> str: ...

    def vision_analyze(self, images: List[str], prompt: str, api_key: str,
                       base_url: Optional[str] = None,
                       model: Optional[str] = None) -> str: ...


@runtime_checkable
class TTSProvider(Protocol):
    """语音合成 Provider：与节点 run 同契约（ctx/inputs/params → {端口名: 资产或值}）。"""

    async def run(self, ctx: Dict[str, Any], inputs: Dict[str, Any],
                  params: Dict[str, Any]) -> Dict[str, Any]: ...


@runtime_checkable
class ASRProvider(Protocol):
    """语音识别 Provider：与节点 run 同契约（ctx/inputs/params → {端口名: 资产或值}）。"""

    async def run(self, ctx: Dict[str, Any], inputs: Dict[str, Any],
                  params: Dict[str, Any]) -> Dict[str, Any]: ...


# ---------------------------------------------------------------- 注册表
_PROVIDERS: Dict[str, Dict[str, Any]] = {}


def register_provider(kind: str, name: str = "default", impl: Any = None) -> Any:
    """注册 Provider（kind: llm|tts|asr；name 默认 "default"）。同名覆盖（幂等）。"""
    if kind not in ("llm", "tts", "asr"):
        raise ValueError(f"未知 Provider 种类: {kind!r}（llm|tts|asr）")
    if impl is None:
        raise ValueError("impl 不能为空")
    _PROVIDERS.setdefault(kind, {})[name] = impl
    return impl


def get_provider(kind: str, name: str = "default") -> Any:
    """取 Provider；未注册 → KeyError（显式失败，不静默降级）。"""
    entry = _PROVIDERS.get(kind, {}).get(name)
    if entry is None:
        raise KeyError(f"Provider 未注册: {kind}/{name}（llm|tts|asr，默认名 default）")
    return entry


def list_providers() -> Dict[str, List[str]]:
    return {k: sorted(v.keys()) for k, v in _PROVIDERS.items()}


# ---------------------------------------------------------------- 默认 LLM Provider
class OpenAICompatLLM:
    """默认 LLM Provider：OpenAI 兼容协议（包装 app/llm.py：chat / vision_analyze）。"""

    def chat(self, messages: List[Dict[str, Any]], api_key: str,
             base_url: Optional[str] = None, model: Optional[str] = None,
             temperature: float = 0.8, max_tokens: int = 4096,
             stream: bool = False) -> str:
        from . import llm
        return llm.chat(messages, api_key,
                        base_url or llm.DEFAULT_BASE_URL,
                        model or llm.DEFAULT_MODEL,
                        temperature=temperature, max_tokens=max_tokens,
                        stream=stream)

    def vision_analyze(self, images: List[str], prompt: str, api_key: str,
                       base_url: Optional[str] = None,
                       model: Optional[str] = None) -> str:
        from . import llm
        return llm.vision_analyze(images, prompt, api_key,
                                  base_url or llm.DEFAULT_BASE_URL,
                                  model or llm.DEFAULT_MODEL)


register_provider("llm", "default", OpenAICompatLLM())
