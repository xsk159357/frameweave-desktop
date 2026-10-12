"""统一依赖解析层（t31，P0 协议 s6 §9）。

目标：终结各节点各自猜路径的现状（dep-matrix §4/§8 实证三套 ffmpeg 探测链 + 分散模型目录），
让媒体/模型节点只按「依赖 id + 参数 + 环境变量」向本模块查询，得到：
  {ready, path, version, source, warnings, repair_hint}

本模块是纯后端工具（不依赖插件包），但要求插件包经 import 可加载（仅类型注解用 str）。

# ---------------------------------------------------------------- 能力

1) DependencyResolver.resolve(id, requested="", env=None, params=None)
   - id ∈ {"ffmpeg","ffprobe"}（二进制）
   - 探测链（严格优先级，逐级记录 source，跳过失败项不短路）：
       ffmpeg:  params.ffmpeg_path → FRAMEWEAVE_FFMPEG → FFMPEG_BIN → FFMPEG
                → FRAMEWEAVE_FFMPEG_DIR/{ffmpeg.exe|ffmpeg} → shutil.which("ffmpeg")
                → imageio_ffmpeg.get_ffmpeg_exe()
                → 默认安装位置（C:\\ffmpeg\\bin\\ffmpeg.exe、C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe、
                   工作区 jieshuomao\\ffmpeg.exe、真实构建 index-tts python310\\ffmpeg\\bin\\ffmpeg.exe）
       ffprobe: params.ffprobe_path → FRAMEWEAVE_FFPROBE → FFPROBE_BIN → shutil.which("ffprobe")
                → 与已解析 ffmpeg 同目录的 ffprobe.exe/ffprobe → 默认安装位置（同 ffmpeg 目录系）
   - 返回 version（`<bin> -version` 首行 / ffprobe 解析 build 行），失败给 repair_hint。

2) 模型注册表：DATA_DIR/models/ 约定 + models_registry.json + 环境覆盖。
   - 模型根：env FRAMEWEAVE_MODELS_DIR 覆盖，否则 DATA_DIR/models/（DATA_DIR 与 server.py 一致：
     frozen → %APPDATA%/FrameWeave，否则 backend/data）。
   - resolve_model(id, requested="", env=None, params=None)：
       id ∈ {"faster-whisper-small","faster-whisper-large-v3","transnetv2","chinese-clip","index-tts",...}
       逐层候选（先到先得）：
         a. requested（显式参数路径，若为目录或模型名）         source=param
         b. env 覆盖（FRAMEWEAVE_WHISPER_DIR / FRAMEWEAVE_CLIP_DIR / TRANS_... / INDEX_TTS_ROOT）source=env
         c. DATA_DIR/models/<id>（目录约定）                    source=models_dir
         d. models_registry.json 登记的 key（model_download 清单，给出下载 repo/file/sha）source=registry
         e. HF 缓存 ~/.cache/huggingface/hub/models--<org>--<name>（whisper 系）        source=hf_cache
   - 返回 {ready, path, source, warnings, repair_hint, registry?}；模型不存在不下载，只给 repair_hint。

3) 稳定错误码 + 统一中文文案：
   - ERROR_CODES: dependency_missing / binary_missing / model_missing / service_unavailable
   - message_for(code, id, detail) 生成 {code, message}（message 带放置/安装指引）。

4) 服务探测（可选，供 tts/indextts 使用）：
   - ServiceProbe.probe(urls, headers=None, timeout=3) -> {ready, base, protocol, version, capabilities, latency, error_code}
   - protocol: omnivoice（GET /engines）→ voice_clone（GET /health）→ 端口兜底。

# ---------------------------------------------------------------- 用法

from app.plugin import deps
r = deps.resolve_binary("ffmpeg", params={"ffmpeg_path": ""})
if not r["ready"]:
    raise RuntimeError(deps.error_message("binary_missing", "ffmpeg", r["repair_hint"]))
ffmpeg = r["path"]

m = deps.resolve_model("faster-whisper-small", params={"model": "small"})
whisper_dir = m["path"] if m["ready"] else "small"   # 未就绪时交给 WhisperModel 默认下载

节点执行报告合并：report.update({**deps.dep_report("ffmpeg"), "dependency": {...}})
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 常量 / 数据目录

def _backend_root() -> str:
    """backend 根：<repo>/FrameWeave/backend（deps.py 位于 backend/app/plugin/）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    """与 server.py _resolve_data_dir 一致：frozen → %APPDATA%/FrameWeave；源码 → backend/data。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "FrameWeave")
    return os.path.join(_backend_root(), "data")


def models_dir() -> str:
    env = os.environ.get("FRAMEWEAVE_MODELS_DIR", "").strip()
    if env:
        return env
    return os.path.join(data_dir(), "models")


ERROR_CODES = {
    "dependency_missing": "dependency_missing",   # 依赖缺失（宽泛，如 Python 包未装）
    "binary_missing": "binary_missing",           # 二进制缺失（ffmpeg/ffprobe）
    "model_missing": "model_missing",             # 本地模型缺失/未就绪
    "service_unavailable": "service_unavailable",  # 本地服务不可达（IndexTTS 侧车等）
}

# 统一中文文案模板（{id} {path} {hint} 为可替换项）
_MESSAGES: Dict[str, str] = {
    "dependency_missing": "依赖缺失：{id}。请安装对应依赖后重试。{hint}",
    "binary_missing": "未找到 {id}。请安装 {id} 或将 FRAMEWEAVE_{upper}（或 {upper}_BIN）环境变量指向其可执行文件。{hint}",
    "model_missing": "本地模型缺失：{id}。请将其放到 {path}（DATA_DIR/models/ 约定），或设置对应环境变量/节点参数指向模型目录。{hint}",
    "service_unavailable": "本地服务不可用：{id}。请先启动服务或检查地址/端口/健康接口。{hint}",
}


def error_code(code: str) -> str:
    """稳定错误码（白名单外回落 dependency_missing）。"""
    return ERROR_CODES.get(code, ERROR_CODES["dependency_missing"])


def error_message(code: str, id: str = "", path: str = "", hint: str = "") -> Dict[str, str]:
    """生成 {code, message}；message 为统一中文文案。"""
    code = error_code(code)
    tmpl = _MESSAGES.get(code, _MESSAGES["dependency_missing"])
    msg = tmpl.format(id=id, path=path or models_dir(), upper=id.upper(), hint=hint or "")
    return {"code": code, "message": msg}


def coded_message(code: str, id: str = "", path: str = "", hint: str = "") -> str:
    """带稳定错误码前缀的统一中文文案（供节点 raise 直接使用）。

    例：`[binary_missing] 未找到 ffmpeg。请安装 ffmpeg 或将 FRAMEWEAVE_FFMPEG...`
    错误码前缀是机器可判定的稳定契约（dep-matrix §7 / s9 §1.1 错误码表）。
    """
    e = error_message(code, id=id, path=path, hint=hint)
    return f"[{e['code']}] {e['message']}"


class DependencyError(RuntimeError):
    """带稳定错误码的依赖异常：node.error 可直接携带 code 供前端/集成判定。"""

    def __init__(self, code: str, id: str = "", path: str = "", hint: str = ""):
        self.code = error_code(code)
        self.dep_id = id
        super().__init__(coded_message(self.code, id=id, path=path, hint=hint))


def raise_for(code: str, id: str = "", path: str = "", hint: str = "") -> "None":
    """节点依赖缺失统一抛错入口（raise DependencyError）。"""
    raise DependencyError(code, id=id, path=path, hint=hint)


# ---------------------------------------------------------------- 二进制版本探测

def _run_simple(cmd: List[str], timeout: float = 10.0) -> Optional[str]:
    """短超时子进程取 stdout（失败返回 None；不抛）。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if p.returncode == 0:
            return (p.stdout or b"").decode("utf-8", "replace")
        return None
    except Exception:  # noqa: BLE001
        return None


