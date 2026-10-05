"""QQ 邮箱验证码与阿里云 DirectMail 服务。"""
from __future__ import annotations
import hashlib, os, random, re, threading, time

_CODES: dict[tuple[str, str], dict] = {}
_SEND_LOG: dict[str, list[float]] = {}
_LOCK = threading.Lock()
_EMAIL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@qq\.com$", re.I)

def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

def normalize_email(email: str) -> str: return (email or "").strip().lower()
def valid_qq_email(email: str) -> bool: return bool(_EMAIL_RE.fullmatch(normalize_email(email)))
def _hash(code: str) -> str: return hashlib.sha256((_cfg("FRAMEWEAVE_VERIFY_SECRET", "frameweave-dev-verify") + code).encode()).hexdigest()

def issue_code(email: str, scene: str) -> tuple[bool, str]:
    email = normalize_email(email); now = time.time()
    if scene not in ("register", "reset_password"): return False, "验证码场景不正确"
    with _LOCK:
        old = _CODES.get((email, scene))
        if old and now - old["sent_at"] < 60: return False, "验证码发送过于频繁，请稍后再试"
        history = [x for x in _SEND_LOG.get(email, []) if now - x < 86400]
        if len(history) >= 10: return False, "今日验证码发送次数已达上限"
        _SEND_LOG[email] = history
        code = f"{random.SystemRandom().randrange(100000, 1000000):06d}"
    if _cfg("ALIYUN_DM_ENABLED", "false").lower() != "true":
        with _LOCK: _CODES[(email, scene)] = {"hash": _hash(code), "expires": now + 300, "sent_at": now, "attempts": 0}; _SEND_LOG[email].append(now)
        print(f"[dev-mail] email={email} scene={scene} code={code}")
        return True, "验证码已生成（开发模式，请查看后端日志）"
    try:
        from alibabacloud_dm20151123.client import Client
        from alibabacloud_dm20151123.models import SingleSendMailRequest
        from alibabacloud_tea_openapi.models import Config
        client = Client(Config(access_key_id=_cfg("ALIYUN_DM_ACCESS_KEY_ID"), access_key_secret=_cfg("ALIYUN_DM_ACCESS_KEY_SECRET"), region_id=_cfg("ALIYUN_DM_REGION_ID", "cn-hangzhou"), endpoint=_cfg("ALIYUN_DM_ENDPOINT", "dm.aliyuncs.com")))
        subject = "【拾帧 FrameWeave】" + ("注册验证码" if scene == "register" else "密码重置验证码")
        body = f"你好：<br><br>你的验证码是：<b>{code}</b><br><br>验证码 5 分钟内有效。如非本人操作，请忽略此邮件。<br><br>拾帧 FrameWeave"
        req = SingleSendMailRequest(account_name=_cfg("ALIYUN_DM_ACCOUNT_NAME"), address_type=1, to_address=email, subject=subject, html_body=body, reply_to_address=False)
        client.single_send_mail(req)
        with _LOCK: _CODES[(email, scene)] = {"hash": _hash(code), "expires": now + 300, "sent_at": now, "attempts": 0}; _SEND_LOG[email].append(now)
        return True, "验证码已发送到 QQ 邮箱"
    except Exception as exc:
        print(f"[mail-error] scene={scene} type={type(exc).__name__}")
        return False, "邮件发送失败，请稍后重试"

def verify_code(email: str, scene: str, code: str) -> tuple[bool, str]:
    key = (normalize_email(email), scene)
    with _LOCK:
        item = _CODES.get(key)
        if not item or time.time() > item["expires"]: _CODES.pop(key, None); return False, "验证码已过期，请重新获取"
        item["attempts"] += 1
        if item["attempts"] > 5: _CODES.pop(key, None); return False, "验证码错误次数过多，请重新获取"
        if not code or _hash(code.strip()) != item["hash"]: return False, "验证码不正确"
        _CODES.pop(key, None)
        return True, ""
