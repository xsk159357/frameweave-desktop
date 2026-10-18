# FrameWeave 全量审阅报告（2026-10-19）

> 范围：工作区 `F:\1223\解说工坊` 全量 + `FrameWeave` 仓库（main @ ed63a14）源码逐模块通读 + `frameweave-plugins` + 构建/发布/CI/文档链。
> 方法：静态通读 + 实测命令（pytest / tsc / sdk 校验 / git / 体积统计 / 目录扫描）。凡标「实测」的均有本轮命令输出，未执行项在 §12 单列。
> 前序基线：`docs/reports/FrameWeave_项目深度分析报告.md`（v0.2.12，已过期 10 个版本）；本报告以 0.3.12 现状为准。

---

## 0. 结论摘要

1. **产品层面已闭环**：0.3.12 三件套齐备并已双通道发布（GitHub + 韩国镜像），四端版本号一致（desktop/web/preload/server/cloud_gateway/cloud_server 全为 `0.3.12`），源码树 `git status` 干净。
2. **架构是四层 + 两独立体**：Electron 壳 / React 画布 / FastAPI 执行引擎 / 云端授权；插件与工作流已从平台仓库剥离为独立 `frameweave-plugins`。平台侧 `backend/user_nodes` 保持空壳（`.gitkeep`）。
3. **工程化程度高于同类自研项目**：worker 子进程隔离 + 五作用域权限门 + manifest v2 G1-G10 校验 + Ed25519 插件签名 + PyArmor/PyInstaller + 启动完整性自检 + CI 门禁，均已在代码里落地，不是文档承诺。
4. **当前门禁不是全绿**：实测 pytest `109 用例 / 102 通过 / 7 失败`——2 项健康治理（测试残留 + cloud.db 未移出）、5 项 ws（本机缺 `websockets`）；`tsc --noEmit` 0 错误；插件 `check_all` 70 包 0 失败。
5. **最高优先级的不是代码缺陷而是两件事**：`.tmp` 里仍有 25+ 份含明文 root 口令的部署脚本（审计 A12 遗留未处置）；仓库内存在一个指向自身的 junction，导致任何递归扫描把 77.9 GB 重复计入。

---

## 1. 身份与版本状态（实测）

| 项 | 值 |
|---|---|
| 产品 | 拾帧 FrameWeave —— 无限画布节点化 AI 视频创作桌面软件 |
| 形态 | Windows 桌面（Electron 33 + NSIS），后端 PyInstaller + PyArmor 混淆 |
| 版本 | **0.3.12**（desktop/web/backend/cloud 四端一致，实测 grep） |
| git | `main` @ `ed63a14`（2026-10-19 00:37），135 commits，工作区干净 |
| 与远端 | **领先 origin/main 2 个提交，未推送** |
| tag | 本地 11 个、远端 14 个；远端已有 `v0.3.5/v0.3.11/v0.3.12`，**缺 `v0.3.6–v0.3.10`**（无独立提交，见 P1-6） |
| 发布产物 | `release/0.3.12/`：exe 224,370,119 B + blockmap 236,087 B + latest.yml（sha512 一致） |
| 商业模式 | 订阅制：QQ 邮箱验证码 + 卡密激活 + 1 账号 1 设备 + 30s 在线校验，无离线宽限 |

---

## 2. 工作区地图（体积为实测，已排除 junction 重复计入）

工作区真实占用 ≈ **110 GB**（`FrameWeave` 77.9 GB + `jieshuomao` 25 GB + `_archive` 11 GB + `Python` 0.2 GB + 其余）。

