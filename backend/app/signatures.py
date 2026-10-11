"""插件签名链（P3 第一阶段）：Ed25519 签名 / 验签 / JCS 规范化 / 信任锚 / 撤销。

模型（对齐 G10 占位与商城分发现状）：
- manifest_sig：对剥离 signature 后的 manifest 做 JCS 风格规范化
  （键升序 + 递归；值/数组保序；\u0000 转义；键转义 \u0000-\u001F）——
  RFC 8785 实用子集，签名双方共用同一实现即幂等。
  结果 base64（标准）存 manifest["signature"]["value"]；signature.over == "manifest"。
- archive_sig：对插件 zip 整体字节做 sha256 摘要再签，over == "archive"；
  签名单独存 .sig 文件（部署侧）或 manifest 内。
- 密钥对：Ed25519（cryptography）。私钥仅打包/发布机持有，绝不入库/入日志；
  公钥可入库。L0 官方公钥编译锚点 = backend/app/fw_official_pubkey.json（常量锚）。

信任降级（declarative.install_zip / plugin_registry 接入）：
  L0 core/* 官方：锚公钥强制验签，缺失/不匹配/撤销 → 拒绝（ERR_SIGNATURE_VERIFY）。
  L1 市场：条目绑定 publisher_pubkey 时验签（manifest 或 archive），失败 → 拒绝。
  L2 社区：未绑定/未签名 → 仅警告（校验器已给 G10 警告）。

依赖：仅 cryptography（backend/.venv 已装）与标准库。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ---------------------------------------------------------------- 常量
SIG_ALG = "ed25519"
SIG_OVER_MANIFEST = "manifest"
SIG_OVER_ARCHIVE = "archive"
SIG_OVER_VALUES = {SIG_OVER_MANIFEST, SIG_OVER_ARCHIVE}

# L0 官方锚：type_id 前缀 → 强制验签。常量编译进客户端。
OFFICIAL_PREFIX = "core"
OFFICIAL_PREFIXES = {OFFICIAL_PREFIX}

REVOCATIONS_FILENAME = "revocations.json"

# 私钥元数据落在仓库外运行数据目录，默认 backend/data（gitignore）。
_KEY_DIR_ENV = "FRAMEWEAVE_SIGN_KEY_DIR"
_DEFAULT_KEY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sign-keys")
_KEY_FILENAME = "signing.key"
_PUB_FILENAME = "signing.pub.pem"

_SIG_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
# Ed25519 原始公钥 32B → base64 44 字符
_PUBKEY_B64_RE = re.compile(r"^[A-Za-z0-9+/=]{44}$")

_REVOCATIONS_PATH_OVERRIDE: Optional[str] = None


# ---------------------------------------------------------------- 编码/密钥
def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def generate_keypair() -> tuple:
    """生成 Ed25519 密钥对（私钥仅本机内存，调用方负责落盘到仓库外目录）。"""
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def pubkey_bytes(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def pubkey_b64(pub: Ed25519PublicKey) -> str:
    """公钥 base64（key_id 载体；44 字符）。"""
    return _b64e(pubkey_bytes(pub))


def pubkey_b64_to_bytes(b64: str) -> bytes:
    if not isinstance(b64, str) or not _PUBKEY_B64_RE.match(b64):
        raise ValueError("公钥 base64 非法（须 44 字符原始 32B 编码）")
    raw = _b64d(b64)
    if len(raw) != 32:
        raise ValueError(f"Ed25519 公钥必须 32 字节，收到 {len(raw)}")
    return raw


def _pubkey_from_b64(b64: str) -> Ed25519PublicKey:
    """原始 32 字节 base64（或 PEM 兜底）→ Ed25519PublicKey。失败抛 ValueError。"""
    raw = pubkey_b64_to_bytes(b64)
    return Ed25519PublicKey.from_public_bytes(raw)


def pubkey_from_b64(b64: str) -> Ed25519PublicKey:
    """公开入口：原始 32 字节 base64 → Ed25519PublicKey（工具/外部调用用）。"""
    return _pubkey_from_b64(b64)


def key_id(pub: Optional[Ed25519PublicKey] = None,
           pubkey_b64_str: Optional[str] = None) -> str:
    """key_id = 公钥 sha256 前 16 字节 hex（识别发布者，非安全锚——验证仍用公钥本身）。"""
    if pub is not None:
        return hashlib.sha256(pubkey_bytes(pub)).hexdigest()[:16]
    return hashlib.sha256(pubkey_b64_to_bytes(pubkey_b64_str)).hexdigest()[:16]


def public_key_from_pem(pem: bytes) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(pem)  # type: ignore[return-value]


# 私钥落盘（仓库外）：backend/data/sign-keys/（默认，FRAMEWEAVE_SIGN_KEY_DIR 覆盖）
def key_dir() -> str:
    return os.environ.get(_KEY_DIR_ENV) or _DEFAULT_KEY_DIR


def load_private_key() -> Optional[Ed25519PrivateKey]:
    """加载发布私钥（仅打包/发布机；缺失返回 None）。"""
    p = os.path.join(key_dir(), _KEY_FILENAME)
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)  # type: ignore[return-value]


def ensure_private_key() -> Ed25519PrivateKey:
    """存在则加载，否则生成并落盘（目录仓库外，私钥绝不入库）。"""
    priv = load_private_key()
    if priv is not None:
        return priv
    priv, _pub = generate_keypair()
    d = key_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, _KEY_FILENAME), "wb") as f:
        f.write(priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    return priv


def save_public_key_pem(pub: Ed25519PublicKey, path: Optional[str] = None) -> str:
    """导出公钥 PEM（可随发布公开；私钥绝不导出）。"""
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    p = path or os.path.join(key_dir(), _PUB_FILENAME)
    d = os.path.dirname(os.path.abspath(p))
    os.makedirs(d, exist_ok=True)
    with open(p, "wb") as f:
        f.write(pem)
    return p


# ---------------------------------------------------------------- JCS 规范化
def _esc(s: str) -> str:
    """RFC 8785 键转义：\u0000-\u001F（\" 转义由 json.dumps 负责）。"""
    return "".join(
        "\\u%04x" % ord(ch) if 0x00 <= ord(ch) <= 0x1F else ch for ch in s)


