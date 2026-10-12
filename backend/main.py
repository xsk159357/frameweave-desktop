"""后端启动入口（P3/t3 ② 启动自检前置）。

frozen 打包版：在导入 app.*（授权逻辑/云端网关可被替换的代码）之前，先做关键产物
完整性自检（integrity.manifest 验签 + 哈希比对）；失败直接退出，不启动服务。
源码模式：跳过自检（无打包期清单），本地联调/CI 不受影响。

worker 子进程入口（t34）：frozen 打包版 PyInstaller bootloader 不接受 `-m 模块` 执行，
supervisor 以 `[sys.executable, "--fw-worker", "--job", <job>]` 拉起 worker；
本入口识别该参数并转发到 app.worker.host_bootstrap.main()（不启动 uvicorn server）。
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
    # t34：worker 子进程模式（frozen bootloader 无法 `-m`，经此参数转发）
    if "--fw-worker" in sys.argv:
        from app.worker.host_bootstrap import main as worker_main
        sys.exit(worker_main(sys.argv[sys.argv.index("--fw-worker") + 1:]))
    # t36-L1：frozen 版 env-check CLI 入口（源码态用 `python -m app.plugin.envcheck` / `fw env-check`）
    if "--env-check" in sys.argv:
        from app.plugin.envcheck import main as envcheck_main
        sys.exit(envcheck_main([]))
    import uvicorn
    port = int(os.environ.get("FRAMEWEAVE_PORT", "8788"))
    from app.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")