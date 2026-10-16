# -*- coding: utf-8 -*-
"""S3-T2 core/llm_call（D6 统一 LLM 调用）验收测试。

验收（对齐 08 §S3-T2 acceptance/verify）：
  A. 统一实现内退避（默认 [2,5,10]，可参数化）/ fallback_models / JSON mode / schema 校验
  B. 既有 LLM 节点行为不回退：全部保留 internal 原内嵌调用通道；llm_call 通道经注册表/单实现复用
  C. R1 规避：URL 一律 base_url.rstrip("/") + "/chat/completions"（不重复拼 /v1）
  D. R2 规避：402 等按 HTTP 状态显式报错（附 body），不做"余额不足"猜测

实测设计（本地 mock OpenAI/Gemini HTTP 服务器 + SDK 门禁）：
  1) 单元：core_llm_call.llm_call_json 直调——openai/gemini 成功、429 退避重试、
     500 主模型耗尽回退 fallback_models、400 永久错误不重试不回退、402 显式报错（R2）、
     JSON mode+schema 校验（通过 / 全部失败归类 schema）、缺 api_key/model 显式错误、
     R1 URL 直拼断言（mock 记录请求路径）
  2) 节点集成：注册表 get_class("core/llm_call") 跑节点——prompt 输入→text/report 资产、
     fallback_models+schema 参数管道、非法 backoff 参数回落默认序列
  3) 既有节点全量冒烟（llm_call 通道）：core/translate、core/ai_narrate（llm_channel）、
     user/jsm_script_doctor、user/jsm_highlight_plan、user/jsm_highlight_mixcut、
     user/jsm_translate_batch——每个节点经 llm_call 后端跑通并断言输出结构；
     同时断言这些节点的 internal 内嵌通道函数仍存在（行为不回退）
  4) SDK 门禁：core/llm_call check_manifest OK + 签名真实；check_all 全绿；7 个包
     spec/manifest 端口一致性（core_scene_detect 缺 numpy 为既有环境问题，非本任务范围）
"""
import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys

# LLM 通道配置化隔离：测试环境不得读取开发机全局配置（backend/data/settings.json），
# 否则"空 api_key → 抛 auth"单元语义会被全局 Key 破坏。指向独立空数据目录。
_ISOLATED_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".t18_llmcfg_isolated")
os.makedirs(_ISOLATED_DATA, exist_ok=True)
os.environ["FRAMEWEAVE_DATA_DIR"] = _ISOLATED_DATA
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = os.path.dirname(os.path.abspath(__file__))   # FrameWeave/backend
sys.path.insert(0, BACKEND)

PACKAGES = r"F:\1223\解说工坊\frameweave-plugins\packages"
if PACKAGES not in sys.path:
    sys.path.insert(0, PACKAGES)
os.environ["FRAMEWEAVE_USER_NODES"] = PACKAGES

from app.assets import Asset, LocalAssetStore        # noqa: E402
from app.registry import autodiscover, get_class     # noqa: E402

# 直连 mock，不经系统代理
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


TMP = os.path.join(r"F:\1223\解说工坊\.tmp", "t18_llm_call_scratch")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))


# ================================================================ mock LLM 服务器
BEHAVIOR = {"mode": "ok", "content": "你好世界", "fail_count": 0, "n": 0, "paths": []}


class MockLLMHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        BEHAVIOR["n"] += 1
        BEHAVIOR["paths"].append(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        mode = BEHAVIOR["mode"]
        n = BEHAVIOR["n"]
        fc = BEHAVIOR["fail_count"]
        if mode == "429_then_ok" and n <= fc:
            self._send(429, b'{"error":{"message":"rate limited"}}')
            return
        if mode == "500_then_ok" and n <= fc:
            self._send(500, b'{"error":{"message":"upstream boom"}}')
            return
        if mode == "always_429":
            self._send(429, b'{"error":{"message":"rate limited"}}')
            return
        if mode == "http400":
            self._send(400, b'{"error":{"message":"bad request"}}')
            return
        if mode == "http402":
            self._send(402, b'{"error":{"message":"insufficient quota"}}')
            return
        if mode == "gemini":
            payload = {"candidates": [{"content": {"parts": [{"text": BEHAVIOR["content"]}]}}],
                       "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 9}}
            self._send(200, json.dumps(payload).encode("utf-8"))
            return
        payload = {"choices": [{"message": {"role": "assistant", "content": BEHAVIOR["content"]}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        self.do_POST()


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLLMHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return "http://127.0.0.1:%d" % server.server_address[1], server


def reset_behavior(mode="ok", content="你好世界", fail_count=0):
    BEHAVIOR.update({"mode": mode, "content": content, "fail_count": fail_count,
                     "n": 0, "paths": []})


def make_ctx(tag):
    root = os.path.join(TMP, tag)
    os.makedirs(root, exist_ok=True)
    store = LocalAssetStore(root)
    return {"store": store, "workdir": root, "progress": (lambda p: None),
            "node_id": tag, "resolve_secret": (lambda ref: "")}, store


# ================================================================ 单元：llm_call_json
def test_unit():
    from core_llm_call.node import LLMCallError, llm_call_json

    mock, server = start_mock()
    try:
        # 1) openai 成功：文本 + usage + attempts=1 + 无退避
        reset_behavior("ok", "统一LLM调用成功")
        r = llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          temperature=0.4, max_tokens=4096, timeout=10,
                          backoff=[0.05, 0.1])
        check("unit: openai 成功（text/usage/attempts）",
              r["text"] == "统一LLM调用成功" and r["attempts"] == 1
              and r["usage"]["prompt_tokens"] == 10 and r["usage"]["completion_tokens"] == 20
              and r["backoffs"] == [] and r["schema_valid"] is True,
              json.dumps({k: r[k] for k in ("text", "attempts", "backoffs", "usage")}, ensure_ascii=False))

        # 2) gemini 成功：文本 + usageMetadata 映射
        reset_behavior("gemini", "谷歌统一调用成功")
        r = llm_call_json([{"role": "user", "content": "hi"}], provider="gemini",
                          api_key="gk", base_url=mock + "/v1beta", model="gemini-2.0-flash",
                          timeout=10, backoff=[0.05, 0.1])
        check("unit: gemini 成功（text/usage）",
              r["text"] == "谷歌统一调用成功"
              and r["usage"]["prompt_tokens"] == 5 and r["usage"]["completion_tokens"] == 9,
              r["text"])

        # 3) 429 退避重试：2 次 429 → 第 3 次成功，backoffs=[0.05,0.1]（3 退避=3 次尝试）
        reset_behavior("429_then_ok", "限流后成功", fail_count=2)
        r = llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          timeout=10, backoff=[0.05, 0.1, 0.15])
        check("unit: 429 退避重试（429×2→成功）",
              r["attempts"] == 3 and r["text"] == "限流后成功"
              and r["backoffs"] == [0.05, 0.1] and r["model_used"] == "gpt-4o",
              f"attempts={r['attempts']} backoffs={r['backoffs']}")

        # 4) 500 主模型重试耗尽 → fallback_models 回退成功
        reset_behavior("500_then_ok", "回退模型成功", fail_count=3)
        r = llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          fallback_models=["gpt-4o-mini"], timeout=10,
                          backoff=[0.05, 0.1, 0.15])
        check("unit: 500×3 → fallback 回退成功",
              r["model_used"] == "gpt-4o-mini" and r["text"] == "回退模型成功"
              and r["attempts"] == 4 and r["warnings"]
              and "回退" in r["warnings"][0],
              f"model={r['model_used']} attempts={r['attempts']} warn={r['warnings'][:1]}")

        # 5) 400 永久错误：不重试不回退，显式 LLMCallError(http)
        reset_behavior("http400")
        try:
            llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          fallback_models=["gpt-4o-mini"], timeout=10, backoff=[0.05, 0.1])
            raise AssertionError("应抛 LLMCallError")
        except LLMCallError as e:
            check("unit: 400 永久失败不重试不回退",
                  e.code == "http" and "400" in str(e) and "永久失败" in str(e), str(e)[:120])
        check("unit: 400 未触发任何请求（attempts 无消耗）",
              BEHAVIOR["n"] == 1, f"n={BEHAVIOR['n']}")

        # 6) 402 显式报错（R2 规避：附状态码与 body，不做余额猜测）
        reset_behavior("http402")
        try:
            llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          timeout=10, backoff=[0.05, 0.1])
            raise AssertionError("应抛 LLMCallError")
        except LLMCallError as e:
            msg = str(e)
            # R2：显式报出 HTTP 状态与 body 摘要，不做"余额不足"猜测
            check("unit: 402 显式报错（R2）",
                  e.code == "http" and "402" in msg and "insufficient" in msg
                  and "余额" not in msg,
                  msg[:120])

        # 7) JSON mode + schema 通过
        reset_behavior("ok", '{"ok": true, "value": 7}')
        schema = {"type": "object", "required": ["ok", "value"],
                  "properties": {"ok": {"type": "boolean"}, "value": {"type": "integer"}}}
        r = llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          json_mode=True, schema=schema, timeout=10, backoff=[0.05, 0.1])
        check("unit: JSON mode + schema 校验通过",
              r["parsed"] == {"ok": True, "value": 7} and r["schema_valid"] is True
              and r["schema_errors"] == [], json.dumps(r.get("parsed"), ensure_ascii=False))

        # 8) schema 全部失败：重试→回退→全失败 → LLMCallError(code=schema)
        reset_behavior("ok", "这不是合法JSON")
        try:
            llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                          api_key="sk-test", base_url=mock, model="gpt-4o",
                          fallback_models=["gpt-4o-mini"], json_mode=True,
                          schema=schema, timeout=10, backoff=[0.05, 0.05])
            raise AssertionError("应抛 LLMCallError")
        except LLMCallError as e:
            check("unit: schema 失败归类 LLMCallError(schema)",
                  e.code == "schema" and "JSON 解析失败" in str(e), str(e)[:150])

        # 9) 缺 api_key / 空 model 显式错误
        try:
            llm_call_json([{"role": "user", "content": "hi"}], api_key="",
                          base_url=mock, model="gpt-4o")
            raise AssertionError("应抛 auth")
        except LLMCallError as e:
            check("unit: 缺 api_key → code=auth", e.code == "auth", str(e)[:80])
        try:
            llm_call_json([{"role": "user", "content": "hi"}], api_key="k",
                          base_url=mock, model="  ")
            raise AssertionError("应抛 invalid")
        except LLMCallError as e:
            check("unit: 空 model → code=invalid", e.code == "invalid", str(e)[:80])

        # 10) R1：URL 直拼 /chat/completions（尾斜杠归一，不重复拼 /v1）
        reset_behavior("ok", "R1路径正确")
        llm_call_json([{"role": "user", "content": "hi"}], provider="openai",
                      api_key="sk-test", base_url=mock.rstrip("/") + "/", model="gpt-4o",
                      timeout=10, backoff=[0.05, 0.1])
        check("unit: R1 请求路径恰为 /chat/completions",
              BEHAVIOR["paths"] == ["/chat/completions"],
              f"paths={BEHAVIOR['paths']}")
    finally:
        server.shutdown()


