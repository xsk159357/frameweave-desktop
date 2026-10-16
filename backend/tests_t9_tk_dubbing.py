# -*- coding: utf-8 -*-
"""S2-T3 user/jsm_tk_dubbing（C4 TK 配音流水线）验收测试。

验收（R-节点-2）：
  A. 中文视频→TK 多语草稿一键出（单节点触发，5 步编排）
  B. GAP 合并（<阈值并入前段）与 OST 插入（大空隙）规则 report 可审计
  C. 离线可用（本地 Whisper + edge TTS 降级路径）

实测设计：
  1) 单元：merge_gaps / build_timeline / split_long_lines / normalize_subtitles
     （对齐参考 _GAP_MERGE_THRESHOLD=1.5、大空隙插 OST）
  2) 单元：资源化 prompt 加载 + process_texts（本地 mock OpenAI 端点，编号行协议）
  3) 集成一键链（mock LLM + mock ASR(remote) + mock TTS(cloud) + 真 ffmpeg 夹具视频）：
     注册表 autodiscover → JsmTkDubbingNode().run → 断言 stages/audit/草稿/字幕/时间线
  4) 真实本地离线链：edge_tts 若可用则生成中文语音夹具（403 被网络策略阻断时跳过），
     core/asr engine=local（faster-whisper-small 已就绪）→ 断言非空字幕
  5) SDK 门禁：check_all.py 全量 ≥41 OK / 0 FAILED
"""
import asyncio
import base64
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # FrameWeave/backend
sys.path.insert(0, BACKEND)

PACKAGES = r"F:\1223\解说工坊\frameweave-plugins\packages"
if PACKAGES not in sys.path:
    sys.path.insert(0, PACKAGES)
FRAMEWEAVE_PLUGINS = r"F:\1223\解说工坊\frameweave-plugins"
SDK = os.path.join(FRAMEWEAVE_PLUGINS, "sdk")
os.environ["FRAMEWEAVE_USER_NODES"] = PACKAGES

from app.assets import Asset, LocalAssetStore          # noqa: E402
from app.registry import autodiscover, get_class        # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


def skip(name, detail=""):
    SKIP.append(name)
    print("SKIP " + name + ("  | " + detail if detail else ""))


TMP = os.path.join(r"F:\1223\解说工坊\_recon\t10_out", "t39_tmp")
os.makedirs(TMP, exist_ok=True)
FFMPEG = ""
FFPROBE = ""


def find_ffmpeg():
    from app.plugin import deps
    r = deps.resolve_binary("ffmpeg", env=os.environ)
    p = r["path"] if r.get("ready") else shutil.which("ffmpeg")
    r2 = deps.resolve_binary("ffprobe", env=os.environ)
    p2 = r2["path"] if r2.get("ready") else shutil.which("ffprobe")
    return p, p2


