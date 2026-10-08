# 拾帧 FrameWeave 项目深度分析报告（v0.2.12）

> 分析对象：F:\1223\解说工坊\FrameWeave（自研产品，全量源码逐文件阅读）
> 依据：backend/app 全部模块 + cloud_server.py + 10 个核心节点插件 + web/src 全部组件 + desktop 壳 + scripts 构建链 + docs/M1-M19 里程碑报告 + git 历史（HEAD f8d37fc）
> 声明：本文所有结论来自磁盘源码与文档，未运行程序；「观察」标注为代码审查意见，非已验证缺陷

---

## 0. 项目身份

| 项 | 值 |
|---|---|
| 产品 | 拾帧 FrameWeave —— 无限画布节点化 AI 视频创作桌面软件 |
| 形态 | Windows 桌面应用（Electron 33 + NSIS 安装器） |
| 商业模式 | 订阅制：QQ 邮箱 + 验证码注册 + 卡密激活绑定邮箱；1 账号 1 设备；无离线宽限（每次启动在线校验） |
| 当前版本 | **v0.2.12**（desktop/web/backend/cloud 四端同步） |
| git | main @ f8d37fc（2026-10-08），工作区干净，与 origin/main 同步 |
| 仓库 | github.com/xsk159357/frameweave-desktop + frameweave-plugins（官方插件） |
| 生产环境 | 授权服务 https://frameweave.ameaaos.com（雨云日本，OpenResty→127.0.0.1:8789）+ 管理后台 /admin/ |
| 成熟度 | M0–M19 全部完成，发布闭环（安装-更新-运行-事件-云端授权）已打通并上线 |

---

## 1. 总体架构（四层）

```
desktop/   Electron 壳：单实例锁 → 探测8788(复用/占用/空闲) → 拉起后端 → 加载前端 → 自动更新
web/       React18 + @xyflow/react12 无限画布 + Zustand（登录门禁 → 画布 → 面板）
backend/   FastAPI 本地服务（engine 执行引擎 + 插件 + 资产 + 授权 + 商城），PyInstaller 打包
cloud/     cloud_server.py 云端授权（SQLite + 邮箱验证码 + scrypt + 卡密 + 限流 + 后台）
能力层     LLM(ameaaos 中转) / Edge-TTS / faster-whisper(ModelScope) / IndexTTS2(8791 独立venv) / ffmpeg
```

---

## 2. 后端核心机制（backend/app/）

### 2.1 执行引擎 engine.py（DAG）
- **校验**：三色 DFS 环检测
- **调度**：Kahn 拓扑 → 同层批次并行（asyncio.gather）
- **运行模式**：all / selection(仅选中) / upstream(运行到选中) / downstream(仅重算下游)
- **增量缓存**：
  - 缓存键 = SHA256[ type_id + version + 参数JSON + 各输入端口资产id ]（哈希前 32 位）
  - 持久化 cache_index.json，上限 2000 条，LRU（命中刷新 ts），孤儿（资产已删）启动清理
  - 命中 → 状态 cached、复用 asset_ids、跳过执行
- **状态机**：pending → queued → running → success/failed/cancelled + cached
- **容错**：失败/取消回滚本次落盘资产（saved_ids 跟踪删除）；失败隔离不拖垮兄弟分支；run_history 保留 20 条
- **执行语义细节**：参数/输入变化 → cache_key 变化 → SUCCESS 节点自动重置重算（不用手动重置）

### 2.2 节点协议（nodespec/ports/registry/declarative）
- PortType 13 种：VIDEO/IMAGE/AUDIO/SCRIPT/SUBTITLE/SEGMENTS/TIMELINE/JSON/STRING/INT/FLOAT/BOOL/ANY
- can_connect：ANY 万能；JSON 可宽连（→SCRIPT/SEGMENTS/SUBTITLE/STRING）；媒体类型严格（VIDEO 不可直连 IMAGE，需显式抽帧节点）
- **三类节点来源**：
  1. transform 声明式（manifest.json + 白名单 ops：concat/json_get/math/str/upper/lower/len/list/get——纯数据变换，不执行代码）
  2. Python 插件（manifest runtime=python + entrypoint node.py + class_name，importlib 动态加载，路径校验防越界）
  3. （历史）内置 app.nodes 已全部迁移为官方插件
