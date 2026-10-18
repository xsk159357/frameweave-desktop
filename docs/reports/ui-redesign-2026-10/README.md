# 拾帧 FrameWeave · 全量 UI / 布局重设计（2026-10）

- 日期：2026-10-18
- 设计系统：**墨织 INKWEAVE v2.0** —— `FrameWeave/docs/specs/design-system-inkweave.md`
- 覆盖面：桌面应用、安装器与图标、运营后台 `houtai.frameweave.top`、官网 `frameweave.top`
- 分册：`app/`（桌面端）、`admin/`（后台）、`site/`（官网）

## 1. 结果一览

| 交付面 | 状态 | 关键证据 |
|---|---|---|
| 桌面应用 | ✅ 重设计完成，构建通过 | `tsc --noEmit` 0 错误；`npm run build` 成功（CSS 58.23 kB / JS 603.48 kB）；18 张界面截图；`app/README.md` |
| 应用图标 / 安装器资产 | ✅ 全部替换 | `desktop/build/`：`icon.ico`（16–256 七档）、`icon.png`、`installer*` / `uninstaller*` 图标与 150×57、164×314 位图；exe 图标经 rcedit 写入 |
| 运营后台 | ✅ 整站重写并上线 | <https://houtai.frameweave.top/> 200，标题「登录 · 拾帧 FrameWeave 运营控制台」，无 JS 错误；源文件 `admin/admin.html`（163,337 B，MD5 `b642b87644b79f3303b5567295b8db06`）；9 张截图；`admin/README.md` |
| 官网 | ✅ 新建并上线 | <https://frameweave.top/> 200，Let's Encrypt 证书（2026-10-18 → 2027-01-16），无外部依赖，1440/1024/768/390 全通过；`site/deploy-report.md` |
| Windows 安装包重出 | ⚠️ NSIS 本机阻塞，portable 已验证 | portable 包构建成功并实机启动验证（新登录页）；NSIS 安装包需在干净机器/CI 重建（§5） |

## 2. 设计系统：墨织 INKWEAVE

| 维度 | 取值 |
|---|---|
| 底色 | `#0B0C0E` 画布 / `#101113` 面板 / `#15161A` 浮层 / `#22242A` 发丝线 |
| 文字 | `#EDE8DE` 主 / `#8B8F98` 次 / `#5E626B` 弱 |
| 强调 | 黄铜 `#D2A45E`（浅色主题 `#9A6B1F`），只用于主操作、选中、运行进度、品牌符号 |
| 语义 | 成功 `#6FA97F` / 警告 `#D2A45E` / 失败 `#C4695C` / 信息 `#7C93A8` |
| 字体 | 标题 Georgia；UI Inter / Segoe UI / PingFang SC；时间码与数值 JetBrains Mono |
| 形态 | 直角与 4–10px 圆角；1px 发丝线；**无**玻璃模糊、彩色发光、大面积渐变、胶囊堆叠 |
| 语言 | 取景框四角括号、帧格、时间码、细线网格 —— 来自摄影机与胶片，而不是霓虹 |

三端共用同一份令牌与排版规则：后台与官网都在各自文件内以内联 CSS 变量复刻，不引外部 CDN、不加载外部字体。

## 3. 各面改动摘要

### 3.1 桌面应用（`app/README.md` 详述）

- 新骨架：46px 顶栏 / 52px 图标栏 / 248px 工作流面板 / 命令条 + 无限画布 / 320px 检查器 / 26px 状态栏。
- `index.css` 整表重写（≈1000 行），33 个文件变更、+1212 / −1459。
- 节点库由常驻面板改为抽屉；插件悬浮球移除，改由顶栏与事件触发；节点卡的几何、端口、拖拽行为不变。
- 业务逻辑、IPC、API 契约零改动。

### 3.2 运营后台（`admin/README.md` 详述）