def _stable_json(v: Any) -> str:
    """数字稳定序列化：int 直接；float 越过 ECMA 细节（1e-7 边界）用 repr，签名/验签共用。"""
    if isinstance(v, int):
        return str(v)
    if v != v or v in (float("inf"), float("-inf")):  # NaN / ±inf → null
        return "null"
    if v == int(v) and abs(v) < 1e16:
        return str(int(v))
    return repr(v)


def jcs_canonicalize(obj: Any) -> str:
    """JCS 风格规范化（RFC 8785 实用子集）：对象键升序、字符串 \u0000-\u001F 转义。

    与 RFC 8785 的差异（签名/验签共用同一实现，幂等安全）：
    - 不转义 \u2028/\u2029（json.dumps 默认不转义，保证跨 Python 一致）。
    - 数字字面量不做 ECMA 重写（_stable_json 保证双方一致）。
    数组/值保序按值序列化。
    """
    if isinstance(obj, dict):
        inner = ",".join(
            '"%s":%s' % (_esc(str(k)), jcs_canonicalize(v))
            for k, v in sorted(obj.items(), key=lambda kv: str(kv[0])))
        return "{" + inner + "}"
    if isinstance(obj, list):
        return "[" + ",".join(jcs_canonicalize(x) for x in obj) + "]"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, (int, float)):
        return _stable_json(obj)
    raise TypeError(f"无法规范化的 JSON 值: {type(obj)!r}")


def manifest_canonical_bytes(manifest: Dict[str, Any]) -> bytes:
    """剥除 signature 后规范化（覆盖 manifest 的签名体）。"""
    m = dict(manifest)
    m.pop("signature", None)
    return jcs_canonicalize(m).encode("utf-8")


# ---------------------------------------------------------------- 签名
def sign_bytes(data: bytes, priv: Ed25519PrivateKey) -> str:
    """对任意字节签名，返回 base64 签名。"""
    return _b64e(priv.sign(data))


def sign_manifest(manifest: Dict[str, Any], priv: Ed25519PrivateKey,
                  pub: Optional[Ed25519PublicKey] = None,
                  over: str = SIG_OVER_MANIFEST) -> Dict[str, Any]:
    """对 manifest（剥 signature 后规范化）签名，写入 signature 块。

    signature = {alg:"ed25519", key_id, value, over:"manifest"}。
    返回带签名的 manifest 副本（调用方落盘）。签名体=剥离 signature 后的规范化字节。
    """
    assert over in SIG_OVER_VALUES, f"over 必须是 {sorted(SIG_OVER_VALUES)}"
    out: Dict[str, Any] = dict(manifest)
    out["signature"] = {
        "alg": SIG_ALG,
        "key_id": key_id(pub) if pub is not None else "",
        "value": sign_bytes(manifest_canonical_bytes(out), priv),
        "over": over,
    }
    return out


