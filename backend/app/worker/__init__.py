"""平台插件执行安全共享模块（P0.4 → P3 worker 进程隔离）。

- worker/limits.py    —— 上传/解压参数唯一引用点（server/商城/install_zip 三入口共享）。
- worker/perm.py      —— PermissionGate 五作用域（fs/network/subprocess/secret/env）。
- worker/rpc.py       —— worker↔host 受限 RPC（JSONL + §1.4 错误码）。
- worker/jobs.py      —— Windows Job Object 整树 kill + taskkill 降级。
- worker/ns.py        —— NamespaceJail 导入封锁（按信任级）。
- worker/context.py   —— WorkerContext（worker 内插件运行上下文）。
- worker/host_bootstrap.py —— worker 进程入口（python -m app.worker.host_bootstrap）。
- worker/supervisor.py      —— host 侧 WorkerSupervisor（spawn/握手/超时/输出导入）。
- worker/perm_hooks.py      —— install/scan/run 三入口共享校验钩子。

对外 API：WorkerSupervisor / PermissionGate / WorkerJobError（engine.py 经 engines.WorkerExecutor
按 FW_PLUGIN_EXEC=worker 环境开关使用）。
"""
