# M19 云端授权 · VM 桌面端端到端测试报告

> 日期：2026-09-30 ｜ 客户端：FrameWeave v0.2.8（VM Windows 10）｜ 链路：VM → 宿主 192.168.88.1:8900（node 反代）→ 云端 154.36.178.119:8789（cloud_server.py）

## 测试结果（5 项全部通过 ✅）

| # | 场景 | 结果 | 证据 |
|---|------|------|------|
| 1 | backend 云化链路直测（login API） | ✅ | 8900 日志 `POST /api/auth/login → 200`；云端 db 落 `fwvmapi@fw.local(VM-API-TEST)` |
| 2 | UI 云端注册新账号 | ✅ | UI 注册 fwvm8@fw.local 进工作区「试用中 剩 1 天」；8900 日志 `login+verify → 200`；云端 db 落 `fwvm8 trial` |
| 3 | UI 卡密激活（云端签发卡） | ✅ | 注册 tab 填 email+卡 → 「卡密激活」→ 提示「激活成功：month +30 天」；8900 日志 `POST /api/auth/activate → 200` |
| 4 | 会员状态登录 | ✅ | email+password 登录 → 工作区显示「⭐ 会员」（非试用中） |
| 5 | 重启 app 会话保持 | ✅ | app 重启 → 8900 日志 `POST /api/auth/verify → 200` → 自动登录 → 「⭐ 会员」保持 |

## 云端 db 证据（/opt/frameweave-cloud/cloud.db sessions 表）

```
('fwpub@fw.local',     'member', 'PUB-DEV-2',    '1793447456.53541', '["2673f3c4f6"]')
('fwvmapi@fw.local',   'trial',  'VM-API-TEST',  '1790864394.43137', '[]')
('fwvm8@fw.local',     'member', 'dev-krwj4q6b', '1793458924.85936', '["ec7e7ffc14"]')
```

- fwvm8：云端注册(trial) → 卡密激活 → **plan=member**，+30 天，**cards_used 为本次签发的卡序列号 ec7e7ffc14**
- 签发卡：`FW-eyJwbGFuIjoibW9udGgiLCJkYXlzIjozMCwic2VyaWFsIjoiZWM3ZTdmZmMxNCJ9-0808e3b89131`

## 关键发现（之前多次测试全落本地桩的根因）

早期用 runProgramInGuest 手动启动的 **Session 0 常驻 backend（无 FRAMEWEAVE_CLOUD_URL env）占住 8788**；Electron 启动时 probePort(8788) 探测到已占用（frameweave）即**复用而不 spawn 自己的 backend**，导致带 env 的后端永不生效 → 一切注册/登录走本地桩。

**修复**：硬重启 VM（无任何常驻 backend）→ 手动启动 backend 时带 `set FRAMEWEAVE_CLOUD_URL=http://192.168.88.1:8900` → 云端链路立即生效。

## 运维备注

- 宿主 8900 反代进程会被 DSH pwsh 的 Job Object 回收（Start-Process 子进程随 pwsh 结束被杀）；当前用 run_in_background 前台跑 node 保持；彻底持久化建议宿主计划任务（schtasks 方案尚未验证通过）。
- cloud_gateway.py 已加 `_dbg` 调试日志（`FRAMEWEAVE_DEBUG_LOG` env 开关，默认关闭），保留以便排查。

## 遗留待办（已闭环，v0.2.11 实测确认）

- [x] cloud_gateway 云端不可达时回落本地桩——正式版应视为离线不允许使用
      → 已修复：全部 6 个 auth 路由（login/verify/register/reset/send-code/activate）在 cloud_enabled() 时
        云端不可达（resp=None）返回 {"ok":false,"message":"云端授权服务不可用"}，不回落本地桩；
        仅显式 FRAMEWEAVE_CLOUD_URL=""（dev）才走本地 license_svc。
        实测（云端指向不可达 127.0.0.1:9999）：login/send-code/activate 均返回"云端授权服务不可用"。
- [x] backend 无鉴权（本地调用者直接可用）——云化发布前必修
      → 已修复（H3）：local_token_guard 中间件保护 /api/secrets*、/api/export*、/api/assets* 全部 +
        POST/PUT/DELETE 写操作；/api/auth/*、/api/specs、/api/templates、/ws 保持开放（登录前必须可达）。
        实测：无 token 访问 /api/export → 401；带 X-FW-Local-Token → 200。
- [x] 畸形 body 返回 500
      → 已修复：RequestValidationError→422 结构化 errors、JSONDecodeError→400、兜底→500 统一结构。
        实测：畸形 JSON → 422 json_invalid；缺字段 → 422 missing。