| 条目 | 体量 | 职责 | 备注 |
|---|---:|---|---|
| `FrameWeave/` | 77.9 GB | 主项目（git） | 其中缓存/产物占大头，见下 |
| `jieshuomao/` | 25.0 GB | 解说猫 v5.4.6 商业参考软件（非 git） | 竞品/需求参照，含 python312 + ffmpeg |
| `_archive/` | 11.0 GB | 归档区（只进不出） | 单批 `2026-10-17-b3-tts-demucs` 占 8.2 GB |
| `frameweave-plugins/` | 4.5 MB | 插件独立仓库 | 70 包源 + 73 zip + 70 sig |
| `docs/` | 5.6 MB | 工作区文档中心（s1–s17 + reports） | |
| `_recon/` | 78.7 MB | jieshuomao 侦察产物（r1–r5） | |
| `Python/` | 224 MB | 嵌入式 Python 3.14 工具链 | 后端打包运行时 |
| `release/0.3.0/` | 215 MB | 0.3.0 三件套 | 与 `FrameWeave/release/0.3.0` 重复 |
| `ComfyUI/` | 69 MB | 上游框架克隆 | 仅作参考/转换兼容 |
| `.tmp/` | ≈270 MB | 运维/侦察脚本 + 保留数据 | **内含指向仓库根的 junction**（§10-P0-2） |

`FrameWeave/` 内部构成（实测）：

| 子项 | 体量 | 说明 |
|---|---:|---|
| `backend/` | 24.6 GB | `data/models` 18.0 GB（index-tts-code 17.5 GB）+ `.venv` 5.4 GB + `packaging` 1.0 GB |
| `.uvcache/` | 21.3 GB | torch/scipy/sympy 等 wheel 解包缓存 |
| `.tmp/` | 11.0 GB | pip-unpack 残留、installer-build、p4-pentest、eb_out_022 等历史构建目录 |
| `.wheels/` `.cache/` `.pipcache/` `.dl/` | 13.2 GB | 安装器缓存 |
| `desktop/` | 3.8 GB | `dist/win-unpacked` 740 MB + node_modules 544 MB |
| `release/` | 3.2 GB | **15 个历史版本**目录（0.2.9–0.3.12） |
| `web/` | 118 MB | src 7.5 k 行 + node_modules |

---

## 3. 代码架构

```
desktop/  Electron 壳（main.cjs 582 行 + preload.cjs 44 行）
          ├ 单实例锁 → 探测 8788（本服务/他占/空闲）→ 拉起后端 → 加载 web/dist
          ├ 打包期生成随机本地令牌 → 经 additionalArguments 注入渲染进程
          ├ asar integrity 自校验（integrity.json，8 个关键文件 sha256）
          └ electron-updater 双源回退（韩国镜像 → GitHub），单次检查 25s 超时
web/      React18 + @xyflow/react12 + Zustand（7,490 行 / 30 文件）
          ├ 登录门禁 → 画布（Canvas 1264 行）→ 面板（参数/结果/时间线/资产/队列/模型/商城）
          └ 30s 在线校验 + 订阅过期只读模式 + 单端互踢
backend/  FastAPI 本地服务（75 条路由，16,687 行 / 34 模块）
          ├ engine.py        DAG 校验（三色 DFS）/ Kahn 拓扑 / 同层并行 / 增量缓存 / 状态机
          ├ engines.py       executor 装配：WorkerExecutor（默认）/ TrustAware / 进程内
          ├ worker/          supervisor + host_bootstrap + rpc(JSONL) + perm(五作用域) + ns + jobs + pool
          ├ plugin/          manifest_v2（G1-G10）+ deps + envcheck + errors
          ├ declarative.py   声明式 transform 节点（白名单 ops，不执行代码）+ zip 安全安装
          ├ registry/nodespec/ports/slots/graph_validator   节点契约与图契约
          ├ assets/batchlib/queue/wfstore/templates/comfy_convert  资产·批量·队列·版本·模板·Comfy 互转
          ├ license(死)/cloud_gateway/authz/signatures/integrity/obf/secrets  授权与加固链
          └ market/payment/model_download/llm/llm_config/providers/email_service  商业与能力层
cloud     cloud_server.py（890 行，零第三方依赖，stdlib ThreadingHTTPServer）+ admin.html（844 行）
plugins   frameweave-plugins：70 包（68 python 运行时 + 1 声明式 transform + 1 未标 runtime）
```

