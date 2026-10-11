# -*- coding: utf-8 -*-
"""IndexTTS2 本地声音克隆常驻服务（Python 3.11 venv 运行）。

供主后端(3.14)通过 HTTP 调用。模型加载一次，多请求复用。
端点:
  GET  /health            -> {"status":"ok","ready":bool}
  POST /synthesize        -> 克隆合成
       body: {text, ref_audio_base64, ref_audio_sample_rate, lang, duration_factor?, emotion?}
       resp: 200 wav 二进制

加固（t7 设计 §6.2 V1-V5）：
  V1 令牌鉴权：请求头 X-FW-Local-Token 必须等于环境 FRAMEWEAVE_LOCAL_TOKEN（/health 与
     /synthesize 均要求；未配置令牌 → 拒绝一切请求）
  V2 路径白名单：ref_audio_path 一律拒绝（仅接受 ref_audio_base64 ≤10MB）——任意本地路径读封死
  V3 请求体上限：Content-Length 预检 ≤20MB + 读取截断
  V4 频控：令牌桶——并发 ≤3、10 req/min/令牌；超限 429；模型推理由 _load_lock 串行化
  V5 输出/日志：响应 wav ≤50MB；每次 synthesize 记审计日志；异常不回显 traceback（仅错误码）
"""
import os, sys, base64, io, time, json, threading

# M10 修复：硬编码路径改为环境变量优先（FW_PATCH_DIR / INDEX_TTS_CODE_DIR），
# 便于换机与打包分发；未设置时回退到本机开发默认路径。
_patch = os.environ.get("FW_PATCH_DIR", r"C:/Users/Administrator/fw-patch")  # wetext ASCII 副本
if _patch and os.path.isdir(_patch):
    sys.path.insert(0, _patch)
CODE_DIR = os.environ.get("INDEX_TTS_CODE_DIR", r"F:/1223/解说工坊/FrameWeave/backend/models/index-tts-code")
if not os.path.isdir(CODE_DIR):
    print("[indextts] WARN: INDEX_TTS_CODE_DIR 不存在: %s（设置环境变量指定正确路径）" % CODE_DIR, flush=True)
sys.path.insert(0, CODE_DIR)
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_CACHE"] = os.path.join(CODE_DIR, "checkpoints", "hf_cache")

MODEL_DIR = os.path.join(CODE_DIR, "checkpoints")
_load_lock = threading.Lock()
_tts = None

# ---------------- V1-V4 常量与状态 ----------------
LOCAL_TOKEN = os.environ.get("FRAMEWEAVE_LOCAL_TOKEN", "") or ""
MAX_BODY_BYTES = 20 * 1024 * 1024          # V3：请求体 ≤20MB
MAX_REF_AUDIO_BYTES = 10 * 1024 * 1024    # V2：参考音频 ≤10MB（仅 base64）
MAX_OUT_BYTES = 50 * 1024 * 1024          # V5：输出 wav ≤50MB
AUDIT_LOG = os.environ.get("FW_AUDIT_LOG",
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "indextts_audit.log"))
MAX_CONCURRENT = 3                         # V4：并发 ≤3
RATE_LIMIT = (10, 60.0)                    # V4：10 req / 60s / 令牌
_req_sem = threading.BoundedSemaphore(MAX_CONCURRENT)
_rate_lock = threading.Lock()
_rate_hist = []                            # (ts,) 最近请求时间戳


def _audit(**fields):
    """V5：结构化审计日志（不抛错）。"""
    try:
        rec = dict(fields)
        rec["ts"] = time.time()
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _rate_ok() -> bool:
    """令牌桶：滑动窗口限频。"""
    global _rate_hist
    with _rate_lock:
        now = time.time()
        limit, window = RATE_LIMIT
        _rate_hist = [t for t in _rate_hist if now - t < window]
        if len(_rate_hist) >= limit:
            return False
        _rate_hist.append(now)
        return True


def _check_token(headers) -> bool:
    """V1：令牌匹配（未配置令牌 = 拒绝一切）。"""
    if not LOCAL_TOKEN:
        return False
    return headers.get("X-FW-Local-Token") == LOCAL_TOKEN


def get_tts():
    global _tts
    if _tts is not None:
        return _tts
    with _load_lock:
        if _tts is not None:
            return _tts
        t0 = time.time()
        print("[indextts] ensure_models_available...", flush=True)
        from indextts.utils.model_download import ensure_models_available
        ensure_models_available(MODEL_DIR)
        print("[indextts] loading IndexTTS2...", flush=True)
        from indextts.infer_v2_5 import IndexTTS2
        _tts = IndexTTS2(
            cfg_path=os.path.join(MODEL_DIR, "config.yaml"),
            model_dir=MODEL_DIR,
            use_bf16=True,
            use_cuda_kernel=False,
        )
        print("[indextts] loaded in %.1fs" % (time.time() - t0), flush=True)
        return _tts


