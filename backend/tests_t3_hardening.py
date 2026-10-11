# -*- coding: utf-8 -*-
"""P3/t3 授权链硬化自测（本地真实 cloud_server + client cloud_gateway 闭环）。"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # FrameWeave/
BACKEND = os.path.join(REPO, "backend")
sys.path.insert(0, BACKEND)

os.environ["FRAMEWEAVE_VERIFY_SECRET"] = "fw-verify-secret-2026"
os.environ["FRAMEWEAVE_CLOUD_URL"] = "http://127.0.0.1:9789"

from app import cloud_gateway as cg  # noqa: E402

CLOUD_PORT = 9789
DB_PATH = os.path.join(BACKEND, ".tmp_p3_cloud.db")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


def main():
    for p in [DB_PATH, DB_PATH + "-journal"]:
        try:
            os.remove(p)
        except OSError:
            pass
    env = dict(os.environ)
    env["FRAMEWEAVE_CARD_SECRET"] = "test-card-secret"
    env["CLOUD_DB"] = DB_PATH
    env["ALIYUN_DM_ENABLED"] = "false"
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BACKEND, "cloud_server.py"), "--port", str(CLOUD_PORT)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # wait health
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{CLOUD_PORT}/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            check("cloud server 启动", False, "health 超时")
            return 1

        # 1) 云端签名响应（login/verify 闭环）
        #    先 create user via register path - 需要发验证码。直接用 register 需要 code。
        #    简化：直接往 DB 插一个用户（会话 token），走 verify 签名链路。
        import sqlite3

        c = sqlite3.connect(DB_PATH)
        c.execute("INSERT INTO sessions(email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                  ("t@qq.com", "", "s", "u1", "tok123", "member", time.time() + 86400 * 30, "dev-1", 100, "[]", time.time(), time.time()))
        c.commit()
        c.close()

        # 2) 客户端请求 verify（带 nonce，强制验签）
        resp = cg.cloud_verify("tok123", "dev-1")
        check("cloud_verify 成功且 cloud_verified", resp is not None and resp.get("ok") is True and resp.get("cloud_verified") is True,
              f"resp={str(resp)[:160]}")
        check("verify 响应带 req_nonce 回显", resp is not None and bool(resp.get("req_nonce")), f"nonce={resp and resp.get('req_nonce')}")

        # 3) 篡改响应（plan 被 fiddler 改成 member 已存在；改 credits）→ 验签失败 → 拒绝
        tampered = dict(resp)
        tampered["credits"] = 99999
        tampered.pop("signature", None)
        checked = cg._checked(tampered, require_signature=True, nonce=resp.get("req_nonce", ""))
        check("篡改响应 → sig_fail 拒绝", checked is not None and checked.get("sig_fail") is True,
              f"resp={str(checked)[:160]}")

        # 4) 无签名响应 → 拒绝（verify 门禁 fail-closed：先查缺签）
        checked2 = cg._checked({"ok": True, "plan": "member", "req_nonce": "x"}, require_signature=True, nonce="x")
        check("无签名响应 → sig_fail 拒绝", checked2 is not None and checked2.get("sig_fail") is True,
              f"resp={str(checked2)[:120]}")

        # 5) nonce 不匹配（重放旧响应，但带有效签名）→ 拒绝
        good_resp = dict(resp)
        checked3 = cg._checked(good_resp, require_signature=True, nonce="other-nonce")
        check("nonce 不匹配 → sig_fail 拒绝", checked3 is not None and checked3.get("sig_fail") is True,
              f"resp={str(checked3)[:120]}")

        # 6) 过期 token → ok=False（签名仍有效，业务拒绝）
        c = sqlite3.connect(DB_PATH)
        c.execute("UPDATE sessions SET expires_at=? WHERE token='tok123'", (time.time() - 10,))
        c.commit()
        c.close()
        resp_exp = cg.cloud_verify("tok123", "dev-1")
        check("过期 token → 云端业务拒绝（签名有效）", resp_exp is not None and resp_exp.get("ok") is False
              and "过期" in (resp_exp.get("reason") or ""), f"resp={str(resp_exp)[:120]}")

        # 7) 本地桩（FRAMEWEAVE_CLOUD_URL=""）→ degraded 标记
        os.environ["FRAMEWEAVE_CLOUD_URL"] = ""
        from app import license as lic

        svc = lic.LicenseService(os.path.join(BACKEND, ".tmp_p3_data"))
        # 注册一个账号
        r = svc.register_verified("abc@qq.com", "pass1234")
        check("本地桩注册（degraded）", r.get("ok") is True and r.get("degraded") is True, f"resp={str(r)[:120]}")
        # 往数据文件塞 session 再 verify
        sess = r.get("session", {})
        v = svc.verify(sess.get("token"), sess.get("device_id", ""))
        check("本地桩 verify（degraded）", v.get("ok") is True and v.get("degraded") is True, f"resp={str(v)[:120]}")

        # 8) 桩替换检测：monkey-patch 授权辅助函数（_parse_card 恒返回有效 payload → 卡密激活永远通过）
        #    真实攻击路径：替换 _parse_card 让 activate_card 放行任意卡密。原 verify/activate_card
        #    仍会先执行 _tamper_check() → code-object 快照 diff → 拦截。
        orig_parse = lic._parse_card
        lic._parse_card = lambda card: {"plan": "member", "days": 3650, "serial": "hacked"}
        v2 = svc.activate_card(sess.get("email"), "FW-garbage")
        check("桩替换（_parse_card monkey-patch）→ tampered 拒绝", v2.get("ok") is False and v2.get("tampered") is True,
              f"resp={str(v2)[:140]}")
        lic._parse_card = orig_parse
        # 恢复正常后，卡密签名校验仍然生效（伪卡被拒）
        v2b = svc.activate_card(sess.get("email"), "FW-garbage")
        check("恢复 _parse_card → 伪卡签名校验拒绝", v2b.get("ok") is False, f"resp={str(v2b)[:120]}")

        # 9) PROD：DEV_ENDPOINTS 打包关闭由 server.py frozen 分支保证；源码态默认关（tests 单独开）——
        #    这里验证 DEV_ENDPOINTS 在导入 server 时默认 False
        from app import server as srv  # noqa: F401  （dev 关闭由 server import 时 check）

        check("server 导入且 DEV_ENDPOINTS 默认 False", srv.DEV_ENDPOINTS is False, f"DEV={srv.DEV_ENDPOINTS}")

        print("\nRESULT:", "PASS" if not FAIL else f"FAIL({len(FAIL)})", f"pass={len(PASS)} fail={len(FAIL)}")
        return 0 if not FAIL else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        for p in [DB_PATH, DB_PATH + "-journal"]:
            try:
                os.remove(p)
            except OSError:
                pass
        import shutil

        shutil.rmtree(os.path.join(BACKEND, ".tmp_p3_data"), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())