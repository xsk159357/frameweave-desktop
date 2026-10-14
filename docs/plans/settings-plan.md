# 设置系统方案 v1（设置面板 / 拖线连接 / 亮色主题）

> 状态：待确认。对应 UI v1.2 霜幕体系，改动集中 web 前端，无需动后端。

## 1. 需求解读
1. 整体加「设置」入口：拖线连接开关、主题切换
2. 拖线开关：可从端口圆点牵线出来（可关闭）
3. 拖线未落到节点 → 自动弹出可链接列表（候选端口），点击直连
4. 主题：亮色 / 暗色（现状霜幕=暗色），可切换并持久

## 2. 设置系统架构

### 2.1 存储与状态
- localStorage key `fw_settings_v1`（Electron 持久，重启生效）
  - `connect.dragEnabled: boolean`（拖线连接开关）
  - `theme: 'dark' | 'light'`
- 前端全局 store（useAppStore 新增 `settings` slice：theme / dragEnabled + setter），App 启动时从 localStorage 恢复，变更时写回
- 不沉后端（纯前端偏好；如需跨设备同步后续加 GET/PUT /api/settings）

### 2.2 设置 UI
- 顶栏右侧新增「设置」齿轮图标按钮（ghost 样式，与「插件/商城」同组）
- SettingsPanel 弹窗（复用现有 panel 浮层体系，overlay 风格）：
  - 「编辑器」区：『从端口牵线连接』开关（SubRow 开关样式，同参数面板 toggle）
  - 「外观」区：主题两卡片选择（暗色 = 当前霜幕 / 亮色 = 新浅色，卡片内色块预览 + 选中 accent 描边）
  - 提示文案：设置自动保存
- 关闭弹窗即时生效，无需重启

## 3. 拖线连接（drag to connect）

### 3.1 现状
ReactFlow 原生已支持拖线（onConnect + 类型校验 portsConnectable/端口类型过滤）。需求本质 = 开关控制 + 落空弹出候选列表。

### 3.2 开关控制
- SettingsPanel 开关 → store → Canvas 读取 `dragEnabled`
- 关闭：ReactFlow 设 `nodesConnectable={false}`（全局禁拖线），保留已连边不受影响
- 开启：恢复 connectable，行为同现状（类型不匹配的端口不接受，沿用现有 compatCheck）

### 3.3 落空弹「可链接列表」
- ReactFlow `onConnectEnd`：拖线结束且未成功连接时触发（v12 签名 (event, connectionState)，含 source/sourceHandle 与落点坐标）
- 弹出**连接选择浮层**（锚定在落点附近，深色浮层样式）：
  - 搜索框过滤
  - 候选 = 所有其他节点的**类型兼容输入端口**（按 PortType 枚举 image/video/audio/text/number/bool/any 匹配，复用现有端口类型校验逻辑；兼容规则与 onConnect 一致）
  - 每项：节点标题 + 端口名 + 类型色圆点，分组按节点
  - 点击项 → 直接 addEdge（源端口 → 该项端口），关闭浮层；Esc/点外部关闭
- 拖到画布空白自动触发；拖到不兼容端口时也可触发（给出可链接替代项）
- 兼容性/找不到候选：空态提示「没有可链接的端口」

风险：ReactFlow v12 的 onConnectEnd 事件细节需实测（connectionState 结构），预留 0.2d 适配。

## 4. 亮色主题

### 4.1 双主题机制：CSS 变量覆盖（零组件改动）
- `:root` 保留暗色（霜幕）为默认；新增 `html[data-theme='light']` 变量覆盖集
- App 启动/切换时 `document.documentElement.dataset.theme = store.theme`
- 覆盖清单（新增亮色 token 值）：
  | token | 暗色（现） | 亮色 |
  |---|---|---|
  | --bg | #0e0f14 | #f4f5f8 |
  | --panel | #16181f | #ffffff |
  | --panel-2 | #1b1e27 | #eef0f4 |
  | --overlay | #20242f | #e6e9f0 |
  | --hover | rgba(255,255,255,.045) | rgba(0,0,0,.05) |
  | --accent | #8b93ff | #5b64d6（浅底深一档保对比）|
  | --accent-strong | #a2a8ff | #4a53c4 |
  | --accent-soft | rgba(139,147,255,.12) | rgba(91,100,214,.12) |
  | --accent-grad | 同现 | #5b64d6→#4a53c4 |
  | --text | #e8eaf1 | #1c1e26 |
  | --text-dim | #a2a8b8 | #4a4f5c |
  | --text-faint | #8a93a8 | #6d7384 |
  | --border | rgba(255,255,255,.08) | rgba(0,0,0,.1) |
  | --border-strong | rgba(255,255,255,.14) | rgba(0,0,0,.16) |
  | --success/danger/warning/cached | 同现 | 加深一档 |
  | --shadow-* | 深 | 浅（rgba(0,0,0,.12)）|
- 全部组件已 token 化（`--panel`、`--text-dim` 等），切主题即全局生效

### 4.2 硬编码清理清单（需改为 token 或条件）
用 grep 逐项清：
- Canvas 点阵 Background color #1a1d26 → 新 token --canvas-dot
- 画布 vignette radial rgba(0,0,0,.26) → --vignette
- 节点卡 shadow rgba(0,0,0,.38) / inset 高光 → 亮色变浅
- Timeline 播放头/片段 rgba(139,147,255,…) 已 token 化（accent 摘取）？核对
- 登录页 splash glow rgba(139,147,255,.38) → accent 变量取
- MiniMap/Controls 底色 → --panel-2
- Splash 进度条/背景 → token
- 更新面板/升级页残留深色 → 核对
（亮色工作量大头在此，约 0.5-1d）

## 5. 实施拆分与验收
| 阶段 | 内容 | 估时 |
|---|---|---|
| A | 设置面板 + useAppStore settings + localStorage 持久化 | 0.5d |
| B | 拖线开关 + onConnectEnd 连接列表浮层 + 候选端类型过滤 | 1d |
| C | 亮色主题：变量覆盖集 + 硬编码清理 | 0.5-1d |
| D | build → pack → VM 部署 → CDP 验证（三功能全链路）| 0.5d |

验收标准：
- 设置重启后保持；开关/主题即时生效
- 关拖线后端口不可牵线；开启原行为不变
- 拖线到空白 → 弹出兼容端口列表 → 点击创建连线成功（类型匹配）
- 亮色切换后：画布/节点卡/面板/时间线/登录页全部跟随，无深色残留块

## 6. 待确认
1. **拖线开关默认值**：默认「开」（保持现状可拖线）还是「关」？
2. **亮色主题节奏**：与设置面板/拖线同批交付，还是先做 A+B 验证后下一批做 C？
3. **设置是否需同步服务端**（跨设备/多机器）：默认本机 localStorage 即可？
