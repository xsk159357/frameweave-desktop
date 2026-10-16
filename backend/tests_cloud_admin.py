# -*- coding: utf-8 -*-
"""后台控制台 P0-P3 测试：admin 鉴权/统计/用户/卡密/商城/设置/备份/运维代理/审计留痕。

运行：设置临时 CLOUD_DB + FRAMEWEAVE_ADMIN_KEY 后 import cloud_server 直接测函数。
"""
import os
import tempfile

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".t_cloud_admin")
os.makedirs(_TMP, exist_ok=True)
os.environ["CLOUD_DB"] = os.path.join(_TMP, "cloud_admin_test.db")
os.environ["FRAMEWEAVE_ADMIN_KEY"] = "test-admin-key-123"
os.environ["FRAMEWEAVE_CARD_SECRET"] = "test-card-secret"
os.environ["FRAMEWEAVE_VERIFY_SECRET"] = "test-verify-secret"

import cloud_server as cs  # noqa: E402

_seq = [0]
def _ip():
    _seq[0] += 1
    return f"127.0.{_seq[0]}.1"

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name} | {detail}")

def token():
    r = cs.admin_login({"admin_key": "test-admin-key-123", "ip": _ip()})
    check("admin_login 成功", r.get("ok") is True, str(r))
    return r["token"]

