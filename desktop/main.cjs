// Electron 主进程：启动本地 Python 后端 + 加载前端
const { app, BrowserWindow, dialog, ipcMain, nativeTheme, Menu } = require('electron')
const { autoUpdater } = require('electron-updater')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')
const crypto = require('crypto')

// ---------- P2 加固：asar 完整性自检（仅打包版生效） ----------
// 启动最早阶段（在单实例锁、autoUpdater、任何网络访问之前）校验 app.asar 内
// 关键文件哈希与构建期清单 integrity.json 一致；不一致说明 asar 被解包篡改
// （替换 main.cjs/preload.cjs/web/dist 绕过授权校验），直接拒绝启动。
function verifyAsarIntegrity() {
  if (!app.isPackaged) { fwLog('[integrity] dev 模式跳过 asar 自检'); return true }
  try {
    const appPath = app.getAppPath()             // 指向 resources/app.asar
    const manifestRaw = fs.readFileSync(path.join(appPath, 'integrity.json'), 'utf8')
    const manifest = JSON.parse(manifestRaw)
    if (!manifest || manifest.v !== 1 || !manifest.files) return false
    for (const rel of Object.keys(manifest.files)) {
      const expected = String(manifest.files[rel]).toLowerCase()
      if (!/^[0-9a-f]{64}$/.test(expected)) return false
      const actual = crypto.createHash('sha256').update(fs.readFileSync(path.join(appPath, rel))).digest('hex')
      if (actual !== expected) {
        fwLog('[integrity] FAIL:', rel, '哈希不匹配')
        return false
      }
    }
    fwLog('[integrity] OK:', Object.keys(manifest.files).length, '个关键文件校验通过')
    return true
  } catch (e) {
    fwLog('[integrity] 自检异常，拒绝启动:', e && e.message)
    return false
  }
}

// ---------- P2 加固：调试参数 / DevTools 拒绝（仅打包版） ----------
// 生产仅打包版禁用 DevTools（webPreferences.devTools=false + 移除默认菜单 +
// before-input-event 拦截 F12/Ctrl+Shift+I）；并拒绝携带调试类命令行参数启动
// （--remote-debugging-port / --inspect / --devtools 等），防止远程调试接管。
const DEBUG_ARG_RE = /^(--(devtools|dev-tools|auto-open-devtools-for-tabs|remote-debugging-port|inspect|inspect-brk|js-flags))/

function detectDebugArgs(argv) {
  return argv.some((a) => DEBUG_ARG_RE.test(String(a)))
}

function scrubDebugArgs(argv) {
  return argv.filter((a) => !DEBUG_ARG_RE.test(String(a)))
}

// 打包版：检测到调试参数直接退出（Chromium 在脚本运行前已解析部分开关，
// 唯一可靠拦截是拒绝启动）；开发版忽略，便于本地联调。
if (app.isPackaged && detectDebugArgs(process.argv)) {
  try { fwLog('[integrity] 检测到调试参数，拒绝启动:', process.argv.filter((a) => DEBUG_ARG_RE.test(String(a)))) } catch { /* 日志失败忽略 */ }
  app.exit(1)
}
process.argv = scrubDebugArgs(process.argv)

// 单实例锁：双击/多开时聚焦已有窗口并退出新实例
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
}

// ---------- 运行日志（黑匣子）：userData/debug.log，供故障排查 ----------
const LOG_MAX = 2 * 1024 * 1024  // 2MB 轮转

function fwLog(...args) {
  try {
    const line = new Date().toISOString() + ' ' + args.map(String).join(' ') + '\n'
    const p = path.join(app.getPath('userData'), 'debug.log')
    try { if (fs.existsSync(p) && fs.statSync(p).size > LOG_MAX) fs.renameSync(p, p + '.old') } catch { /* 轮转失败忽略 */ }
    fs.appendFileSync(p, line)
  } catch { /* 日志失败不影响主流程 */ }
}