def _binary_version(path: str, kind: str) -> Optional[str]:
    out = _run_simple([path, "-version"])
    if not out:
        return None
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if kind == "ffprobe" and lines:
        m = re.search(r"ffprobe version\s+([\w.\-]+)", lines[0])
        if m:
            return m.group(1)
    if lines:
        m = re.search(r"version\s+([\w.\-]+)", lines[0])
        if m:
            return m.group(1)
        return lines[0][:80]
    return None


# ---------------------------------------------------------------- 默认安装位置

def _workspace_root() -> str:
    """解说工坊工作区根：backend 上两级（FrameWeave/backend → 解说工坊）。"""
    return os.path.dirname(os.path.dirname(_backend_root()))


_DEFAULT_FFMPEG_CANDIDATES: List[str] = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    # 解说猫工作区：真实构建（index-tts 随附）优先于根目录包装器
    os.path.join(_workspace_root(), "jieshuomao", "video_analyzer", "index-tts",
                 "python310", "ffmpeg", "bin", "ffmpeg.exe"),
    os.path.join(_workspace_root(), "jieshuomao", "ffmpeg.exe"),
]

_DEFAULT_FFPROBE_CANDIDATES: List[str] = [
    r"C:\ffmpeg\bin\ffprobe.exe",
    r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
    os.path.join(_workspace_root(), "jieshuomao", "video_analyzer", "index-tts",
                 "python310", "ffmpeg", "bin", "ffprobe.exe"),
    os.path.join(_workspace_root(), "jieshuomao", "ffprobe.exe"),
]