- 216px 石墨侧栏 + 52px 页头；仪表盘 6 KPI + 10 指标带 + 纯 CSS/SVG 近 14 天柱状图 + 构成条 + 到期提醒 + 最近审计 + 快捷操作。
- 登录页左右分栏，管理密钥可显示/隐藏、行内错误、失败限流提示；`/api/admin/*` 契约全部保留。
- 深色默认 + 浅色主题（`data-theme=light`，`localStorage.fw_admin_theme`，刷新前内联脚本防闪白）。
- 响应式：≥1280 双栏宽表 / <1100 收窄图标栏 / <860 抽屉侧栏 / <560 单列。
- 可访问性：跳转链接、可见焦点、`role=dialog` + 焦点陷阱 + Esc、`aria-live` Toast。

### 3.3 官网（`site/deploy-report.md` 详述）

- 单页深色编辑式站点：首屏 `FRAME / WEAVE` 大字、序列帧标题带、能力矩阵、工作流、界面、生态、下载、FAQ、页脚。
- 零外部依赖（无 CDN / 外部字体 / 外部 JS），11 个自托管资产。
- 下载 CTA：`https://shouquan.frameweave.top/update/FrameWeave-Setup-0.3.12.exe`，GitHub Releases 兜底。
- TLS：ACME http-01 签发，HSTS、gzip、图片 7 天缓存、`index.html` no-cache、安全响应头。

## 4. 验证矩阵

| 项目 | 方式 | 结果 |
|---|---|---|
| 桌面类型检查 | `web: npx tsc --noEmit` | 0 错误 |
| 桌面生产构建 | `web: npm run build` | 成功（仅 chunk >500 kB 提示） |
| 桌面集成自检 | `desktop: npm run integrity` | 8 个关键文件哈希写入 `integrity.json` |
| 桌面界面 | Playwright + Edge 无头，1440×900 / 1024×768 | 18 张截图，无 JS 报错、无横向滚动 |
| 生产包渲染 | 静态伺服 `web/dist` + 模拟后端 8788 截图 | 新设计正确加载 |
| 打包版实机启动 | portable exe 解压运行 + `PrintWindow` 抓帧 | 窗口「拾帧 FrameWeave」1400×900，后端就绪，新登录页渲染正常 |
| 后台线上 | `curl` + Playwright 实拍 | GET 200、163,337 B，MD5 与本地一致（`b642b876…`）、无 JS 错误 |
| 官网上线 | `curl -I` + Playwright 1440/1024/768/390 | 200、301→HTTPS、证书链有效、无 JS 错误、无横向滚动 |
| 回滚点 | 后台旧版备份 | `/opt/frameweave-cloud/backups/20261018233753/admin.html`（81,171 B） |

## 5. 打包现状

### 5.1 已通过：portable 包可构建、可运行

`npx electron-builder --win portable` 成功，且**实机启动验证通过**（2026-10-18 23:20）：

- 启动打包后的单文件 exe，NSIS 自解压后拉起 `FrameWeave.exe`（4 个 Electron 进程）与 `frameweave-backend.exe`；
- 窗口标题「拾帧 FrameWeave」，尺寸 1400×900，`PrintWindow` 抓取正常；
- 渲染的是新设计登录页，`LOCAL ENGINE READY`，说明本地后端与前端产物都正常 —— 见 `app/20-packaged-app-login.png`。

### 5.2 未通过：NSIS 安装包在本机无法产出

`electron-builder --win nsis` 在生成卸载器阶段失败：

```
• building        target=nsis file=dist\FrameWeave-Setup-0.3.12.exe
⨯ Exit code: 2. Command failed: ...\dist\FrameWeave-Setup-0.3.12.exe
failedTask=build
```

单独运行 electron-builder 生成的安装器桩，会弹出 NSIS 对话框
`Error writing temporary file. Make sure your temp folder is valid.`。

**已排除**：图标/位图格式、`package.json` 配置、中文路径（改用 ASCII junction `C:\fwbuild`）、
`TEMP`/`TMP` 指向（`C:\Temp`、`C:\T`、`C:\FWTemp`、`C:\FWTemp3`、用户临时目录）、默认资产回退、
重生成 BMP-DIB 图标、`npm run integrity` 状态。

