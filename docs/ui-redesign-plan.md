# 拾帧 FrameWeave · UI 重构完整方案 v1.0

> 定位：专业创作者 · 节点式 AI 视频创作工作台
> 路线：Linear（产品级克制）+ ComfyUI（节点语言）+ DaVinci 19（时间线深色）混合专业美学
> 日期：2026-10 · 状态：待批准实施

---

## 1. 市场调研（真实联网抓取，2026-10）

### 1.1 调研源与证据

| 源 | 抓取 | 关键信号 |
|---|---|---|
| linear.app | 产品页 54KB | 中性深灰背景 + 单一紫蓝强调 + 8 级字号体系（CSS 变量 title-8 系列），极简克制 |
| runway.com | 产品页 31KB | AI 创作工具同行：超大标题 + 大量留白 + 极简品牌文案 |
| capcut.cn（剪映） | 官网 | 消费级：功能矩阵极丰富，官网明亮多彩；专业版桌面 UI 实为深色三区 |
| ui.shadcn.com | 组件库 | 现代组件基准：16px 徽标圆角、1px 边框、text-xs 辅助、3px focus ring |
| awwwards.com | 获奖站 98KB | 全球网站风向：grid 系统 + 留白优先 |
| uisdc.com / zcool.com.cn | 社区 | 中文设计社区：AI 趋势/极光风在消费级仍活跃，专业级已收敛 |

### 1.2 调研结论（四条）

1. **产品级专业工具审美已收敛**：中性深色分层 + 单一强调色 + 大字号层级 + 1px 边框 + 克制动效（Linear / Runway / shadcn 三源一致）。
2. **节点式工作流语言**：同底色节点卡 + 端口色点 + 低调网格（ComfyUI 生态）。
3. **视频工具同行**：专业版一律深色三区（顶栏 + 素材/画布 + 时间线），时间线轨道深色细分割线（DaVinci 19 风格）。
4. **FrameWeave 应对标**：Linear × ComfyUI × DaVinci，而非剪映官网的明亮消费风。

---

## 2. 现状诊断（代码级）

### 2.1 底子（保留项）
- 设计 token 化彻底：颜色/圆角/阴影/动效全部走 CSS 变量（web/src/index.css :root）
- 语义色齐全（danger/success/running/cached）
- 登录页精致（双栏、光斑、网格）
- 三区布局骨架正确（顶栏/侧栏/画布/状态栏）

### 2.2 五个核心问题
1. **三色霓虹滥用**：蓝 #6c8cff + 紫 #a78bfa + 青 #4dd0e1 同时大面积用于背景光斑/品牌渐变/按钮/节点图标 → 视觉噪音、游戏化。
2. **玻璃拟态铺满**：顶栏/侧栏/工具栏/参数面板/时间线/结果/商城/右键菜单/弹窗全部 backdrop-filter blur(22px) → 层次拉平，无主次。
3. **字号层级弱、密度紧**：正文 12–13px、标题无 16–24 层级；顶栏一行 8 控件无分组。
4. **节点卡过时**：渐变图标方块（Web2.0 感）+ 无内部分层；缺低对比同底色语言。
5. **细节不统一**：圆角 10/14/16/18/22/999 混用；hover 位移（translateX 2px）有"抖感"；dark 三元为死代码。

---

## 3. 设计方向：「霜幕」专业创作美学

从"深空霓虹"降为"中性霜幕"：底色灰蓝、强调单色、玻璃仅浮层、内容成为主角。

---

## 4. 设计系统规范（token 级）

### 4.1 色彩系统（index.css :root 重写）