- **zip 安装安全**：zip-slip 防护（规范化路径禁 ../ 逃逸、禁绝对路径/盘符）、50MB 上限、安装后重扫

### 2.3 资产库 assets.py
- 双目录落盘（files/ + meta/ 元数据 JSON），结构化数据写 JSON、媒体复制文件
- 指纹 = 前 8MB + 文件长度 SHA256
- prune_orphans 启动清理无引用孤儿资产（容忍损坏元数据）

### 2.4 授权 license.py + cloud_gateway.py
- 卡密协议：`FW-<b64(payload)>-<hmac-sha256[:12]>`，payload={plan,days,serial}；本地桩 dev 密钥，正式密钥只在云端
- 状态机：plan(trial/member) + expires_at + device_id + credits + cards_used(防重复)
- **云端优先、无离线回落**：cloud_enabled() 时 login/verify/activate 全部走远程，不可达即拒绝服务（产品决策）
- 设备绑定：新设备登录 → 旧设备 device_kick 下线；客户端 30s 周期在线校验

### 2.5 商城 market.py（积分经济）
- 条目 kind=node|workflow；price 积分；官方 0 价
- 下载付费：扣费 1 次永久可下；20% 平台 / 80% 作者入账（作者不可提现）
- 免费插件：download-auth 返回 GitHub Release 直链（服务器不代理大文件）+ SHA-256 + size + min_client_version
- 客户端流程：download-auth → GitHub 直链下载 → SHA-256 校验 → 本地 zip 安全安装 → 重扫 → install-report 回报

### 2.6 密钥保险箱 secrets.py
- ctypes 调 Crypt32 DPAPI（CryptProtectData/UnprotectData），绑定当前 Windows 用户，换机/换用户不可解
- 原子写入（tmp + os.replace）；工作流保存时 key/secret/token/password/api 字段自动脱敏为 @secret: 引用