数据流（一次运行）：前端建图 → `POST /api/workflows/{id}/run` → `graph_validator` 契约校验 → `engine` 拓扑调度 → 每节点按信任级路由到 worker 子进程（默认）→ `worker/rpc` 受控出口（fs/network/subprocess/secret/env）→ 节点产出落 `AssetStore`（files + meta 双目录 + 指纹）→ WS 事件回推前端（nodeStatus/progress/assets）→ 缓存键 `sha256(type_id+version+params+inputs)` 命中则 `cached`。

---

## 4. 后端模块清单（行数实测，Top 20）

| 模块 | 行数 | 职责 |
|---|---:|---|
| `server.py` | 2,403 | 75 路由 + 本地令牌中间件 + 统一异常（422/400/500）+ WS |
| `engine.py` | 976 | DAG 引擎：校验/调度/缓存/回滚/子图嵌套/ExecutionBlocker |
| `plugin_registry.py` | 878 | 插件状态机（absent/active/disabled/quarantined/updating）+ 安装事务 |
| `declarative.py` | 741 | 声明式节点 + zip 安装（zip-slip 防护/50 MB 上限/白名单 ops） |
| `engines.py` | 738 | executor 层：Worker / TrustAware / 进程内 / 缓存键 |
| `plugin/manifest_v2.py` | 676 | manifest v2 强校验 G1-G10 |
| `plugin/deps.py` | 556 | 统一依赖解析（ffmpeg/模型目录） |
| `worker/supervisor.py` | 538 | worker 托管：spawn / Job Object / 超时取消 / stderr 落盘 |
| `nodes/draft_export.py` | 521 | 剪映草稿 + Pr XML + EDL + FCPXML |
| `signatures.py` | 484 | Ed25519 + JCS 规范化 + 信任锚 + 撤销 |
| `worker/perm.py` | 400 | 五作用域权限门（默认 deny-all） |
| `cloud_gateway.py` | 377 | 云端网关：重试 + HMAC 响应验签 + 拒绝离线回落 |
| `templates.py` | 360 | 模板注册与导入 |
| `nodes/tts.py` | 355 | TTS 多引擎（edge/indextts/cloud/manual） |
| `worker/host_bootstrap.py` | 354 | worker 进程入口（仅 stdlib + worker.*） |
| `license.py` | 328 | **DEPRECATED 死代码**（纯云化后无路由调用） |
| `market.py` | 320 | 商城：download-auth / install / report / 积分分成 |
| `plugin/envcheck.py` | 304 | 首次运行环境检查 CLI |
| `authz.py` / `integrity.py` / `obf.py` | — | 分层授权降级 / 打包启动自检 / 敏感常量 XOR 混淆 |

关键机制（读码确认，非文档转述）：
- **执行隔离默认开启**：`FW_PLUGIN_EXEC` 留空 = worker；`inprocess` 仅显式降级，且存在 L2 无签名包时逐节点强制 worker（`engine.py:225-241`）。
- **权限门默认 deny-all**：插件未在 manifest 声明即零权限；fs 出口做 realpath + roots 边界 + 拒绝 `..`/符号链接/盘符（`worker/perm.py`）。
- **导入白名单**：worker 内 `import socket/urllib/requests` 一律拒绝（`worker/ns.py` meta_path finder + `__import__` 钩子）。
- **缓存键**含 type_id + version + 参数 JSON + 上游资产 id，参数变化自动触发 SUCCESS 节点重算。
- **本地 API 鉴权**：`X-FW-Local-Token` 保护 secrets/export/assets 全部 + 写操作；auth/health/ws 开放（`server.py:122-158`）。

---

## 5. 前端与桌面壳

