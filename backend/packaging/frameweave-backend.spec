# -*- mode: python ; coding: utf-8 -*-
# FrameWeave 后端打包配置（PyInstaller + PyArmor 加固）
# 构建源 = backend/.obf_main（pyarmor 混淆后全树，含 app/ + pyarmor_runtime_011765/ + main.py）
# 说明：
#   * pyarmor 9.2.7 pro runtime 模式：app/* 全部加密，strings 无明文敏感串、无 .py 明文逻辑
#   * pyarmor 会把模块内 import 语句折叠进加密字节，PyInstaller 静态分析不可见 ->
#     必须把 app.* 全树显式加入 hiddenimports（见下方 _APP_MODULES 动态计算）
#   * 数据目录固化：sys.frozen -> %APPDATA%/FrameWeave（server.py/_resolve_data_dir）
#     USER_NODES_DIR 打包版同样指向 %APPDATA%/FrameWeave/user_nodes（declarative.py sys.frozen 分流）

import os

block_cipher = None

# PyInstaller 的 SPECPATH 就是 spec 文件所在目录（backend/packaging），直接使用
_SPEC_DIR = os.path.abspath(SPECPATH)
_OBF_ROOT = os.path.join(_SPEC_DIR, '..', '.obf_main')


def _collect_obf_app_modules():
    """从混淆树收集 app.* 全量模块名（含子包），供 hiddenimports 使用。"""
    mods = []
    app_root = os.path.join(_OBF_ROOT, 'app')
    for dirpath, _dirnames, filenames in os.walk(app_root):
        for fn in filenames:
            if not fn.endswith('.py') or '.work.' in fn:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), _OBF_ROOT)
            mod = rel[:-3].replace(os.sep, '.')
            if mod.endswith('.__init__'):
                mod = mod[:-len('.__init__')]
            if mod:
                mods.append(mod)
    return sorted(set(mods))


OBF_APP_MODULES = _collect_obf_app_modules()


def _load_closure_hiddenimports():
    """读取 collect_hiddenimports.py 生成的真实运行闭包（site-packages 全量模块）。

    生成：backend/.venv/Scripts/python.exe backend/packaging/collect_hiddenimports.py
    产出：backend/packaging/build/obf_hiddenimports.txt（1000+ 模块）
    pyarmor 折叠了 app 内 import 语句 -> 静态分析不可见，靠真实闭包兜底。
    """
    p = os.path.join(_SPEC_DIR, 'build', 'obf_hiddenimports.txt')
    if not os.path.isfile(p):
        raise SystemExit(
            '缺少 obf_hiddenimports.txt：请先运行 '
            'backend/.venv/Scripts/python.exe backend/packaging/collect_hiddenimports.py')
    return [ln.strip() for ln in open(p, encoding='utf-8') if ln.strip()]


CLOSURE_IMPORTS = _load_closure_hiddenimports()

a = Analysis(
    [os.path.join(_OBF_ROOT, 'main.py')],
    # 仅混淆树进 pathex：避免源码 backend/app（明文）被重复收集
    pathex=[_OBF_ROOT],
    binaries=[],
    datas=[
        # 随包默认模型清单（app.model_download 的 _DEFAULT_REGISTRY_PATH = _internal/models_registry.json）
        (os.path.join(_SPEC_DIR, '..', 'models_registry.json'), '.'),
        # 插件 schema 权威依据（manifest_v2 文档引用；含则不缺）
        (os.path.join(_SPEC_DIR, '..', 'schemas'), 'schemas'),
        # P3 官方插件验签信任锚与撤销清单：signatures.py 按 __file__ 同目录读取
        (os.path.join(_SPEC_DIR, '..', 'app', 'fw_official_pubkey.json'), 'app'),
        (os.path.join(_SPEC_DIR, '..', 'app', 'revocations.json'), 'app'),
        # IndexTTS2 服务脚本（打包版外部 indextts 目录未就绪时保留随包副本）
        (os.path.join(_SPEC_DIR, '..', 'voice_clone'), 'voice_clone'),
    ],
    hiddenimports=
        # 混淆后的 app 全树（pyarmor 折叠了 import，静态分析不可见，必须显式收集）
        OBF_APP_MODULES +
        # pyarmor 运行时（混淆模块顶部 from pyarmor_runtime_011765 import __pyarmor__）
        ['pyarmor_runtime_011765'] +
        # 真实运行闭包（collect_hiddenimports.py 生成，含 fastapi.middleware.cors 等
        # 被 pyarmor 折叠、静态分析跟不到的传递子模块）
        CLOSURE_IMPORTS +
        [
        # 既有保留项（第三方/框架，仍在站点包明文，静态分析可跟随）
        'app.declarative',
        'app.market',
        'app.cloud_gateway',
        'app.payment',
        'app.obf',
        'app.integrity',
        'multipart',
        'python_multipart',

        'edge_tts',
        'faster_whisper',
        'ctranslate2',
        'imageio_ffmpeg',
        'aiofiles',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'pydantic',
        'pydantic_core',
        'cv2',
        'alibabacloud_dm20151123',
        'alibabacloud_tea_openapi',
        'alibabacloud_tea_util',
        'alibabacloud_tea',
        'darabonba',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 后端不依赖的重型/无关库（收紧体积；保持既有排除面并补充）
        'torch', 'torchaudio', 'transformers', 'diffusers', 'accelerate',
        'librosa', 'soundfile', 'vocos', 'einops', 'omegaconf', 'numba',
        'modelscope', 'scipy', 'matplotlib', 'pandas', 'sentencepiece',
        'tokenizers', 'tensorboard', 'keras', 'fugashi', 'unidic',
        'openai-whisper', 'wetext', 'kaldifst', 'indextts',
        'tensorflow', 'flask', 'django', 'scikit_learn',
        'IPython', 'jupyter_core', 'nbformat', 'notebook',
        'pytest', 'setuptools.command', 'tkinter',
    ],
    noarchive=False,
    optimize=0,  # pyarmor 混淆代码要求 optimize=0（保留 docstring/assert 语义）
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='frameweave-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=True,
    name='backend-dist',
)