### 2.7 本地 API 鉴权（server.py middleware）
- 保护面：/api/secrets*、/api/export/*、/api/assets/* 全部 + 写操作（workflows/market/user_nodes/util/draft）
- /api/auth/*、health、/ws 开放（登录前置必须可达）
- 打包版随机令牌经 FRAMEWEAVE_LOCAL_TOKEN 注入，dev 回落常量；CORS 仅 localhost:5180 + null

---

## 3. 核心节点（10 个官方插件）

| type_id | 分类 | 关键实现 |
|---|---|---|
| core/load_video | 输入 | ffprobe 解析（时长/分辨率/fps/编码），无 ffprobe 降级文件信息 |
| core/scene_detect | 分析 | ffmpeg 抽帧 + opencv 灰度直方图 Bhattacharyya 差 / 纯 Python 兜底；输出 SEGMENTS |
| core/ai_narrate | 语义 | 4 模式：上帝视角(vision 抽帧 base64 送 LLM + 片段摘要)/扩写/配画面(文案-片段匹配 JSON)/仿写 |
| core/tts | 音频 | 4 引擎：edge(43 音色)/manual(用户音频)/indextts(本地克隆,自动拉起 8791 服务)/cloud(OpenAI 兼容 /audio/speech) |
| core/asr | 分析 | faster-whisper（ctranslate2），ModelScope 下载 large-v3 |
| core/subtitle_compose | 输出 | 花字/描边/字号/动画样式 → 剪映 style 字段映射 |
| core/video_render | 输出 | ffmpeg filter_complex：trim+scale+pad+concat/xfade 转场(7 种)+ASS 字幕烧录+aac 混音；16:9/9:16/1:1，1080p/720p/4k |
| core/batch_render | 控制 | 矩阵批量：`名称|文案` 行 + {{变量}} 模板替换 + Semaphore 并发 + 进度上报 + 失败隔离 |
| core/draft_export | 输出 | 剪映草稿 draft_content.json（3 轨+素材注册+转场 id 表）+ Pr XML + EDL + FCPXML 1.9 四格式 |
| core/translate | 语义 | 18 语种文案/字幕翻译，字幕保留时间轴编号回填 |

**一条龙模板拓扑**（8 节点 15 边）：
load_video → scene_detect → ai_narrate → tts（配音）‖ asr → subtitle_compose → video_render + draft_export 双输出

---

## 4. 前端设计（web/src）

### 4.1 画布交互（Canvas.tsx 51KB，核心组件）
- 拖线落空 → 弹**可链接列表**（三层端口类型解析：spec → handle title → 放弃）
- 牵线到空白 → **自动新建节点并连线**（Blender/Figma 式，按源类型过滤兼容输入）
- 复制/粘贴（重生成 id + 内部连线映射）、撤销/重做 40 步、导出/导入 .fwflow.json
- 整列垂直重排（内镶参数展开防遮挡，收起缝合，手动拖动节点作障碍保留）
- 快捷键：Ctrl+S/Z/Shift+Z/C/V、Delete、双击参数面板、右键菜单（单独运行/运行下游/查看时间线/重置/删除）

### 4.2 节点卡（FlowNode.tsx）
- 类型色端口（VIDEO 红/AUDIO 青/SCRIPT 绿/SEGMENTS 蓝…）+ 摘要行 + 状态区（错误/进度条/参数差异高亮/状态徽章 + 输出数）
- 内镶参数区（展开/收起，secret 字段 password 显示）；高度恒定防穿模

### 4.3 状态与反馈
- store.ts：Zustand，WS 事件 → nodeStatus/nodeError/nodeProgress/nodeAssets
- 订阅门禁：登录页（QQ 邮箱验证码/注册/找回密码，双主题）→ 登录成功才加载主界面
- 订阅过期 → **只读受限模式**（不退出登录，操作弹提示）；30s 在线校验；单端互踢
- 结果面板（批量）：成功/失败列表 + 定位文件(explorer /select) + 失败重试(selection)
- 时间线 v0：多轨只读预览 + 播放头 + 缩放（字幕轨标注 M1 接入，属待办）

### 4.4 设计系统
- 霜幕：暗色默认 + 亮色，全部 CSS token 化（--panel/--accent/--text-* 三级/圆角三档/软阴影）
- 无边框窗口（titleBarStyle:hidden + titleBarOverlay），主题切换联动原生控件配色

---

## 5. 云端授权服务器（cloud_server.py，单文件 35KB，零第三方依赖）

| 面 | 实现 |
|---|---|
| 表 | sessions / email_codes / mail_audit / send_log / rate_limits / admin_tokens / cards / market_downloads / market_installs |
| 密码 | **scrypt** N=16384 r=8 p=1 dklen=32（`scrypt$N$R$P$hex`），旧 SHA256 登录后渐进迁移 |
| 验证码 | 6 位数字、5 分钟、错误 5 次失效、成功即失效、60s 冷却、每日 10 次；SQLite 持久化（重启仍有效） |
| 限流 | 验证码：IP 20/h、设备 10/h、全局 30/min；登录：IP 20/5min、账号失败 5 次锁 10 分钟 |
| 管理 | 密钥登录（FRAMEWEAVE_ADMIN_KEY）+ 8h token + IP 白名单 + 登录锁；签发卡密(月/季/年,≤50张)/用户加时/禁用/审计/卡密作废 |
| 商城 | market_items.json 元数据 + download-auth（记账+GitHub 直链）+ install-report |

---

## 6. 发布链路（scripts/）

1. **build-backend.ps1**：PyInstaller 重建后端（spec：排除 torch 4.12GB，hiddenimports 全量，datas=user_nodes+voice_clone），校验 exe ≥10MB 且为本轮新产物
2. **build-installer.ps1**：vite build → 认证路由契约检查 → electron-builder --dir(asar:false) → 手写 app-update.yml → NSIS 模板注入 → makensis；全程 EAP=Continue+$LASTEXITCODE 防 NativeCommandError 假失败
3. **publish/release-to-github/check_release.ps1**：自签 pfx 签名(SHA256+RFC3161 时间戳) → 三件套(exe+blockmap+latest.yml) → sha512 校验 → GitHub Release
4. **package-official-plugins.ps1**：核心节点打包 zip + SHA256 + 商城元数据 JSON（min_client_version=0.2.12）
5. **update-cloud-auth.ps1**：SSH 备份远端 → 上传 → py_compile → systemctl restart → health + send-code 自检
6. **verify-vm.ps1**：VMware 全链路验收（卸载/静默安装/启动/CDP 样式断言）

版本真源：desktop/package.json（vite define __APP_VERSION__ 注入前端；server.py APP_VERSION / cloud_server VERSION / preload 同步）

---

## 7. 里程碑与当前状态（M1–M19 完成）

- M1 文案→配音→字幕→导出闭环｜M2 混剪+批量+首个安装包｜M3 模板变量+增量缓存+IndexTTS2+后端捆绑｜M4 FCPXML+一条龙+DPAPI+翻译｜M5 并发(30x)+自动更新+草稿校验｜M6 进度条+结果面板｜M7 节点右键｜M8 签名+发布脚本｜M9 参数差异高亮｜M10 v0.2.0+端口冲突+真实更新闭环｜M11 运行历史｜M12 v0.2.1+失败历史｜M13 冒烟测试(抓 3 真 bug：WS 事件丢失/Props 崩溃/重置按钮)｜M14 v0.2.2 发布+WS 回归｜M15 真实安装+自动更新端到端｜M16 声明式节点｜M17 授权升级｜M18 商城｜M19 云化对接位｜平台化：官方节点插件化 + 商城自动安装协议 + 云端 E6-E10 加固 → **v0.2.12**

**当前唯一未闭环项**：新 VM 内插件商城端到端回归（登录→商城→download-auth→GitHub 下载→SHA-256→自动安装→install-report→节点运行）——download-auth/install-report 接口与云端元数据均已就绪，只差 VM 实机走一遍。

**已决策暂不做**：OV 代码签名证书、首次分发、OSS 分流、付费插件、UI 重构实施（调研完成待批准）。

---

## 8. 安全盘点（已落地）

| 面 | 措施 |
|---|---|
| 本地服务 | 随机本地令牌 + 中间件保护敏感面；CORS 收窄；统一异常 422/400/500 不泄露 |
| 密钥 | DPAPI 绑定用户；工作流密钥自动脱敏 @secret: |
| 授权 | HMAC 卡密 + scrypt + 设备互踢 + 无离线宽限 + 多维度限流 + 管理 IP 白名单 |
| 插件 | zip-slip 防护 + manifest 校验 + transform 白名单（不执行代码）+ SHA-256 + 50MB 上限 |
| 壳 | contextIsolation + nodeIntegration:false + 单实例锁 + 端口占用检测 + 启动失败即退出 |
| 云端 | 验证码 5 次错失效 + 每日 10 次 + IP/设备/全局限流 + 管理密钥轮换记录 |

---

## 9. 代码观察（审查意见，非已验证缺陷）

1. **engine.py** `_run_one` 中 `for port_name, spec_p in zip(spec.outputs, spec.outputs)`：zip 同一对象两遍，冗余但无害，可简化为遍历 spec.outputs。
2. **engine.py** `for nid, ok in zip(to_run, results): pass`：空循环残留，可删除。
3. **engine.py** `_cache_key` 对 JSON 参数用 `json.dumps(params, sort_keys=True)` 整体序列化后哈希——参数含 @secret: 引用时缓存键变化可能导致重复请求 LLM，属设计权衡（引用值不变则无影响）。
4. **scene_detect** 采用亮度/直方图差分（轻量、无模型依赖），文档已标注为 M0 算法；如需更高准确率可后续接 TransNetV2（项目已预留参照）。
5. **Timeline.tsx** 为只读预览，字幕轨/波形/裁剪属 M1 待办——PRD 的 FR-08~FR-12 未全部落地（记录在案）。
6. **cloud_server.py** 单文件承载全部逻辑 + 内嵌管理 HTML——可维护性随功能增长会下降，建议后续按模块拆分（不阻塞当前）。
7. **本地桩与云端并存**：market.py/license.py 保留本地桩分支，云端不可达时商城读回落本地桩（授权不回落）——商城读降级是合理设计，注意本地桩数据与云端不一致时的展示（有 market_log 留痕）。

---

## 10. 结论

FrameWeave 是一个**架构完整、工程纪律强、已生产上线**的自研商业软件：七条链路（画布/引擎/插件/资产/授权/商城/发布）全部闭环且有文档沉淀，安全设计达商业分发水准，里程碑交付均有端到端验证证据。当前工程重心应为：① 补齐 VM 商城回归（唯一未闭环）；② 按待办清单推进 UI 重构（方案已就绪）；③ 视用户决策处理 OV 签名与付费生态。代码层面的观察项均为轻量清理/增强，不构成阻塞。