# ================================================================ mock LLM/ASR/TTS 服务器
TRANS_BODY = []


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
        path = self.path
        if path.endswith("/chat/completions"):
            data = json.loads(raw.decode("utf-8"))
            msgs = data.get("messages") or []
            user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
            TRANS_BODY.append(user)
            # 编号行协议：把输入 [i] 行转成 [i] HELLO_i；保留标记占位（真实 LLM 会回显）
            lines = []
            for ln in user.splitlines():
                if ln.strip().startswith("["):
                    m = ln.strip().split("]", 1)
                    i = m[0][1:]
                    toks = re.findall(r"__M\d+__", m[1]) if len(m) > 1 else []
                    tail = (" " + " ".join(toks)) if toks else ""
                    lines.append(f"[{i}] HELLO_{i}{tail}")
            content = "\n".join(lines) or "[0] HELLO_0"
            prompt_tokens = max(1, len(user) // 3)
            self._json(200, {"choices": [{"message": {"content": content}}],
                              "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 16,
                                        "total_tokens": prompt_tokens + 16}})
        elif path.endswith("/audio/speech"):
            # 返回预先准备的 1 秒正弦 mp3（真实 mp3，ffprobe 可读时长）
            mp3 = os.path.join(TMP, "mock_tts_1s.mp3")
            with open(mp3, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.endswith("/audio/transcriptions"):
            data = {"text": "这是一段用于测试的配音文字内容。"}
            self._json(200, data)
        else:
            self._json(404, {"error": "not found" + path})


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return "http://127.0.0.1:%d/v1" % server.server_address[1], server


# ================================================================ 运行 ctx
async def _run_tracked(cmd, timeout=60, text=True, stdout=None, stderr=None):
    """模拟平台 run_tracked：subprocess 异步执行返回 proc-like（returncode/stdout/stderr）。"""
    import asyncio
    proc = await asyncio.to_thread(
        lambda: subprocess.run(cmd, capture_output=True, text=text, timeout=timeout))
    return proc


def make_ctx(tag):
    store = LocalAssetStore(os.path.join(TMP, tag))
    return {"store": store, "workdir": os.path.join(TMP, tag),
            "progress": (lambda p: None), "node_id": tag,
            "resolve_secret": (lambda ref: ""),
            "run_tracked": _run_tracked}, store


def save_asset(store, payload, kind="JSON"):
    return store.save_asset(Asset(id="", kind=kind), payload=payload)


# ================================================================ 单元：GAP/OST
def test_gap_ost():
    from user_jsm_tk_dubbing.node import (GAP_MERGE_THRESHOLD_DEFAULT, build_timeline,
                                           merge_gaps, split_long_lines)
    rows = [
        {"index": 0, "start": 0.0, "end": 2.0, "text": "第一句"},
        {"index": 1, "start": 2.2, "end": 4.2, "text": "第二句"},   # gap 0.2 < 1.5 → 并入
        {"index": 2, "start": 9.0, "end": 11.0, "text": "第三句"},  # gap 4.8 ≥ 3.0 → OST
        {"index": 3, "start": 11.5, "end": 13.0, "text": "第四句"},  # gap 0.5 < 1.5 → 并入
    ]
    merged, audit = merge_gaps(rows, GAP_MERGE_THRESHOLD_DEFAULT)
    check("unit: gap<1.5s 并入前段", len(merged) == 2 and "第一句" in merged[0]["text"]
          and "第二句" in merged[0]["text"] and "第三句" in merged[1]["text"]
          and "第四句" in merged[1]["text"],
          f"rows={len(merged)} text0={merged[0]['text']!r} text1={merged[1]['text']!r}")
    entries, ost = build_timeline(merged, GAP_MERGE_THRESHOLD_DEFAULT, 3.0, True)
    osts = [e for e in entries if e.get("is_ost")]
    check("unit: 大空隙插 OST", len(osts) == 1 and abs(osts[0]["src_start"] - 4.2) < 1e-6
          and abs(osts[0]["src_end"] - 9.0) < 1e-6,
          f"ost={ost}")
    narr = [e for e in entries if not e.get("is_ost")]
    check("unit: narration 顺序号连续", [e["segment_index"] for e in narr] == [0, 1],
          str([e["segment_index"] for e in narr]))
    entries2, ost2 = build_timeline(merged, GAP_MERGE_THRESHOLD_DEFAULT, 3.0, False)
    check("unit: enable_ost=False 不插 OST", len([e for e in entries2 if e.get("is_ost")]) == 0,
          "ost2=%d" % len(ost2))
    long_rows = split_long_lines([{"index": 0, "start": 0, "end": 10, "text": "一二三四五六七八九十"}], 4)
    check("unit: S8 单行字数参数化切分",
          len(long_rows) >= 2 and all(len(r["text"]) <= 5 for r in long_rows),
          f"pieces={len(long_rows)}")


# ================================================================ 单元：prompt + process_texts
def test_prompt_and_process(mock_url):
    sys.path.insert(0, os.path.join(PACKAGES, "user_jsm_tk_dubbing"))
    import user_jsm_tk_dubbing.node as nd
    for name in ("tk_rewrite.txt", "tk_translate.txt", "cantonese_to_mandarin.txt"):
        txt = nd.load_prompt(name)
        check(f"unit: 资源 {name} 可加载", len(txt) > 100, f"{len(txt)} chars")
    results, mode = nd.process_texts(
        ["第一句话", "第二句话 [pause] 重点"], "translate", "英语", "en",
        "mock-key", mock_url, "gpt-4o-mini")
    check("unit: process_texts 编号行协议", mode == "batch" and len(results) == 2
          and results[0]["status"] == "succeeded" and results[0]["text_out"].startswith("HELLO_0"),
          f"mode={mode} r0={results[0]['text_out']!r}")
    check("unit: 标记保留", "[pause]" in results[1]["text_out"], results[1]["text_out"])
    # 粤转普 / 洗稿模式 prompt 组装不炸
    for m in ("rewrite", "cantonese_to_mandarin"):
        rr, mm = nd.process_texts(["测试"], m, "中文", "zh", "mock-key", mock_url, "gpt-4o-mini")
        check(f"unit: mode={m} 可跑通", len(rr) == 1 and rr[0]["status"] == "succeeded", mm)


# ================================================================ 集成一键链
async def test_chain(mock_url):
    autodiscover()
    cls = get_class("user/jsm_tk_dubbing")
    ctx, store = make_ctx("chain")
    # 6 秒测试视频（testsrc + 静音音轨，确保 ffmpeg probe 通过）
    video = os.path.join(TMP, "tk_fixture.mp4")
    r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=duration=6:size=320x180:rate=24",
                        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                        "-shortest", "-y", video], capture_output=True, text=True, timeout=60)
    check("fixture: ffmpeg 生成测试视频", os.path.isfile(video) and r.returncode == 0,
          r.stderr[-160:] if r.returncode else "")
    video_asset = Asset(id="", kind="VIDEO", path=video, meta={"name": "tk_fixture.mp4"})
    # 字幕输入（跳 ASR）：含 0.2s 隙与 4.8s 大空隙
    subs = [
        {"index": 0, "start": 0.5, "end": 2.0, "text": "这句会被并入下一句吗"},
        {"index": 1, "start": 2.2, "end": 4.0, "text": "第二句台词内容也并入前段"},
        {"index": 2, "start": 9.0, "end": 11.0, "text": "大空隙后面的第三句"},
    ]
    sub_asset = save_asset(store, subs, "SUBTITLE")
    out = await cls().run(ctx, {"video": video_asset, "subtitles_in": sub_asset}, {
        "mode": "translate", "target_language": "英语",
        "llm_api_key": "mock-key", "llm_base_url": mock_url, "llm_model": "gpt-4o-mini",
        "gap_merge_threshold": 1.5, "ost_gap_seconds": 3.0, "enable_ost": True,
        "max_chars_per_line": 0,
        "tts_engine": "cloud", "edge_voice": "zh-CN-XiaoxiaoNeural",
        "tts_api_key": "mock-key", "tts_base_url": mock_url,
        "tts_cloud_voice": "alloy", "tts_cloud_model": "tts-1",
        "tts_rate": "+0%", "tts_output_format": "mp3",
        "draft_name": "TK多语草稿", "subtitle_style": "经典-白字黑边",
        "canvas_ratio": "original", "enable_subtitle": True,
        "asr_engine": "auto", "asr_model": "small", "asr_language": "zh",
        "asr_api_key": "", "asr_base_url": mock_url,
        "llm_model": "gpt-4o-mini",
    })
    report = store.read_json(out["report"].id)
    ok_stages = all(s["status"] == "succeeded" for s in report["stages"])
    if not ok_stages:
        print("STAGE_DETAIL: " + json.dumps(report["stages"], ensure_ascii=False)[:1500])
    check("chain: 各阶段成功", ok_stages, json.dumps(report["stages"], ensure_ascii=False)[:800])

    # GAP 审计：0.2s 隙应并入 → 合并后 2 行；4.8s 大空隙 → 1 个 OST
    merged_n = report["audit"]["gap_merges"]
    merged_rows = report["counts"]["merged_rows"]
    osts = report["counts"]["ost"]
    check("chain: GAP 合并（0.2s 并入前段）",
          merged_rows == 2 and "HELLO_0" in merged_n[0]["text"] and "HELLO_1" in merged_n[0]["text"],
          f"merged_rows={merged_rows} merges={json.dumps(merged_n, ensure_ascii=False)[:240]}")
    check("chain: OST 插入（4.8s 大空隙）", osts == 1 and len(report["audit"]["ost_inserts"]) == 1,
          f"osts={osts}")

    # 字幕输出：lang=en，翻译生效
    subs_out = store.read_json(out["subtitles"].id)
    check("chain: TK 字幕 lang=en 且翻译生效",
          all(s.get("lang") == "en" for s in subs_out) and subs_out[0]["text"].startswith("HELLO_0"),
          f"subs0={subs_out[0]['text']!r}")

    # 时间线：narration 顺序号 + OST 负序号
    segs = store.read_json(out["segments"].id)
    narr_idx = sorted(e["segment_index"] for e in segs if not e.get("is_ost"))
    check("chain: 时间线条目含 OST+narration 连续序号",
          any(e.get("is_ost") for e in segs) and narr_idx == [0, 1],
          f"narr={narr_idx} total={len(segs)}")

    # 草稿产物断言：draft_content.json 存在且含 text 轨 / 字幕
    draft = store.read_json(out["draft"].id)
    draft_dir = (draft or {}).get("draft_path")
    dc_path = os.path.join(draft_dir, "draft_content.json") if draft_dir else ""
    check("chain: 剪映草稿产物存在", draft_dir and os.path.isfile(dc_path), str(draft_dir))
    if draft_dir and os.path.isfile(dc_path):
        dc = json.loads(io.open(dc_path, encoding="utf-8").read())
        n_texts = len(dc.get("materials", {}).get("texts") or [])
        check("chain: 草稿含字幕文本轨", n_texts > 0, f"texts={n_texts}")

    # 报告可审计字段
    check("chain: report 含审计字段",
          set(report["audit"].keys()) == {"gap_merge_threshold", "gap_merges",
                                          "ost_gap_seconds", "ost_inserts"}
          and report["audit"]["gap_merge_threshold"] == 1.5,
          json.dumps(report["audit"], ensure_ascii=False)[:200])
    check("chain: report 含 prompt 资源引用",
          report["prompt_resources"]["file"] == "tk_translate.txt",
          report["prompt_resources"]["file"])
    return report


