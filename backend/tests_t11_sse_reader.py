# -*- coding: utf-8 -*-
"""S3-T1 core/sse_reader（E5 SSE 流读取）验收测试。

验收：
  A. OpenAI 与 Google 两种 SSE 格式均可解析
  B. 0.2s 节流生效
  C. 中断/重试语义明确

实测设计（本地 mock SSE 服务器 + SDK 门禁）：
  1) 单元：consume_sse 纯函数——openai/google/auto 格式、坏 payload 跳过、
     [DONE] 终止、节流门（on_push 间隔 ≥ throttle）
  2) 集成：mock SSE 服务器 4 个端点（openai/google 正常流 / 500 先败后成 / 中途断流），
     经注册表 get_class 跑节点 → 断言 text/events/report
  3) 重试语义：500×2 后成功 → report.retries=2；中途断流 → partial=true 且 retries=0
  4) 中断语义：cancel_event 提前置位 → interrupted=true 且返回已收部分
  5) SDK 门禁：check_manifest OK + 签名真实 + check_all 全绿
"""
import asyncio
import importlib.util
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

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

PACKAGES = r"F:\1223\解说工坊\frameweave-plugins\packages"
if PACKAGES not in sys.path:
    sys.path.insert(0, PACKAGES)
os.environ["FRAMEWEAVE_USER_NODES"] = PACKAGES

from app.assets import LocalAssetStore        # noqa: E402
from app.registry import autodiscover, get_class  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


TMP = os.path.join(r"F:\1223\解说工坊\_recon\t10_out", "t17_tmp")
os.makedirs(TMP, exist_ok=True)

OPENAI_CHARS = list("你好世界，这是一段流式文本。")
GOOGLE_CHARS = list("你好世界，这是一段谷歌流式文本。")

CALLS = {"count": 0}


class MockSSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _sse(self, chunks, delay=0.03, done=True, fmt="openai", garbage=False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for i, ch in enumerate(chunks):
            if fmt == "openai":
                ev = {"choices": [{"delta": {"content": ch}}]}
            else:
                ev = {"candidates": [{"content": {"parts": [{"text": ch}]}}]}
            line = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            try:
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if garbage and i == 1:
                try:
                    self.wfile.write(b"data: {not json}\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
            time.sleep(delay)
        if done:
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

    def do_POST(self):
        CALLS["count"] += 1
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        p = self.path
        if p == "/openai":
            self._sse(OPENAI_CHARS, fmt="openai")
        elif p == "/google":
            self._sse(GOOGLE_CHARS, fmt="google")
        elif p == "/auto_openai":
            self._sse(OPENAI_CHARS, fmt="openai")
        elif p == "/err500":
            n = CALLS["count"]
            if n <= 2:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
            else:
                self._sse(OPENAI_CHARS, fmt="openai")
        elif p == "/midfail":
            self._sse(OPENAI_CHARS[:3], done=False)
            # 写完 3 块后立即断开（不写 [DONE]）
            self.wfile.close()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"nope")

    def do_GET(self):
        self.do_POST()


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockSSEHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return "http://127.0.0.1:%d" % server.server_address[1], server


def make_ctx(tag, stream_push=None, cancel_event=None):
    store = LocalAssetStore(os.path.join(TMP, tag))
    return {"store": store, "workdir": os.path.join(TMP, tag),
            "progress": (lambda p: None), "node_id": tag,
            "resolve_secret": (lambda ref: ""),
            "stream_push": stream_push, "cancel_event": cancel_event}, store


# ================================================================ 单元
def test_unit():
    from core_sse_reader.node import consume_sse, iter_sse_payloads

    def lines(s):
        return [ln.encode("utf-8") for ln in s.split("\n")]

    sse_o = "data: {\"choices\":[{\"delta\":{\"content\":\"你\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"好\"}}]}\n\ndata: [DONE]\n\n"
    text, fmt, done = consume_sse(lines(sse_o), fmt="openai", throttle_seconds=0.2)
    check("unit: openai 格式解析 + [DONE]", text == "你好" and fmt == "openai" and done, f"{text!r} fmt={fmt} done={done}")

    sse_g = "data: {\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"世\"}]}}]}\n\ndata: {\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"界\"}]}}]}\n\ndata: [DONE]\n\n"
    text, fmt, done = consume_sse(lines(sse_g), fmt="google", throttle_seconds=0.2)
    check("unit: google 格式解析 + [DONE]", text == "世界" and fmt == "google" and done, f"{text!r}")

    mixed = "data: {\"choices\":[{\"delta\":{\"content\":\"自\"}}]}\n\ndata: {\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"动\"}]}}]}\n\ndata: [DONE]\n\n"
    text, fmt, done = consume_sse(lines(mixed), fmt="auto", throttle_seconds=0.2)
    check("unit: auto 双格式混流逐块识别", text == "自动" and done,
          f"{text!r} fmt={fmt}")

    bad = "data: {not json}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"好\"}}]}\n\ndata: [DONE]\n\n"
    warns = []
    text, fmt, done = consume_sse(lines(bad), fmt="openai", throttle_seconds=0.2, warnings=warns)
    check("unit: 坏 payload 跳过继续流", text == "好" and any("坏 payload" in w for w in warns),
          f"{text!r} warns={warns[:1]}")

    # 节流：慢速流（每块 60ms、8 块共约 0.48s，跨多个节流窗）→ 推送 ≥2 次且间隔 ≥ 0.25s
    pushes = []
    t0 = time.monotonic()

    def slow_lines():
        for c in "abcdefgh":
            yield ("data: {\"choices\":[{\"delta\":{\"content\":\"%s\"}}]}\n\n" % c).encode("utf-8")
            time.sleep(0.06)

    consume_sse(slow_lines(), fmt="openai", throttle_seconds=0.25,
                on_push=lambda total, delta: pushes.append((time.monotonic() - t0, delta)))
    gaps = [b - a for (a, _), (b, _) in zip(pushes, pushes[1:])]
    check("unit: 0.25s 节流门生效（间隔 ≥0.24-ε）",
          len(pushes) >= 2 and all(g >= 0.24 for g in gaps),
          f"pushes={len(pushes)} gaps={[round(g, 3) for g in gaps]}")


# ================================================================ 集成
async def run_node(mock_url, path, *, fmt="auto", retries=2, stream_push=None,
                   cancel_event=None, tag="run"):
    autodiscover()
    cls = get_class("core/sse_reader")
    ctx, store = make_ctx(tag, stream_push=stream_push, cancel_event=cancel_event)
    out = await cls().run(ctx, {}, {
        "endpoint": mock_url + path, "method": "POST",
        "payload": '{"stream": true}', "headers": "",
        "api_key": "", "format": fmt, "throttle_seconds": 0.2,
        "stream_timeout": 30, "retries": retries, "retry_delay": 0.2,
    })
    text = store.read_json(out["text"].id).get("text", "")
    events = store.read_json(out["events"].id)
    report = store.read_json(out["report"].id)
    return text, events, report, store


async def test_integration(mock_url):
    # OpenAI 流
    text, events, report, _ = await run_node(mock_url, "/openai", tag="o")
    check("集成: OpenAI 流解析", text == "".join(OPENAI_CHARS)
          and report["format"] == "openai" and report["done"] is True
          and len(events) == len(OPENAI_CHARS),
          f"chars={len(text)} events={len(events)} format={report['format']}")
    # Google 流
    text, events, report, _ = await run_node(mock_url, "/google", tag="g")
    check("集成: Google 流解析", text == "".join(GOOGLE_CHARS)
          and report["format"] == "google" and report["done"] is True,
          f"chars={len(text)} format={report['format']}")
    # auto 识别
    text, events, report, _ = await run_node(mock_url, "/auto_openai", fmt="auto", tag="a")
    check("集成: auto 识别 openai", text == "".join(OPENAI_CHARS) and report["format"] == "openai",
          f"format={report['format']}")
    # 重试：前两次 500 → 第 3 次成功
    CALLS["count"] = 0
    text, events, report, _ = await run_node(mock_url, "/err500", retries=2, tag="r")
    check("集成: 连接级失败重试（500×2 后成功）",
          text == "".join(OPENAI_CHARS) and report["retries"] == 2,
          f"retries={report['retries']} chars={len(text)}")
    # 中途断流：部分内容 + 不重试
    text, events, report, _ = await run_node(mock_url, "/midfail", retries=2, tag="m")
    check("集成: 中途断流 → partial 且不重试",
          report["partial"] is True and report["retries"] == 0
          and text == "".join(OPENAI_CHARS[:3]),
          f"partial={report['partial']} retries={report['retries']} text={text!r}")
    # 节流：stream_push 间隔 ≥ 0.2s（服务器 0.03s/块 → 全程约 0.6s → 3-4 次推送）
    pushes = []
    text, events, report, _ = await run_node(mock_url, "/openai", stream_push=lambda t, d: pushes.append((time.monotonic(), d)), tag="t")
    gaps = [b - a for (a, _), (b, _) in zip(pushes, pushes[1:])]
    check("集成: 0.2s 节流生效（stream_push 间隔 ≥0.19s）",
          len(pushes) >= 2 and all(g >= 0.19 for g in gaps),
          f"pushes={len(pushes)} gaps={[round(g, 2) for g in gaps]}")
    # 中断语义：cancel_event 提前置位 → interrupted + 部分内容
    class CE:
        def __init__(self, after):
            self.after = after
            self.n = 0
            self.flag = False
        def is_set(self):
            self.n += 1
            if self.n >= self.after:
                self.flag = True
            return self.flag
    ce = CE(2)
    text, events, report, _ = await run_node(mock_url, "/openai", cancel_event=ce, tag="c")
    check("集成: cancel_event 中断 → interrupted 且返回部分内容",
          report["interrupted"] is True and 0 < len(text) < len("".join(OPENAI_CHARS)),
          f"interrupted={report['interrupted']} chars={len(text)}")


def test_sdk():
    sdk = r"F:\1223\解说工坊\frameweave-plugins\sdk"
    pkg = os.path.join(PACKAGES, "core_sse_reader")
    r = subprocess.run([sys.executable, os.path.join(sdk, "check_manifest.py"), pkg],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("sdk: core/sse_reader check_manifest OK", r.returncode == 0, r.stdout[-200:])
    with open(os.path.join(pkg, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    sig = m.get("signature") or {}
    check("sdk: 签名真实（非占位）", bool(sig.get("value")) and "pending" not in str(sig.get("value")),
          sig.get("value", "")[:24])
    r2 = subprocess.run([sys.executable, os.path.join(sdk, "check_all.py")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    my_ok = any("OK " in ln and "core_sse_reader" in ln and "FAIL" not in ln
                for ln in r2.stdout.splitlines())
    ext = [ln.strip() for ln in r2.stdout.splitlines() if "FAIL" in ln and ln.strip().startswith("FAIL")]
    check("sdk: 本包 core/sse_reader 在 check_all 中 OK", my_ok,
          " ".join(l for l in r2.stdout.splitlines() if "core_sse_reader" in l)[:160])
    if ext:
        print("  [info] check_all 外部失败（非本任务 in-scope，上报 captain 路由对应任务）: %s" % ext[0])


def main():
    mock_url, server = start_mock()
    try:
        test_unit()
        asyncio.run(test_integration(mock_url))
        test_sdk()
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