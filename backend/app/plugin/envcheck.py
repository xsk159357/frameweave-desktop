# -*- coding: utf-8 -*-
"""首次运行环境检查向导（t32 P0）。

用法：
    python -m app.plugin.envcheck            # 全量逐项检查（含 CLI 输出）
    python packaging/fw_cli.py env-check    # 同上的 `fw env-check` 后端命令入口

检测项（每项给 status/detail/repair：可复制修复命令——下载/放置/接线三步）：
  1. ffmpeg   —— 统一 resolver（params → FRAMEWEAVE_FFMPEG → FFMPEG_BIN → FFMPEG →
                 FRAMEWEAVE_FFMPEG_DIR → PATH → imageio → 默认安装位），带版本输出；
  2. ffprobe  —— 同上（含与 ffmpeg 同目录兄弟优先）；
  3. Python   —— 版本/解释器/冻结模式/平台架构；
  4. 模型就绪 —— whisper(small/large-v3)/transnetv2/chinese-clip/index-tts/demucs
                 经 resolve_model（DATA_DIR/models/ 约定 + env 覆盖 + HF 缓存 + 注册表）；
  5. 本地服务 —— 3900 OmniVoice / 8791 voice_clone（/engines /health 探测）；
  6. SecretStore —— @secret 引用解析：secrets.json 存在性 + 已存密钥名 + 引用解析回路；
  7. 权限门   —— worker(FW_PLUGIN_EXEC) vs 进程内执行模式 + PermissionGate
                 network/secret/subprocess 门对样例 URL 的判定；
  8. 数据目录 —— DATA_DIR/models 可写性（env 覆盖提示）。

退出码：0 = 全部 ok/warn；1 = 存在 fail（环境不可直接开箱）。
返回契约：run_envcheck() -> {"generated_at", "python", "checks": [...], "summary": {...}}
每条 check: {id, name, status: ok|warn|fail, detail, code, repair: [命令列表]}
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

from . import deps


# ---------------------------------------------------------------- 修复命令模板（下载/放置/接线三步）

def _repair_binary(id: str, hint: str = "") -> List[str]:
    upper = id.upper()
    d = [f"下载：https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip （含 {id}.exe）",
         f"放置：解压 ffmpeg.exe/ffprobe.exe 到 C:\\ffmpeg\\bin\\ 或解说工坊 index-tts\\ffmpeg\\bin\\",
         f"接线：$env:{'FRAMEWEAVE_' + upper} = \"C:\\ffmpeg\\bin\\{id}.exe\""]
    if hint:
        d.append(f"说明：{hint}")
    return d


def _repair_model(id: str) -> List[str]:
    mdir = os.path.join(deps.models_dir(), deps._MODEL_DIR_NAMES.get(id, id))
    env = deps._MODEL_ENV.get(id, "")
    info = (deps.load_models_registry() or {}).get(id) or {}
    repo = info.get("repo", "")
    d = []
    if repo:
        d.append(f"下载：hf download {repo} --local-dir \"{mdir}\"")
    else:
        d.append(f"下载：按 dep-matrix §依赖表获取 {id} 权重文件")
    d.append(f"放置：{mdir}（DATA_DIR/models 约定）")
    d.append(f"接线：{(env + ' 指向该目录').lstrip() if env else '在节点参数 model_dir 中指定该目录'}")
    return d


_REPAIR_SERVICE = [
    "启动：运行 IndexTTS2 / OmniVoice 常驻服务（index_tts_server.py），或节点参数 auto_start_server=true",
    "接线：设置 INDEX_TTS_URL=http://127.0.0.1:3900（或 server_url 参数）；8791 旧服务同理",
    "验证：浏览器打开 http://127.0.0.1:3900/engines 应返回 200",
]

_REPAIR_SECRET = [
    "写入：python -c \"from app.secrets import store_secret; store_secret('ameaao','你的key')\"",
    "接线：节点 api_key 填 @secret:ameaao（引用保险箱，不落明文）",
    "验证：本向导第 6 项应显示该项密钥可解析",
]


# ---------------------------------------------------------------- 单项检查

def _check_ffmpeg() -> Dict[str, Any]:
    r = deps.resolve_binary("ffmpeg", env=os.environ)
    if r.get("ready"):
        return {"id": "ffmpeg", "name": "ffmpeg", "status": "ok",
                "detail": f"{r['path']}（version={r.get('version') or '未知'}，source={r.get('source')}）",
                "code": "", "repair": []}
    return {"id": "ffmpeg", "name": "ffmpeg", "status": "fail",
            "detail": "未找到 ffmpeg 可执行文件（已按统一探测链检查）",
            "code": "binary_missing", "repair": _repair_binary("ffmpeg", r.get("repair_hint", ""))}


def _check_ffprobe() -> Dict[str, Any]:
    r = deps.resolve_binary("ffprobe", env=os.environ)
    if r.get("ready"):
        return {"id": "ffprobe", "name": "ffprobe", "status": "ok",
                "detail": f"{r['path']}（version={r.get('version') or '未知'}，source={r.get('source')}）",
                "code": "", "repair": []}
    return {"id": "ffprobe", "name": "ffprobe", "status": "fail",
            "detail": "未找到 ffprobe 可执行文件",
            "code": "binary_missing", "repair": _repair_binary("ffprobe", r.get("repair_hint", ""))}


def _check_python() -> Dict[str, Any]:
    frozen = bool(getattr(sys, "frozen", False))
    mode = "打包(frozen)" if frozen else "源码"
    try:
        import platform
        arch = platform.machine()
    except Exception:  # noqa: BLE001
        arch = "?"
    return {"id": "python", "name": "Python", "status": "ok",
            "detail": (f"{sys.version.split()[0]} @ {sys.executable}（{mode}，{arch}，平台 "
                       f"{sys.platform}）"),
            "code": "", "repair": []}


_MODEL_CHECKS = [
    ("faster-whisper-small", "Whisper 小型转写模型"),
    ("faster-whisper-large-v3", "Whisper 大模型转写"),
    ("transnetv2", "TransNetV2 镜头切分模型"),
    ("chinese-clip", "Chinese-CLIP 图文匹配模型"),
    ("dinov2-small", "DINOv2-Small 语义精排模型(B3)"),
    ("dinov2-base", "DINOv2-Base 语义精排模型(B3)"),
    ("index-tts", "IndexTTS2 本地克隆配音"),
    ("demucs", "Demucs 人声分离模型"),
]


def _check_models() -> List[Dict[str, Any]]:
    out = []
    for mid, name in _MODEL_CHECKS:
        r = deps.resolve_model(mid, env=os.environ)
        if r.get("ready"):
            out.append({"id": mid, "name": name, "status": "ok",
                        "detail": f"{r['path']}（source={r.get('source')}）",
                        "code": "", "repair": []})
        else:
            warnings = "; ".join(r.get("warnings") or [])
            out.append({"id": mid, "name": name, "status": "warn",
                        "detail": "本地未就绪" + (f"（{warnings}）" if warnings else ""),
                        "code": "model_missing", "repair": _repair_model(mid)})
    return out


def _check_services() -> List[Dict[str, Any]]:
    sp = deps.ServiceProbe(timeout=2.0)
    out = []
    for base, name in (("http://127.0.0.1:3900", "IndexTTS/OmniVoice 3900"),
                       ("http://127.0.0.1:8791", "voice_clone 8791")):
        res = sp.probe([base])
        if res.get("ready"):
            out.append({"id": "service:" + base, "name": name, "status": "ok",
                        "detail": f"{res.get('protocol','')} @ {res.get('base','')}（"
                                  f"version={res.get('version') or '?'}，{res.get('latency',0)}ms）",
                        "code": "", "repair": []})
        else:
            out.append({"id": "service:" + base, "name": name, "status": "warn",
                        "detail": "服务未启动（配音节点使用该服务时需要）",
                        "code": "service_unavailable", "repair": list(_REPAIR_SERVICE)})
    return out


def _check_secrets() -> Dict[str, Any]:
    try:
        from app.secrets import _secrets_path, list_secret_names
        path = _secrets_path()
        names = list_secret_names()
    except Exception as e:  # noqa: BLE001
        return {"id": "secrets", "name": "SecretStore(@secret)", "status": "fail",
                "detail": f"SecretStore 不可用: {e}", "code": "dependency_missing", "repair": []}
    if os.path.exists(path) and names:
        return {"id": "secrets", "name": "SecretStore(@secret)", "status": "ok",
                "detail": f"{path} 已存密钥：{', '.join(names[:8])}" + (" …" if len(names) > 8 else ""),
                "code": "", "repair": []}
    if os.path.exists(path):
        return {"id": "secrets", "name": "SecretStore(@secret)", "status": "warn",
                "detail": f"{path} 存在但未存任何密钥（需要 API Key 的节点会提示）",
                "code": "", "repair": list(_REPAIR_SECRET)}
    return {"id": "secrets", "name": "SecretStore(@secret)", "status": "warn",
            "detail": "未初始化（首次写入密钥时自动创建 secrets.json）",
            "code": "", "repair": list(_REPAIR_SECRET)}


def _check_permissions() -> Dict[str, Any]:
    from app.worker.perm import PermissionGate, PermissionDenied
    mode = "inprocess" if os.environ.get("FW_PLUGIN_EXEC", "") == "inprocess" else "worker"
    # 样例 manifest：网络通配 + 本地 TTS 服务 + subprocess ffmpeg + secret 通配
    manifest = {
        "permission": {
            "network": {"allow": ["*", "127.0.0.1:3900", "127.0.0.1:8791"]},
            "subprocess": {"enabled": True, "argv_allowlist": ["ffmpeg", "ffprobe"]},
            "secret": {"refs": ["*"]},
        }
    }
    gate = PermissionGate(manifest, {"pkg": ".", "scratch": "."}, trust_level="L2")
    url_cases = [
        ("https://api.openai.com/v1/chat/completions", "公网 LLM API"),
        ("http://127.0.0.1:3900/generate", "本地 TTS(3900)"),
        ("http://169.254.169.254/latest/meta-data", "元数据面(应拒)"),
        ("http://192.168.1.10/api", "私网(未声明应拒)"),
    ]
    verdicts = []
    for url, label in url_cases:
        try:
            gate.check_network(url)
            verdicts.append(f"{label}=放行")
        except PermissionDenied as e:
            verdicts.append(f"{label}=拒绝({e.detail})")
    return {"id": "permissions", "name": "权限门(执行模式+网络)", "status": "ok",
            "detail": f"执行模式={mode}；网络判定：" + "；".join(verdicts),
            "code": "", "repair": []}


def _check_model_help_hint() -> Dict[str, Any]:
    """B3 DINOv2 模型下载指引（S2-T0）：主下载 + 备选源 + 降级路径。"""
    mdir = os.path.join(deps.models_dir(), "dinov2-small")
    return {
        "id": "model_help:dinov2",
        "name": "DINOv2 下载指引(B3)",
        "status": "ok",
        "detail": ("DINOv2-S 下载: hf download facebook/dinov2-small --local-dir "
                   f"\"{mdir}\"（或 modelscope download --model facebook/dinov2-small）；"
                   "DINOv2-Base 同理（facebook/dinov2-base → DATA_DIR/models/dinov2-base）。"
                   "语义精排回退链：dinov2_base → dinov2_small → chinese-clip → 灰度投票粗筛；"
                   "chinese-clip 已在本机 HF 缓存就绪（可先行复用 match_visual embedder 路径）。"
                   "log-mel 音频特征依赖 ffmpeg 已由统一 resolver 解析。"),
        "code": "", "repair": [
            f"下载：hf download facebook/dinov2-small --local-dir \"{mdir}\"",
            f"放置：{mdir}（DATA_DIR/models 约定；B3 节点参数 model_dir 可直指）",
            "接线：设置 FRAMEWEAVE_DINO_DIR 指向模型目录，或让 B3 节点回退 chinese-clip/灰度投票",
        ],
    }


def _check_data_dir() -> Dict[str, Any]:
    mdir = deps.models_dir()
    ok = os.path.isdir(mdir) and os.access(mdir, os.W_OK)
    if ok:
        return {"id": "data_dir", "name": "数据目录(DATA_DIR/models)", "status": "ok",
                "detail": f"{mdir} 可写（FRAMEWEAVE_MODELS_DIR 覆盖="
                          f"{bool(os.environ.get('FRAMEWEAVE_MODELS_DIR'))}）",
                "code": "", "repair": []}
    return {"id": "data_dir", "name": "数据目录(DATA_DIR/models)", "status": "warn",
            "detail": f"{mdir} 不存在或不可写（模型放置前将自动创建目录）",
            "code": "", "repair": [f"放置：mkdir -p \"{mdir}\"（或设置 FRAMEWEAVE_MODELS_DIR 到可写目录）"]}


# ---------------------------------------------------------------- 聚合

def run_envcheck() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = [
        _check_ffmpeg(),
        _check_ffprobe(),
        _check_python(),
    ]
    checks += _check_models()
    checks += _check_services()
    checks.append(_check_secrets())
    checks.append(_check_permissions())
    checks.append(_check_data_dir())
    checks.append(_check_model_help_hint())
    # 概要
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "generated_at": __import__("time").time(),
        "tool": "fw env-check",
        "python": {"version": sys.version.split()[0], "executable": sys.executable,
                   "frozen": bool(getattr(sys, "frozen", False))},
        "checks": checks,
        "summary": {"total": len(checks), **counts},
    }


# ---------------------------------------------------------------- CLI

def _fmt_row(c: Dict[str, Any]) -> str:
    icon = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}.get(c["status"], "[?]   ")
    return f"{icon} {c['name']:<28} {c['detail']}"


def main(argv: List[str] | None = None) -> int:
    # frozen 版控制台默认 GBK（Windows），⚠/✅ 等非 GBK 字符会 UnicodeEncodeError；
    # 与 fw_cli.py 一致强制 UTF-8（源码态 PowerShell 亦可正常显示）。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    report = run_envcheck()
    print("=" * 88)
    print(f"FrameWeave 环境检查（{report['tool']}）  @ "
          f"{__import__('time').strftime('%Y-%m-%d %H:%M:%S', __import__('time').localtime(report.get('generated_at', 0)))}")
    print("=" * 88)
    for c in report["checks"]:
        print(_fmt_row(c))
        for r in (c.get("repair") or []):
            print(f"         修复：{r}")
    s = report["summary"]
    print("-" * 88)
    print(f"汇总：{s['total']} 项检查 — ok={s['ok']} warn={s['warn']} fail={s['fail']}")
    for c in report["checks"]:
        if c["status"] == "fail":
            print(f"  ⚠ 未通过：{c['name']}（错误码 {c.get('code','')}）— 修复命令见上")
    return 1 if s["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())