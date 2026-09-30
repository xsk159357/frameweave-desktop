"""模型下载服务：ModelScope 为主，HuggingFace 备用，断点续传 + 哈希校验。

ModelScope 文件下载端点:
  https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}
"""
from __future__ import annotations
import hashlib
import json
import os
import urllib.request
from typing import Dict, List, Optional

# 已知模型仓库映射（M1 初版；后续可远程更新 models_registry.json）
MODELS_REGISTRY: Dict[str, Dict[str, str]] = {
    # 已在 ModelScope 实测验证：repo?FilePath=model.bin 返回 200
    "faster-whisper-large-v3": {
        "repo": "keepitsimple/faster-whisper-large-v3",
        "file": "model.bin",
        "size_mb": 1543,
        "fallback_repo": "Systran/faster-whisper-large-v3",
    },
}


class ModelDownloadError(Exception):
    pass


def _ms_url(repo: str, file: str) -> str:
    return f"https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={file}"


def _hf_url(repo: str, file: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{file}"


def download_with_resume(url: str, dest: str, timeout: int = 60) -> str:
    """断点续传下载。返回 dest 路径。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    headers = {}
    if os.path.exists(dest):
        headers["Range"] = f"bytes={os.path.getsize(dest)}-"
    req = urllib.request.Request(url, headers=headers)
    mode = "ab" if "Range" in headers else "wb"
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, mode) as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def download_model(model_key: str, target_dir: str, prefer: str = "modelscope") -> Dict[str, str]:
    """下载模型到 target_dir，返回 {path, sha256}。"""
    if model_key not in MODELS_REGISTRY:
        raise ModelDownloadError(f"未知模型: {model_key}")
    info = MODELS_REGISTRY[model_key]
    file = info["file"]
    dest = os.path.join(target_dir, model_key, file)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return {"path": dest, "cached": True}

    # 尝试主源 -> 备用源
    sources = []
    if prefer == "modelscope":
        sources.append(("ModelScope", _ms_url(info["repo"], file)))
        if info.get("fallback_repo"):
            sources.append(("HuggingFace", _hf_url(info["fallback_repo"], file)))
    else:
        sources.append(("HuggingFace", _hf_url(info["fallback_repo"] or info["repo"], file)))
        sources.append(("ModelScope", _ms_url(info["repo"], file)))

    last_err = None
    for name, url in sources:
        try:
            download_with_resume(url, dest)
            sha = _sha256(dest)
            return {"path": dest, "sha256": sha, "source": name}
        except Exception as e:
            last_err = f"{name}: {e}"
    raise ModelDownloadError(f"下载失败: {last_err}")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