```css
:root {
  /* 背景四层 */
  --bg: #0e0f14;                         /* 画布底色 */
  --bg-grad: linear-gradient(180deg, #0e0f14 0%, #10121a 100%);
  --panel: #16181f;                       /* 顶栏/侧栏/面板 */
  --panel-2: #1b1e27;                     /* 次级面板/输入 */
  --overlay: #20242f;                     /* 浮层/菜单/弹窗 */
  --hover: rgba(255,255,255,.045);

  /* 强调：单一电感紫蓝（Linear 气质） */
  --accent: #8b93ff;
  --accent-strong: #a2a8ff;
  --accent-soft: rgba(139,147,255,.12);
  --accent-grad: linear-gradient(135deg, #8b93ff, #7a83f2);   /* 仅按钮/logo */
  /* 语义色（3 个） */
  --success: #4ade80; --danger: #f87171; --warning: #fbbf24;

  /* 文字三级 */
  --text: #e8eaf1; --text-dim: #a2a8b8; --text-faint: #6b7280;

  /* 边框 */
  --border: rgba(255,255,255,.08); --border-strong: rgba(255,255,255,.14);

  /* 圆角三档 */
  --r-sm: 8px; --r-md: 12px; --r-lg: 16px;

  /* 阴影（软） */
  --shadow-sm: 0 1px 2px rgba(0,0,0,.35);
  --shadow-md: 0 4px 16px rgba(0,0,0,.38);
  --shadow-lg: 0 16px 48px rgba(0,0,0,.5);

  /* 动效 */
  --t-fast: .16s; --t-ease: cubic-bezier(.25,.1,.25,1);
}
```

删除：--bg-panel/--glass/--glass-strong/--glass-hover/--glass-blur/--glass-border/--glass-inner/--cyan/--accent2/--brand-grad/--running/--cached/--cached-soft/--login-* 的霓虹/玻璃语义，全部并入四层体系。
改动面：index.css + 全部内联 style 中的 var(--glass*) / var(--bg-panel*) / var(--accent2) / var(--cyan) 引用。

### 4.2 字体层级

| 用途 | 字号/字重 | 颜色 |
|---|---|---|
| 页面标题 | 20px / 700 | --text |
| 区段标题 | 12px / 600 / uppercase + 0.8px 字距 | --text-dim |
| 正文 | 13px / 400 | --text |
| 辅助 | 12px / 400 | --text-faint |
| 快捷键 | 11px / mono | --text-faint |

### 4.3 间距系统（4px 基准 8px 节奏）

4 / 8 / 12 / 16 / 24 / 32 六档；卡片内 12–16，区段间 16–24，面板边距 16。

### 4.4 组件规范

| 组件 | 规范 |
|---|---|
| 主按钮 | accent-grad 或 accent 底色，12px/700，r-md，hover 提亮 4% |
| 次按钮 | panel-2 底色 + border，12px，r-md，hover 变 --hover |
| 输入框 | panel-2 + border，13px，focus 3px accent-soft ring |
| 菜单/弹窗 | overlay 底色 + border + shadow-lg，r-lg，进入 200ms 缩放 0.98→1 |
| 徽标 | r-sm（8px），panel-2 + border，11px/600 |
| 分段选择 | panel 底 + 滑片 accent-soft + 无霓虹阴影 |
| 列表行 | 12px 高，hover 仅变 --hover 背景（禁位移） |
| 滚动条 | 4px 细条，thumb --border-strong |

### 4.5 布局规范

| 区域 | 规范 |
|---|---|
| 顶栏 52px | 品牌区（logo+名+版本）｜工作流区（下拉+保存状态）｜账号区（会员/邮箱/退出）；组间距 24px、细分隔线 |
| 侧栏 248px | 与画布同底色，右侧 1px 分隔；搜索框 + 分类 + 节点行 |
| 画布工具栏 | 运行区/编辑区/文件区三分组，主动作 14px 加大 |
| 参数面板 328px | overlay 浮层 + 左侧 1px 分隔，固定头（标题/关闭）+ 固定底（操作区），中部滚动 |
| 时间线 200px | 轨道 panel-2 底 + 1px 分隔，播放头 1.5px accent 细线（DaVinci 风格） |
| 状态栏 28px | 左服务 / 中保存 / 右快捷键，12px faint |