| 文件 | 行数 | 备注 |
|---|---:|---|
| `Canvas.tsx` | 1,264 | 画布主体：拖线落空弹可链接列表、牵线到空白自动建节点、复制粘贴、撤销 40 步、整列重排 |
| `MarketPage.tsx` | 733 | 商城：发布表单、导出、安装报告 |
| `ParamPanel.tsx` | 427 | 参数面板 + 历史失败记录 |
| `App.tsx` | 415 | 登录门禁 / 10s 健康轮询 / 30s 在线校验 / 只读模式 / 更新状态 |
| `api.ts` | 394 | 全部 API 封装 + 本地令牌注入 |
| `index.css` | 48 KB | 全量 token 化（暗/亮双主题 + INKWEAVE 设计系统） |

- 桌面壳：单实例锁、端口占用判定（本服务/他人/空闲三态）、后端 20s 等待、失败弹窗退出、随机令牌注入、asar 完整性校验、双源自动更新回退。
- 类型健康：`npx tsc --noEmit` → **exit 0**（实测）。
- 观察：Canvas 单文件 74 KB / 1264 行、index.css 单文件 48 KB，属于典型的「先跑通后拆分」形态，功能演进会持续抬高改错成本。

---

## 6. 插件生态（实测）

- **包数**：70（`packages/`），分类：语义 13 / 剪辑 12 / 分析 11 / 输出 11 / 音频 9 / 控制 6 / 输入 5 / 用户节点 3。
- **契约**：`schema_version: 2`、`engine_api: 1.0`，端口 13 类型（VIDEO/IMAGE/AUDIO/SCRIPT/SUBTITLE/SEGMENTS/TIMELINE/JSON/STRING/INT/FLOAT/BOOL/ANY）。
- **权限声明分布**：`subprocess` 33、`fs` 24、`secret` 16、`network` 8、`env` 1 —— 与 worker 五作用域对应，权限门不是摆设。
- **校验**：`python sdk/check_all.py` → `校验 70 个包：70 OK / 0 FAILED`（实测）。
- **产物**：`dist/` 73 zip / 70 sig；**3 个 zip 无 `.sig`**：`hello.zip`、`user_jsm_batch_map.zip`、`user_jsm_batch_render_v2.zip`，且后两者在 `packages/` 已无对应源目录 → 陈旧产物，建议清出 dist。

---

## 7. 授权、商城与安全设计（读码确认）

| 面 | 实现 |
|---|---|
| 卡密 | `FW-<b64(payload)>-<hmac-sha256[:12]>`，正式密钥只在云端 |
| 账号 | scrypt N=16384 r=8 p=1，旧 SHA256 登录渐进迁移 |
| 设备 | 1 账号 1 设备，新设备登录踢旧设备，客户端 30s 校验 |
| 响应防伪 | 云端用 `FRAMEWEAVE_VERIFY_SECRET` 对 auth 响应 HMAC 签名，客户端验签（防 fiddler 改 member） |
| 云端加固 | 验证码 5 分钟/5 次错失效/60s 冷却/每日 10 次；IP 20 次/5 分钟；账号失败 5 次锁 10 分钟；1 MB 请求体上限；安全响应头；XFF 可信开关 |
| 打包加固 | PyArmor runtime 混淆 + 敏感常量 XOR + `integrity.manifest` 启动验签（失败直接退出） |
| 插件 | zip-slip 防护、50 MB 上限、manifest G1-G10、Ed25519 签名 + 撤销清单、worker 权限门 |
| 本地 | DPAPI 密钥保险箱、工作流 `@secret:` 自动脱敏、本地令牌中间件、CORS 收窄到 localhost:5180 |

审计遗留（`docs/reports/服务器安全运维审计_20261017.md` §4）仍有效：`.tmp` 明文口令脚本未轮换/未隔离。

---

## 8. 构建与发布链

