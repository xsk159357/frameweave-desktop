# -*- coding: utf-8 -*-
"""F3 授权链敏感字符串混淆（t3/P3 ⑤）。

把云端 URL / 授权密钥 / 端点路径 / 令牌等敏感常量以 XOR(0x5A) 密文存储，
运行时 _x() 还原：打包产物（exe/PYZ/pyd）与源码快照都不以明文承载这些字面量。
环境变量名同样走 XOR 通道——不把"密钥名/令牌名"作为可搜索明文暴露。

注意：这是纵深防御的一层。真正的强加密由 pyarmor(Runtime 模式) mix-str 完成；
本模块消除"源码明文常量 + 打包前未混淆窗口"，并给启动自检的 HMAC 密钥
（integrity.py）一个非明文内嵌载体。
"""
from __future__ import annotations

_SEED = 0x5A


def _x(s: str) -> str:
    return "".join(chr(ord(c) ^ _SEED) for c in s)


# ---- 常量密文（生成方式见 .agent-teams-read/_p3_gen_consts*.py，不入库）----
_CLOUD_URL_ENC = "2..*)`uu)25/+/;4t<(;7?-?;,?t.5*"                       # https://shouquan.frameweave.top
_CLOUD_URL_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x19\x16\x15\x0f\x1e\x05\x0f\x08\x16"  # FRAMEWEAVE_CLOUD_URL
_VERIFY_SECRET_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x0c\x1f\x08\x13\x1c\x03\x05\t\x1f\x19\x08\x1f\x0e"  # FRAMEWEAVE_VERIFY_SECRET
_VERIFY_SECRET_DEFAULT_ENC = "<-w,?(3<#w)?9(?.whjhl"                   # fw-verify-secret-2026
_DEV_ENDPOINTS_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x1e\x1f\x0c\x05\x1f\x14\x1e\n\x15\x13\x14\x0e\t"  # FRAMEWEAVE_DEV_ENDPOINTS
_CARD_SECRET_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x19\x1b\x08\x1e\x05\t\x1f\x19\x08\x1f\x0e"  # FRAMEWEAVE_CARD_SECRET
_DEV_CARD_SECRET_ENC = "<(;7?-?;,?w>?,w9;(>w)?9(?.whjhl"               # frameweave-dev-card-secret-2026
_LOCAL_TOKEN_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x16\x15\x19\x1b\x16\x05\x0e\x15\x11\x1f\x14"  # FRAMEWEAVE_LOCAL_TOKEN
_DEV_TOKEN_ENC = ">?,w659;6w.51?4"                                     # dev-local-token
_X_FW_TOKEN_ENC = "\x02w\x1c\rw\x1659;6w\x0e51?4"                       # X-FW-Local-Token
_VERIFY_PATH_ENC = "u;*3u;/.2u,?(3<#"                                  # /api/auth/verify
_SIGNATURE_FIELD_ENC = ",?(3<#\x05)3="                                 # signature
_EMAIL_VERIFY_DEFAULT_ENC = "<(;7?-?;,?w>?,w,?(3<#"                    # frameweave-dev-verify
_INTEGRITY_KEY_ENC = "<(;7?-?;,?w34.?=(3.#w1?#w,k"                     # frameweave-integrity-key-v1
_INTEGRITY_KEY_ENV_ENC = "\x1c\x08\x1b\x17\x1f\r\x1f\x1b\x0c\x1f\x05\x13\x14\x0e\x1f\x1d\x08\x13\x0e\x03\x05\x11\x1f\x03"  # FRAMEWEAVE_INTEGRITY_KEY
_MEMBER_PLAN_ENC = "7?78?("                                           # member
_TRIAL_PLAN_ENC = ".(3;6"                                             # trial


def cloud_url_default() -> str:
    return _x(_CLOUD_URL_ENC)


def cloud_url_env() -> str:
    return _x(_CLOUD_URL_ENV_ENC)


def verify_secret_env() -> str:
    return _x(_VERIFY_SECRET_ENV_ENC)


def verify_secret_default() -> str:
    return _x(_VERIFY_SECRET_DEFAULT_ENC)


def dev_endpoints_env() -> str:
    return _x(_DEV_ENDPOINTS_ENV_ENC)


def card_secret_env() -> str:
    return _x(_CARD_SECRET_ENV_ENC)


def dev_card_secret() -> str:
    return _x(_DEV_CARD_SECRET_ENC)


def local_token_env() -> str:
    return _x(_LOCAL_TOKEN_ENV_ENC)


def dev_token() -> str:
    return _x(_DEV_TOKEN_ENC)


def x_fw_token_header() -> str:
    return _x(_X_FW_TOKEN_ENC)


def verify_path() -> str:
    return _x(_VERIFY_PATH_ENC)


def signature_field() -> str:
    return _x(_SIGNATURE_FIELD_ENC)


def email_verify_default() -> str:
    return _x(_EMAIL_VERIFY_DEFAULT_ENC)


def integrity_key_default() -> str:
    return _x(_INTEGRITY_KEY_ENC)


def integrity_key_env() -> str:
    return _x(_INTEGRITY_KEY_ENV_ENC)


def member_plan() -> str:
    return _x(_MEMBER_PLAN_ENC)


def trial_plan() -> str:
    return _x(_TRIAL_PLAN_ENC)