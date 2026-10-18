# 拾帧 FrameWeave · 运营后台重设计（墨织 INKWEAVE）

- 日期：2026-10-18
- 线上地址：<https://houtai.frameweave.top/>
- 源文件：`FrameWeave/docs/reports/ui-redesign-2026-10/admin/admin.html`
- 部署位置：`/opt/frameweave-cloud/admin.html`
- 设计基线：`FrameWeave/docs/specs/design-system-inkweave.md` + `FrameWeave/web/src/index.css`

## 1. 改了什么

这是一次整体重写，不是样式微调：

- **壳层**：216px 石墨侧栏（品牌锁定 / 运营与系统两组导航 / 底部云端运行状态）+ 52px 页头（页面标题与说明、环境指标、刷新、主题、退出登录）。
- **令牌**：近黑石墨 `#060708–#2B2E34`、黄铜 `#D2A45E`、米白 `#EDE8DE`；1px 发丝线；圆角 4–10px；无玻璃模糊、无彩色发光、无大面积渐变、无外部 CDN。
- **字体**：UI 使用 Inter / Segoe UI / PingFang SC / Microsoft YaHei UI；数字、ID、时间使用 JetBrains Mono / Cascadia Code / Consolas；面板标签统一 10.5px / 700 / letter-spacing .14em。
- **表格**：行高 36px，表头小型大写字距，数字右对齐等宽，状态用 5px 圆点 + 文本，不使用糖果色胶囊。
- **仪表盘**：6 个主 KPI + 10 项运营指标带 + 纯 CSS/SVG 授权活动柱状图（近 14 天审计）+ 运营构成比例条 + 到期提醒 + 最近审计 + 快捷操作。
- **登录页**：左右分栏，左侧品牌叙事与安全提示，右侧窄登录卡；管理密钥显示切换、错误行内提示、失败限流提示；保留 `/api/admin/login` 原逻辑。
- **主题**：深色默认，`html[data-theme=light]` 令牌覆盖，`localStorage.fw_admin_theme` 持久化，刷新前内联脚本设置，避免闪白。
- **响应式**：≥1280 宽表与双栏；<1100 侧栏收窄为 64px 图标栏；<860 侧栏抽屉化；<560 单列/图标按钮。表格在窄屏内部横向滚动，不撑破页面。
- **可访问性**：跳到主要内容、可见键盘焦点、抽屉 `aria-expanded`、弹层 `role=dialog/alertdialog` + 焦点陷阱 + Esc 关闭 + 焦点归还、导航 `aria-current`、图标按钮 `aria-label`、Toast `aria-live`。

### 功能与接口契约（全部保留）

登录/登出、统计、用户列表/详情/加时/积分/启禁用/踢设备、设备列表与解踢、卡密签发/筛选/作废/导出、商城统计/列表/新增/编辑/上下架/导出、审计筛选/分页/导出、设置读写、管理密钥轮换、管理会话吊销、备份导出/恢复、运维只读代理。

新增组织能力（不改变服务端契约）：

- 用户页增加「设备」子视图（`/api/admin/devices`、`/api/admin/device/reset`），并在用户页下方提供账号构成与设备概览。
- 卡密页下方提供卡密构成与最近使用。
- 审计改为服务端筛选/分页（`q`、`scene`、`result`、`page`、`per_page`）。
- 商城列表客户端分页，避免 113 条一次性渲染过长。
- 运维视图增加接入信息与接口目录；未配置 `ops_base` 时显示明确的接入空态。

### 接口清单

`/api/admin/login`、`/logout`、`/stats`、`/users`、`/user/get`、`/user/extend`、`/user/credits`、`/user/toggle`、`/user/kick`、`/devices`、`/device/reset`、`/cards`、`/issue-card`、`/card/revoke`、`/market/list`、`/market/upsert`、`/market/stats`、`/audit`、`/settings/get`、`/settings`、`/key/rotate`、`/tokens`、`/token/revoke`、`/backup`、`/restore`、`/ops`。

## 2. 部署

| 项 | 值 |
|---|---|
| 线上文件 | `/opt/frameweave-cloud/admin.html` |
| 权限 | `644 root:root` |
| 大小 | 163,337 bytes |
| MD5 | `b642b87644b79f3303b5567295b8db06` |
| 原始后台备份 | `/opt/frameweave-cloud/backups/20261018233753/admin.html`（81,171 bytes，重设计前） |
| 迭代备份 | `/opt/frameweave-cloud/backups/20261018234040/admin.html`、`/opt/frameweave-cloud/backups/20261018234207/admin.html` |

cloud_server 每次请求读取 `admin.html`，未重启服务；未修改 `cloud_server.py`，未修改数据库结构。

## 3. 验证结果

- `http://127.0.0.1:8789/` → **200**
- `https://houtai.frameweave.top/` → **200**
- 线上文件 MD5 与本地源文件一致：`b642b87644b79f3303b5567295b8db06`
- Playwright（Edge，1440×900）逐页截图：**console error 0、pageerror 0、失败请求 0**
- 登录成功 / 退出登录流程通过；错误密钥显示「管理密钥错误」；空用户筛选显示空态；明暗主题切换与持久化通过；1024px 与 390px 响应式检查通过，无页面级横向溢出。
- 截图 token 与验证 token 已全部清理：`admin_tokens` = **0**；数据库用户数 = **2**、卡密数 = **2**，与部署前一致。
- 运维视图当前显示「本地服务尚未接入」，因为线上 `ops_base` 为空；填写后即可使用原有健康/队列/历史/插件/LLM/节点数代理。

> 线上运营数据较少（2 位用户、2 张卡密、21 条审计、113 个商城条目），因此截图保留真实空态与真实稀疏数据；密度与长表走查另在本地 mock（248 用户 / 320 卡密 / 140 审计）完成，mock 文件未上传服务器。

## 4. 截图（均为 1440×900 PNG）

| 视图 | 文件 |
|---|---|
| 登录页 | `01-login.png` |
| 仪表盘（深色） | `02-dashboard-dark.png` |
| 用户管理 | `03-users.png` |
| 卡密管理 | `04-cards.png` |
| 商城管理 | `05-market.png` |
| 审计日志 | `06-audit.png` |
| 系统设置 | `07-settings.png` |
| 运维视图 | `08-ops.png` |
| 仪表盘（亮色） | `09-dashboard-light.png` |

## 5. 迭代记录（视觉走查后修正）

1. 修复 KPI 数值与详情 `.kv` 网格类名冲突导致的灰色块。
2. 重排仪表盘：图表与运营构成等高，10 项指标独立为全宽指标带，首屏信息密度更均匀。
3. 卡密页由「左表单 + 右窄表」改为「全宽签发栏 + 全宽表格 + 构成/最近使用摘要」，消除表格横向溢出。
4. 用户页增加账号构成与设备概览，避免真实少量数据下的大面积空白。
5. 修复 390px 图表 X 轴标签拥挤（移动端隐藏部分标签）。
6. 修复运维「查询」链接被误改为按钮的类名选择器问题。
7. 修复亮色主题截图时 hash 路由未重新进入仪表盘的问题（验证脚本）。
8. 提升 `--paper-faint` 暗/亮对比度，补充跳过链接，退出登录改为带文字按钮。