`scripts/`：`build-backend.ps1`（PyArmor → PyInstaller → build_seed 24 包种子 → integrity）→ `build-installer.ps1`（vite build → electron-builder --dir → app-update.yml → NSIS）→ `check_release.ps1`（sha512）→ `publish*/release-*.ps1`（GitHub / 韩国镜像）→ `update-cloud-auth.ps1`（远端 sha256 校验 + 重启 + 健康自检）→ `verify-vm.ps1`（VM 卸载/静默安装/CDP 样式断言）。

- 版本真源：`desktop/package.json`（vite `__APP_VERSION__` 注入前端），server/cloud/preload 同步。
- 0.3.12 构建链（发布记录）：后端 20.7 MB、bundle `index-D43OsqxJ.js`、NSIS 成功、blockmap 成功、sha512 match。
- 隐患：构建脚本全为 PowerShell 5.1 且依赖 BOM/编码纪律，历史上多次因 EAP=Stop + stderr 误判假失败（已修，但仍是脆弱点）。

---

## 9. 本轮实测结果

| 验证 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `python -m pytest -q`（cwd=backend） | **109 收集 / 102 通过 / 7 失败** |
| 失败明细 | 同上 | `test_health_governance::test_backend_fw_dirs_subset_of_pending_list`、`::test_cloud_db_and_pyarmor_log_removed`；`test_ws_events` 5 项（`ModuleNotFoundError: websockets`） |
| 前端类型 | `npx tsc --noEmit`（cwd=web） | **exit 0** |
| 插件校验 | `python sdk/check_all.py` | **70 OK / 0 FAILED** |
| 依赖完整性 | `pip install -r requirements.txt` 等价检查 | `websockets` 未在 `requirements.txt`，CI 手工安装 |
| 目录健康 | 扫描 `backend/fw_*` | **1,232 个目录 / 11,109 文件 / 5.85 MB** |
| 卫生 | `backend/cloud.db` | 仍存在（90,112 B） |
| git | `git status -sb` / `rev-list --count` | 干净；领先 origin 2；tag 停在 v0.3.4 |

---

## 10. 问题清单

### P0-1 `.tmp` 明文口令脚本未处置（安全）
- 证据：按「SSH 口令直传写法」正则扫描 `.tmp/`，命中 25 个 `.py/.ps1`（`kr_deploy_update.py`、`diag_xui_db.py`、`git_push_via_kr.py` 等）；`服务器安全运维审计_20261017.md` A12 已标「约 70 个脚本含韩国服务器 root 明文口令」，处置状态=未轮换。
- 影响：任一脚本外流即等于服务器 root。
- 动作：① 轮换服务器 root 口令与密钥；② 把凭据收进系统凭据管理器/环境变量；③ 脚本改读凭据后归档旧脚本（移动不删除）；④ 全仓 grep 复检。

### P0-2 仓库内 junction 自引用（工具链风险）
- 证据：`.tmp/t6_hygiene_scratch/FrameWeave` 是指向仓库根的 reparse point；`os.path.islink()` 返回 **False**、`st_file_attributes & 0x400` 为真。任何递归遍历（备份、杀软、体积统计、打包脚本）都会把 77.9 GB 重复计入，且可能无限展开。
- 动作：删除该 junction（它是 scratch 目录，无内容），在 `.tmp/` 放说明文件替代；归档/备份脚本加 `FILE_ATTRIBUTE_REPARSE_POINT` 跳过。

### P1-3 测试残留把健康门禁打红（实测 2 项失败）
- 证据：`backend/` 下 1,232 个 `fw_*` 目录；`test_health_governance` 的待提权清单仅 118 行。
- 根因：测试统一用 `os.path.join(tempfile.gettempdir(), "fw_xxx_%d")` 建工作目录；当进程环境缺 `TEMP/TMP/TMPDIR` 时，Python `_candidate_tempdir_list()` 会回落到 **当前工作目录**（= `backend/`），且用例无 teardown 清理 → 每跑一轮就落一批。
- 动作：① conftest 里 `os.environ.setdefault("TMPDIR", …)` 或直接改用 pytest `tmp_path`；② 加 autouse fixture 结束后 `shutil.rmtree`；③ 现存 1,232 个按归档公约移入 `_archive/<日期>-测试残留/` 并登记 `QUARANTINE_MANIFEST.md`；④ 之后门禁才能真正常绿。