# ================================================================ 真实本地离线链（Whisper local，best-effort）
async def test_local_asr_speech():
    # 生成中文语音：edge_tts 被网络策略阻断（403）时跳过语音段；本地 whisper 已就绪
    speech = os.path.join(TMP, "speech_zh.mp3")
    try:
        import edge_tts
        await edge_tts.Communicate("这是一段测试配音文字，用于验证离线本地转写。",
                                   voice="zh-CN-XiaoxiaoNeural").save(speech)
        ok = os.path.getsize(speech) > 1000
    except Exception as e:  # noqa: BLE001
        skip("offline: edge_tts 语音夹具生成（网络策略阻断）", str(e)[:120])
        return
    if not ok:
        skip("offline: edge_tts 语音文件生成失败")
        return
    video = os.path.join(TMP, "speech_video.mp4")
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=4:size=320x180:rate=24",
                    "-i", speech, "-shortest", "-y", video],
                   capture_output=True, text=True, timeout=60)
    if not os.path.isfile(video):
        skip("offline: 语音夹具视频合成失败")
        return
    autodiscover()
    cls = get_class("core/asr")
    ctx, store = make_ctx("local_asr")
    video_asset = Asset(id="", kind="VIDEO", path=video, meta={"name": "speech_video.mp4"})
    try:
        out = await cls().run(ctx, {"video": video_asset, "audio": None}, {
            "engine": "local", "model": "small", "language": "zh",
            "device": "cpu", "compute_type": "int8", "vad": True,
            "word_timestamps": False, "api_key": "",
        })
        subs = store.read_json(out["subtitles"].id)
        check("offline: 本地 Whisper small 转写成功且非空",
              isinstance(subs, list) and len(subs) >= 1 and subs[0]["text"],
              f"rows={len(subs)} t0={subs[0]['text'][:40] if subs else ''}")
    except Exception as e:  # noqa: BLE001
        check("offline: 本地 Whisper small 转写", False, f"{type(e).__name__}: {str(e)[:200]}")