# ================================================================ 节点集成
async def test_node(mock_url):
    autodiscover()
    cls = get_class("core/llm_call")

    # 1) prompt 输入 → text/response/report 资产
    reset_behavior("ok", "节点级统一调用")
    ctx, store = make_ctx("node1")
    prompt_a = store.save_asset(Asset(id="", kind="STRING"), payload={"text": "请回复测试"})
    out = await cls().run(ctx, {"prompt": prompt_a, "messages": None, "system": None}, {
        "provider": "openai", "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "fallback_models": "", "temperature": 0.4, "max_tokens": 4096,
        "json_mode": False, "schema": "", "timeout": 30, "backoff": "[0.05,0.1]",
    })
    text = store.read_json(out["text"].id).get("text", "")
    report = store.read_json(out["report"].id)
    resp = store.read_json(out["response"].id)
    check("节点: prompt→text 资产",
          text == "节点级统一调用" and resp.get("model_used") == "gpt-4o",
          f"text={text!r}")
    check("节点: report 审计字段齐全",
          report["ok"] is True and report["attempts"] == 1
          and report["backoffs"] == [] and report["schema_valid"] is True
          and report["usage"]["prompt_tokens"] == 10,
          json.dumps(report, ensure_ascii=False)[:200])

    # 2) 节点参数管道：500×3 → fallback_models 回退 + schema 校验（JSON mode）
    reset_behavior("500_then_ok", '{"ok": true}', fail_count=3)
    ctx, store = make_ctx("node2")
    prompt_a = store.save_asset(Asset(id="", kind="STRING"), payload={"text": "结构化输出"})
    schema = '{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}'
    out = await cls().run(ctx, {"prompt": prompt_a, "messages": None, "system": None}, {
        "provider": "openai", "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "fallback_models": '["gpt-4o-mini"]', "temperature": 0.4, "max_tokens": 4096,
        "json_mode": True, "schema": schema, "timeout": 30, "backoff": "[0.05,0.1,0.15]",
    })
    report = store.read_json(out["report"].id)
    resp = store.read_json(out["response"].id)
    check("节点: fallback+schema 管道贯通",
          report["model"] == "gpt-4o-mini" and report["attempts"] == 4
          and report["schema_valid"] is True and resp.get("parsed") == {"ok": True}
          and any("回退" in w for w in report["warnings"]),
          f"model={report['model']} attempts={report['attempts']}")

    # 3) 非法 backoff 参数 → 回落默认序列（不报错；成功路径无退避消耗）
    reset_behavior("ok", "backoff参数回落")
    ctx, store = make_ctx("node3")
    prompt_a = store.save_asset(Asset(id="", kind="STRING"), payload={"text": "x"})
    out = await cls().run(ctx, {"prompt": prompt_a, "messages": None, "system": None}, {
        "provider": "openai", "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "fallback_models": "", "temperature": 0.4, "max_tokens": 4096,
        "json_mode": False, "schema": "", "timeout": 30, "backoff": "not-json",
    })
    report = store.read_json(out["report"].id)
    check("节点: 非法 backoff 回落默认序列", report["attempts"] == 1 and report["ok"] is True,
          json.dumps(report.get("backoffs")))