def sign_manifest_to_disk(manifest: Dict[str, Any], priv: Ed25519PrivateKey,
                          pub: Optional[Ed25519PublicKey] = None,
                          manifest_path: Optional[str] = None) -> Dict[str, Any]:
    """签名并落盘到 manifest_path（默认 manifest 所在目录），返回签名后副本。"""
    signed = sign_manifest(manifest, priv, pub)
    p = manifest_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(signed, f, ensure_ascii=False, indent=2)
    return signed


def verify_signature_bytes(data: bytes, sig_b64: str, pub: Ed25519PublicKey) -> bool:
    """Ed25519 验签；任何格式问题返回 False（不抛）。"""
    try:
        if not isinstance(sig_b64, str) or not _SIG_B64_RE.match(sig_b64):
            return False
        pub.verify(_b64d(sig_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_manifest_signature(manifest: Dict[str, Any], pub: Ed25519PublicKey) -> bool:
    """验 manifest 签名：signature.over 必须为 manifest；value 为规范化体的签名。"""
    sig = manifest.get("signature")
    if not isinstance(sig, dict):
        return False
    if sig.get("over") != SIG_OVER_MANIFEST:
        return False
    return verify_signature_bytes(manifest_canonical_bytes(manifest),
                                  str(sig.get("value") or ""), pub)


def verify_manifest_signature_b64(manifest: Dict[str, Any], pub_b64: str) -> bool:
    """公钥以 base64 传入的便捷入口（失败返回 False）。"""
    try:
        return verify_manifest_signature(manifest, _pubkey_from_b64(pub_b64))
    except ValueError:
        return False


# ---------------------------------------------------------------- archive 签名
def sha256_bytes(path: str, block: int = 1 << 20) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.digest()


def sign_archive_signature(zip_path: str, priv: Ed25519PrivateKey,
                           pub: Optional[Ed25519PublicKey] = None) -> Dict[str, str]:
    """对 zip 整体 sha256 摘要签名，返回 {alg, key_id, value, over:"archive"}。"""
    return {
        "alg": SIG_ALG,
        "key_id": key_id(pub) if pub is not None else "",
        "value": sign_bytes(sha256_bytes(zip_path), priv),
        "over": SIG_OVER_ARCHIVE,
    }


def extract_archive_signature(zip_path: str) -> Optional[Dict[str, Any]]:
    """读取 zip 内 <pkg>/manifest.json 的 signature.over=="archive" 块；无则 None。"""
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist()
                     if n.endswith("/manifest.json") or n == "manifest.json"]
            if not names:
                return None
            name = sorted(names, key=lambda n: -len(n))[0]  # 最深处（顶层包）
            with zf.open(name) as f:
                data = json.loads(f.read().decode("utf-8"))
        sig = data.get("signature")
        if isinstance(sig, dict) and sig.get("over") == SIG_OVER_ARCHIVE:
            return sig
    except Exception:  # noqa: BLE001
        return None
    return None


def verify_archive_signature(zip_path: str, sig_b64: str, pub: Ed25519PublicKey) -> bool:
    """验 zip 整体字节签名（对 sha256 摘要签）。"""
    return verify_signature_bytes(sha256_bytes(zip_path), sig_b64, pub)


def verify_archive_signature_b64(zip_path: str, sig_b64: str, pub_b64: str) -> bool:
    try:
        return verify_archive_signature(zip_path, sig_b64, _pubkey_from_b64(pub_b64))
    except ValueError:
        return False


def pubkey_b64_to_pem(b64: str) -> bytes:
    """原始 32 字节 base64 → SubjectPublicKeyInfo PEM（工具/展示用）。"""
    pub = _pubkey_from_b64(b64)
    return pub.public_bytes(encoding=serialization.Encoding.PEM,
                            format=serialization.PublicFormat.SubjectPublicKeyInfo)


# ---------------------------------------------------------------- 信任锚
def official_pubkey_anchor_path() -> str:
    """L0 官方公钥锚文件路径（编译进客户端：backend/app/fw_official_pubkey.json）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fw_official_pubkey.json")


def load_official_anchor() -> Optional[Dict[str, Any]]:
    """读取 L0 官方公钥锚；缺失/损坏返回 None（core 包将拒绝安装，符合强制验签语义）。"""
    try:
        with open(official_pubkey_anchor_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("public_key"):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def official_public_key() -> Optional[Ed25519PublicKey]:
    """L0 官方验签公钥（从锚文件原始 32 字节 base64 构造）。"""
    anchor = load_official_anchor()
    if anchor is None:
        return None
    try:
        return _pubkey_from_b64(str(anchor.get("public_key") or ""))
    except ValueError:
        return None


def official_pubkey_b64() -> Optional[str]:
    """L0 官方锚公钥 base64（安装管线 core 强制验签用）。"""
    a = load_official_anchor()
    if a is None:
        return None
    pk = a.get("public_key")
    return pk if isinstance(pk, str) and pk else None


def is_official_type_id(type_id: str) -> bool:
    """type_id 前缀是否官方（L0 core/*）。"""
    if not isinstance(type_id, str) or "/" not in type_id:
        return False
    return type_id.split("/", 1)[0] in OFFICIAL_PREFIXES


# ---------------------------------------------------------------- 撤销
def _revocations_path() -> str:
    if _REVOCATIONS_PATH_OVERRIDE:
        return _REVOCATIONS_PATH_OVERRIDE
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        REVOCATIONS_FILENAME)


def revocations() -> Dict[str, Any]:
    """加载撤销清单（最小本地缓存实现；future 可替换为云端拉取）。

    返回 {"keys": {"<key_id>": "reason", ...}, "all": bool}。
    all=true 表示官方全部密钥已吊销（熔断）。
    """
    empty = {"keys": {}, "all": False}
    try:
        with open(_revocations_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return empty
        keys = data.get("keys")
        if not isinstance(keys, dict):
            keys = {}
        return {"keys": keys, "all": bool(data.get("all"))}
    except (OSError, json.JSONDecodeError):
        return empty


def is_revoked(key_id_str: str) -> bool:
    """key_id 是否在撤销清单（all 熔断同样判定为撤销）。"""
    r = revocations()
    if r.get("all"):
        return True
    return bool(r.get("keys", {}).get(key_id_str))


def set_revocations_path_for_test(path: str) -> None:
    """测试注入：覆盖撤销清单路径（t5 用独立临时文件，不污染仓库清单）。"""
    global _REVOCATIONS_PATH_OVERRIDE
    _REVOCATIONS_PATH_OVERRIDE = path


def reset_revocations_path() -> None:
    global _REVOCATIONS_PATH_OVERRIDE
    _REVOCATIONS_PATH_OVERRIDE = None


# ---------------------------------------------------------------- 组合验签（安装管线接入）
def check_install_signature(manifest: Dict[str, Any],
                            pubkey_b64: Optional[str],
                            required: bool = False,
                            zip_path: Optional[str] = None) -> Dict[str, Any]:
    """安装管线验签入口：返回 {ok, level, detail}。

    - 无 signature：required（core，L0）→ 拒；否则 → L2 社区级。
    - 有 signature 但无 pubkey_b64（无锚）：required → 拒；否则 → L2 降级。
    - 有签名 + 锚：先查撤销（key_id），再按 over 验签；失败/撤销 → 拒（L0）。
    """
    sig = manifest.get("signature")
    if not isinstance(sig, dict):
        if required:
            return {"ok": False, "level": "L0",
                    "detail": "core/ 官方包强制验签：缺少 signature"}
        return {"ok": True, "level": "L2", "detail": "无签名——社区级安装（L2）"}
    kid = str(sig.get("key_id") or "")
    if is_revoked(kid):
        return {"ok": False, "level": "L0",
                "detail": f"签名密钥已撤销（key_id={kid}）"}
    if not pubkey_b64:
        if required:
            return {"ok": False, "level": "L0",
                    "detail": "core/ 官方包强制验签：无公钥锚可验"}
        return {"ok": True, "level": "L2",
                "detail": "已签名但无公钥锚，按社区级（L2）降级"}
    try:
        pub = _pubkey_from_b64(pubkey_b64)
    except ValueError as e:
        return {"ok": False, "level": "L0", "detail": f"公钥锚非法: {e}"}
    over = sig.get("over")
    if over == SIG_OVER_MANIFEST:
        ok = verify_manifest_signature(manifest, pub)
        detail = "manifest 验签通过" if ok else "manifest 验签失败（签名不匹配/内容被篡改）"
    elif over == SIG_OVER_ARCHIVE:
        sb = str(sig.get("value") or "")
        if zip_path is None:
            return {"ok": False, "level": "L0",
                    "detail": "archive 签名需要 zip 原件，未提供"}
        ok = verify_archive_signature(zip_path, sb, pub)
        detail = "archive 验签通过" if ok else "archive 验签失败（zip 被篡改）"
    else:
        return {"ok": False, "level": "L0",
                "detail": f"signature.over 必须为 manifest|archive，收到 {over!r}"}
    if not ok:
        return {"ok": False, "level": "L0", "detail": detail}
    return {"ok": True, "level": "L1", "detail": detail}