def synthesize(text, ref_audio_path, lang="ZH", duration_factor=1.0, emo_vector=None):
    tts = get_tts()
    out = os.path.join(MODEL_DIR, "..", "tmp_out", "syn_%d.wav" % int(time.time() * 1000))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    kwargs = dict(
        spk_audio_prompt=ref_audio_path,
        text=text,
        lang=lang,
        output_path=out,
    )
    if duration_factor is not None and duration_factor > 0:
        kwargs["duration_factor"] = float(duration_factor)
    if emo_vector:
        kwargs["emo_vector"] = [float(x) for x in emo_vector]
    tts.infer(**kwargs)
    if not os.path.isfile(out):
        raise RuntimeError("合成失败: 未生成输出文件")
    return out


def main():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port = int(os.environ.get("INDEX_TTS_PORT", "8791"))

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _deny(self, code, msg):
            _audit(event="denied", code=code, reason=msg, path=self.path)
            self._send(code, json.dumps({"error": msg}).encode("utf-8"))

        def do_GET(self):
            if not _check_token(self.headers):
                self._deny(403, "missing/invalid token")
                return
            if self.path.startswith("/health"):
                try:
                    ready = get_tts() is not None
                except Exception:
                    ready = False
                self._send(200, json.dumps({"status": "ok", "ready": ready}).encode("utf-8"))
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if not _check_token(self.headers):
                self._deny(403, "missing/invalid token")
                return
            if not _rate_ok():
                _audit(event="rate_limited", path=self.path)
                self._deny(429, "rate limit exceeded")
                return
            if not self.path.startswith("/synthesize"):
                self._send(404, b"not found")
                return
            with _req_sem:                       # V4：并发上限
                try:
                    # V3：Content-Length 预检 + 截断读取
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    if length > MAX_BODY_BYTES:
                        raise ValueError("body too large (>%d)" % MAX_BODY_BYTES)
                    raw = self.rfile.read(min(length, MAX_BODY_BYTES))
                    req = json.loads(raw.decode("utf-8"))
                    text = req.get("text", "")
                    lang = req.get("lang", "ZH")
                    if not text.strip():
                        raise ValueError("text 不能为空")
                    # V2：路径白名单——ref_audio_path 一律拒绝，仅 base64
                    if req.get("ref_audio_path"):
                        raise PermissionError("ref_audio_path 已禁用（仅 base64）")
                    ref64 = req.get("ref_audio_base64", "")
                    if not ref64:
                        raise ValueError("需要 ref_audio_base64")
                    ref_bytes = base64.b64decode(ref64)
                    if len(ref_bytes) > MAX_REF_AUDIO_BYTES:
                        raise ValueError("参考音频 >10MB 拒绝")
                    ref_path = os.path.join(MODEL_DIR, "..", "tmp_out", "ref_%d.wav" % int(time.time() * 1000))
                    os.makedirs(os.path.dirname(ref_path), exist_ok=True)
                    with open(ref_path, "wb") as f:
                        f.write(ref_bytes)
                    t0 = time.time()
                    out = synthesize(
                        text=text,
                        ref_audio_path=ref_path,
                        lang=lang,
                        duration_factor=req.get("duration_factor", 1.0),
                        emo_vector=req.get("emo_vector"),
                    )
                    dt = time.time() - t0
                    with open(out, "rb") as f:
                        data = f.read()
                    os.remove(out)
                    if len(data) > MAX_OUT_BYTES:      # V5：输出上限
                        raise ValueError("输出音频 >50MB 拒绝")
                    _audit(event="synthesize", ok=True, rt_s=round(dt, 2),
                           len=len(data), lang=lang)
                    self._send(200, data, "audio/wav")
                except Exception as e:
                    # V5：不回显 traceback，仅错误码 + 审计
                    _audit(event="synthesize", ok=False, error="%s: %s" % (type(e).__name__, str(e))[:400])
                    self._send(500, json.dumps({"error": "%s: %s" % (type(e).__name__, str(e))}).encode("utf-8"))

    srv = HTTPServer(("127.0.0.1", port), H)
    print("[indextts] server listening on 127.0.0.1:%d (token=%s)" %
          (port, "configured" if LOCAL_TOKEN else "MISSING——拒绝一切请求"), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
