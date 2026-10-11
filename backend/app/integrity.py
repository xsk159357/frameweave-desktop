# -*- coding: utf-8 -*-
"""F2 打包版启动自检（t3/P3 ②）：关键产物完整性校验，不匹配拒绝启动。

威胁模型：攻击者替换打包产物（主 exe / _internal/ 下 pyarmor 运行时、依赖库），
伪造"本地授权桩永远通过"，绕过订阅校验。授权核心逻辑（app.license / app.cloud_gateway
等）经 pyarmor 混淆后内嵌在 frameweave-backend.exe 的 PYZ 中，因此对 exe 文件与
_internal/ 全量文件的哈希自检，即覆盖"关键模块未被替换"。

机制（打包期生成签名清单，运行期验签）：
  - 打包后运行 build_manifest.py：对 dist 根目录（frameweave-backend.exe + _internal/*）
    逐文件计算 SHA-256，清单体 HMAC(HKDF(integrity_key, nonce)) 签名，写
    backend-dist/integrity.manifest（与 exe 同目录，随安装包分发）。
  - frozen 启动时（server.py 导入最前）verify_manifest：验签失败或缺文件/哈希不匹配
    → 打印错误并退出（拒绝启动），绝不静默降级。
  - 密钥：环境变量 FRAMEWEAVE_INTEGRITY_KEY 优先，否则内嵌混淆默认（obf.py，
    不经明文出现于 exe）。

源码/开发模式：无打包期清单，跳过自检（本地联调与 CI 不受影响）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
from typing import Dict, Optional, Tuple

from . import obf

MANIFEST_FILENAME = "integrity.manifest"
MANIFEST_V = 1

# 关键自检范围（打包版启动校验的产物）：
#   - 主 exe（内含 PYZ，授权核心 app.* 全在其中）
#   - pyarmor 运行时（混淆模块的解密密钥载体）
#   - 解释器/TLS/核心扩展（python314.dll / libssl / _ssl.pyd 等——被替换会改变运行环境）
# 重型第三方库（cv2/numpy/onnx 等）不纳入：体积大、对授权链无关，逐文件哈希拖慢启动。
# 理由：授权核心（license/cloud_gateway/verify 判定）全部在 exe 的 PYZ 内，
# 对 exe + pyarmor 运行时做哈希，即覆盖"本地授权桩被替换"的主威胁面。
CRITICAL_REL_PREFIXES = ("frameweave-backend.exe", "_internal/pyarmor_runtime_")
CRITICAL_REL_EXACT = {
    "_internal/python314.dll", "_internal/python3.dll", "_internal/base_library.zip",
    "_internal/libssl-3.dll", "_internal/libcrypto-3.dll", "_internal/_ssl.pyd",
    "_internal/_hashlib.pyd", "_internal/unicodedata.pyd", "_internal/_ctypes.pyd",
    "_internal/_socket.pyd", "_internal/select.pyd", "_internal/sqlite3.dll",
}


def _critical_rel(rel: str) -> bool:
    if rel.startswith(CRITICAL_REL_PREFIXES):
        return True
    return rel in CRITICAL_REL_EXACT


def _integrity_key() -> bytes:
    """冗余密钥通道：环境变量优先（部署/运维可轮换），否则内嵌混淆默认。"""
    k = os.environ.get(obf.integrity_key_env(), "") or obf.integrity_key_default()
    return k.encode("utf-8")


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _derive_hmac_key(base: bytes, salt: bytes) -> bytes:
    """HKDF 风格密钥派生：让签名密钥与内嵌密钥解耦，防直接重放/宽度攻击。"""
    return hashlib.pbkdf2_hmac("sha256", base, salt, 10000, dklen=32)


def compute_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str, out_path: str, key: bytes) -> Dict:
    """构建打包期签名清单。

    仅收录关键自检文件（_critical_rel）；manifest 体经 HMAC(HKDF(key,nonce)) 签名。
    """
    import time

    files: Dict[str, str] = {}
    if not os.path.isdir(root):
        raise RuntimeError(f"manifest 构建失败：目录不存在 {root}")
    for dirpath, _dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.normpath(os.path.join(rel_dir, fn)).replace("\\", "/")
            if rel == MANIFEST_FILENAME:
                continue
            if not _critical_rel(rel):
                continue
            files[rel] = compute_file_sha256(full)
    if not files:
        raise RuntimeError("manifest 构建失败：未匹配到关键文件")
    nonce = os.urandom(16)
    body = {
        "v": MANIFEST_V,
        "ts": int(time.time()),
        "files": files,
    }
    body_b64 = _b64e(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    dk = _derive_hmac_key(key, nonce)
    sig = hmac.new(dk, body_b64.encode("ascii"), hashlib.sha256).digest()[:24]
    manifest = {
        "enc": "v1",
        "body": body_b64,
        "sig": _b64e(sig),
        "nonce": _b64e(nonce),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, separators=(",", ":"))
    return manifest


def verify_manifest(root: str, manifest_path: str, key: bytes) -> Tuple[bool, str]:
    """验签 + 逐文件哈希比对。返回 (ok, message)。"""
    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as f:
            manifest = json.load(f)
        if manifest.get("enc") != "v1" or not manifest.get("body") or not manifest.get("sig"):
            return False, "清单格式无效"
        body_b64 = str(manifest["body"])
        nonce = _b64d(str(manifest["nonce"])) if manifest.get("nonce") else b""
        dk = _derive_hmac_key(key, nonce)
        expect = _b64d(str(manifest["sig"]))
        actual = hmac.new(dk, body_b64.encode("ascii"), hashlib.sha256).digest()[:24]
        if not hmac.compare_digest(actual, expect):
            return False, "清单签名校验失败（清单被篡改）"
        body = json.loads(_b64d(body_b64).decode("utf-8"))
        if int(body.get("v") or 0) != MANIFEST_V or not isinstance(body.get("files"), dict):
            return False, "清单正文无效"
        files: Dict[str, str] = body["files"]
        for rel, expected in files.items():
            p = os.path.join(root, rel.replace("/", os.sep))
            if not os.path.isfile(p):
                return False, f"关键文件缺失: {rel}"
            if compute_file_sha256(p) != expected:
                return False, f"关键文件被篡改: {rel}"
        return True, f"通过（{len(files)} 项）"
    except Exception as exc:  # noqa: BLE001
        return False, f"自检异常: {exc!r}"


def startup_selfcheck() -> Tuple[bool, str]:
    """后端启动自检入口：仅 frozen 打包版生效。

    (True, msg) = 通过；(False, msg) = 拒绝启动。异常路径一律按失败处理。
    """
    if not getattr(sys, "frozen", False):
        return True, "源码模式跳过启动自检"
    base = os.path.dirname(os.path.abspath(sys.executable))
    mp = os.path.join(base, MANIFEST_FILENAME)
    if not os.path.isfile(mp):
        return False, "缺少完整性清单，拒绝启动"
    key = _integrity_key()
    ok, msg = verify_manifest(base, mp, key)
    if not ok:
        print(f"[integrity] FATAL 启动自检失败: {msg}")
        return False, msg
    print(f"[integrity] 启动自检通过: {msg}")
    return True, msg