def main():
    if os.path.exists(os.environ["CLOUD_DB"]):
        os.remove(os.environ["CLOUD_DB"])
    cs.db().close()
    # 1) 鉴权
    r = cs.admin_login({"admin_key": "wrong", "ip": _ip()})
    check("错误密钥拒绝", r.get("ok") is False)
    tk = token()
    check("无 token 拒绝", cs.admin_users_v2({}).get("ok") is False)
    check("坏 token 拒绝", cs.admin_users_v2({"token": "bad"}).get("ok") is False)

    # 2) 造数据：注册 2 用户
    cs.register({"email": "a@qq.com", "password": "password123", "code": "x", "device_id": "dev-a", "ip": _ip()})
    cs.register({"email": "b@qq.com", "password": "password123", "code": "x", "device_id": "dev-b", "ip": _ip()})
    # 手动放行注册（绕过验证码：直接 new+save 更快更稳）
    for e, dev in (("a@qq.com", "dev-a"), ("b@qq.com", "dev-b")):
        if not cs.find(e):
            cs.save(cs.new(e, dev, "password123"))

    # 3) 用户管理
    r = cs.admin_users_v2({"token": tk})
    check("users 返回全部", r.get("ok") and r["total"] >= 2, str(r)[:120])
    r2 = cs.admin_users_v2({"token": tk, "q": "b@qq"})
    check("users 搜索", r2.get("ok") and r2["total"] == 1, str(r2)[:120])
    r3 = cs.admin_users_v2({"token": tk, "status": 1, "page": 1, "per_page": 1})
    check("users 分页", r3.get("ok") and r3["pages"] >= 2, str(r3)[:120])

    cr = cs.admin_user_credits({"token": tk, "email": "a@qq.com", "delta": 100, "ip": _ip()})
    check("credits 加分", cr.get("ok") and cr["credits"] == 100, str(cr))
    cr2 = cs.admin_user_credits({"token": tk, "email": "a@qq.com", "delta": -30, "ip": _ip()})
    check("credits 减分", cr2.get("ok") and cr2["credits"] == 70, str(cr2))
    check("credits 审计留痕", any(l.get("scene") == "admin_credits" for l in cs.admin_audit({"token": tk})["logs"]))

    k = cs.admin_user_kick({"token": tk, "email": "a@qq.com", "ip": _ip()})
    check("kick 清空设备", k.get("ok") and cs.find("a@qq.com")["device_id"] == "", str(k))
    ug = cs.admin_user_get({"token": tk, "email": "a@qq.com"})
    check("user/get 详情", ug.get("ok") and "trail" in ug and "devices" in ug, str(ug)[:120])
    ug2 = cs.admin_user_get({"token": tk, "email": "none@qq.com"})
    check("user/get 不存在", ug2.get("ok") is False)

    # 4) 卡密
    iss = cs.admin_issue({"token": tk, "plan": "month", "days": 30, "count": 3})
    check("签发卡密", iss.get("ok") and len(iss["cards"]) == 3, str(iss)[:120])
    cards = cs.admin_cards({"token": tk})
    check("卡密列表", cards.get("ok") and len(cards["cards"]) == 3, str(cards)[:120])
    rev = cs.admin_card_revoke({"token": tk, "id": cards["cards"][0]["id"]})
    check("卡密作废", rev.get("ok") and cs.admin_cards({"token": tk})["cards"][0]["revoked"] == 1, str(rev))

    # 5) 商城
    up = cs.admin_market_upsert({"token": tk, "item": {"id": "test_node", "title": "测试节点", "kind": "node", "price": 10}})
    check("市场 upsert", up.get("ok") and up["item"]["id"] == "test_node", str(up)[:120])
    ml = cs.admin_market_list({"token": tk})
    check("市场 list", ml.get("ok") and any(i["id"] == "test_node" for i in ml["items"]), str(ml)[:120])
    up2 = cs.admin_market_upsert({"token": tk, "item": {"id": "test_node", "status": "inactive"}})
    check("市场下架", up2.get("ok") and up2["item"]["status"] == "inactive", str(up2)[:120])
    st = cs.admin_market_stats({"token": tk})
    check("市场统计", st.get("ok") and st["stats"]["items"] >= 1, str(st)[:120])

    # 6) 设置 / 密钥轮换 / 会话
    sget = cs.admin_settings_get({"token": tk})
    check("设置读取", sget.get("ok") and "admin_key_set" in sget["settings"], str(sget)[:120])
    sput = cs.admin_settings_put({"token": tk, "settings": {"plans": '{"month":30,"quarter":90,"year":365}', "ops_base": "http://127.0.0.1:8788"}})
    check("设置写入", sput.get("ok") and sput["settings"]["ops_base"] == "http://127.0.0.1:8788", str(sput)[:120])
    rot = cs.admin_key_rotate({"token": tk, "new_key": "new-admin-key-456", "ip": _ip()})
    check("密钥轮换", rot.get("ok"), str(rot))
    check("旧密钥登录失败", cs.admin_login({"admin_key": "test-admin-key-123", "ip": _ip()}).get("ok") is False)
    tk2 = cs.admin_login({"admin_key": "new-admin-key-456", "ip": _ip()})
    check("新密钥登录成功", tk2.get("ok") is True, str(tk2)[:120])
    tks = cs.admin_tokens_list({"token": tk2["token"]})
    check("会话列表", tks.get("ok") and len(tks["tokens"]) >= 1, str(tks)[:120])
    revt = cs.admin_token_revoke({"token": tk2["token"], "token": tks["tokens"][0]["token"], "auth_token": tk2["token"]})
    check("吊销自己会被告警", revt.get("ok") is False, str(revt)[:120])

    # 7) 备份 / 恢复
    bk = cs.admin_backup({"token": tk2["token"]})
    check("备份", bk.get("ok") and bk["data"]["users"] and bk["data"]["cards"], str(bk)[:120])
    rs = cs.admin_restore({"token": tk2["token"], "confirm": "yes-i-understand", "data": bk["data"]})
    check("恢复", rs.get("ok"), str(rs)[:160])

    # 8) 运维代理
    cs.set_setting("ops_base", "")
    cs.set_setting("ops_token", "")
    ops = cs.admin_ops_proxy({"token": tk2["token"], "path": "/api/health"})
    check("ops 未配置 base 报错", ops.get("ok") is False and "未配置本地服务地址" in ops.get("message", ""), str(ops)[:120])
    cs.set_setting("ops_base", "http://127.0.0.1:1")  # 不可达端口
    cs.set_setting("ops_token", "dev-local-token")
    ops2 = cs.admin_ops_proxy({"token": tk2["token"], "path": "/api/health"})
    check("ops 不可达显式错误", ops2.get("ok") is False, str(ops2)[:120])
    cs.set_setting("ops_base", "http://127.0.0.1:8788")  # 本地后端
    ops3 = cs.admin_ops_proxy({"token": tk2["token"], "path": "/api/health"})
    check("ops 本地健康代理（可达则 ok）", ops3.get("ok") is True or (not ops3.get("ok") and "HTTP" in str(ops3.get("message", ""))), str(ops3)[:160])

    # 9) 统计
    stt = cs.admin_stats({"token": tk2["token"]})
    check("仪表盘统计", stt.get("ok") and stt["stats"]["users"] >= 2 and "online_15m" in stt["stats"], str(stt)[:160])

    print(f"=== cloud admin 测试: PASS={PASS} FAIL={FAIL} ===")


if __name__ == "__main__":
    main()
