"""模型下载服务：ModelScope 为主，HuggingFace 备用，断点续传 + 哈希校验。

P2（t4）：MODELS_REGISTRY 外置 models_registry.json——默认清单随包发布，
远程更新（update_registry）+ registry 内容 sha256 校验 + 本地缓存回落
（env 覆盖 → DATA_DIR 缓存 → 随包默认文件）。单模型可携带 sha256 校验下载产物。

ModelScope 文件下载端点:
  https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}
"""
from __future__ import annotations
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# 随包默认清单（models_registry.json）；安装/更新优先级：
#   FRAMEWEAVE_MODELS_REGISTRY 显式路径 > 本地缓存 > 随包默认
_DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "models_registry.json")

# 本地缓存（远程更新落盘；env 可覆盖，默认 backend/data/）
def _default_cache_path() -> str:
    env = os.environ.get("FRAMEWEAVE_MODELS_CACHE")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "models_registry.cache.json")

REMOTE_URL = os.environ.get("FRAMEWEAVE_MODELS_REGISTRY_URL",
                            "https://shouquan.frameweave.top/models_registry.json")

# 模型清单（键 → info）；pydict 保持旧名 MODELS_REGISTRY 兼容（无外部引用方写操作）
MODELS_REGISTRY: Dict[str, Dict[str, Any]] = {}


class ModelDownloadError(Exception):
    pass


def _sha256_hex(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_registry_file(path: str) -> Dict[str, Dict[str, Any]]:
    """解析 registry 文件：顶层可为扁平 {key: info} 或 {models: {...}}（v1 容器）。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("models") if isinstance(data, dict) and isinstance(data.get("models"), dict) else data
    if not isinstance(raw, dict):
        raise ModelDownloadError(f"registry 结构非法: {path}")
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def load_registry(source: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """按优先级加载模型清单到 MODELS_REGISTRY：
    env 显式路径 → 本地缓存 → 随包默认；全部失败抛 ModelDownloadError。
    """
    candidates = []
    env_path = os.environ.get("FRAMEWEAVE_MODELS_REGISTRY")
    if env_path:
        candidates.append((env_path, "env"))
    cache = _default_cache_path()
    if os.path.isfile(cache):
        candidates.append((cache, "cache"))
    if os.path.isfile(_DEFAULT_REGISTRY_PATH):
        candidates.append((_DEFAULT_REGISTRY_PATH, "default"))
    if source:
        candidates.insert(0, (source, "explicit"))
    if not candidates:
        raise ModelDownloadError("models_registry.json 未找到（随包默认缺失且无缓存）")
    last_err: Optional[Exception] = None
    for path, kind in candidates:
        try:
            models = _parse_registry_file(path)
            MODELS_REGISTRY.clear()
            MODELS_REGISTRY.update(models)
            return dict(MODELS_REGISTRY)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise ModelDownloadError(f"加载 models_registry 失败: {last_err}")


def update_registry(remote_url: Optional[str] = None, timeout: int = 30,
                    cache_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """远程更新模型清单：拉取 → registry sha256 校验 → 写本地缓存 → 更新内存。

    远程文件结构：{"models": {...}, "registry_sha256": "<sha256(models 规范化 JSON)>"}。
    任一步失败 → 保留既有内存清单（本地缓存回落）并抛 ModelDownloadError。
    """
    old = dict(MODELS_REGISTRY)
    url = remote_url or REMOTE_URL
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "frameweave/0.2.3"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
            raise ModelDownloadError("远程 registry 缺少 models 映射")
        models = data["models"]
        declared = str(data.get("registry_sha256") or "").strip().lower()
        if declared:
            canonical = json.dumps(models, sort_keys=True, ensure_ascii=False).encode("utf-8")
            actual = hashlib.sha256(canonical).hexdigest()
            if actual != declared:
                raise ModelDownloadError(
                    f"远程 registry sha256 不匹配（声明 {declared[:12]}…，实际 {actual[:12]}…）")
        MODELS_REGISTRY.clear()
        MODELS_REGISTRY.update({str(k): dict(v) for k, v in models.items() if isinstance(v, dict)})
        try:
            cache = cache_path or _default_cache_path()
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "models": MODELS_REGISTRY,
                           "registry_sha256": declared},
                          f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # 缓存写失败不影响本次更新（下次回落旧缓存）
        return dict(MODELS_REGISTRY)
    except Exception as e:  # noqa: BLE001
        # 本地缓存回落：内存保留既有清单
        if old:
            MODELS_REGISTRY.clear()
            MODELS_REGISTRY.update(old)
        if isinstance(e, ModelDownloadError):
            raise
        raise ModelDownloadError(f"远程更新失败（保留本地清单）: {e}") from e


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
    """下载模型到 target_dir，返回 {path, sha256}。清单未加载时先 load_registry()。"""
    if not MODELS_REGISTRY:
        load_registry()
    if model_key not in MODELS_REGISTRY:
        raise ModelDownloadError(f"未知模型: {model_key}（models_registry 未登记或需 update_registry）")
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

    expected = str(info.get("sha256") or "").strip().lower()
    last_err = None
    for name, url in sources:
        try:
            download_with_resume(url, dest)
            sha = _sha256_hex(dest)
            if expected and sha != expected:
                try:
                    os.remove(dest)  # 哈希不匹配：删除污染产物
                except OSError:
                    pass
                last_err = f"{name}: sha256 不匹配（声明 {expected[:12]}…，实际 {sha[:12]}…）"
                continue
            return {"path": dest, "sha256": sha, "source": name}
        except Exception as e:
            last_err = f"{name}: {e}"
    raise ModelDownloadError(f"下载失败: {last_err}")


# 启动即加载（失败不阻断启动——registry 可由 update_registry 之后补齐）
try:
    load_registry()
except ModelDownloadError as _e:
    print("[model_download] registry 加载失败，可 update_registry 远程更新:", _e)