### 4.6 节点卡（224 宽）

- 背景 --panel、边框 --border、r-md（12px）
- 图标：类别色 16px 圆角块（纯色，非渐变），白字首字母
- 标题 13px/650 单行省略；类别 pill r-sm 8px/600 faint
- 参数摘要：accent-soft 背景 + 单行省略（双击开面板保持）
- 端口：7px 色点 + 12px 标签；Handle 外圈 1px 描边，hover 放大 1.2
- 状态区：8px 圆点 + 12px（失败红 / 运行黄 / 成功绿）
- 选中：accent 边框 + 0 0 0 3px accent-soft
- 失败：红边框（v048 保留）；运行：整卡慢闪（v048 保留）

### 4.7 画布氛围

- 背景 --bg + 点阵 #1a1d26（22px）
- 连线 #3b4256 1.8px，选中/悬停 accent 2.2px
- MiniMap：--panel-2 底，无玻璃
- Controls：紧凑 32px 圆钮组（右下中）
- 视口角落 3% 透明 vignette

### 4.8 动效规范

- 一律 160ms 缓出；hover 只改背景/边框色，删除 translateX/Y 位移
- 运行闪动 fw-node-pulse 保留；登录页 lp-* 氛围动画保留
- 弹窗/菜单：200ms 缩放 0.98→1 + 淡入

### 4.9 品牌微调

- logo：单一电感紫蓝 + 内部白色取景框图形，r-md
- 登录页：主色光斑 1 处 + 网格保留

---


## 5A. 信息架构重排（用户新需求 v1.2）

### 5A.1 左侧：节点库 → 工作流列表
| 项 | 现在 | 改为 |
|---|---|---|
| 侧栏内容 | 节点库（搜索/分类/收藏/拖拽/点击添加） | **工作流列表** |
| 顶部 | 搜索框 | 「+ 新建工作流」主按钮 + 搜索框 |
| 列表项 | — | 名称 / 节点数 / 更新时间 / 状态点（运行中黄·有未保存圆点） |
| 项操作 | — | 右键：重命名 / 复制 / 导出 zip / 删除（确认框） |
| 空状态 | — | 「创建第一个工作流」引导（9.9 复用） |
| 数据源 | specs | api.listWorkflows()（已有）+ 定时刷新 + 保存后本地更新 |
| 与顶栏下拉 | — | 顶栏下拉保留做快速切换，侧栏承载完整管理 |

### 5A.2 节点添加：右键画布空白处
- **画布空白处右键 → 「添加节点」菜单**（ComfyUI 模式）：搜索框置顶 + 按类别分组（输入/语义/分析/控制/输出/用户节点）+ 每组节点行（图标+标题，hover 显示描述）
- 与现有节点右键菜单区分：**节点上右键 = 操作菜单**（运行/下游/时间线/重置/复制/删除，已实现）；**空白处右键 = 添加菜单**；两菜单顶部都保留「导入节点插件…」入口
- 保留拖拽添加（Sidebar 移除后改为从添加菜单/收藏）；双击节点 = 参数面板不变
- 位置记忆：按空白处坐标落点（复用 onAddNode 级联逻辑）

### 5A.3 插件系统前端补全（后端已就绪，前端缺口）
现状核对（代码证据）：
- 后端：`declarative.py`（manifest 声明式 + scan/install_zip/list_specs + 白名单安全模型）、`/api/user_nodes`、`/api/user_nodes/install`（zip 上传）、`/api/user_nodes/reload`、`/api/export/node/{type_id}`、商城安装接口——**全部已实现**
- 前端：api.ts 仅有 marketItems/marketPublish/marketInstall/exportNodeUrl，**无 user_nodes 方法、无导入 UI、无插件管理面板**

