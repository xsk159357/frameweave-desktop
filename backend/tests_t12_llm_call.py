# -*- coding: utf-8 -*-
"""S3-T2 core/llm_call（D6 统一 LLM 调用）验收测试（t18 续做版）。

验收：
  A. 统一实现内：退避 [2,5,10] / fallback_models / JSON mode / schema 校验
  B. 既有节点行为不回退（llm_backend/internal 默认路径冒烟 + llm_call 路径回归）
  C. R1（URL 不再重复拼 /v1）/ R2（402 不判余额）继续被规避

实测（本地 mock OpenAI 兼容服务器）：
  - mock：/chat/completions 可配置 429×N / mode=402/400 / 按 model 路由内容；记录 URL 与请求体；
  - 单元：llm_call_json——429 退避重试、fallback 回退、JSON mode、schema 通过/失败、
    R1 URL 断言（无双 /v1）、R2 402 显式错误（含 HTTP 402、无余额措辞）；
  - 集成：注册表 get_class 跑 core/llm_call 节点 → text/response/report 资产；
    既有 6 节点（core/translate、core/ai_narrate、user/jsm_script_doctor、
    user/jsm_translate_batch、user/jsm_highlight_plan、user/jsm_highlight_mixcut）
    internal 与 llm_call 双路径冒烟；
  - SDK 门禁：7 包 check_manifest OK + 签名真实 + check_all 全量。
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

PACKAGES = r"F:\1223\解说工坊\frameweave-plugins\packages"
if PACKAGES not in sys.path:
    sys.path.insert(0, PACKAGES)
os.environ["FRAMEWEAVE_USER_NODES"] = PACKAGES

from app.assets import Asset, LocalAssetStore  # noqa: E402
from app.registry import autodiscover, get_class  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


TMP = os.path.join(r"F:\1223\解说工坊\_recon\t10_out", "t18_tmp")
os.makedirs(TMP, exist_ok=True)

STATE = {"calls": 0, "urls": [], "bodies": []}


class MockLLMHandler(BaseHTTPRequestHandler):
    """mock OpenAI 兼容端点（429 可编程、402/400 模式、按 model 路由内容、记录 URL/body）。"""

    fail429 = 0      # 前 N 次请求返回 429（全局计数）
    mode = "ok"      # ok | schema_bad_first | 402 | 400

    def log_message(self, *a):
        pass

    def route_content(self, body: dict) -> str:
        model = str((body or {}).get("model", "") or "")
        if model == "m_doctor":
            # 脚本医生：3 套改写 JSON 数组
            return ('[{"id":"hit","title":"爆款化改写",'
                    '"text":"这是爆款化改写的全文内容，钩子开头短句节奏引导互动，足够长用于质检。",'
                    '"diff":"LLM 生成改写"}]')
        if model == "m_translate":
            return "[0] Hello there, global translation."
        if model == "m_plan":
            # 高光规划 focus 合同
            return ('{"items":[{"start":0.0,"end":10.0,"score":7,"reason":"开场精彩",'
                    '"focus_tier":"core"}],'
                    '"guides":[{"story_order":1,"guide_type":"目标","offset_percent":0,'
                    '"text":"精彩开场"}],"summary":"主题概括"}')
        if model == "m_mixcut":
            return '[{"index":0,"score":7,"reason":"精彩高潮"}]'
        if model == "m_narrate":
            return "这是一段解说文案。故事从这里开始，主角走进了夜色。"
        return '{"ok": true, "msg": "你好"}'

    def do_POST(self):
        STATE["calls"] += 1
        STATE["urls"].append(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body_raw = self.rfile.read(length) if length else b""
        STATE["bodies"].append(body_raw.decode("utf-8", "replace")[:300])
        n = STATE["calls"]
        if n <= self.fail429:
            self._json(429, {"error": {"message": "rate limited", "type": "rate_limit_error"}})
            return
        if self.mode == "402":
            self._json(402, {"error": {"message": "insufficient quota"}})
            return
        if self.mode == "400":
            self._json(400, {"error": {"message": "bad request"}})
            return
        try:
            body = json.loads(body_raw or b"{}")
        except ValueError:
            body = {}
        if self.mode == "schema_bad_first" and n <= 1:
            content = '{"ok": "nope"}'
        else:
            content = self.route_content(body)
        self._json(200, {"choices": [{"message": {"content": content}}],
                          "usage": {"prompt_tokens": 12, "completion_tokens": 5,
                                    "total_tokens": 17}})

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLLMHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return "http://127.0.0.1:%d/v1" % server.server_address[1], server


def make_ctx(tag):
    store = LocalAssetStore(os.path.join(TMP, tag))
    return {"store": store, "workdir": os.path.join(TMP, tag),
            "progress": (lambda p: None), "node_id": tag,
            "resolve_secret": (lambda ref: "")}, store


def reset():
    STATE["calls"] = 0
    STATE["urls"] = []
    STATE["bodies"] = []
    MockLLMHandler.fail429 = 0
    MockLLMHandler.mode = "ok"


def all_urls_r1():
    """R1：URL 直拼 /chat/completions，无重复 /v1。"""
    return all(u.endswith("/chat/completions") and "/v1/v1" not in u for u in STATE["urls"])


# ================================================================ 单元（llm_call_json）
def test_unit(mock_url):
    from core_llm_call.node import LLMCallError, llm_call_json

    msgs = [{"role": "user", "content": "hi"}]

    reset()
    res = llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1",
                        backoff=[0.01, 0.01, 0.01])
    check("unit: 正常调用返回文本", res["text"].startswith("{") and res["model_used"] == "m1",
          res["text"][:60])

    # R1: URL 直拼 /chat/completions，无双 /v1
    reset()
    llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1")
    check("unit: R1 URL 直拼 /chat/completions（无重复 /v1）",
          all_urls_r1(), str(STATE["urls"]))

    # 429 重试：退避序列用极小值，断言 attempts 增长
    reset()
    MockLLMHandler.fail429 = 2
    res = llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1",
                        backoff=[0.01, 0.01, 0.01])
    check("unit: 429×2 退避重试成功（attempts=3）",
          res["attempts"] == 3 and res["text"].startswith("{"),
          f"attempts={res['attempts']} backoffs={res['backoffs']}")

    # fallback：主模型 429 耗尽全部退避 → 回退模型成功
    # （fail429=3：主模型 3 次尝试全 429 → 回退模型第 4 次成功）
    reset()
    MockLLMHandler.fail429 = 3
    res = llm_call_json(msgs, api_key="k", base_url=mock_url, model="m_primary",
                        fallback_models=["m_fallback"], backoff=[0.01, 0.01, 0.01])
    check("unit: fallback_models 回退成功（model_used=m_fallback）",
          res["model_used"] == "m_fallback" and any("回退" in w for w in res["warnings"]),
          f"used={res['model_used']} warns={res['warnings']}")

    # JSON mode + schema 通过
    reset()
    res = llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1", json_mode=True,
                        schema={"type": "object", "required": ["ok"],
                                "properties": {"ok": {"type": "boolean"}, "msg": {"type": "string"}}},
                        backoff=[0.01, 0.01, 0.01])
    check("unit: JSON mode + schema 校验通过", res["schema_valid"] is True
          and isinstance(res["parsed"], dict) and res["parsed"].get("ok") is True,
          f"valid={res['schema_valid']} parsed={res['parsed']}")

    # schema 失败 → 重试后成功（mock 第 1 次返回坏 schema）
    reset()
    MockLLMHandler.mode = "schema_bad_first"
    res = llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1",
                        schema={"type": "object", "required": ["ok"],
                                "properties": {"ok": {"type": "boolean"}}},
                        backoff=[0.01, 0.01, 0.01])
    check("unit: schema 校验失败→重试成功（attempts>=2 且最终通过）",
          res["schema_valid"] is True and res["attempts"] >= 2,
          f"attempts={res['attempts']} valid={res['schema_valid']}")

    # R2：402 显式报错（http 错误码 + HTTP 402 字样，不做余额判定）
    reset()
    MockLLMHandler.mode = "402"
    try:
        llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1")
        check("unit: 402 显式报错", False, "未抛错")
    except LLMCallError as e:
        check("unit: R2 402 显式报错含 HTTP 402、无余额措辞",
              e.code == "http" and "402" in str(e) and "余额" not in str(e),
              f"code={e.code} msg={str(e)[:80]}")

    # 400 永久失败不重试（attempts=1）
    reset()
    MockLLMHandler.mode = "400"
    try:
        llm_call_json(msgs, api_key="k", base_url=mock_url, model="m1",
                      backoff=[0.01, 0.01])
        check("unit: 400 永久失败不重试", False, "未抛错")
    except LLMCallError as e:
        check("unit: 400 永久失败不重试（attempts=1）", e.code == "http" and STATE["calls"] == 1,
              f"calls={STATE['calls']}")

    # 统一实现默认退避常量 = [2,5,10]（S1 §9）
    from core_llm_call.node import DEFAULT_BACKOFF
    check("unit: 默认退避序列 [2,5,10]", DEFAULT_BACKOFF == [2.0, 5.0, 10.0],
          str(DEFAULT_BACKOFF))

    # 默认 base_url：不传 base_url 时按 provider 补默认（不拼出非法 /chat/completions 根路径）
    try:
        llm_call_json(msgs, api_key="k", base_url="", model="m1",
                      timeout=5.0, backoff=[0.01])
        check("unit: provider 默认 base_url 填充（不崩）", True, "")
    except LLMCallError as e:
        check("unit: provider 默认 base_url 填充（不崩）",
              e.code in ("network", "http"), f"code={e.code}")

    MockLLMHandler.mode = "ok"


# ================================================================ 集成（节点 + 既有节点接入）
async def test_integration(mock_url):
    autodiscover()

    # ---- core/llm_call 节点经注册表
    reset()
    cls = get_class("core/llm_call")
    ctx, store = make_ctx("run")
    prompt_asset = store.save_asset(
        Asset(id="", kind="STRING"), payload={"text": "hi"})
    out = await cls().run(ctx, {"prompt": prompt_asset, "messages": None, "system": None}, {
        "provider": "auto", "api_key": "k", "base_url": mock_url, "model": "m1",
        "fallback_models": "", "temperature": 0.4, "max_tokens": 512,
        "json_mode": True, "schema": "", "timeout": 30, "backoff": "[0.01,0.01,0.01]",
    })
    text = store.read_json(out["text"].id).get("text", "")
    report = store.read_json(out["report"].id)
    check("集成: llm_call 节点输出 text+report", text.startswith("{") and report["ok"] is True
          and report["schema_valid"] is True and all_urls_r1(),
          f"chars={len(text)}")

    # ---- core/translate 双路径（internal 默认 / llm_call 经注册表）
    from core_translate.node import TranslateNode
    for backend in ("internal", "llm_call"):
        reset()
        tctx, tstore = make_ctx("tr_" + backend)
        script_asset = tstore.save_asset(
            Asset(id="", kind="SCRIPT"), payload={"text": "这是一段用于翻译的解说文案。"})
        o1 = await TranslateNode().run(tctx, {"script": script_asset, "subtitle": None}, {
            "target": "英文", "keep_style": True, "api_key": "k",
            "base_url": mock_url, "model": "m_translate", "llm_backend": backend})
        ts = tstore.read_json(o1["translated_script"].id).get("text", "")
        check(f"既有节点: core/translate {backend} 路径可跑通", len(ts) > 0,
              f"len={len(ts)} url_ok={all_urls_r1()}")

    # ---- core/ai_narrate llm_channel=llm_call（经注册表）扩写模式
    from core_ai_narrate.node import AINarrateNode
    reset()
    actx, astore = make_ctx("narrate")
    draft_asset = astore.save_asset(Asset(id="", kind="SCRIPT"),
                                    payload={"text": "这是一段待扩写的解说草稿。"})
    ao = await AINarrateNode().run(actx, {"draft": draft_asset, "video": None,
                                          "scenes": None, "descriptions": None}, {
        "mode": "扩写", "style": "悬疑解说", "custom_style": "", "perspective": "第三人称",
        "language": "中文", "provider": "auto", "api_key": "k", "base_url": mock_url,
        "model": "m_narrate", "target_duration": 0.0, "target_chars": 0,
        "avg_segment_chars": 25, "temperature": 0.7, "max_tokens": 512,
        "keyframes": 4, "json_mode": False, "local_match": True,
        "retries": 1, "llm_channel": "llm_call"})
    script_payload = astore.read_json(ao["script"].id)
    script_text = script_payload.get("text", "") if isinstance(script_payload, dict) else str(script_payload or "")
    check("既有节点: core/ai_narrate llm_channel=llm_call 路径可跑通",
          len(script_text) > 0 and all_urls_r1(),
          f"chars={len(script_text)} url_ok={all_urls_r1()}")

    # ---- user/jsm_script_doctor 双路径
    from user_jsm_script_doctor.node import ScriptDoctorNode
    for backend in ("internal", "llm_call"):
        reset()
        dctx, dstore = make_ctx("doctor_" + backend)
        script_asset = dstore.save_asset(
            Asset(id="", kind="SCRIPT"),
            payload={"text": "这是一个足够长的解说文案用于质检，故事有起承转合，节奏紧凑。"})
        do = await ScriptDoctorNode().run(dctx, {"script": script_asset, "items": None}, {
            "min_length": 20, "api_key": "k", "base_url": mock_url, "model": "m_doctor",
            "llm_backend": backend, "rewrite_count": 3, "max_concurrent": 2,
            "retries": 1, "resume": True})
        diag = dstore.read_json(do["diagnosis"].id)
        rewrites = dstore.read_json(do["rewrites"].id)
        check(f"既有节点: user/jsm_script_doctor {backend} 路径可跑通",
              diag.get("conclusion_level") is not None and isinstance(rewrites, list)
              and len(rewrites) > 0 and all_urls_r1(),
              f"level={diag.get('conclusion_level')} rewrites={len(rewrites)} url_ok={all_urls_r1()}")

    # ---- user/jsm_translate_batch 双路径
    from user_jsm_translate_batch.node import TranslateBatchNode
    for backend in ("internal", "llm_call"):
        reset()
        tctx, tstore = make_ctx("tbat_" + backend)
        texts_asset = tstore.save_asset(Asset(id="", kind="JSON"), payload=["你好"])
        to = await TranslateBatchNode().run(tctx, {"texts": texts_asset, "subtitles": None,
                                                   "items": None}, {
            "target_language": "英语", "api_key": "k", "base_url": mock_url,
            "model": "m_translate", "llm_backend": backend, "keep_style": True,
            "length_tolerance": 0.2, "max_concurrent": 2, "retries": 2, "resume": True})
        trans = tstore.read_json(to["translations"].id)
        rep = tstore.read_json(to["report"].id)
        ok = isinstance(trans, list) and len(trans) == 1 \
            and str(trans[0].get("translated", "")).strip() and all_urls_r1()
        check(f"既有节点: user/jsm_translate_batch {backend} 路径可跑通",
              ok, f"mode={rep.get('mode')} trans={trans[0] if trans else None} url_ok={all_urls_r1()}")

    # ---- user/jsm_highlight_plan 双路径
    from user_jsm_highlight_plan.node import HighlightPlanNode
    scenes = [
        {"index": 0, "start": 0.0, "end": 10.0, "text": "夜幕降临城市灯光危险逼近"},
        {"index": 1, "start": 10.0, "end": 20.0, "text": "主角街头逃亡追逐场面紧张"},
    ]
    for backend in ("internal", "llm_call"):
        reset()
        pctx, pstore = make_ctx("plan_" + backend)
        scenes_asset = pstore.save_asset(Asset(id="", kind="SEGMENTS"), payload=scenes)
        po = await HighlightPlanNode().run(pctx, {"scenes": scenes_asset, "instruction": None,
                                                  "items": None}, {
            "mode": "focus", "target_duration": 20.0, "max_clip": 10.0,
            "hook_enabled": False, "api_key": "k", "base_url": mock_url,
            "model": "m_plan", "llm_backend": backend, "max_concurrent": 2,
            "retries": 1, "resume": True})
        plan = pstore.read_json(po["plan"].id)
        ok = isinstance(plan.get("items"), list) and len(plan["items"]) > 0 \
            and all_urls_r1()
        check(f"既有节点: user/jsm_highlight_plan {backend} 路径可跑通",
              ok, f"items={len(plan.get('items', []))} status={plan.get('planner_status')} url_ok={all_urls_r1()}")

    # ---- user/jsm_highlight_mixcut 双路径
    from user_jsm_highlight_mixcut.node import HighlightMixcutNode
    rows = [
        {"start": 0.0, "end": 5.0, "role": "旁白", "text": "第一段精彩内容从这里开始"},
        {"start": 6.0, "end": 11.0, "role": "主角", "text": "第二段高潮引爆全场"},
    ]
    for backend in ("internal", "llm_call"):
        reset()
        mctx, mstore = make_ctx("mix_" + backend)
        rows_asset = mstore.save_asset(Asset(id="", kind="JSON"), payload=rows)
        mo = await HighlightMixcutNode().run(mctx, {"rows": rows_asset, "items": None}, {
            "target_seconds": 300.0, "min_clip": 3.0, "max_clip": 20.0,
            "merge_gap": 1.5, "hook_max": 5.0, "hook_enabled": False,
            "scoring": "llm", "api_key": "k", "base_url": mock_url, "model": "m_mixcut",
            "llm_backend": backend, "batch_size": 40, "max_concurrent": 2,
            "retries": 1, "resume": True})
        selected = mstore.read_json(mo["selected"].id)
        rep = mstore.read_json(mo["report"].id)
        ok = isinstance(selected, list) and len(selected) > 0 and all_urls_r1()
        check(f"既有节点: user/jsm_highlight_mixcut {backend} 路径可跑通",
              ok, f"scoring={rep.get('scoring_mode')} selected={len(selected)} url_ok={all_urls_r1()}")

    # 节点 llm_call 路径的 429 落地：translate（经注册表）遇 429×1 后续成功
    MockLLMHandler.fail429 = 1
    STATE["calls"] = 0
    tctx, tstore = make_ctx("tr_429")
    script_asset = tstore.save_asset(Asset(id="", kind="SCRIPT"),
                                     payload={"text": "这是一段用于翻译的解说文案。"})
    o1 = await TranslateNode().run(tctx, {"script": script_asset, "subtitle": None}, {
        "target": "英文", "keep_style": True, "api_key": "k",
        "base_url": mock_url, "model": "m_translate", "llm_backend": "llm_call"})
    ts = tstore.read_json(o1["translated_script"].id).get("text", "")
    check("集成: translate llm_call 路径 429×1 退避后成功（既有节点不回退）",
          len(ts) > 0 and STATE["calls"] >= 2, f"calls={STATE['calls']} len={len(ts)}")
    MockLLMHandler.fail429 = 0


def test_sdk(packages):
    sdk = r"F:\1223\解说工坊\frameweave-plugins\sdk"
    for pkg in packages:
        d = os.path.join(PACKAGES, pkg)
        r = subprocess.run([sys.executable, os.path.join(sdk, "check_manifest.py"), d],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        sig = m.get("signature") or {}
        check(f"sdk: {pkg} check_manifest OK + 签名真实",
              r.returncode == 0 and bool(sig.get("value")) and "pending" not in str(sig.get("value")),
              (r.stdout[-80:] or r.stderr[-80:]).strip())
        # 新参数在 manifest 中声明（declarative 运行时契约）
        pnames = [p.get("name") for p in m.get("params", [])]
        if pkg == "core_ai_narrate":
            check(f"sdk: {pkg} manifest 含 llm_channel 参数",
                  "llm_channel" in pnames, str(pnames))
        elif pkg == "core_llm_call":
            check(f"sdk: {pkg} manifest 版本 1.1.0",
                  str(m.get("version")) == "1.1.0", str(m.get("version")))
        else:
            check(f"sdk: {pkg} manifest 含 llm_backend 参数",
                  "llm_backend" in pnames, str(pnames))
    r = subprocess.run([sys.executable, os.path.join(sdk, "check_all.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("sdk: check_all 全量 OK", r.returncode == 0, r.stdout.splitlines()[-1] if r.stdout else "")


def main():
    mock_url, server = start_mock()
    try:
        test_unit(mock_url)
        asyncio.run(test_integration(mock_url))
        test_sdk(["core_llm_call", "core_ai_narrate", "core_translate",
                  "user_jsm_script_doctor", "user_jsm_translate_batch",
                  "user_jsm_highlight_plan", "user_jsm_highlight_mixcut"])
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