### P1-4 `backend/cloud.db` 未移出（实测失败）
- 证据：`backend/cloud.db` 90,112 B；`test_cloud_db_and_pyarmor_log_removed` 断言其不存在。
- 动作：移入归档区（`.gitignore` 已忽略，但源码树纪律要求移出）；`pyarmor.bug.log` 已在 FrameWeave 根（`*.log` 忽略）需一并归位。

### P1-5 `websockets` 未进依赖清单（实测 5 项失败）
- 证据：`tests/test_ws_events.py` 5 项全部 `ModuleNotFoundError: No module named 'websockets'`；CI 用 `pip install pytest httpx websockets` 手工补，`requirements.txt` 无此项。
- 动作：新增 `requirements-dev.txt`（pytest/httpx/websockets）或在 `requirements.txt` 补 `websockets>=12`；本地与 CI 用同一份清单。

### P1-6 git 发布纪律缺口（复核后修正）
- 证据（`git ls-remote --tags origin` 实测）：远端已有 `v0.3.5`(5b5bd16)、`v0.3.11`(9509cad)、`v0.3.12`(87e3e42)，
  **缺 `v0.3.6 / v0.3.7 / v0.3.8 / v0.3.9 / v0.3.10`**；本机 tag 列表陈旧（未 fetch），首轮误判为「停在 v0.3.4」。
- 另：`main` 曾领先 `origin/main` 2 个提交未推送。
- 0.3.6–0.3.10 无独立提交：这几个版本是同一工作树的连续构建，改动全部并入 `9509cad`(v0.3.11)，
  因此无法补打有意义的 tag（不伪造指向）。
- 动作：`git fetch --tags --force` 同步本地；已推送 `main`；把「发布前 `git tag -a` + `git push --tags`」写进发布规范检查项。

### P1-7 CI 两处必失败点
- 证据（读 `.github/workflows/ci_backend.yml`）：① `health` job 无 `actions/setup-python`、无依赖安装，直接 `python -m pytest tests/test_health_governance.py` → ubuntu-latest 缺 fastapi 必失败；② `health` job 上传 `classify.json`，该文件在工作区根、不在 `FrameWeave` 仓库内 → `if-no-files-found: warn` 空跑。
- 动作：health job 补 setup-python + `pip install -r requirements.txt`；上传路径改为 `docs/工作区审计清单_*.md` 或删掉该步骤。

### P2-8 死代码
- `app/nodes/`：**2,256 行 / 11 文件**，全仓无任何 `from app.nodes` 引用（registry 已停止内置导入）；但打包 spec 以 `app.*` 全树收集 → 仍被混淆并打进产物。
- `app/license.py`：328 行，文件头自述 `DEPRECATED · 纯云化后不可达`。
- 动作：先加 `@deprecated` 注释与 CI 检查（禁止新增引用），下个 minor 版本整目录删除；打包 spec 的 `_collect_obf_app_modules()` 排除 `app.nodes`、`app.license`，可再省一点包体。

### P2-9 大文件单点
- `server.py` 2,403 行 / 75 路由；`cloud_server.py` 890 行 + `admin.html` 844 行（内嵌管理页）；`Canvas.tsx` 1,264 行 / 74 KB；`index.css` 48 KB。
- 动作：按域拆路由（auth/market/workflow/assets/plugins）；cloud 管理页外置静态文件；Canvas 拆 hook（useCanvasGraph/useCanvasShortcuts/useDropConnect）。