补全项：
1. **api.ts 补接口**：`userNodes()` / `installUserNode(file)`（FormData multipart）/ `reloadUserNodes()`
2. **插件管理面板**（顶栏「插件」按钮 → 弹窗）：
   - 已装插件列表（名称 / type_id / 版本 / 类别 / 节点数 / 导出 zip / 删除）
   - 「导入插件包…」文件选择（.zip）→ installUserNode → 重扫 → 列表刷新 + toast
   - 「打开插件目录」按钮（后端 reveal 机制复用）
   - 「插件规范」帮助链接 → docs/node-plugin-spec.md
   - 商城入口跳转（MarketPage kind=node）
3. **添加节点菜单整合**：用户节点/插件节点按 category 出现；缺失节点提示安装（9.2 错误态 + 商城查询）
4. **删除**：后端无 uninstall 接口 → 本期「删除」= 移除插件目录（新增 `POST /api/user_nodes/{type_id}/remove`，P2）

### 5A.4 插件规范与识别方法
完整规范见 [docs/node-plugin-spec.md](docs/node-plugin-spec.md)（新交付）：
- 包结构：`user_nodes/<pkg>/manifest.json` + 可选资源
- manifest：type_id（唯一 `pkg/节点名`）/ title / category / kind（transform）/ inputs / outputs / params / transform ops
- 识别：启动与 reload 时 scan() 扫目录读 manifest → 校验白名单 → 注册 NodeSpec → specs 下发
- 安装：zip 上传解压 / 商城付费 / 网盘直链；导出：/api/export/node/{type_id}
- 安全：白名单 transform op（concat/json_get/math/str/list/upper/lower/len），不执行任意代码

## 5. 页面级改动清单（文件 → 改动点）

| 文件 | 改动 |
|---|---|
| web/src/index.css | :root 重写（4.1）；lp-* 改单色；组件类（btn/input/menu/badge）按 4.4 归一 |
| web/src/App.tsx | 顶栏三分组、状态栏规范、toast 风格 |
| web/src/Sidebar.tsx | 同底色、行 hover 无位移、分类块规范 |
| web/src/Canvas.tsx | 工具栏分组、Controls/MiniMap 风格、右键菜单/确认弹窗 overlay |
| web/src/nodes/FlowNode.tsx | 节点卡 4.6（去渐变图标、同底色、端口规范） |
| web/src/ParamPanel.tsx | overlay 抽屉、头/底固定、表单组件规范、删 dark 死代码 |
| web/src/Timeline.tsx | 轨道深色规范、播放头细线 |
| web/src/ResultPanel.tsx | 列表行规范、删 dark 死代码 |
| web/src/MarketPage.tsx | 弹窗 overlay、删 dark 死代码 |
| web/src/LoginPage.tsx | 单色光斑、按钮规范 |

---

## 6. 实施计划

| 优先级 | 内容 | 工作量 | 验收 |
|---|---|---|---|
| P0 | 色彩系统落地（4.1）+ 删除玻璃/霓虹引用 | 1 天 | 全站无 --glass* 霓虹残留 |
| P0 | 节点卡（4.6） | 0.5 天 | CDP 几何截图 |
| P1 | 布局秩序（4.5：顶栏/侧栏/参数面板/工具栏） | 1 天 | 页面截图 |
| P1 | 排版动效（4.2/4.8）+ 删 dark 死代码 | 1 天 | 动效实测 |
| P1.5 | Logo 重绘 + Splash 启动层（7/8 章） | 1 天 | 截图 + 启动录屏 |
| P2 | 画布氛围（4.7） | 0.5 天 | 截图 |
| P2 | VM 部署 + CDP 全页面回归（登录/工作区/参数/商城/时间线） | 0.5 天 | 全部通过 |

总工期约 8 天（v1.2 新增侧栏工作流列表 / 右键添加 / 插件管理）（含评审补遗 10.2 状态系统 / 9.3 交互规范 / 9.6 时间线深化）。全部样式层改动，不碰业务逻辑。


## 7. 品牌 Logo 设计（v2）