# ---------------------------------------------------------------- 二进制解析

def _ffmpeg_candidates(params: Dict[str, Any], env: Dict[str, str]) -> List[tuple]:
    """返回 [(path, source)] 优先级链（未过滤存在性；由调用方逐一验证）。"""
    out: List[tuple] = []
    if params:
        p = str(params.get("ffmpeg_path") or "").strip()
        if p:
            out.append((p, "param"))
    for key in ("FRAMEWEAVE_FFMPEG", "FFMPEG_BIN", "FFMPEG"):
        v = env.get(key, "").strip()
        if v:
            out.append((v, "env:" + key))
    d = env.get("FRAMEWEAVE_FFMPEG_DIR", "").strip()
    if d:
        for cand in (os.path.join(d, "ffmpeg.exe"), os.path.join(d, "ffmpeg")):
            out.append((cand, "env:FRAMEWEAVE_FFMPEG_DIR"))
    found = shutil.which("ffmpeg")
    if found:
        out.append((found, "path"))
    try:
        import imageio_ffmpeg
        out.append((imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg"))
    except Exception:  # noqa: BLE001
        pass
    for cand in _DEFAULT_FFMPEG_CANDIDATES:
        out.append((cand, "default"))
    return out


def _ffprobe_candidates(params: Dict[str, Any], env: Dict[str, str]) -> List[tuple]:
    out: List[tuple] = []
    if params:
        p = str(params.get("ffprobe_path") or "").strip()
        if p:
            out.append((p, "param"))
    for key in ("FRAMEWEAVE_FFPROBE", "FFPROBE_BIN"):
        v = env.get(key, "").strip()
        if v:
            out.append((v, "env:" + key))
    found = shutil.which("ffprobe")
    if found:
        out.append((found, "path"))
    # 与已解析 ffmpeg 同目录（由 resolve_binary 在拿到 ffmpeg 后追加，避免重复探测）
    for cand in _DEFAULT_FFPROBE_CANDIDATES:
        out.append((cand, "default"))
    return out


def _dedupe(cands: List[tuple]) -> List[tuple]:
    seen = set()
    out = []
    for path, source in cands:
        ap = os.path.abspath(path)
        if ap in seen:
            continue
        seen.add(ap)
        out.append((path, source))
    return out


def resolve_binary(id: str, requested: str = "", env: Optional[Dict[str, str]] = None,
                   params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统一二进制解析。id ∈ {"ffmpeg","ffprobe"}；返回契约 dict。

    契约：{ready, path, version, source, warnings, repair_hint, requested}
    """
    env = dict(env or os.environ)
    params = dict(params or {})
    warnings: List[str] = []

    if requested:
        requested = str(requested).strip()
        if requested and os.path.isfile(requested):
            ver = _binary_version(requested, id)
            return {"ready": True, "path": os.path.abspath(requested),
                    "version": ver, "source": "requested", "warnings": [],
                    "repair_hint": "", "requested": requested}
        if requested:
            warnings.append(f"requested 路径不存在: {requested}（忽略，继续探测链）")

    if id == "ffprobe":
        base = _ffprobe_candidates(params, env)
        # ffmpeg 已解析时优先同目录 ffprobe
        fm = resolve_binary("ffmpeg", env=env, params=params)
        if fm["ready"]:
            d = os.path.dirname(fm["path"])
            for cand in (os.path.join(d, "ffprobe.exe"), os.path.join(d, "ffprobe")):
                base.insert(0, (cand, "sibling:" + fm["source"]))
    else:
        base = _ffmpeg_candidates(params, env)

    for path, source in _dedupe(base):
        if not os.path.isfile(path):
            warnings.append(f"{source} 候选不存在: {path}")
            continue
        # 存在即视为可用（版本探测 is best-effort：读不到仅告警，不因此拒用）
        ver = _binary_version(path, id)
        if ver is None:
            warnings.append(f"{source} 版本探测失败（文件存在，继续使用）: {path}")
        return {"ready": True, "path": os.path.abspath(path),
                "version": ver, "source": source, "warnings": warnings,
                "repair_hint": "", "requested": requested}

    return {"ready": False, "path": "", "version": None,
            "source": "none", "warnings": warnings,
            "repair_hint": _repair_hint_binary(id, env), "requested": requested}


def _repair_hint_binary(id: str, env: Dict[str, str]) -> str:
    upper = id.upper()
    return (f"请安装 {id}，或设置环境变量 FRAMEWEAVE_{upper}（/ {upper}_BIN）指向其可执行文件；"
            f"Windows 示例：$env:FRAMEWEAVE_{upper} = \"...\\ffmpeg\\bin\\{id}.exe\"")


# ---------------------------------------------------------------- 模型注册表 / 模型解析

def load_models_registry() -> Dict[str, Dict[str, Any]]:
    """models_registry.json 清单（env 显式路径 → backend/models_registry.json）。不抛。"""
    env = os.environ.get("FRAMEWEAVE_MODELS_REGISTRY", "").strip()
    paths = [env] if env else []
    paths.append(os.path.join(_backend_root(), "models_registry.json"))
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("models") if isinstance(data, dict) and isinstance(data.get("models"), dict) else data
            if isinstance(raw, dict):
                return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            continue
    return {}


# 模型 id → 默认本地目录名（DATA_DIR/models/<name> 约定；whisper 系按 size 命名）
_MODEL_DIR_NAMES: Dict[str, str] = {
    "faster-whisper-small": "faster-whisper-small",
    "faster-whisper-base": "faster-whisper-base",
    "faster-whisper-medium": "faster-whisper-medium",
    "faster-whisper-large-v3": "faster-whisper-large-v3",
    "whisper-srt": "faster-whisper-large-v3",
    "transnetv2": "transnetv2",
    "chinese-clip": "chinese-clip-vit-base-patch16",
    "index-tts": "index-tts-code",
    "demucs": "demucs",
}
# 模型 id → 环境变量覆盖
_MODEL_ENV: Dict[str, str] = {
    "faster-whisper-small": "FRAMEWEAVE_WHISPER_DIR",
    "faster-whisper-base": "FRAMEWEAVE_WHISPER_DIR",
    "faster-whisper-medium": "FRAMEWEAVE_WHISPER_DIR",
    "faster-whisper-large-v3": "FRAMEWEAVE_WHISPER_DIR",
    "whisper-srt": "FRAMEWEAVE_WHISPER_DIR",
    "transnetv2": "FRAMEWEAVE_TRANSNET_DIR",
    "chinese-clip": "FRAMEWEAVE_CLIP_DIR",
    "index-tts": "INDEX_TTS_ROOT",
    "demucs": "FRAMEWEAVE_DEMUCS_DIR",
}
# HF 缓存路径模式（faster-whisper 系）
_HF_PATTERNS: Dict[str, str] = {
    "faster-whisper-small": "models--Systran--faster-whisper-small",
    "faster-whisper-base": "models--Systran--faster-whisper-base",
    "faster-whisper-medium": "models--Systran--faster-whisper-medium",
    "faster-whisper-large-v3": "models--Systran--faster-whisper-large-v3",
}


def resolve_model(id: str, requested: str = "", env: Optional[Dict[str, str]] = None,
                  params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """模型路径解析。返回 {ready, path, source, warnings, repair_hint, registry?}。

    候选链（先到先得）：
      a. requested（显式参数/本地目录或名字）
      b. env 覆盖（FRAMEWEAVE_WHISPER_DIR / FRAMEWEAVE_CLIP_DIR / FRAMEWEAVE_TRANSNET_DIR / INDEX_TTS_ROOT / ...）
      c. DATA_DIR/models/<dir_name>
      d. models_registry.json 登记的模型 key（仅登记存在性；不下载）
      e. HF 缓存（whisper 系）快照目录（存在即 ready，作为模型目录喂 WhisperModel）
    缺失不下载——只给 repair_hint（引导放置/下载/环境变量）。
    """
    env = dict(env or os.environ)
    params = dict(params or {})
    warnings: List[str] = []
    mdir = os.path.join(models_dir(), _MODEL_DIR_NAMES.get(id, id))

    # a. requested（param 优先；仅当形如路径时视为本地路径，模型名/尺寸名不误判）
    req = str(requested or "").strip()
    if not req and params:
        req = str(params.get("model_dir") or "").strip()
        if not req:
            req = str(params.get("model") or "").strip()
    path_like = bool(req) and (os.path.sep in req or (os.path.altsep and os.path.altsep in req)
                               or req.endswith((".bin", ".pth", ".safetensors", ".pth.tar")))
    if req and path_like and os.path.isdir(req):
        return {"ready": True, "path": os.path.abspath(req), "source": "param",
                "warnings": [], "repair_hint": "", "requested": req}
    if req and path_like:
        warnings.append(f"requested 模型目录不存在: {req}（继续探测链）")

    # b. env 覆盖
    env_key = _MODEL_ENV.get(id)
    if env_key:
        ev = env.get(env_key, "").strip()
        if ev:
            if os.path.isdir(ev):
                return {"ready": True, "path": os.path.abspath(ev), "source": "env:" + env_key,
                        "warnings": warnings, "repair_hint": "", "requested": req}
            warnings.append(f"{env_key} 目录不存在: {ev}")

    # c. DATA_DIR/models/<name>
    if os.path.isdir(mdir):
        return {"ready": True, "path": os.path.abspath(mdir), "source": "models_dir",
                "warnings": warnings, "repair_hint": "", "requested": req}

    # d. registry 登记（只查存在性）
    reg = load_models_registry()
    registry_info: Optional[Dict[str, Any]] = None
    if id in reg:
        registry_info = reg[id]
    elif any(k == id or k.startswith(id + "/") or id.startswith(k) for k in reg):
        for k in reg:
            if k == id:
                registry_info = reg[k]
                break
    if registry_info:
        warnings.append(f"models_registry 已登记 {id}（下载源 {registry_info.get('repo', '?')}），"
                        "但本地未就绪；请先运行模型下载/就绪节点")

    # e. HF 缓存（whisper 系）
    pat = _HF_PATTERNS.get(id)
    if pat:
        hf_root = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub", pat)
        if os.path.isdir(hf_root):
            snaps = os.path.join(hf_root, "snapshots")
            if os.path.isdir(snaps):
                for name in sorted(os.listdir(snaps)):
                    sp = os.path.join(snaps, name)
                    if os.path.isfile(os.path.join(sp, "model.bin")):
                        return {"ready": True, "path": os.path.abspath(sp), "source": "hf_cache",
                                "warnings": warnings, "repair_hint": "", "requested": req}

    return {"ready": False, "path": "", "source": "none", "warnings": warnings,
            "registry": registry_info,
            "repair_hint": _repair_hint_model(id, mdir, env_key, env), "requested": req}


def _repair_hint_model(id: str, mdir: str, env_key: Optional[str], env: Dict[str, str]) -> str:
    lines = [f"请把模型放到 {mdir}（DATA_DIR/models/ 约定）"]
    if env_key:
        lines.append(f"或设置环境变量 {env_key} 指向模型目录")
    reg = load_models_registry()
    info = reg.get(id)
    if info:
        repo = info.get("repo") or ""
        if repo:
            lines.append(f"下载：hf download {repo} --local-dir {mdir}（或 modelscope download --model {repo}）")
    return "；".join(lines)


def dep_report(id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """节点报告合并用：dependency 字段。"""
    return {
        "dependency": id,
        "ready": bool(result.get("ready")),
        "source": result.get("source", ""),
        "path": result.get("path", ""),
        "version": result.get("version"),
        "warnings": list(result.get("warnings") or []),
    }


# ---------------------------------------------------------------- 服务探测（IndexTTS 等）

class ServiceProbe:
    """本地服务探测：{ready, base, protocol, version, capabilities, latency, error_code}。

    protocol: omnivoice（GET /engines 200）/ voice_clone（GET /health 200）/ port_fallback。
    """

    _OMNIVOICE_PORT = 3900
    _VOICE_CLONE_PORT = 8791

    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout

    def _get(self, url: str, headers: Optional[Dict[str, str]] = None) -> tuple:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=dict(headers or {}))
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(64 * 1024).decode("utf-8", "replace")
                return r.status, body, round((time.time() - t0) * 1000, 1)
        except Exception:  # noqa: BLE001
            return 0, "", round((time.time() - t0) * 1000, 1)

    def classify(self, base: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        base = (base or "").strip().rstrip("/")
        if not base:
            return None
        port = 443 if base.startswith("https://") else 80
        m = re.search(r":(\d{4,5})/?$", base)
        if m:
            port = int(m.group(1))
        st, body, lat = self._get(base + "/engines", headers)
        if st == 200:
            caps = {}
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    caps = data
            except Exception:  # noqa: BLE001
                pass
            return {"ready": True, "base": base, "protocol": "omnivoice",
                    "version": caps.get("version") if isinstance(caps, dict) else None,
                    "capabilities": caps, "latency": lat, "error_code": ""}
        st2, body2, lat2 = self._get(base + "/health", headers)
        if st2 == 200:
            proto = "voice_clone" if port == self._VOICE_CLONE_PORT else "omnivoice"
            caps = {}
            try:
                data = json.loads(body2)
                if isinstance(data, dict):
                    caps = data
            except Exception:  # noqa: BLE001
                pass
            return {"ready": True, "base": base, "protocol": proto,
                    "version": caps.get("version") if isinstance(caps, dict) else None,
                    "capabilities": caps, "latency": lat2, "error_code": ""}
        if port == self._VOICE_CLONE_PORT:
            return {"ready": True, "base": base, "protocol": "voice_clone",
                    "version": None, "capabilities": {}, "latency": lat2,
                    "error_code": "service_unavailable_health"}
        return None

    def probe(self, urls: List[str], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        for u in urls:
            hit = self.classify(u, headers)
            if hit:
                return hit
        return {"ready": False, "base": "", "protocol": "", "version": None,
                "capabilities": {}, "latency": 0.0,
                "error_code": "service_unavailable",
                "warnings": [f"服务不可达：{urls}"]}


# ---------------------------------------------------------------- 便捷入口
def resolve(id: str, requested: str = "", env: Optional[Dict[str, str]] = None,
            params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统一入口：id ∈ {"ffmpeg","ffprobe"} 走二进制；其它走模型/服务解析。"""
    if id in ("ffmpeg", "ffprobe"):
        return resolve_binary(id, requested=requested, env=env, params=params)
    return resolve_model(id, requested=requested, env=env, params=params)