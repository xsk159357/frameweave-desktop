# -*- coding: utf-8 -*-
"""FrameWeave 后端 hiddenimports 收集器（PyArmor 混淆后使用）

问题：pyarmor 会把模块内 import 语句折叠进加密字节，PyInstaller 静态分析
看不到 app.* 的导入图，导致 fastapi.middleware.cors 这类传递子模块漏打包，
exe 启动即 ModuleNotFoundError。

方案（确定性）：
  1) 静态扫描 backend/app、main.py、voice_clone 中所有绝对 import，
     收集直接依赖的完整点路径（含子模块，如 fastapi.responses）；
  2) 在 venv 中真实执行导入 app.server（完整 FastAPI 栈）并显式加载
     延迟/函数级 import 的重型依赖（edge_tts/faster_whisper/cv2/...），
     快照 sys.modules 中所有位于 site-packages 的模块 = 真实运行时闭包；
  3) 两者并集排序写回 <out>，供 spec 的 hiddenimports 使用。

用法：python packaging/collect_hiddenimports.py [out_path]
"""
from __future__ import annotations
import os
import re
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
APP_DIR = os.path.join(BACKEND, "app")
MAIN_PY = os.path.join(BACKEND, "main.py")
VOICE = os.path.join(BACKEND, "voice_clone", "index_tts_server.py")

# 使 app 包可在 collector 进程中导入（spec 需要 app 运行闭包）
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

STDLIB_TOPS = {
    "_thread", "__future__", "abc", "argparse", "array", "ast", "asyncio",
    "base64", "binascii", "bisect", "builtins", "cmath", "collections",
    "concurrent", "configparser", "contextlib", "copy", "copyreg", "csv",
    "ctypes", "dataclasses", "datetime", "decimal", "difflib", "dis",
    "email", "enum", "errno", "fcntl", "functools", "gc", "getpass",
    "glob", "hashlib", "heapq", "hmac", "html", "http", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "linecache",
    "locale", "logging", "lzma", "math", "mimetypes", "mmap", "msvcrt",
    "multiprocessing", "netrc", "ntpath", "nturl2path", "numbers",
    "operator", "os", "pathlib", "pdb", "pickle", "platform", "plistlib",
    "posixpath", "pprint", "profile", "pstats", "queue", "random", "re",
    "readline", "reprlib", "runpy", "sched", "secrets", "select",
    "selectors", "shutil", "signal", "site", "socket", "socketserver",
    "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sys", "sysconfig", "tarfile", "tempfile",
    "textwrap", "threading", "time", "timeit", "tokenize", "trace",
    "traceback", "types", "typing", "unicodedata", "urllib", "uuid",
    "venv", "warnings", "wave", "weakref", "winreg", "winsound", "xml",
    "zipfile", "zipimport", "zlib", "pydoc", "faulthandler", "socket",
}

IMPORT_RE = re.compile(
    r"(?m)^\s*(?:from\s+([A-Za-z_][\w\.]*)\s+import\s+.+|import\s+([A-Za-z_][\w\.]*))")


def static_direct_imports() -> set:
    """静态扫描所有绝对 import 的完整点路径。"""
    out = set()
    files = [MAIN_PY, VOICE]
    for root, _dirs, names in os.walk(APP_DIR):
        for n in names:
            if n.endswith(".py") and ".work." not in n:
                files.append(os.path.join(root, n))
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in IMPORT_RE.finditer(text):
            mod = m.group(1) or m.group(2)
            top = mod.split(".")[0]
            if top in STDLIB_TOPS or top == "app":
                continue
            out.add(mod)
    return out


def runtime_closure(extra_imports) -> set:
    """在 venv 导入 app.server + 延迟依赖，快照 site-packages 模块闭包。"""
    snap = set()
    try:
        for mod in extra_imports:
            try:
                __import__(mod)
            except Exception as exc:  # noqa: BLE001  个别延迟依赖缺失不影响主闭包
                sys.stderr.write(f"[collect] extra import skip: {mod}: {exc!r}\n")
        # app 包必须在 sys.path 首位（BACKEND 已插入）；失败也要保留已收集闭包
        try:
            import app.server  # noqa: F401  完整 FastAPI 栈 + 应用初始化
            import uvicorn  # noqa: F401  main.py 入口依赖
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[collect] app.server import failed: {exc!r}\n")
        site_root = os.path.abspath(os.path.join(
            os.path.dirname(sys.executable), "..", "Lib", "site-packages"))
        if not os.path.isdir(site_root):
            import site as _site
            sp = _site.getsitepackages()
            if sp:
                site_root = os.path.abspath(sp[0])
            else:
                site_root = os.path.abspath(os.path.dirname(os.__file__))
        for name, module in list(sys.modules.items()):
            if not name or name.startswith("_"):
                continue
            if name.split(".")[0] in ("app", "pyarmor"):
                continue
            p = getattr(module, "__file__", None)
            if p and os.path.abspath(p).startswith(site_root.replace("\\", os.sep)):
                snap.add(name)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[collect] runtime closure error: {exc!r}\n")
    return snap


def main() -> int:
    static = static_direct_imports()
    # 显式延迟/函数级依赖：这些仅在调用时 import，导入 app.server 不会触发
    delayed = [
        "fastapi.responses", "fastapi.exceptions", "fastapi.middleware.cors",
        "alibabacloud_dm20151123", "alibabacloud_dm20151123.client",
        "alibabacloud_dm20151123.models", "alibabacloud_tea_openapi",
        "alibabacloud_tea_util", "alibabacloud_tea", "darabonba",
        "edge_tts", "faster_whisper", "cv2", "numpy", "imageio_ffmpeg",
        "ctranslate2", "aiofiles", "multipart", "python_multipart",
    ]
    rtc = runtime_closure(delayed)
    merged = sorted(static | rtc)
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        BACKEND, "packaging", "build", "obf_hiddenimports.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(merged))
    sys.stderr.write(f"[collect] static={len(static)} runtime={len(rtc)} total={len(merged)} -> {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())