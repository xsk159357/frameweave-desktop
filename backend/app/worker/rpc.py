"""worker 受限 RPC 协议（P3，t7 §1.3/§1.4）：JSONL 编解码 + 消息 schema + 错误码。

依据《万物插件化_改造_运行时安全隔离.md》§1.3（worker_rpc_version = 1）：
- 传输：stdio JSONL（每行一个 JSON 对象；stderr 不进协议通道）
- 方向：host→worker 命令（op），worker→host 应答（ok）或主动事件（evt，id=null）
- 大载荷（>1MB）不走 stdio：走 scratch 文件 + staged 引用（supervisor/context 实施）
- 错误语义对齐总纲 §3.1.4：配置/运行/平台三类（§1.4 错误码表）
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

# ---------------------------------------------------------------- 版本
WORKER_RPC_VERSION = 1

# ---------------------------------------------------------------- 消息 op（host → worker 命令）
OP_HELLO = "hello"               # 握手：job 描述 + 权限声明
OP_RUN = "run"                   # 执行插件 run()
OP_PULL_INPUT = "pull_input"     # 拉取输入（host 物化到 scratch in/ 后经 staged 引用返回）
OP_WRITE_OUTPUT = "write_output" # 声明输出（host 校验后移入 AssetStore）
OP_RESOLVE_SECRET = "resolve_secret"  # 仅 permission.secret 白名单，host 侧解析一次注入
OP_SPAWN = "spawn"               # 子进程（host 侧托管，argv 白名单 + job object，无 shell）
OP_HTTP = "http"                 # 网络（host 侧代理，permission.network allowlist）
OP_FS_READ = "fs_read"           # 相对 roots.pkg 只读
OP_FS_WRITE = "fs_write"         # 相对 roots.scratch(out) 写入（受磁盘配额）
OP_PING = "ping"                 # 心跳（worker 存活探测）
OP_SHUTDOWN = "shutdown"         # 正常退休
OP_CANCEL = "cancel"             # 协作式取消（worker 收到后尽快退出）

# 全部 op（测试/审计用）
ALL_OPS = frozenset({
    OP_HELLO, OP_RUN, OP_PULL_INPUT, OP_WRITE_OUTPUT, OP_RESOLVE_SECRET,
    OP_SPAWN, OP_HTTP, OP_FS_READ, OP_FS_WRITE, OP_PING, OP_SHUTDOWN, OP_CANCEL,
})

# ---------------------------------------------------------------- 主动事件（worker → host，id=null）
EVT_PROGRESS = "progress"        # {"p": 0.42}
EVT_LOG = "log"                  # {"level": "warning", "msg": "…"}
EVT_CANCEL_ACK = "cancel_ack"    # 协作式取消确认

# ---------------------------------------------------------------- 错误码（§1.4 三类语义）
ERR_CONFIG = "PLUGIN_CONFIG_ERROR"            # 配置错误（manifest 校验失败/依赖缺失/venv 不可用）
ERR_RUNTIME = "PLUGIN_RUNTIME_ERROR"          # 运行错误（插件抛异常）
ERR_TIMEOUT = "PLUGIN_TIMEOUT"                # 超时（wall 时钟，supervisor kill 进程树）
ERR_PERMISSION = "PLUGIN_PERMISSION_DENIED"   # 越权（PermissionGate 拒绝）
ERR_RESOURCE = "PLUGIN_RESOURCE_LIMIT"        # 资源配额超限（Job Object 限额/scratch 预算）
ERR_CRASHED = "PLUGIN_CRASHED"                # 崩溃（异常退出/段错误/OOM）
ERR_CANCELLED = "PLUGIN_CANCELLED"            # 取消
ERR_RPC = "PLATFORM_RPC_ERROR"                # 管道损坏/协议错误
ERR_INTERNAL = "PLATFORM_INTERNAL"            # 主进程内部错误

# ---------------------------------------------------------------- 消息构造
def new_message(msg_id: Any, op: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    """构造 host→worker 命令消息。"""
    m: Dict[str, Any] = {"v": WORKER_RPC_VERSION, "id": msg_id}
    if op is not None:
        m["op"] = op
    m.update(fields)
    return m


def ok(msg_id: Any, **fields: Any) -> Dict[str, Any]:
    """构造 worker→host 成功应答。"""
    m: Dict[str, Any] = {"v": WORKER_RPC_VERSION, "id": msg_id, "ok": True}
    m.update(fields)
    return m


def error(msg_id: Any, code: str, detail: str = "") -> Dict[str, Any]:
    """构造 worker→host 错误应答（code 用 §1.4 错误码）。"""
    return {"v": WORKER_RPC_VERSION, "id": msg_id, "ok": False,
            "code": code, "error": detail}


def event(evt: str, **fields: Any) -> Dict[str, Any]:
    """构造 worker→host 主动事件（id=null）。"""
    m: Dict[str, Any] = {"v": WORKER_RPC_VERSION, "id": None, "evt": evt}
    m.update(fields)
    return m


# ---------------------------------------------------------------- JSONL 编解码
def encode(obj: Dict[str, Any]) -> str:
    """序列化一条消息为一行 JSON（JSONL）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"


def decode_line(line: str) -> Optional[Dict[str, Any]]:
    """解析一行 JSONL。空行/损坏返回 None（不抛——防插件输出轰炸解析器）。"""
    if not line or not line.strip():
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


class JSONLWriter:
    """stdout 行式写入器（worker 端）。"""

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout
        self._buffer = ""

    def send(self, obj: Dict[str, Any]) -> None:
        self._stream.write(encode(obj))
        self._stream.flush()


class JSONLReader:
    """stdin 行式读取器（worker 端；同步阻塞读一行）。"""

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdin

    def read_message(self) -> Optional[Dict[str, Any]]:
        line = self._stream.readline()
        if not line:
            return None                    # EOF：host 关闭管道
        return decode_line(line)


# ---------------------------------------------------------------- schema 校验（hello/job 结构）
def check_message(msg: Dict[str, Any], allowed_ops: frozenset = ALL_OPS) -> Optional[str]:
    """浅校验 host→worker 命令消息。返回错误 detail 或 None。"""
    if not isinstance(msg, dict):
        return "消息必须是对象"
    if msg.get("v") != WORKER_RPC_VERSION:
        return f"不支持的 rpc 版本: {msg.get('v')!r}"
    if "id" not in msg:
        return "消息缺少 id"
    op = msg.get("op")
    if op not in allowed_ops:
        return f"未知 op: {op!r}"
    return None