process.on('uncaughtException', (e) => fwLog('[CRASH] uncaughtException:', e && e.stack || e))
process.on('unhandledRejection', (e) => fwLog('[CRASH] unhandledRejection:', e && e.stack || e))

ipcMain.on('fw-log', (_e, payload) => {
  const d = payload || {}
  fwLog('[renderer:' + (d.level || 'info') + ']', d.msg || '')
})

let mainWindow = null
let backendProc = null
const BACKEND_PORT = 8788
// P2：打包版随机本地 API 令牌（模块级保存，startBackend 生成后注入后端环境
// 与渲染进程；dev 留空，后端/前端回落 dev-local-token）
let localToken = ''

// 找到本机后端 Python
function killStaleBackendTree() {
  if (process.platform !== 'win32') return
  try { require('child_process').execFileSync('taskkill.exe', ['/IM', 'frameweave-backend.exe', '/T', '/F'], { stdio: 'ignore' }) } catch { /* no stale process */ }
}

function findBackendCommand() {
  const isPacked = app.isPackaged
  const base = isPacked ? process.resourcesPath : path.join(__dirname, '..')
  const candidates = []
  // 优先：PyInstaller 打包后的独立后端 exe（M3④）
  if (isPacked) {
    candidates.push(path.join(process.resourcesPath, 'backend-dist', 'frameweave-backend.exe'))
    candidates.push(path.join(process.resourcesPath, 'backend', '.venv', 'Scripts', 'python.exe'))
    candidates.push(path.join(process.resourcesPath, 'backend', 'python.exe'))
  } else {
    // 开发模式：源码后端（.venv python + main.py）
    candidates.push(path.join(base, 'backend', '.venv', 'Scripts', 'python.exe'))
  }
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      const isExe = p.endsWith('.exe') && p.includes('frameweave-backend')
      return { cmd: p, backendMain: isExe ? null : path.join(base, 'backend', 'main.py') }
    }
  }
  throw new Error('未找到后端环境（源码 venv 或打包后端）')
}

// 探测 8788 是否已被占用；返回 'frameweave'(本服务) | 'other'(其他程序) | null(空闲)
function probePort() {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:' + BACKEND_PORT + '/api/health', (res) => {
      let body = ''
      res.on('data', (c) => { body += c })
      res.on('end', () => {
        try {
          const j = JSON.parse(body)
          const compatible = j && j.service === 'frameweave' && j.protocol_version === 3 && j.auth_revision === 'email-code-v2'
          // S1-T10（分层授权）：壳层对降级模式给出可见提示（本地节点执行不受影响）
          if (compatible && j.auth && j.auth.mode && j.auth.mode !== 'online') {
            console.warn('[auth] 授权降级模式: ' + j.auth.mode +
              (j.auth.degraded ? '（离线缓存/未配置——云端功能受限，本地节点执行不受影响）' : ''))
          }
          resolve(compatible ? 'frameweave' : (j ? 'other' : null))
        } catch { resolve('other') }
      })
    })
    req.on('error', () => resolve(null))
    req.setTimeout(1200, () => { req.destroy(); resolve('other') })
  })
}

function waitForBackend(timeoutMs = 20000) {
  return new Promise((resolve) => {
    const start = Date.now()
    const check = () => {
      const req = http.get('http://127.0.0.1:' + BACKEND_PORT + '/api/health', (res) => {
        res.resume()
        resolve(true)
      })
      req.on('error', () => {
        if (Date.now() - start > timeoutMs) resolve(false)
        else setTimeout(check, 300)
      })
      req.setTimeout(1000, () => { req.destroy() })
    }
    check()
  })
}

