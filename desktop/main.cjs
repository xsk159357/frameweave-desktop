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
    backgroundColor: '#0e0f14',
    autoHideMenuBar: true,
    // ---- 无边框（方案A）：隐藏系统标题栏 + 原生窗口控件 Overlay（Windows/Linux） ----
    titleBarStyle: 'hidden',
    ...(process.platform !== 'darwin'
      ? { titleBarOverlay: { color: '#00000000', symbolColor: nativeTheme.shouldUseDarkColors ? '#a2a8b8' : '#596176', height: 52 } }
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

// 官方更新镜像（大陆可达直链；GitHub 检查失败时自动回退，用户无感）
// 镜像源需同步发布 GitHub release 产物（latest.yml + exe + blockmap），见 docs/s17-auth-network-optimization.md
const UPDATE_MIRROR_URL = process.env.FRAMEWEAVE_MIRROR_URL || 'https://shouquan.frameweave.top/update'

function sendUpdateStatus(type, message, version = '') {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('update:status', { type, message, version })
  }
}

function friendlyUpdateError(err) {
  const raw = String(err && err.message || err || '')
  const lower = raw.toLowerCase()
  if (lower.includes('app-update.yml') || lower.includes('cannot parse releases feed')) {
    return '更新源暂时不可用，请稍后重试'
  }
  if (lower.includes('timed_out') || lower.includes('timeout') || lower.includes('enotfound') || lower.includes('network')) {
    return '网络暂时不可用，请检查网络后重试'
  }
  if (lower.includes('403') || lower.includes('401')) {
    return '更新源访问被拒绝，请稍后重试'
  }
  return '更新检查失败，请稍后重试'
}

// 网络类失败判定（用于触发镜像回退；业务性错误如 4xx/无更新不触发）
function isNetworkError(err) {
  const raw = String(err && err.message || err || '').toLowerCase()
  return /timed_?out|timeout|enotfound|network|econnreset|socket|fetch failed|getaddrinfo|eai_again|und_|err_.*net/i.test(raw)
}

// 检查更新：GitHub（默认源）失败时自动回退官方镜像源重试一次
async function checkForUpdatesWithFallback() {
  if (!app.isPackaged) {
    sendUpdateStatus('info', '开发模式暂不检查更新')
    return
  }
  sendUpdateStatus('info', '正在检查更新…')
  const explicitFeed = process.env.FRAMEWEAVE_UPDATE_URL
  try {
    await autoUpdater.checkForUpdates()
    // 成功（含 update-available / update-not-available 事件由监听器处理）
  } catch (e) {
    const msg = e && e.message || String(e)
    fwLog('[updater] check 失败:', msg)
    // 未显式指定更新源 + 网络类失败 → 回退镜像源重试一次
    if (!explicitFeed && isNetworkError(e)) {
      fwLog('[updater] 网络失败，回退镜像源:', UPDATE_MIRROR_URL)
      try {
        autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_MIRROR_URL })
        await autoUpdater.checkForUpdates()
        sendUpdateStatus('info', '正在检查更新…（镜像源）')
        return
      } catch (e2) {
        fwLog('[updater] 镜像源也失败:', e2 && e2.message || String(e2))
        sendUpdateStatus('error', friendlyUpdateError(e2))
        return
      }
    }
    sendUpdateStatus('error', friendlyUpdateError(e))
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
  // 更新源：默认使用打包时写入的 app-update.yml（build.publish = github provider）；
  // 显式设置 FRAMEWEAVE_UPDATE_URL 时覆盖为 generic（联调/企业部署用，且禁用镜像回退）
  const feedUrl = process.env.FRAMEWEAVE_UPDATE_URL
  if (feedUrl) {
    autoUpdater.setFeedURL({ provider: 'generic', url: feedUrl })
    console.log('[updater] 使用覆盖更新源:', feedUrl)
  } else {
    console.log('[updater] 使用打包配置更新源 (github provider)，失败自动回退镜像:', UPDATE_MIRROR_URL)
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
    updateInProgress = false
    const msg = err && err.message || String(err)
    console.error('[updater] 更新失败:', msg)
    fwLog('[updater] 更新失败:', msg)
    sendUpdateStatus('error', friendlyUpdateError(err))
  })

  // 启动 5 秒后检查（无网/更新源不通时静默失败，不再产生 unhandledRejection；
  // GitHub 失败自动回退镜像源，同样静默——不打断启动体验）
  setTimeout(() => {
    if (!app.isPackaged) return
    try {
      autoUpdater.checkForUpdates().catch(async (e) => {
        const msg = e && e.message || String(e)
        fwLog('[updater] 启动检查失败:', msg)
        // 网络类失败 → 静默回退镜像源再查一次
        if (!process.env.FRAMEWEAVE_UPDATE_URL && isNetworkError(e)) {
          fwLog('[updater] 启动检查回退镜像源:', UPDATE_MIRROR_URL)
          try {
            autoUpdater.setFeedURL({ provider: 'generic', url: UPDATE_MIRROR_URL })
            await autoUpdater.checkForUpdates()
          } catch (e2) {
            fwLog('[updater] 镜像源启动检查也失败:', e2 && e2.message || String(e2))
          }
        }
      })
    } catch (e) {
      fwLog('[updater] 启动检查异常:', e && e.message || String(e))
    }
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
