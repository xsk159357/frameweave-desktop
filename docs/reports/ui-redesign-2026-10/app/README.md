# 拾帧 FrameWeave · 桌面应用 UI 重设计（墨织 INKWEAVE）

- 日期：2026-10-18
- 范围：桌面端主界面（Electron 33 + React 18 + Vite 6）全部视觉与布局；应用图标、安装器图标与位图
- 设计基线：`FrameWeave/docs/specs/design-system-inkweave.md`
- 样式入口：`FrameWeave/web/src/index.css`（整表重写，≈1000 行）
- 截图：本目录 `01-*` ~ `18-*`

## 1. 布局骨架

```
┌──────────────────────────────────────────────────────────────────────┐
│ 46px 顶栏：品牌锁定 · 工作流名/未保存/版本 · 更新/插件/商城/账户/设置   │
├──────┬───────────────────┬───────────────────────────────────────────┤
│ 52px │ 248px 工作流面板    │  画布（命令条 + 无限画布 + 小地图）        │
│ 图标 │ 新建/导入/模板      │  ┌───────────────────────────────┐ 320px │
│ 竖排 │ 搜索               │  │ 节点画布                       │ 检查器 │
│      │ 列表（黄铜左标）    │  └───────────────────────────────┘        │
├──────┴───────────────────┴───────────────────────────────────────────┤
│ 26px 状态栏：本地引擎状态 · 保存状态 · 版本 · 快捷键                    │
└──────────────────────────────────────────────────────────────────────┘
```

- 顶栏 46px，与 Electron `titleBarOverlay.height = 46` 对齐（`desktop/main.cjs`），窗口按钮叠在顶栏右端。
- 图标栏 52px，常驻 9 个入口：工作流 / 节点库 / 素材 / 队列 / 模型 / 商城 / 插件 / 主题 / 账户，选中态用 2px 黄铜左标而非填充块。
- 工作流面板 248px，可收起（点击顶栏工作流名或图标栏首项）。
- 检查器 320px 常驻右侧；节点库改为**抽屉**（命令条「节点库」或图标栏触发），不再与画布抢宽度。
- 画布命令条取代原来的浮动工具簇：一键出片（主操作）/ 仅下游 / 保存 / 自动布局 / 右侧视图控制。

## 2. 设计令牌落地

| 维度 | 取值 |
|---|---|
| 底色 | `#0B0C0E` / 面板 `#101113` / 浮层 `#15161A` / 发丝线 `#22242A` |
| 文字 | 主 `#EDE8DE` / 次 `#8B8F98` / 弱 `#5E626B` |
| 黄铜 | `#D2A45E`（主操作、选中、运行中），浅色主题 `#9A6B1F` |
| 语义 | 成功 `#6FA97F` / 警告 `#D2A45E` / 失败 `#C4695C` / 信息 `#7C93A8` |
| 字体 | 标题 Georgia / 正文 Inter + PingFang SC / 时间码与数值 JetBrains Mono |
| 圆角 | 4 / 6 / 8 / 10px，无胶囊、无玻璃模糊、无彩色发光、无大面积渐变 |
| 动效 | 120–200ms，位移 ≤ 8px，只用于状态变化 |

浅色主题（纸色）通过 `html[data-theme=light]` 令牌覆盖，同一套组件结构，不复制样式表。

## 3. 重写与调整的组件

| 文件 | 内容 |
|---|---|
| `web/src/index.css` | 全量重写：令牌、壳层、图标栏、顶栏、画布命令条、节点卡、抽屉、时间线、状态栏、Toast、模态、登录页（`lp-*`）、引导层、响应式 |
| `web/src/Logo.tsx` | 新标识组件：米白取景框 + 黄铜 `F`，`tile` / `color` / `accent` 三种用法，`useId` 渐变避免多实例冲突 |
| `web/src/App.tsx` | 新顶栏（衬线品牌锁定、工作流名/未保存/版本）、9 项图标栏、可收起侧栏、新启动屏、新状态栏、`sidebarOpen` 状态 |
| `web/src/Canvas.tsx` | 浮动工具簇 → `.fw-canvas-toolbar` 命令条；`fw-open-library` 事件；小地图与背景重绘 |
| `web/src/nodes/FlowNode.tsx` | 节点卡视觉重写（`fw-node*` 类），**几何不变**：头 44 / 端口 24 间距 / 状态行 36 / 左右内边距 12，连接点位置与拖拽行为完全保留 |
| `web/src/LoginPage.tsx` | 编辑式左右分栏：衬线 `FRAMEWEAVE`、中文主张、序列帧、扁平表单；登录/注册逻辑不变 |
| `web/src/WorkflowPanel.tsx` | 扁平列表、黄铜左标、新表头与搜索 |
| `web/src/ParamPanel.tsx` | 检查器排版、中文状态文案、表头 |
| `web/src/Timeline.tsx` | 黄铜/仪表化重绘 |
| `web/src/SettingsPanel.tsx` | 主题更名「墨色 / 纸色」，控件扁平化 |
| `web/src/MarketPage.tsx` | 卡片/CTA/徽标/搜索/分类片重绘 |
| `web/src/PluginPanel.tsx`、`panels/ProHub.tsx` | 抽屉 40px 标签栏；移除悬浮球，改由顶栏「插件」与 `fw-open-pro` 事件触发 |
| `panels/NodeSearchMenu`、`OnboardingOverlay`、`QueuePanel`、`AssetsPanel`、`ModelCenter`、`ResumeBar`、`ResultPanel`、`WorkflowModals` | 令牌、圆角、字体与间距对齐 |
| `web/index.html` | 标题/描述/favicon/apple-touch/theme-color |