async function startBackend() {
  fwLog('[boot] startBackend begin, packaged=', app.isPackaged, 'port=', BACKEND_PORT)
  // 端口占用检测：已是本服务(复用) / 其他程序(友好报错) / 空闲(正常启动)
  const occ = await probePort()
  fwLog('[boot] probePort =>', occ)
  if (occ === 'frameweave') {
    fwLog('[boot] 已检测到运行中的 FrameWeave 后端，直接复用')
    console.log('[backend] 已检测到运行中的 FrameWeave 后端，直接复用')
    return true
  }
  if (occ === 'other') {
    dialog.showErrorBox('拾帧 FrameWeave',
      '端口 ' + BACKEND_PORT + ' 被其他程序占用。\n\n请关闭占用该端口的程序后重启应用。')
    return false  // 上层负责退出，不再开窗
  }
  try {
    killStaleBackendTree()
    const { cmd, backendMain } = findBackendCommand()
    // H3 修复：打包模式生成随机本地 API 令牌（dev 模式不注入，后端回落 dev-local-token）
    localToken = app.isPackaged ? crypto.randomBytes(16).toString('hex') : ''
    const extraEnv = localToken ? { FRAMEWEAVE_LOCAL_TOKEN: localToken } : {}
    backendProc = backendMain
      ? spawn(cmd, ['-u', backendMain], { env: { ...process.env, ...extraEnv, PYTHONIOENCODING: 'utf-8' }, stdio: 'ignore' })
      : spawn(cmd, [], { env: { ...process.env, ...extraEnv, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, stdio: 'ignore' })
    const ok = await waitForBackend()
    fwLog('[boot] waitForBackend =>', ok)
    if (!ok) {
      dialog.showErrorBox('FrameWeave', '后端服务启动超时，请检查 Python 环境后重启应用')
      return false
    }
    return true
  } catch (e) {
    fwLog('[boot] 启动后端失败:', e.message)
    dialog.showErrorBox('FrameWeave', '启动后端失败: ' + e.message)
    return false
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: '拾帧 FrameWeave',
    backgroundColor: '#0B0C0E',
    icon: path.join(__dirname, 'build', 'icon.png'),
    autoHideMenuBar: true,
    // ---- 无边框（方案A）：隐藏系统标题栏 + 原生窗口控件 Overlay（Windows/Linux） ----
    titleBarStyle: 'hidden',
    ...(process.platform !== 'darwin'
      ? { titleBarOverlay: { color: '#00000000', symbolColor: nativeTheme.shouldUseDarkColors ? '#ADA79A' : '#55524B', height: 46 } }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P2：打包版禁用 DevTools（dev 模式保留联调）
      devTools: !app.isPackaged,
      webSecurity: true,
      sandbox: true,
      // H3/P2 修复：本地 API 令牌经 additionalArguments 传给 sandbox 化 preload
      // （此前 main 只注入后端 spawn 环境，渲染层拿不到随机令牌，打包版受保护
      // 接口会 401；dev 回落 dev-local-token 与后端/前端常量一致）
      additionalArguments: [app.isPackaged && localToken ? '--fw-local-token=' + localToken : '--fw-local-token=dev-local-token'],
    },
  })

  // P2：移除默认应用菜单（隐藏"查看→开发者工具"入口），并拦截快捷键
  if (app.isPackaged) {
    try { Menu.setApplicationMenu(null) } catch { /* 低版本忽略 */ }
    mainWindow.webContents.on('before-input-event', (_e, input) => {
      if (input.type !== 'keyDown') return
      const k = (input.key || '').toLowerCase()
      const acc = input.control || input.meta
      if (k === 'f12' || (acc && input.shift && (k === 'i' || k === 'j' || k === 'c'))) {
        _e.preventDefault()
      }
    })
  }

  // P2：外部窗口/新开导航一律拦截（防 window.open / 目标 _blank 逃逸到外部浏览器）
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!app.isPackaged) return
    try {
      const u = new URL(String(url))
      if (u.protocol !== 'file:') { e.preventDefault(); return }
      // Windows 的 file: URL pathname 形如 /F:/...，去掉前导斜杠后与 app.asar 路径比对
      const got = path.normalize(decodeURIComponent(u.pathname).replace(/^\/+/, '')).toLowerCase()
      const expected = path.normalize(app.getAppPath()).toLowerCase()
      if (!got.startsWith(expected)) e.preventDefault()
    } catch { e.preventDefault() }
  })

  const devUrl = 'http://localhost:5180'
  const distDir = path.join(app.getAppPath(), 'web', 'dist')
  const prodFile = path.join(distDir, 'index.html')

  // 打包模式：直接加载构建产物（避免被本地 dev server 带偏）；开发模式才探测 Vite
  fwLog('[boot] createWindow, packaged=', app.isPackaged)
  if (app.isPackaged) {
    if (fs.existsSync(prodFile)) { fwLog('[boot] loadFile:', prodFile); mainWindow.loadFile(prodFile) }
    else { fwLog('[boot] prodFile 缺失, fallback devUrl'); mainWindow.loadURL(devUrl) }
  } else {
    http.get(devUrl + '/api/health', (res) => {
      res.resume()
      mainWindow.loadURL(devUrl)
    }).on('error', () => {
      if (fs.existsSync(prodFile)) mainWindow.loadFile(prodFile)
      else mainWindow.loadURL(devUrl) // 页面会显示连接失败提示
    })
  }

  mainWindow.on('closed', () => { mainWindow = null })

  // 最大化状态变化推送给渲染层（无边框方案A：Overlay 控件状态联动）
  mainWindow.on('maximize', () => { mainWindow && mainWindow.webContents.send('window:maximized-changed', true) })
  mainWindow.on('unmaximize', () => { mainWindow && mainWindow.webContents.send('window:maximized-changed', false) })
}

