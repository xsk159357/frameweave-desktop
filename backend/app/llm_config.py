# -*- coding: utf-8 -*-
"""平台级 LLM 全局配置（LLM 通道配置化）。

设计（captain 决策，2026-10）：
  - 用户/节点的模型各不相同 → 三层解析，无一绑定具体服务商：
      节点显式参数（每节点可配 model/base_url/api_key）＞
      全局 LLM 配置（本模块，用户一次设置共享默认）＞
      节点内置默认（provider 中立占位，空/@global，不绑定厂商）
  - 本模块只存"引用"：api_key_ref 存保险箱秘密名，值经 app.secrets DPAPI 读取，不落明文。
  - 存储：{DATA_DIR}/settings.json 的 "llm" 键。
"""
import json
import os

from .secrets import read_secret

_DEFAULTS: dict = {}


def _settings_path() -> str:
    data_dir = os.environ.get("FRAMEWEAVE_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.join(data_dir, "settings.json")


def _load() -> dict:
    if _DEFAULTS:
        return _DEFAULTS
    path = _settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            root = json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    return root.get("llm") or {}


def _save(cfg: dict) -> None:
    path = _settings_path()
    root = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                root = json.load(f) or {}
        except Exception:  # noqa: BLE001
            root = {}
    root["llm"] = cfg
    with open(path, "w", encoding="utf-8") as f:
        json.dump(root, f, ensure_ascii=False, indent=2)
    _DEFAULTS.clear()


def get_llm_config() -> dict:
    """返回配置（api_key 仅引用名，不含明文）。"""
    cfg = dict(_load())
    return {
        "provider": cfg.get("provider", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "api_key_ref": cfg.get("api_key_ref", ""),
    }


def set_llm_config(provider: str = "", base_url: str = "", model: str = "",
                   api_key_ref: str = "") -> dict:
    cfg = {k: (v or "").strip() for k, v in {
        "provider": provider, "base_url": base_url, "model": model,
        "api_key_ref": api_key_ref}.items()}
    _save(cfg)
    return get_llm_config()


def resolve_global() -> dict:
    """解析全局配置 → {provider, api_key, base_url, model}；缺省为空串/False。"""
    cfg = _load()
    provider = str(cfg.get("provider") or "").strip()
    base_url = str(cfg.get("base_url") or "").strip()
    model = str(cfg.get("model") or "").strip()
    ref = str(cfg.get("api_key_ref") or "").strip()
    api_key = ""
    if ref:
        try:
            api_key = read_secret(ref) or ""
        except Exception:  # noqa: BLE001
            api_key = ""
    return {"provider": provider, "api_key": api_key, "base_url": base_url,
            "model": model, "api_key_ref": ref}


def clear_cache() -> None:
    _DEFAULTS.clear()


def apply_global(api_key: str = "", base_url: str = "", model: str = "",
                 provider: str = "") -> tuple:
    """节点级通道补齐：显式参数为空时用全局配置填充（LLM 通道配置化）。

    返回 (api_key, base_url, model, provider)；调用方在发起 LLM 请求前调用一次。
    纯函数、异常安全（任何失败静默返回原值）。
    """
    try:
        g = resolve_global() or {}
    except Exception:  # noqa: BLE001
        g = {}
    if not api_key:
        api_key = str(g.get("api_key") or "")
    if not base_url:
        base_url = str(g.get("base_url") or "")
    if not model:
        model = str(g.get("model") or "")
    if not provider:
        provider = str(g.get("provider") or "")
    return api_key, base_url, model, provider