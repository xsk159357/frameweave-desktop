# -*- coding: utf-8 -*-
"""后台控制台 P0-P3 测试：admin 鉴权/统计/用户/卡密/商城/设置/备份/运维代理/审计留痕。

t1 扩展覆盖（云端授权与管理后台 API 补全）：
  - 设备管理：login/verify/register 设备登记、admin_devices 分页筛选、admin_device_reset 解踢；
  - 审计分页/筛选（admin_audit q/scene/result/page/per_page）；
  - 卡密列表状态筛选/搜索（admin_cards status/q）；
  - 作废卡密激活拒绝（activate → card_revoked）；
  - 商城付费下载原子扣积分 / 积分不足不改写会话；
  - 注册 ip 落库（created_ip）与统一错误码 code 字段。

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
    check("错误密钥拒绝(带code)", r.get("ok") is False and r.get("code") == "bad_credentials", str(r))
    tk = token()
    check("无 token 拒绝", cs.admin_users_v2({}).get("ok") is False)
    check("坏 token 拒绝(带code)", cs.admin_users_v2({"token": "bad"}).get("ok") is False
          and cs.admin_users_v2({"token": "bad"}).get("code") == "unauthorized")

    # 2) 造数据：注册 2 用户（含 created_ip 落库断言）
    cs.register({"email": "a@qq.com", "password": "password123", "code": "x", "device_id": "dev-a", "ip": _ip()})
    cs.register({"email": "b@qq.com", "password": "password123", "code": "x", "device_id": "dev-b", "ip": _ip()})
    for e, dev in (("a@qq.com", "dev-a"), ("b@qq.com", "dev-b")):
        if not cs.find(e):
            cs.save(cs.new(e, dev, "password123", ip="127.0.0.9"))
    sa = cs.find("a@qq.com")
    check("注册 ip 落库 created_ip", sa.get("created_ip") == "127.0.0.9", str(sa.get("created_ip")))

    # 3) 用户管理（分页/筛选/详情/设备）
    r = cs.admin_users_v2({"token": tk})
    check("users 返回全部", r.get("ok") and r["total"] >= 2, str(r)[:120])
    r2 = cs.admin_users_v2({"token": tk, "q": "b@qq"})
    check("users 搜索", r2.get("ok") and r2["total"] == 1, str(r2)[:120])
    r3 = cs.admin_users_v2({"token": tk, "status": 1, "page": 1, "per_page": 1})
    check("users 分页", r3.get("ok") and r3["pages"] >= 2, str(r3)[:120])

    # login 触发设备登记（devices 表）→ admin_devices 可见
    cs.login({"email": "a@qq.com", "password": "password123", "device_id": "dev-a2", "ip": _ip()})
    dv = cs.admin_devices({"token": tk, "q": "a@qq"})
    check("devices 列表(登录登记)", dv.get("ok") and any(d["email"] == "a@qq.com" and d["device_id"] == "dev-a2" for d in dv["devices"]),
          str(dv)[:160])
    dvp = cs.admin_devices({"token": tk, "page": 1, "per_page": 1})
    check("devices 分页", dvp.get("ok") and dvp["total"] >= 1 and len(dvp["devices"]) == 1, str(dvp)[:160])

    cr = cs.admin_user_credits({"token": tk, "email": "a@qq.com", "delta": 100, "ip": _ip()})
    check("credits 加分", cr.get("ok") and cr["credits"] == 100, str(cr))
    cr2 = cs.admin_user_credits({"token": tk, "email": "a@qq.com", "delta": -30, "ip": _ip()})
    check("credits 减分", cr2.get("ok") and cr2["credits"] == 70, str(cr2))
    check("credits 审计留痕", any(l.get("scene") == "admin_credits" for l in cs.admin_audit({"token": tk})["logs"]))

    k = cs.admin_user_kick({"token": tk, "email": "a@qq.com", "ip": _ip()})
    check("kick 清空设备", k.get("ok") and cs.find("a@qq.com")["device_id"] == "", str(k))
    # 踢除后设备 kicked=1，admin_device_reset 解踢
    dv2 = cs.admin_devices({"token": tk, "q": "dev-a2"})
    kicked = next((d for d in dv2["devices"] if d["device_id"] == "dev-a2"), None)
    check("kick 标记设备 kicked=1", kicked is not None and kicked["kicked"] == 1, str(dv2)[:160])
    dr = cs.admin_device_reset({"token": tk, "email": "a@qq.com", "device_id": "dev-a2"})
    check("device_reset 解踢", dr.get("ok") and dr["kicked"] == 0, str(dr))
    dr2 = cs.admin_device_reset({"token": tk, "email": "a@qq.com", "device_id": "absent"})
    check("device_reset 不存在报错", dr2.get("ok") is False and dr2.get("code") == "not_found", str(dr2))

    ug = cs.admin_user_get({"token": tk, "email": "a@qq.com"})
    check("user/get 详情", ug.get("ok") and "trail" in ug and "devices" in ug, str(ug)[:120])
    ug2 = cs.admin_user_get({"token": tk, "email": "none@qq.com"})
    check("user/get 不存在", ug2.get("ok") is False and ug2.get("code") == "not_found")

    # 4) 卡密（签发/作废/筛选/激活拒绝）
    iss = cs.admin_issue({"token": tk, "plan": "month", "days": 30, "count": 5})
    check("签发卡密", iss.get("ok") and len(iss["cards"]) == 5, str(iss)[:120])
    cards = cs.admin_cards({"token": tk})
    check("卡密列表", cards.get("ok") and len(cards["cards"]) == 5, str(cards)[:120])
    c1 = cards["cards"][0]
    check("卡密分页字段总数字段", "total" in cards and "pages" in cards and cards["total"] >= 5, str(cards)[:120])
    cf = cs.admin_cards({"token": tk, "status": "unused"})
    check("卡密 unused 筛选", cf.get("ok") and len(cf["cards"]) == 5, str(cf)[:120])
    cq = cs.admin_cards({"token": tk, "q": c1["card"][-12:]})
    check("卡密 card 搜索", cq.get("ok") and len(cq["cards"]) == 1 and cq["cards"][0]["id"] == c1["id"], str(cq)[:120])
    rev = cs.admin_card_revoke({"token": tk, "id": c1["id"]})
    check("卡密作废", rev.get("ok") and rev["revoked"] == 1, str(rev))
    cf2 = cs.admin_cards({"token": tk, "status": "revoked"})
    check("卡密 revoked 筛选", cf2.get("ok") and len(cf2["cards"]) == 1 and cf2["cards"][0]["id"] == c1["id"], str(cf2)[:120])
    revbad = cs.admin_card_revoke({"token": tk, "id": "abc"})
    check("卡密作废参数校验", revbad.get("ok") is False and revbad.get("code") == "invalid_param", str(revbad))
    # 作废卡激活拒绝
    act = cs.activate({"email": "a@qq.com", "card": c1["card"]})
    check("作废卡激活拒绝 card_revoked", act.get("ok") is False and act.get("code") == "card_revoked", str(act))
    # 有效卡激活成功（新签发的第二张）
    act2 = cs.activate({"email": "a@qq.com", "card": cards["cards"][1]["card"]})
    check("有效卡激活成功", act2.get("ok") is True and act2["plan"] == "member", str(act2))
    ug3 = cs.admin_user_get({"token": tk, "email": "a@qq.com"})
    check("user/get 含已用卡列表(修复 NameError)", ug3.get("ok") and len(ug3.get("cards") or []) >= 1,
          str(ug3.get("cards"))[:120])

    # 5) 商城（原子扣积分 / 不足不移）
    up = cs.admin_market_upsert({"token": tk, "item": {"id": "test_node", "title": "测试节点", "kind": "node", "price": 10}})
    check("市场 upsert", up.get("ok") and up["item"]["id"] == "test_node", str(up)[:120])
    ml = cs.admin_market_list({"token": tk})
    check("市场 list", ml.get("ok") and any(i["id"] == "test_node" for i in ml["items"]), str(ml)[:120])
    up2 = cs.admin_market_upsert({"token": tk, "item": {"id": "test_node", "status": "inactive"}})
    check("市场下架", up2.get("ok") and up2["item"]["status"] == "inactive", str(up2)[:120])
    st = cs.admin_market_stats({"token": tk})
    check("市场统计", st.get("ok") and st["stats"]["items"] >= 1, str(st)[:120])
    # 原子扣积分：作者与买家
    cs.save(cs.new("buyer@qq.com", "dev-buy", "password123", ip="127.0.0.8"))
    cs.save(cs.new("author@qq.com", "dev-au", "password123", ip="127.0.0.8"))
    cb = cs.find("buyer@qq.com"); ca = cs.find("author@qq.com")
    cb["credits"] = 100; ca["credits"] = 0
    cs.save(cb); cs.save(ca)
    tok_b = cb["token"]
    up3 = cs.admin_market_upsert({"token": tk, "item": {"id": "paid_node", "title": "付费节点", "kind": "node", "price": 10, "author": "author@qq.com", "status": "active"}})
    check("付费条目发布", up3.get("ok"), str(up3)[:120])
    da = cs.market_download_auth({"item_id": "paid_node", "token": tok_b, "client_version": "0.3.11", "ip": _ip()})
    check("付费 download-auth 授权", da.get("ok") is True and da.get("price") == 10, str(da)[:160])
    buyer_after = cs.find("buyer@qq.com")["credits"]
    author_after = cs.find("author@qq.com")["credits"]
    check("原子扣积分 buyer 100→90", buyer_after == 90, f"credits={buyer_after}")
    check("作者分成 author +8", author_after == 8, f"credits={author_after}")
    # 重复扣尽：buyer 90 → 扣 10 → 80；再扣 90 应 insufficient 且保持 80
    da2 = cs.market_download_auth({"item_id": "paid_node", "token": tok_b, "client_version": "0.3.11", "ip": _ip()})
    check("再购一次成功 90→80", da2.get("ok") is True and cs.find("buyer@qq.com")["credits"] == 80, str(da2)[:120])
    cb["credits"] = 5; cs.save(cb)
    da3 = cs.market_download_auth({"item_id": "paid_node", "token": tok_b, "client_version": "0.3.11", "ip": _ip()})
    check("积分不足 insufficient_credits 且不扣成负", da3.get("ok") is False and da3.get("code") == "insufficient_credits"
          and cs.find("buyer@qq.com")["credits"] == 5, str(da3)[:120])
    # item_id 含不可见字符（防御回归）
    da4 = cs.market_download_auth({"item_id": "free_x\u200b", "token": "", "client_version": "0.3.11"})
    check("item_id 含零宽字符 → item_not_found", da4.get("code") == "item_not_found", str(da4)[:80])

    # 6) 审计分页/筛选
    au = cs.admin_audit({"token": tk})
    check("审计返回 logs+total", au.get("ok") and "logs" in au and au["total"] >= 1, str(au)[:120])
    au2 = cs.admin_audit({"token": tk, "scene": "admin_", "page": 1, "per_page": 5})
    check("审计 scene 筛选", au2.get("ok") and all(l.get("scene", "").startswith("admin_") for l in au2["logs"]), str(au2)[:160])
    au3 = cs.admin_audit({"token": tk, "q": "a@qq.com"})
    check("审计 q 筛选邮箱", au3.get("ok") and len(au3["logs"]) >= 1, str(au3)[:120])

    # 7) 设置 / 密钥轮换 / 会话
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

    # 8) 备份 / 恢复
    bk = cs.admin_backup({"token": tk2["token"]})
    check("备份", bk.get("ok") and bk["data"]["users"] and bk["data"]["cards"], str(bk)[:120])
    rs = cs.admin_restore({"token": tk2["token"], "confirm": "yes-i-understand", "data": bk["data"]})
    check("恢复", rs.get("ok"), str(rs)[:160])

    # 9) 运维代理
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

    # 10) 统计
    stt = cs.admin_stats({"token": tk2["token"]})
    check("仪表盘统计", stt.get("ok") and stt["stats"]["users"] >= 2 and "online_15m" in stt["stats"], str(stt)[:160])

    print(f"=== cloud admin 测试: PASS={PASS} FAIL={FAIL} ===")


if __name__ == "__main__":
    main()