> 业务逻辑、状态管理、IPC 通道、API 契约、节点几何与交互均未改动；本次只替换表现层。`git diff --stat`：33 个文件，+1212 / −1459。

## 4. 图标与安装器资产

`FrameWeave/desktop/build/`：

| 文件 | 规格 | 用途 |
|---|---|---|
| `icon.ico` | 363 KB，BMP-DIB 多尺寸 16/24/32/48/64/128/256 | 应用图标（exe/窗口/任务栏） |
| `icon.png` | 512×512 | 渲染层/文档/关于 |
| `installerIcon.ico` | 16–128 | 安装向导左上角 |
| `uninstallerIcon.ico` | 16–128 | 卸载向导 |
| `installerHeaderIcon.ico` | 16–128 | 安装向导页眉小图标 |
| `installerHeader.bmp` | 150×57 | 安装向导页眉条 |
| `installerSidebar.bmp` | 164×314 | 安装向导左侧图 |
| `uninstallerSidebar.bmp` | 164×314 | 卸载向导左侧图 |

标识语义：石墨底 + 米白取景框（四角括号），黄铜 `F` 从框内探出并被裁切 —— 对应「帧 / 取景 / 编织」。

`desktop/main.cjs` 同步更新：窗口图标路径、`backgroundColor: #0B0C0E`、`titleBarOverlay` 高度 46 与符号色，避免启动瞬间闪白。
`desktop/package.json` 的 `files` 增加 `build/icon.png`，NSIS 段保留四个图标与三张位图配置。

## 5. 验证

| 项目 | 命令 / 方式 | 结果 |
|---|---|---|
| 类型检查 | `cd FrameWeave/web && npx tsc --noEmit` | 0 错误 |
| 生产构建 | `npm run build` | 成功：CSS 58.23 kB（gzip 11.45），JS 603.48 kB（gzip 182.10），仅体积告警 |
| 集成自检 | `cd FrameWeave/desktop && npm run integrity` | 生成 `integrity.json`（8 个关键文件哈希） |
| 界面回归 | Playwright + Edge（无头）截图 1440×900 / 1024×768 | 无 JS 报错，无横向滚动 |
| 生产包渲染 | 静态伺服 `web/dist` + 模拟后端（8788）+ Edge 截图 | 新登录页与设计令牌正确加载（`18-production-build-login.png`） |

> 打包产物：`FrameWeave-Setup-0.3.12.exe`（portable，205,979,583 B，MD5 `23d6c476ebd1ebbee6c86bdbb8442c7d`）已生成，
> `win-unpacked/FrameWeave.exe` 已由 rcedit 写入新图标（提取验证见 `19-exe-icon-32.png`）。
> asar 内的 `index-C8iXkHUl.js` 与 `integrity.json` 一致。NSIS 安装包在本机无法产出 —— 见上级 README「打包限制」。

## 6. 截图索引

| 文件 | 场景 |
|---|---|
| `01-workbench-dark.png` | 工作台全景（深色，1600×1000） |
| `02-workbench-light.png` | 工作台（纸色浅色主题） |
| `03-node-inspector.png` | 选中节点 + 右侧检查器 |
| `04-node-library.png` | 节点库抽屉 |
| `05-market.png` | 节点商城 |
| `06-settings.png` | 设置面板（墨色/纸色） |
| `07-profile.png` | 账户与个人中心 |
| `08-plugin-panel.png` | 插件管理 |
| `09-queue.png` | 运行队列 |
| `10-assets.png` | 素材库 |
| `11-onboarding.png` | 首次引导 |
| `12-splash.png` | 启动屏 |
| `13-login-dark.png` / `14-login-light.png` | 登录页深/浅 |
| `15-workbench-1024.png` | 1024×768 窄桌面 |
| `16-empty-state.png` | 空画布 |
| `17-node-search.png` | 双击画布的节点搜索 |
| `18-production-build-login.png` | 生产构建（`web/dist`）渲染验证 |
| `19-exe-icon-32.png` | 从 `win-unpacked/FrameWeave.exe` 提取的实际关联图标 |
| `20-packaged-app-login.png` | **打包版实机运行**（portable 解压启动，PrintWindow 抓帧） |
| `icon-512.png` | 应用图标主视觉 |