# ================================================================ 既有节点全量冒烟（llm_call 通道）
async def smoke_all(mock_url):
    autodiscover()

    # ---- core/translate（llm_backend=llm_call）
    reset_behavior("ok", "This is the translated narration script.")
    ctx, store = make_ctx("smoke_translate")
    script_a = store.save_asset(Asset(id="", kind="SCRIPT"),
                                payload={"text": "这是一段需要翻译成英文的中文解说文案。"})
    out = await get_class("core/translate")().run(ctx, {"script": script_a}, {
        "target": "英文", "keep_style": True, "api_key": "sk-test",
        "base_url": mock_url, "model": "gpt-4o", "llm_backend": "llm_call"})
    ts = store.read_json(out["translated_script"].id).get("text", "")
    check("冒烟: core/translate llm_call",
          ts == "This is the translated narration script.", f"text={ts!r}")

    # ---- core/ai_narrate（llm_channel=llm_call，扩写模式走非视觉 llm_call 通道）
    reset_behavior("ok", '[{"text":"这是扩写后的完整解说文案。"}]')
    ctx, store = make_ctx("smoke_narrate")
    draft_a = store.save_asset(Asset(id="", kind="SCRIPT"),
                               payload={"text": "示例文案草稿，需要扩写。"})
    out = await get_class("core/ai_narrate")().run(ctx, {"draft": draft_a}, {
        "mode": "扩写", "style": "", "custom_style": "", "perspective": "第三人称",
        "language": "中文", "provider": "openai", "api_key": "sk-test",
        "base_url": mock_url, "model": "gpt-4o", "target_duration": 0, "target_chars": 0,
        "avg_segment_chars": 25, "temperature": 0.7, "max_tokens": 4096,
        "keyframes": 8, "json_mode": True, "local_match": False, "retries": 0,
        "llm_channel": "llm_call"})
    script = store.read_json(out["script"].id).get("text", "")
    rep = store.read_json(out["report"].id)
    check("冒烟: core/ai_narrate llm_channel(llm_call)",
          "扩写后" in script and rep.get("llm_calls", 0) >= 1, f"text={script!r}")

    # ---- user/jsm_script_doctor（llm_backend=llm_call）
    reset_behavior("ok", '[{"id":"hit","title":"爆款化改写","text":"改写后的爆款文案。"},'
                         '{"id":"lean","title":"精简强化改写","text":"精简后的文案。"},'
                         '{"id":"trust","title":"沉稳可信改写","text":"沉稳版本文案。"}]')
    ctx, store = make_ctx("smoke_doctor")
    script_a = store.save_asset(
        Asset(id="", kind="SCRIPT"),
        payload={"text": "这是一个非常精彩的悬疑故事，主角在深夜发现了惊人的秘密，最后结局反转令人意外。"})
    out = await get_class("user/jsm_script_doctor")().run(ctx, {"script": script_a}, {
        "min_length": 20, "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "llm_backend": "llm_call", "rewrite_count": 3, "max_concurrent": 2,
        "retries": 0, "resume": False})
    rewrites = store.read_json(out["rewrites"].id)
    rep = store.read_json(out["report"].id)
    check("冒烟: user/jsm_script_doctor llm_call",
          isinstance(rewrites, list) and len(rewrites) == 3
          and rep.get("engine") == "llm" and not rep.get("llm_error"),
          f"rewrites={len(rewrites) if isinstance(rewrites, list) else '?'} engine={rep.get('engine')}")

    # ---- user/jsm_highlight_plan（llm_backend=llm_call）
    reset_behavior("ok", '{"items":[{"start":0,"end":10,"score":8,"reason":"核心","focus_tier":"core"},'
                         '{"start":11,"end":20,"score":6,"reason":"承接","focus_tier":"support"}],'
                         '"chapters":[],"guides":[]}')
    ctx, store = make_ctx("smoke_plan")
    scenes_a = store.save_asset(Asset(id="", kind="SEGMENTS"),
                                payload=[{"start": 0, "end": 10, "index": 0},
                                         {"start": 11, "end": 20, "index": 1}])
    out = await get_class("user/jsm_highlight_plan")().run(ctx, {"scenes": scenes_a}, {
        "mode": "focus", "target_duration": 30.0, "max_clip": 50.0, "hook_enabled": False,
        "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "llm_backend": "llm_call", "max_concurrent": 2, "retries": 0, "resume": False})
    plan = store.read_json(out["plan"].id)
    check("冒烟: user/jsm_highlight_plan llm_call",
          isinstance(plan.get("items"), list) and len(plan["items"]) == 2
          and plan.get("planner_status") == "ok" and plan.get("source") == "llm",
          f"items={len(plan.get('items') or [])} status={plan.get('planner_status')}")

    # ---- user/jsm_highlight_mixcut（llm_backend=llm_call，scoring=llm）
    reset_behavior("ok", '[{"index":0,"score":8,"reason":"精彩"},{"index":1,"score":6,"reason":"一般"},'
                         '{"index":2,"score":9,"reason":"高潮"}]')
    ctx, store = make_ctx("smoke_mixcut")
    rows_a = store.save_asset(Asset(id="", kind="JSON"),
                              payload=[{"start": 0, "end": 8, "text": "片段一", "role": "解说"},
                                       {"start": 9, "end": 16, "text": "片段二", "role": "解说"},
                                       {"start": 17, "end": 24, "text": "片段三", "role": "解说"}])
    out = await get_class("user/jsm_highlight_mixcut")().run(ctx, {"rows": rows_a}, {
        "target_seconds": 30.0, "min_clip": 3.0, "max_clip": 20.0, "merge_gap": 1.5,
        "hook_max": 5.0, "hook_enabled": False, "scoring": "llm",
        "api_key": "sk-test", "base_url": mock_url, "model": "gpt-4o",
        "llm_backend": "llm_call", "batch_size": 40, "max_concurrent": 2,
        "retries": 0, "resume": False})
    rep = store.read_json(out["report"].id)
    selected = store.read_json(out["selected"].id)
    check("冒烟: user/jsm_highlight_mixcut llm_call",
          rep.get("scoring_mode") == "llm" and isinstance(selected, list) and len(selected) >= 1,
          f"scoring={rep.get('scoring_mode')} selected={len(selected) if isinstance(selected, list) else 0}")

    # ---- user/jsm_translate_batch（llm_backend=llm_call）
    reset_behavior("ok", "[0] Hello.\n[1] World.\n")
    ctx, store = make_ctx("smoke_transbatch")
    texts_a = store.save_asset(Asset(id="", kind="JSON"),
                               payload=["第一句台词内容。", "第二句台词内容。"])
    out = await get_class("user/jsm_translate_batch")().run(ctx, {"texts": texts_a}, {
        "target_language": "英语", "api_key": "sk-test", "base_url": mock_url,
        "model": "gpt-4o-mini", "llm_backend": "llm_call", "keep_style": True,
        "length_tolerance": 0.2, "max_concurrent": 2, "retries": 0, "resume": False})
    trans = store.read_json(out["translations"].id)
    rep = store.read_json(out["report"].id)
    check("冒烟: user/jsm_translate_batch llm_call",
          isinstance(trans, list) and len(trans) == 2
          and all(t.get("status") == "succeeded" and t.get("translated") for t in trans)
          and rep.get("succeeded") == 2,
          "; ".join(str(t.get("translated")) for t in trans) if isinstance(trans, list) else "?")


def test_internal_preserved():
    """既有节点行为不回退：internal 原内嵌通道函数仍存在（模块级函数签名未变）。"""
    import core_ai_narrate.node as narrate
    import core_translate.node as translate
    import user_jsm_script_doctor.node as doctor
    import user_jsm_highlight_plan.node as plan
    import user_jsm_highlight_mixcut.node as mixcut
    import user_jsm_translate_batch.node as tbatch

    check("不回退: narrate 保留 internal 调用函数",
          callable(getattr(narrate, "_chat_openai", None))
          and callable(getattr(narrate, "_chat_gemini", None)))
    check("不回退: translate 保留 app.llm.chat 通道（_chat_llm 的 internal 分支）",
          callable(getattr(translate, "chat", None)))
    check("不回退: script_doctor internal→_llm_json(backend=internal) 分支",
          callable(getattr(doctor, "_llm_json", None)))
    check("不回退: highlight_plan internal 分支",
          callable(getattr(plan, "_llm_json", None)) and callable(getattr(plan, "plan_local", None)))
    check("不回退: highlight_mixcut internal 分支",
          callable(getattr(mixcut, "_llm_json", None)) and callable(getattr(mixcut, "local_score", None)))
    check("不回退: translate_batch internal 分支",
          callable(getattr(tbatch, "_llm_json", None)))


def test_internal_path_functional(mock_url):
    """既有节点行为不回退（功能级）：internal 原内嵌 urllib 通道仍可独立跑通。"""
    from core_llm_call.node import llm_call_json  # noqa: F401  (确认单实现可导入)
    import user_jsm_script_doctor.node as doctor
    reset_behavior("ok", '[{"id":"hit","title":"爆款化改写","text":"内部通道改写文案。"},'
                         '{"id":"lean","title":"精简强化改写","text":"内部精简文案。"},'
                         '{"id":"trust","title":"沉稳可信改写","text":"内部沉稳文案。"}]')
    r = doctor._diagnose(
        "这是一个精彩的悬疑故事，主角深夜发现惊人秘密，结局反转令人意外。",
        "sk-test", mock_url, "gpt-4o", 3, "internal")
    check("不回退(功能): script_doctor internal 通道跑通",
          r["llm_used"] is True and len(r["rewrites"]) == 3 and not r["llm_error"],
          f"engine={r['llm_used']} err={r['llm_error'][:80]!r}")


def test_sdk():
    sdk = r"F:\1223\解说工坊\frameweave-plugins\sdk"
    pkg = os.path.join(PACKAGES, "core_llm_call")
    r = subprocess.run([sys.executable, os.path.join(sdk, "check_manifest.py"), pkg],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("sdk: core/llm_call check_manifest OK", r.returncode == 0, r.stdout[-200:])
    with open(os.path.join(pkg, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    sig = m.get("signature") or {}
    check("sdk: 签名真实（非占位）", bool(sig.get("value")) and "pending" not in str(sig.get("value")),
          sig.get("value", "")[:24])
    r2 = subprocess.run([sys.executable, os.path.join(sdk, "check_all.py")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = r2.stdout.splitlines()
    my_ok = any("OK " in ln and "core_llm_call" in ln and "FAIL" not in ln for ln in lines)
    ext = [ln.strip() for ln in lines if ln.strip().startswith("FAIL")]
    check("sdk: 本包 core/llm_call 在 check_all 中 OK", my_ok,
          " ".join(l for l in lines if "core_llm_call" in l)[:160])
    if ext:
        print("  [info] check_all 外部失败（非本任务范围）: %s" % ext[0])
    # spec/manifest 端口一致性：仅核对本任务 7 个包（core_scene_detect 缺 numpy 为既有环境问题）
    mine = {"core_llm_call", "core_translate", "core_ai_narrate", "user_jsm_script_doctor",
            "user_jsm_highlight_plan", "user_jsm_highlight_mixcut", "user_jsm_translate_batch"}
    r3 = subprocess.run([sys.executable, os.path.join(sdk, "check_port_consistency.py")],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    issues = []
    for ln in r3.stdout.splitlines():
        if "pkg" in ln:
            try:
                issues.append(json.loads(ln.strip()))
            except ValueError:
                pass
    mine_bad = [i for i in issues if i.get("pkg") in mine]
    check("sdk: 7 个包 spec/manifest 端口一致", not mine_bad,
          json.dumps(mine_bad, ensure_ascii=False)[:200] if mine_bad else "全部一致")


def main():
    mock_url, server = start_mock()
    try:
        test_unit()
        asyncio.run(test_node(mock_url))
        asyncio.run(smoke_all(mock_url))
        test_internal_preserved()
        test_internal_path_functional(mock_url)
        test_sdk()
    finally:
        server.shutdown()
    print("\n===== 汇总: PASS=%d FAIL=%d =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  - " + f)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()