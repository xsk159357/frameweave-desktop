"""LLM 网关：统一 Provider 抽象，OpenAI 兼容协议（ameaao 中转）。

- 协议: POST {base_url}/chat/completions（OpenAI 兼容）
- 鉴权: Authorization: Bearer {api_key}
- vision: 视频抽帧转 base64 图片 + 文本组合
- 重试: 指数退避 3 次
"""
from __future__ import annotations
import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "https://ameaaos.com/v1"
DEFAULT_MODEL = "gpt-4o"  # 用户确认中转支持 vision


class LLMError(Exception):
    pass


def _post_json(url: str, payload: Dict[str, Any], api_key: str, timeout: int = 120) -> Dict[str, Any]:
    """发送 OpenAI 兼容请求。"""
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, data, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            last_err = LLMError(f"HTTP {e.code}: {raw[:300]}")
            if e.code in (400, 401, 403, 404):
                break  # 客户端错误不重试
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = LLMError(f"网络错误: {e}")
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    raise last_err or LLMError("请求失败")


def chat(messages: List[Dict[str, Any]],
         api_key: str,
         base_url: str = DEFAULT_BASE_URL,
         model: str = DEFAULT_MODEL,
         temperature: float = 0.8,
         max_tokens: int = 4096,
         stream: bool = False) -> str:
    """标准对话补全，返回文本。"""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if stream:
        # M1 先同步；流式留接口
        payload["stream"] = False
    data = _post_json(url, payload, api_key)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"响应解析失败: {json.dumps(data, ensure_ascii=False)[:300]}") from e


def image_to_base64(image_path: str) -> str:
    """本地图片转 base64 data URL（vision 输入用）。"""
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def vision_analyze(images: List[str], prompt: str,
                   api_key: str, base_url: str = DEFAULT_BASE_URL,
                   model: str = DEFAULT_MODEL) -> str:
    """vision 多图分析：将本地抽帧图片 + prompt 发给多模态模型。"""
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": prompt},
    ]
    # 控制图片数量避免超 token（每张图缩略后约 1-2k tokens）
    for img in images[:12]:
        content.append({"type": "image_url", "image_url": {"url": image_to_base64(img)}})
    messages = [{"role": "user", "content": content}]
    return chat(messages, api_key, base_url, model)
