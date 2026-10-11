"""后端启动入口（P3/t3 ② 启动自检前置）。

frozen 打包版：在导入 app.*（授权逻辑/云端网关可被替换的代码）之前，先做关键产物
完整性自检（integrity.manifest 验签 + 哈希比对）；失败直接退出，不启动服务。
源码模式：跳过自检（无打包期清单），本地联调/CI 不受影响。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# P3/t3 ②：frozen 打包版启动自检（必须在任何 app.* 导入之前执行，
# 否则被替换的授权模块可能已加载进内存）。
if getattr(sys, "frozen", False):
    from app.integrity import startup_selfcheck

    _ok, _msg = startup_selfcheck()
    if not _ok:
        print("[integrity] FATAL 启动自检失败:", _msg)
        sys.exit(1)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("FRAMEWEAVE_PORT", "8788"))
    from app.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")