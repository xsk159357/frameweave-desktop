# -*- coding: utf-8 -*-
"""IndexTTS2 本地声音克隆常驻服务（Python 3.11 venv 运行）。

供主后端(3.14)通过 HTTP 调用。模型加载一次，多请求复用。
端点:
  GET  /health            -> {"status":"ok","ready":bool}
  POST /synthesize        -> 克隆合成
       body: {text, ref_audio_base64, ref_audio_sample_rate, lang, duration_factor?, emotion?}
       resp: 200 wav 二进制
"""
import os, sys, base64, io, time, json, traceback, threading

sys.path.insert(0, r"C:/Users/Administrator/fw-patch")          # wetext ASCII 副本
CODE_DIR = r"F:/1223/解说工坊/FrameWeave/backend/models/index-tts-code"
sys.path.insert(0, CODE_DIR)
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_CACHE"] = os.path.join(CODE_DIR, "checkpoints", "hf_cache")

MODEL_DIR = os.path.join(CODE_DIR, "checkpoints")
_load_lock = threading.Lock()
_tts = None

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

        def do_GET(self):
            if self.path.startswith("/health"):
                try:
                    ready = get_tts() is not None
                except Exception:
                    ready = False
                self._send(200, json.dumps({"status": "ok", "ready": ready}).encode("utf-8"))
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if not self.path.startswith("/synthesize"):
                self._send(404, b"not found")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                req = json.loads(raw.decode("utf-8"))
                text = req.get("text", "")
                lang = req.get("lang", "ZH")
                if not text.strip():
                    raise ValueError("text 不能为空")
                # 参考音频: base64 或路径
                ref64 = req.get("ref_audio_base64", "")
                ref_path = req.get("ref_audio_path", "")
                if not ref_path:
                    if not ref64:
                        raise ValueError("需要 ref_audio_base64 或 ref_audio_path")
                    ref_bytes = base64.b64decode(ref64)
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
                self._send(200, data, "audio/wav")
                print("[indextts] synthesize %.1fs len=%dB" % (dt, len(data)), flush=True)
            except Exception as e:
                traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}).encode("utf-8"))

    srv = HTTPServer(("127.0.0.1", port), H)
    print("[indextts] server listening on 127.0.0.1:%d" % port, flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
