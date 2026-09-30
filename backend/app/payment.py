"""充值/支付对接位（M19）。

正式版：支付宝开放平台 app 支付/扫码 —— 需要商户资质（营业执照 + 应用签名 + 回调域名）。
汇率、手续费由云端管理后台配置（M19 云端部分）。
本地桩：/api/dev/add-credits 按 1 元 = 10 积分 直接加积分，用于联调。
"""
from __future__ import annotations

# 对接配置（正式上线前填写；本地桩用 None）
ALIPAY_APP_ID = None          # 支付宝开放平台 app_id
ALIPAY_PRIVATE_KEY = None     # 应用私钥（绝不进前端/仓库）
ALIPAY_PUBLIC_KEY = None      # 支付宝公钥（验签）
ALIPAY_NOTIFY_URL = None      # 异步回调地址（HTTPS）
RATE_YUAN_TO_CREDIT = 10      # 1 元 = 10 积分

# 商户入账（平台分成）占位：正式版按订单流水在服务端结算，本地桩直接改账号积分。