// ---- 窗口控制 IPC（无边框方案A：titleBarOverlay 原生控件 + 渲染层控制桥） ----
ipcMain.on('window:minimize', () => { if (mainWindow) mainWindow.minimize() })
ipcMain.on('window:toggle-maximize', () => {
  if (!mainWindow) return
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
})
ipcMain.on('window:close', () => { if (mainWindow) mainWindow.close() })
ipcMain.handle('window:is-maximized', () => (mainWindow ? mainWindow.isMaximized() : false))
// 主题联动：浅/深色切换时同步 titleBarOverlay 原生控件配色（无边框方案A 补全）
ipcMain.on('window:set-titlebar-overlay', (_e, theme) => {
  if (!mainWindow || process.platform === 'darwin') return
  const isLight = String(theme || '').toLowerCase() === 'light'
  try {
    mainWindow.setTitleBarOverlay({
      color: '#00000000',                     // 透明：直接显示窗口内容背景
      symbolColor: isLight ? '#596176' : '#a2a8b8', // 与 --text-dim 一致
      height: 52,
    })
  } catch { /* 低版本 Electron 无 setTitleBarOverlay 时静默 */ }
})

app.whenReady().then(async () => {
  fwLog('[boot] app ready, version=', app.getVersion())
  // P2：打包版启动自检——asar 内关键文件哈希与 integrity.json 不符即拒绝启动
  // （放在后端启动、建窗、自动更新之前，确保校验失败不会留下任何运行痕迹）
  if (!verifyAsarIntegrity()) {
    fwLog('[boot] asar 完整性校验失败，拒绝启动')
    try { dialog.showErrorBox('拾帧 FrameWeave', '程序完整性校验失败，文件可能已被篡改，请重新安装。') } catch { /* 无 UI 时静默 */ }
    app.exit(1)
    return
  }
  const backendOk = await startBackend()
  if (!backendOk) { fwLog('[boot] backend 未就绪，退出'); app.quit(); return }
  createWindow()
  setupAutoUpdate()
  fwLog('[boot] 启动完成')
})

