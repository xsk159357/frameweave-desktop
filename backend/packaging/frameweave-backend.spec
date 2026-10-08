# -*- mode: python ; coding: utf-8 -*-
# FrameWeave 后端打包配置（PyInstaller）
# 排除 torch/torchaudio（后端不直接 import，IndexTTS2 用独立 3.11 venv 服务）

block_cipher = None

a = Analysis(
    ['../main.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../voice_clone', 'voice_clone'),
        # 节点不再随安装包分发：安装包只含本体；节点全部经商城/插件市场安装到用户数据目录。
    ],
    hiddenimports=[
        'app.declarative',
        'app.market',
        'app.cloud_gateway',
        'app.payment',
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
        'torch', 'torchaudio', 'transformers', 'diffusers', 'accelerate',
        'librosa', 'soundfile', 'vocos', 'einops', 'omegaconf', 'numba',
        'modelscope', 'scipy', 'matplotlib', 'pandas', 'sentencepiece',
        'tokenizers', 'tensorboard', 'keras', 'fugashi', 'unidic',
        'openai-whisper', 'wetext', 'kaldifst', 'indextts',
        'tensorflow', 'flask', 'django', 'scikit_learn',
    ],
    noarchive=False,
    optimize=0,
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