### P2-10 文档版本漂移
- 根 `README.md` 仍写「（M0 骨架）」；`docs/guides/开发指南.md` 写「适用版本：main @ v0.3.0 / 0.3.1 开发中」。实际 0.3.12 已发布。
- 动作：README 去掉 M0 字样；开发指南版本行改为「适用版本：0.3.12（当前 main）」，并补一句「版本真源=desktop/package.json」。

### P2-11 产物与缓存堆积
- `FrameWeave/release/` 15 个版本 3.2 GB；工作区 `release/0.3.0` 与之重复；`desktop/dist/win-unpacked` 740 MB；`backend/packaging/dist` 935 MB；`dist/` 3 个无签名陈旧 zip。
- 缓存类合计 ≈ 34 GB（`.uvcache` 21.3 + `.wheels` 5.1 + `.cache` 2.9 + `.pipcache` 2.6 + `.dl` 2.6）。
- 动作：release 只保留「当前 + 上一版」，其余移入 `_archive/`；缓存目录按需清理（可重建）；`dist/` 删 3 个陈旧 zip。

### P3-12 代码级小问题
- `engine.py:864` `zip(spec.outputs, spec.outputs)` 同一对象 zip 两遍（0.2.12 报告已记，仍在）。
- 测试里 `srv.task_queue.shutdown()` 未 await（RuntimeWarning 满屏，实测 5 处以上）——协程泄漏警告会掩盖真问题。
- 缓存键对 `@secret:` 引用按字面量参与哈希：密钥值不变时无影响，轮换密钥会导致重复调用 LLM（设计权衡，建议文档标注）。

---

## 11. 建议执行顺序

1. **今天**：P0-2 删 junction；P1-4 移 `cloud.db`；P1-3 给 conftest 加 TMPDIR + teardown，然后清 1,232 个残留并登记。
2. **本周**：P1-5 依赖清单统一 → 全量 pytest 复跑到全绿；P1-6 推送 + 补 tag；P1-7 修 CI health job。
3. **接着**：P0-1 轮换服务器凭据（需要你确认窗口，涉及线上授权服务重启）。
4. **下个 minor**：P2-8 删死代码、P2-9 拆文件、P2-10 文档对齐、P2-11 产物归档。

---

## 12. 本次未覆盖 / 缺口

- **未跑真实业务链路**：没有启动后端 + 前端做端到端出片（需要模型/ffmpeg/授权环境），执行引擎的正确性结论来自代码与单测，不是实跑视频。
- **未连生产服务器**：授权服务 `shouquan.frameweave.top` 的实际健康、部署版本、数据库状态未验证（本轮未发起外网请求）。
- **未验安装包**：`release/0.3.12` 三件套只核对了本地尺寸与 latest.yml 哈希一致性，未做 fresh install / 自动更新实测。
- **未做覆盖率统计**：仓库无 coverage 配置，无法给出模块级覆盖数字。

---

## 13. 处置结果（2026-10-19 当日执行）

> 本节记录 §10 清单的实际处置，全部有命令输出佐证；提交范围：`1c29eac..7a85c58`（6 个提交，已推送 origin/main）。