// ---- 自动更新（electron-updater） ----
let updateInProgress = false
// 检查流程进行中（重试/切源期间）：electron-updater 的 error 事件在每次失败都会触发，
// 若直接上报会让 UI 在「重试中」反复闪烁；此标志在 checkForUpdatesWithFallback /
// silentStartupCheck 全程置位，期间 error 事件只记日志，最终结果由流程末尾统一上报。
let updateCheckActive = false

// 更新源：镜像优先（大陆可达直链）→ GitHub 兜底，用户无感切换。
// 镜像源需同步发布 GitHub release 产物（latest.yml + exe + blockmap），见 docs/s17-auth-network-optimization.md
const UPDATE_MIRROR_URL = process.env.FRAMEWEAVE_MIRROR_URL || 'https://shouquan.frameweave.top/update'
const UPDATE_GITHUB_FEED = { provider: 'github', owner: 'xsk159357', repo: 'frameweave-desktop' }
const UPDATE_RETRIES = 2              // 每个更新源最多重试次数（0 = 仅首次）
const UPDATE_ATTEMPT_TIMEOUT_MS = 25000  // 单次检查超时（electron-updater 无内置超时，防 UI 卡在「检查中…」）
const UPDATE_BACKOFF_MS = 800         // 指数退避基数（0.8s → 1.6s）

function sendUpdateStatus(type, message, version = '') {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('update:status', { type, message, version })
  }
}

function friendlyUpdateError(err) {
  const raw = String(err && err.message || err || '')
  const lower = raw.toLowerCase()
  // 网络类错误（复用 isNetworkError 全量覆盖：超时/DNS/连接/代理/undici/断网）
  if (isNetworkError(err)) {
    return '网络暂时不可用，请检查网络后重试'
  }
  if (lower.includes('app-update.yml') || lower.includes('cannot parse releases feed') || lower.includes('latest.yml') || lower.includes('404')) {
    return '更新源暂时不可用，请稍后重试'
  }
  if (lower.includes('403') || lower.includes('401')) {
    return '更新源访问被拒绝，请稍后重试'
  }
  return '更新检查失败，请稍后重试'
}

// 网络类失败判定（用于触发镜像/GitHub 切换与重试；业务性错误如 4xx/无更新不触发）。
// 覆盖 Node/undici/Chromium 常见网络错误码与描述：
//   超时：ETIMEDOUT / ESOCKETTIMEDOUT / undici UND_ERR_*_TIMEOUT / "timed out"
//   DNS：ENOTFOUND / EAI_AGAIN / getaddrinfo
//   连接：ECONNREFUSED / ECONNRESET / ECONNABORTED / EHOSTUNREACH / ENETUNREACH /
//         ENETDOWN / EPIPE / socket hang up
//   代理/断网：tunnel / proxy / network / internet / ERR_NETWORK_* / ERR_INTERNET_*
function isNetworkError(err) {
  const raw = String(err && err.message || err || '').toLowerCase()
  return /timed_?\s?out|timeout|enotfound|eai_again|econnrefused|econnreset|econnaborted|ehostunreach|enetunreach|enetdown|epipe|network|internet|socket|fetch failed|getaddrinfo|und_|tunnel|proxy|hang up|err_.*net/i.test(raw)
}

// 源级失败判定：更新源本身不可用（latest.yml 缺失/解析失败/404/证书异常）——
// 这类错误重试同一源无意义，应直接切换兜底源（GitHub）。
function isSourceError(err) {
  const raw = String(err && err.message || err || '').toLowerCase()
  return /app-update\.yml|cannot parse|latest\.yml|404|not found|certificate|self.signed|unable to verify|unknown url type/i.test(raw)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 单次 checkForUpdates 带超时：electron-updater 未暴露内置超时，网络黑洞会让
// checkForUpdates 长时间悬挂 → UI 卡死在「检查中…」；超时按网络类失败处理（触发重试/切源）。
function checkOnce() {
  return new Promise((resolve, reject) => {
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      reject(new Error('更新检查超时 (timed out)'))
    }, UPDATE_ATTEMPT_TIMEOUT_MS)
    autoUpdater.checkForUpdates().then(
      (r) => { if (settled) return; settled = true; clearTimeout(timer); resolve(r) },
      (e) => { if (settled) return; settled = true; clearTimeout(timer); reject(e) }
    )
  })
}