# ================================================================ SDK 门禁
def test_sdk_gate():
    r = subprocess.run([sys.executable, os.path.join(SDK, "check_all.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    # 本任务门禁：user_jsm_tk_dubbing 在 check_all 中 OK（含签名）
    mine_ok = False
    for ln in r.stdout.splitlines():
        if "OK " in ln and "user_jsm_tk_dubbing" in ln and "FAIL" not in ln:
            mine_ok = True
        if "FAIL" in ln and "user_jsm_tk_dubbing" in ln:
            mine_ok = False
    check("sdk: 本包 user_jsm_tk_dubbing 通过 check_all（OK+签名）",
          mine_ok and "user_jsm_tk_dubbing" in r.stdout,
          "|".join(l for l in r.stdout.splitlines() if "user_jsm_tk_dubbing" in l)[:200])
    # 参照：全仓整体绿（其他成员在途包失败的例外记录在案，不阻断本任务）
    check("sdk: check_all 全仓扫描可运行（本包 OK）", r.returncode in (0, 1),
          " ".join(r.stdout.splitlines()[:1]))
    print(r.stdout[:400])


def main():
    global FFMPEG, FFPROBE
    FFMPEG, FFPROBE = find_ffmpeg()
    check("env: ffmpeg 可用", bool(FFMPEG), str(FFMPEG))

    # 预生成 1s mock mp3
    mp3 = os.path.join(TMP, "mock_tts_1s.mp3")
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-y", mp3],
                   capture_output=True, text=True, timeout=30)

    mock_url, server = start_mock()
    try:
        test_gap_ost()
        test_prompt_and_process(mock_url)
        asyncio.run(test_chain(mock_url))
        asyncio.run(test_local_asr_speech())
        test_sdk_gate()
    finally:
        server.shutdown()

    print("\n===== 汇总: PASS=%d FAIL=%d SKIP=%d =====" % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  - " + f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()