| 编号 | 问题 | 处置 | 证据 |
|---|---|---|---|
| P0-1 | `.tmp` 明文口令 | 31 个脚本移入 `_archive/2026-10-19-凭据脚本隔离/`；`.tmp/kr_fix_upload.py` 改读 `FW_SERVER_PASSWORD`；另发现并清理 3 个 E2E 脚本里写死的云端管理密钥（改 `FW_ADMIN_KEY`）；新增 `scripts/scan_plaintext_secrets.py` + CI 步骤 | 扫描 0 命中；轮换步骤见 `docs/guides/凭据轮换与明文清理.md` |
| P0-2 | junction 自引用 | `cmd rmdir` 删除 `.tmp/t6_hygiene_scratch/FrameWeave` 链接（目标内容未动） | 仓库 21 个顶层条目不变 |
| P1-3 | 1,232 个 `fw_*` 残留 | 全部移入归档；conftest 固定 TMPDIR/TEMP/TMP + `tempfile.tempdir`，`pytest_sessionfinish` 整目录清理 | 测试跑完 `backend/fw_*` = 0 |
| P1-4 | `cloud.db` 在源码树 | 移入归档；`cloud_server.py` 默认路径改为 `data/cloud.db`（生产目录无 `data/` → 行为不变） | `tests_cloud_admin.py` 57/57 |
| P1-5 | `websockets` 未声明 | 新增 `backend/requirements-dev.txt`，CI 与本地共用 | ws 用例 5/5 通过 |
| P1-6 | tag / 推送 | `main` 已推送；`git fetch --tags` 同步远端（远端本就有 0.3.5/0.3.11/0.3.12）；0.3.6–0.3.10 无独立提交，不伪造 tag | `git ls-remote --tags` |
| P1-7 | CI 两处必失败 | 两个 job 均 setup-python + requirements-dev；去掉不存在的 `classify.json` 上传；pytest 与 ci_gate 去重；新增「测试后不得新增 fw_*/cloud.db/pyarmor.bug.log」硬断言 | `.github/workflows/ci_backend.yml` |
| P2-8 | 死代码 | `app/nodes/`(2,256 行) + `app/license.py`(328 行) 归档并 `git rm` | 测试全绿；object_info 仍 70 节点 |
| P2-9 | 大文件单点 | server.py 2404→1940（拆 `http_models`/`prompt_protocol`/`models_api`）；index.css 769 行→7 个 `styles/*.css`；Canvas 撤销重做抽 `useGraphHistory`（1264→1246） | tsc 0 错误；vite build 成功；pytest 109 passed |
| P2-10 | 文档漂移 | README 去掉「M0 骨架」；开发指南版本行改 0.3.12 | — |
| P2-11 | 产物堆积 | `release/` 只留 0.3.11/0.3.12（其余 2.78 GB 归档）；3 个无签名 zip 重新签名（dist 73/73 全验签）；`.pipcache`/`.npm-cache` 清理 | `_archive/2026-10-19-历史发布产物/` |
| P3-12 | 代码小问题 | engine 冗余 `zip` 删除；`task_queue.shutdown()` 未 await 改同步收尾（RuntimeWarning 消失）；超限请求断言容忍 RST（复跑偶发红） | — |

### 13.1 仍未闭环（需你本人操作）

1. **凭据轮换**：服务器 root 口令、X-UI 面板口令、`FRAMEWEAVE_ADMIN_KEY`（曾随 git 历史提交）必须更换。
   本地已无明文，但历史提交/备份里仍有旧值，**只有轮换才能失效**。步骤见 `docs/guides/凭据轮换与明文清理.md` §2。
2. **安装包重建**：`web/dist` 因本次前端改动已重建（bundle `index-CWvk8OiI.js`），`desktop/integrity.json` 已同步；
   但 `release/0.3.12` 三件套仍是改动前的产物。要发布含本次改动的版本，需按 `docs/guides/打包发布规范.md` 重新打包并 bump 版本。
3. **进一步拆分**：server.py 仍有 1,940 行（assets/auth/market/export/user_nodes 四组路由未拆）；Canvas.tsx 仍有 1,246 行。
   本轮按「低风险优先」只做了可被 tsc/pytest 完全覆盖的拆分，剩余拆分建议单独一个 PR 做。

### 13.2 复验命令

```bash
cd backend && python -m pip install -r requirements-dev.txt && python -m pytest -q   # 109 passed
cd web && npx tsc --noEmit && npx vite build                                          # 0 错误 / 构建成功
cd .. && python sdk/check_all.py                                                      # 70 OK / 0 FAILED
python scripts/scan_plaintext_secrets.py --root .                                     # 0 命中
```
