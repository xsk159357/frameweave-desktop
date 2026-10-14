# 拾帧 FrameWeave · 无边框窗口 + UI 视觉统一（方案A）落地记录

> 日期：2026-10 ｜ 状态：P0/P1 已完成并构建通过；P2 打包实测待跑
> 决策：用户确认按方案 A（titleBarStyle:'hidden' + titleBarOverlay 原生控件），不采用 frame:false 全自绘。

## 1. 市场调研结论（权威来源）

- **Electron 无边框两路线**（[官方文档](https://www.electronjs.org/docs/latest/tutorial/window-customization)）：
  1. `titleBarStyle:'hidden'` + `titleBarOverlay` —— 隐藏系统标题栏、保留系统绘制的最小化/最大化/关闭；渲染层用 `env(titlebar-area-*)` 避让。
  2. `frame:false` 全自绘 —— 完全自由但控件/拖拽/双击最大化/右键菜单/圆角/Snap 全要自实现。
- **拖拽规范**（[Custom Window Interactions](https://www.electronjs.org/docs/latest/tutorial/custom-window-interactions)）：CSS `app-region: drag` 标拖拽区（忽略指针事件，按钮必须 `no-drag`）；`user-select:none`；拖拽区勿挂自定义右键菜单。
- **防白闪**（[Custom Window Styles](https://www.electronjs.org/docs/latest/tutorial/custom-window-styles)）：`show:false`+`ready-to-show` 或 `backgroundColor` 与 UI 背景一致。
- **微软材质**（[Materials](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/materials)）：Mica=不透明基础层（随壁纸着色+焦点态）；Acrylic=半透明磨砂仅限临时浮层；Smoke=模态遮罩。Win11 圆角由系统 DWM 处理。
- **产品案例**：VS Code / Figma / Linear / 剪映专业版 / 飞书均走路线 A；DaVinci 类走路线 B。主流 = 路线 A。

## 2. 现状诊断（改造前代码级事实）

1. main.cjs BrowserWindow 未设 frame/titleBarStyle/titleBarOverlay → 系统标题栏 + 应用内假窗栏（.fw-windowbar）双栏并存。
2. preload.cjs 未暴露 window 控制桥 → App.tsx 三个窗口按钮是死按钮（点击报错）。
3. index.css 有 `html,body{background:transparent}` 圆角残留（无透明窗口前提，无害）。
4. 画布连线 `--edge-stroke:#6c8cff` 与强调色 `#8b93ff` 双蓝并存；toast/statusbar 仍真 blur(14px/12px)；登录页三处霓虹光斑。
5. 历史 bug：App.tsx 账户按钮用 UserCircle 但未导入（点击即崩）。

## 3. 已实施改动（P0 + P1）

### desktop/main.cjs
- BrowserWindow：`backgroundColor '#0a0e18'→'#0e0f14'`（与 --bg 一致）；新增 `titleBarStyle:'hidden'` + `titleBarOverlay:{color:'#16181f', symbolColor:'#a2a8b8', height:52}`（非 darwin）。
- 新增 IPC：`window:minimize / window:toggle-maximize / window:close / window:is-maximized(handle)`。
- `maximize/unmaximize` 事件推送 `window:maximized-changed` 给渲染层。

### desktop/preload.cjs
- contextBridge 暴露 `window:{minimize,toggleMaximize,close,isMaximized,onMaximizeChange}`。

### web/src/App.tsx
- 删除假窗栏（fw-windowbar + 三个死按钮 + Minus/Square/CloseIcon 导入）。
- 顶栏改 `.fw-topbar`（拖拽区），`paddingRight:150` 预留右上原生控件区。
- 品牌区保留 drag（内联 `WebkitAppRegion:'drag'`），其余子元素自动 no-drag。
- Splash 启动页整体 drag（可拖窗）。
- 补 UserCircle 导入（修复账户按钮崩溃 bug）。

### web/src/index.css
- 新增无边框拖拽规则：`.fw-topbar{app-region:drag;user-select:none}`、`.fw-topbar *{no-drag}`、`.lp-root{app-region:drag}` + 输入/按钮/卡片 no-drag（登录页可拖窗）。
- `--edge-stroke #6c8cff→#8b93ff`（连线统一强调色）。
- toast / statusbar 移除 backdrop-filter blur（改不透明分层）。
- 登录页三光斑收敛为单主光斑（LoginPage.tsx）。

## 4. 验证

- `node --check main.cjs / preload.cjs`：通过。
- `npm run build`（vite，2073 modules）：通过。
- 注：`tsc --noEmit` 报出若干**历史遗留类型错误**（FlowNode/Canvas/MarketPage 既有代码，vite/esbuild 不受影响，非本次改动引入，列为后续清理项）。

## 5. P2 待办（打包实测验收清单）

1. `desktop` 下 `npm run pack`（electron-builder --win nsis）。
2. win-unpacked 实测：
   - 无系统标题栏，右上原生控件可用且与顶栏同色（#16181f / 符号 #a2a8b8）；
   - 双击标题栏最大化、拖拽移动；
   - Win11 Snap 悬停可用；
   - 深/浅主题切换后 Overlay 颜色跟随（Overlay 颜色当前为固定深色，浅色主题下待评估是否联动）；
   - 登录页可拖窗、输入可交互；
   - 无白屏闪烁。
3. 已知边界：titleBarOverlay 颜色为静态值，若浅色主题下控件区域与顶栏色差过大，后续可经 IPC 动态 `win.setTitleBarOverlay()` 联动。

## 6. P2 实测记录（2026-10-03，本 DSH 宿主会话）

### 环境限制（如实）
- electron-builder 在本会话 **asar 打包步骤被杀**（app.asar 残留 0 字节目录，NSIS 安装包 0.2MB stub，"Exit code: null"）——与宿主进程模型/Job Object 回收一致（见 cloud_e2e_report 运维备注）。
- Electron GUI 在本会话无法启动（`FrameWeave.exe --version` 即退出 0x80000003）——宿主非交互桌面，非代码问题。
- 前端 vite build（2073 modules）、node --check 全部通过；tsc --noEmit 的历史遗留类型错误与本次改动无关。

### 已产出可运行验证产物
- `.tmp/eb_dir2/win-unpacked/FrameWeave.exe`（**asar:false 模式**，app/ 目录含 electron-updater 依赖 + 新前端 dist）——可直接双击运行验证无边框效果（不装安装包）。

### 用户本机验收步骤（正常桌面会话执行）
1. 开发验证（最快）：`cd desktop && npm start`（dev 模式：Vite 5180 + 后端 8788）→ 直接看窗口无系统标题栏、原生控件右上角、顶栏可拖、登录页可拖。
2. 正式打包：`cd desktop && npm run pack`（asar:true 正常环境；本会话 asar 写入被杀属环境特例）。
3. 验收清单（win-unpacked 或安装后）：
   - [ ] 无系统标题栏，右上原生控件可用且与顶栏同色（#16181f / 符号 #a2a8b8）；
   - [ ] 顶栏/品牌区可拖拽移动，控件可点击；
   - [ ] 双击标题栏最大化、最大化/还原状态正常（IPC 事件推送已接）；
   - [ ] Win11 Snap 悬停可用；
   - [ ] 登录页可拖窗、输入/按钮可交互；
   - [ ] 深/浅主题切换后顶栏与 Overlay 色差可接受（Overlay 为静态色，如需联动后续加 `win.setTitleBarOverlay` IPC）；
   - [ ] 无白屏闪烁（backgroundColor #0e0f14 已对齐 --bg）。