// 检查更新：镜像源优先，网络类失败按指数退避重试（UPDATE_RETRIES 次）后切换 GitHub 兜底；
// 源级失败（latest.yml 缺失/404/证书）直接切换兜底源；业务错误（4xx/无更新解析失败）直接结束。
async function checkForUpdatesWithFallback() {
  if (!app.isPackaged) {
    sendUpdateStatus('info', '开发模式暂不检查更新')
    return
  }
  const explicitFeed = process.env.FRAMEWEAVE_UPDATE_URL
  // 显式指定更新源时只查该源（禁用镜像/GitHub 切换）；否则镜像优先 → GitHub 兜底
  const feeds = explicitFeed
    ? [{ name: '自定义源', set: () => autoUpdater.setFeedURL({ provider: 'generic', url: explicitFeed }) }]
    : [
        { name: '镜像源', set: () => autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_MIRROR_URL }) },
        { name: 'GitHub', set: () => autoUpdater.setFeedURL(UPDATE_GITHUB_FEED) },
      ]
  let lastErr = null
  let giveUp = false
  updateCheckActive = true
  try {
    for (const feed of feeds) {
      if (giveUp) break
      feed.set()
      for (let attempt = 0; attempt <= UPDATE_RETRIES; attempt++) {
        try {
          if (attempt > 0) {
            sendUpdateStatus('info', '正在检查更新…（' + feed.name + ' 重试 ' + attempt + '）')
            await sleep(UPDATE_BACKOFF_MS * Math.pow(2, attempt - 1))
          } else {
            sendUpdateStatus('info', '正在检查更新…')
          }
          await checkOnce()
          return  // 检查流程完成（update-available / update-not-available 由事件监听器处理）
        } catch (e) {
          lastErr = e
          const net = isNetworkError(e)
          const src = !net && isSourceError(e)
          fwLog('[updater] ' + feed.name + ' 第 ' + (attempt + 1) + ' 次失败:', e && e.message || String(e), 'network=', net, 'source=', src)
          if (!net && !src) { giveUp = true; break }   // 业务错误：不重试不切源
          if (src || attempt >= UPDATE_RETRIES) break  // 源级错误切源 / 网络重试耗尽切源
        }
      }
    }
    sendUpdateStatus('error', friendlyUpdateError(lastErr))
  } finally {
    updateCheckActive = false
  }
}

// 启动静默检查：镜像 → GitHub，网络/源级失败切换；全程不主动发 UI 状态
// （available/not-available 由事件监听器上报，渲染层对后台检查不弹窗打扰）。
async function silentStartupCheck() {
  const explicitFeed = process.env.FRAMEWEAVE_UPDATE_URL
  const feeds = explicitFeed
    ? [() => autoUpdater.setFeedURL({ provider: 'generic', url: explicitFeed })]
    : [
        () => autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_MIRROR_URL }),
        () => autoUpdater.setFeedURL(UPDATE_GITHUB_FEED),
      ]
  updateCheckActive = true
  try {
    for (const setFeed of feeds) {
      setFeed()
      for (let attempt = 0; attempt <= UPDATE_RETRIES; attempt++) {
        if (attempt > 0) await sleep(UPDATE_BACKOFF_MS * Math.pow(2, attempt - 1))
        try {
          await checkOnce()
          return
        } catch (e) {
          if (!isNetworkError(e) && !isSourceError(e)) return
          fwLog('[updater] 启动检查 ' + feed.name + ' 第 ' + (attempt + 1) + ' 次失败:', e && e.message || String(e))
        }
      }
    }
  } finally {
    updateCheckActive = false
  }
}