### 7.1 图形概念（延续并现代化现有三层语义）
现状 SVG（取景框 + 播放三角 + 字幕条）概念正确，但被三色渐变光晕方块包装成霓虹风。新方案保留语义、重绘为**单色单形**：

| 方案 | 图形 | 特点 |
|---|---|---|
| **A（推荐）取景切角** | 圆角方形取景框，右下切角，内嵌播放三角，底部一条 2px「字幕线」贯通 | 帧=取景、三角=播放、字幕线=剪辑，一眼读懂"拾帧" |
| B 双帧叠印 | 两个错位圆角矩形 + 三角 | 叠化/帧序列语义 |
| C 极简单形 | 圆角矩形 + 三角（Linear 式） | 最克制，辨识度略弱 |

### 7.2 规范
- 配色：深色 UI 用 **电感紫蓝 #8b93ff** 单色（或白色描边版）；浅色场景用深 #2a2d3e 版
- 栅格：48×48 基础网格、安全区 4px、最小使用 20px
- 应用点：顶栏 20px ｜ 侧栏/空状态 48px ｜ 登录页 76px ｜ splash 76px ｜ 窗口/任务栏图标 ｜ 安装包图标（多尺寸 16–256）

## 8. 启动画面（Splash）设计

### 8.1 形态：同窗启动层（不新开窗口，避免双窗闪烁）
- main.cjs：BrowserWindow 底色改 #0e0f14，`show:false` + `ready-to-show` 后显示（现有 window 立即显示深色底，改造为等渲染就绪）
- 前端加载层（engineReady 驱动）即 splash

### 8.2 内容与动效
- 居中：logo（76px）→「拾帧 FrameWeave」（22px/700）→ slogan（13px faint）→ 底部 2px 细进度条（引擎拉起/工作流加载两段）→ 版本微标 11px
- 动效：logo 进场 600ms 上浮淡入 + 呼吸微光（保留 1 处品牌氛围动画，单色，非霓虹）；进度条 240ms 缓动
- 时序：engineReady=false → splash；true → 400ms 淡出进入工作区
- 兜底：轮询 40 次（约 20s）超时强制进入，splash 最长 20s

### 8.3 视觉对齐
splash 底色与 :root --bg 同步（#0e0f14），无玻璃无三色光斑，与新设计语言一致。


## 9. 评审补遗（v1.1 · 顶级设计师评审结论）

> 评审发现 v1.0 全是"静态视觉规范"，缺行为/状态/系统层。以下为必须补充项，按严重度排列。

### 9.1 字体系统（对应 4.2 补强）— 硬伤
```css
/* 中文字体栈（中文产品必须显式定义） */
--font-ui: "Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
--font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
/* 数字/时间码等宽（视频工具刚需，防跳动） */
.timecode, .duration, .ruler-num, .playhead { font-variant-numeric: tabular-nums; }
```
- 时间码格式统一 00:00:00（帧可选 :FF）；标题/区段标题用 600 字重 + 0.6–0.8px 字距。

### 9.2 状态设计系统（新增章节）— 硬伤
| 状态 | 规范 |
|---|---|
| **空状态** | 居中：48px 线性图标（faint）+ 14px 标题 + 12px 说明 + 1 个主操作按钮（如"拖拽节点到画布"）；空画布/空搜索/空时间线/空商城四场景统一 |
| **加载态** | 骨架屏（panel-2 底 + 呼吸 1.6s）优先于 spinner；引擎拉起用 splash 进度条 |
| **错误态** | 节点失败=红边框（v048）；面板错误=顶部红色横幅；toast err=左红图标；后端离线=状态栏红点+不可用禁用 |
| **禁用态** | opacity .45 + 去 hover；主按钮 disabled 不发光 |
| **在线/离线** | 状态栏左：绿点"服务正常" / 红点"服务离线" |