**关键对照**：同一台机器、同一份产物，portable 目标能构建并运行，说明应用打包链路本身没问题；
失败点仅在 NSIS 安装器桩的执行（`BUILD_UNINSTALLER` 需要运行该桩导出卸载器）。
改动前的既有安装包 `release/0.3.12/FrameWeave-Setup-0.3.12.exe` 在本机运行同样报错，
可判定与本轮 UI 改动无关，属于本机执行环境对 NSIS 桩的限制。

**结论**：带新图标的 NSIS 安装包需在普通 Windows 桌面或 CI 上重建；本机交付 portable 包 + 解包目录。

### 5.3 本机产物（2026-10-18 23:17，与最终源码一致）

| 产物 | 路径 | 大小 / 校验 |
|---|---|---|
| portable 单文件 | `desktop/dist/FrameWeave-Setup-0.3.12.exe` | 205,979,583 B；MD5 `23d6c476ebd1ebbee6c86bdbb8442c7d`；SHA-256 `3ff33bab2afa581d5f8e6b0fabc03050cb9b9a0e5a8d07878072fd424111d575` |
| 解包目录（含新图标） | `desktop/dist/win-unpacked/` | `FrameWeave.exe` 189,119,488 B，图标已由 rcedit 写入 |

包内自检：`app.asar` 中的 `web/dist/assets/index-C8iXkHUl.js` 与 `integrity.json` 记录一致；
`win-unpacked/FrameWeave.exe` 的关联图标已提取验证为新标识（`app/19-exe-icon-32.png`）。

**在干净机器上重建（三步）**：

```bash
cd FrameWeave/desktop
npm run build:web    # 构建渲染层到 ../web/dist
npm run integrity    # 生成 asar 完整性清单（漏做会导致打包版自检失败、拒绝启动）
npx electron-builder --win nsis     # 产出带新图标的安装包
```

> 发布前注意：`latest.yml` / `.blockmap` 必须与安装包同步上传到 `shouquan.frameweave.top/update/`，
> 否则客户端自动更新会因哈希不匹配失败。现有线上安装包（224,390,663 B）保持原样未替换。

## 6. 已知限制与后续建议

1. **NSIS 安装包待重出**：见 §5.2。现有线上下载仍是旧 UI 构建；重新打包后需同步 `latest.yml` 与 blockmap。
2. **官网体积文案**：站内写「约 223 MB」对应现网安装包（214 MiB）；换包后需同步更新该数字。
3. **域名**：`www.frameweave.top` 未解析，官网只服务裸域 `frameweave.top`。
4. **后台 `HEAD` 请求**：`https://houtai.frameweave.top/` 对 `HEAD` 返回 501（后端未实现 HEAD），`GET` 正常；浏览器与现有探活不受影响，如需监控可改为 GET。
5. **可选打磨**：画布在 1440 宽下 `fitView` 缩放约 0.55，若希望默认更聚焦，可调 `minZoom` / `defaultViewport`。
6. **CI 建议**：把 `build:web → integrity → electron-builder` 固定为发布流水线，避免手工漏跑 `integrity`。

## 7. 文档与文件索引

| 内容 | 路径 |
|---|---|
| 设计规范 | `FrameWeave/docs/specs/design-system-inkweave.md` |
| 桌面端分册 + 截图 | `FrameWeave/docs/reports/ui-redesign-2026-10/app/` |
| 后台分册 + 截图 + 源码 | `FrameWeave/docs/reports/ui-redesign-2026-10/admin/` |
| 官网分册 + 截图 + 部署记录 | `FrameWeave/docs/reports/ui-redesign-2026-10/site/` |
| 官网本地源 | `FrameWeave/site/official/`（`index.html` + `assets/`） |
| 应用图标与安装器资产 | `FrameWeave/desktop/build/` |