function setupAutoUpdate() {
  // 前端可主动触发检查；开发模式也返回明确反馈，避免按钮无反应
  ipcMain.on('check-for-update', () => {
    checkForUpdatesWithFallback()
  })
  ipcMain.on('download-update', () => {
    if (!updateInProgress) {
      updateInProgress = true
      autoUpdater.downloadUpdate().catch((err) => {
        updateInProgress = false
        sendUpdateStatus('error', friendlyUpdateError(err))
      })
    }
  })
  ipcMain.on('install-update', () => {
    if (updateInProgress) return
    autoUpdater.quitAndInstall()
  })

  if (!app.isPackaged) {
    console.log('[updater] 开发模式跳过自动更新')
    return
  }
  // 更新源：默认镜像优先（无论 app-update.yml 内置哪个源，本会话固定指向官方镜像，
  // GitHub 在镜像网络/源失败时兜底——大陆用户 GitHub CDN 超时是常见现象）；
  // FRAMEWEAVE_UPDATE_URL 显式覆盖时只查该源（联调/企业部署用，且禁用镜像/GitHub 切换）
  const feedUrl = process.env.FRAMEWEAVE_UPDATE_URL
  if (feedUrl) {
    autoUpdater.setFeedURL({ provider: 'generic', url: feedUrl })
    console.log('[updater] 使用覆盖更新源:', feedUrl)
  } else {
    try {
      autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_MIRROR_URL })
      console.log('[updater] 使用镜像源（默认），失败自动回退 GitHub')
    } catch (e) {
      fwLog('[updater] 默认镜像源设置失败:', e && e.message || String(e))
      console.log('[updater] 镜像源设置失败，使用打包内置源（app-update.yml）')
    }
  }

  autoUpdater.autoDownload = false      // 用户确认后下载
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    const v = info && info.version
    sendUpdateStatus('info', '发现新版本 ' + v + '，请在弹窗中确认下载', v)
    mainWindow && mainWindow.webContents.send('update:available', v)

  })

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] 已是最新版本')
    sendUpdateStatus('ok', '当前已是最新版本')
  })

  autoUpdater.on('download-progress', (p) => {
    if (mainWindow) {
      mainWindow.webContents.send('update:progress', Math.round((p.percent || 0) * 100) / 100)
    }
  })

  autoUpdater.on('update-downloaded', (info) => {
    updateInProgress = false
    sendUpdateStatus('downloaded', '新版本 ' + info.version + ' 已下载完成', info.version)
  })

  autoUpdater.on('error', (err) => {
    const msg = err && err.message || String(err)
    console.error('[updater] 更新失败:', msg)
    fwLog('[updater] 更新失败:', msg)
    if (updateCheckActive && !updateInProgress) {
      // 检查流程重试/切源中：每次失败都会触发本事件，最终结果由
      // checkForUpdatesWithFallback / silentStartupCheck 末尾统一上报，避免 UI 抖动。
      // （下载阶段错误 updateInProgress=true，不受抑制，必须及时上报复位）
      return
    }
    updateInProgress = false
    sendUpdateStatus('error', friendlyUpdateError(err))
  })

  // 启动 5 秒后检查（无网/更新源不通时静默失败，不产生 unhandledRejection、
  // 不打断启动体验；镜像优先，失败自动回退 GitHub）
  setTimeout(() => {
    if (!app.isPackaged) return
    silentStartupCheck().catch((e) => fwLog('[updater] 启动检查异常:', e && e.message || String(e)))
  }, 5000)
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// P2：退出前销毁本地令牌（内存中的随机令牌随进程退出自然消失；
// 仅日志中不落明文，避免磁盘残留）
app.on('quit', () => {
  fwLog('[boot] app quit, killing backend pid=', backendProc && backendProc.pid)
  localToken = ''
  if (backendProc && !backendProc.killed) {
    try { backendProc.kill() } catch { /* ignore */ }
  }
})