### 9.3 交互规范（新增章节）— 硬伤
- **快捷键表**：Ctrl+S 保存 ｜ Ctrl+Z/Y 撤销重做 ｜ Del 删除选中 ｜ Ctrl+C/V 复制粘贴 ｜ Esc 关闭面板/菜单/取消 ｜ ？快捷键面板 ｜ 空格拖画布
- **图标按钮必须 title/tooltip**（12px，延迟 300ms 显示）
- **拖拽反馈**：节点拖动时 shadow-lg + 边框 accent；无效连线目标红闪 + 光标 not-allowed；连线吸附成功 accent 光晕
- **Tab 顺序**：顶栏→侧栏→画布→面板；focus ring 统一 3px accent-soft

### 9.4 图标系统（新增章节）
- 尺寸档：12（状态/徽标）/ 14（工具按钮）/ 16（列表）/ 20（区段）；描边统一 strokeWidth 1.75
- 「图标+文字」组合：图标 14 + 文字 13，gap 6
- 语义图标色：操作=text-dim，危险=danger，成功=success（不泛用 accent）

### 9.5 z-index 分层表（新增）
| 层 | 值 | 元素 |
|---|---|---|
| 基础 | 1 | 画布/背景 |
| 浮动 | 10 | 参数面板/时间线 |
| 叠加 | 20 | 右键菜单/快捷键面板 |
| 弹层 | 30 | 确认框/商城/ResultPanel |
| 顶置 | 40 | toast |
| 遮罩 | 50 | 弹层遮罩 |

### 9.6 时间线组件深化（对应 4.5 补强）
- 标尺：10px 高，整秒刻度 + 半秒短线；时间码 00:00 tabular
- 播放头：1.5px accent 竖线 + 顶部 6px 三角手柄
- 片段：panel-2 底 + hover 边框微亮 + 选中 accent 边框；视频片段内显示缩略预览（占位渐变）
- 缩放：20–80px/s 连续；滚动跟随播放头

### 9.7 可访问性基线
- 对比度：正文 ≥ 7:1，faint 辅助 ≥ 4.5:1（--text-faint 提亮为 #8a93a8 防 11px 小字不达标）
- 语义双通道：状态/端口必须颜色+文字（已有标签，写为规范防回归）
- 键盘可达：所有操作有键盘路径

### 9.8 触控与窗口降级
- 最小点击区 ≥ 32×32（图标按钮 padding 补齐）；工具栏主按钮 ≥ 36 高
- 窗口 < 1180：参数面板改覆盖式（现有浮层已是）；侧栏可折叠（新增折叠按钮）
- 高 DPI：1px 边框用 0.5px 视觉宽度适配（transform: scaleY(.5)）——以实际渲染验收

### 9.9 空画布引导
首次进入（nodes 为空）：中央引导卡「从左侧拖入节点，或点击添加」+ 主操作"添加第一个节点"；可勾选"不再显示"（localStorage）。

### 9.10 验收指标量化（对应 6 章补强）
- 对比度实测（axe/Lighthouse 跑一轮）
- 动效时长实测（Performance 面板，≥60fps）
- focus ring 全控件可达
- 无 --glass*/霓虹残留（grep 断言）
- 全部页面 CDP 截图比对（含空态/错误态/离线态三态）

### 9.11 完善级清单
1. 侧栏 248px、面板 328px 固定（可后续支持拖拽调节，本期不做）
2. 四类选中态（节点/列表/侧栏/时间线片段）统一：accent 边框 + accent-soft 填充
3. 明确深色唯一主题（不排浅色），后续如需另立计划
4. 渐变仅按钮/logo；数据可视化（缩略图/资产预览）可保留渐变，UI 元素禁用
5. 品牌文案：按钮动词开头（保存/运行/删除），提示语 12px 陈述句，错误语 12px 含原因+动作

## 10. 风险与回归

- 风险低：token 变更已全部变量化，改 :root + 替换引用即可
- 回归：复用现有 CDP 验证链（fw_v3.ps1 机制）逐页截图比对
- 保留 .work 备份，可一键回滚
