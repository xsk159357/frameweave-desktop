// Electron 主进程：启动本地 Python 后端 + 加载前端
const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { autoUpdater } = require('electron-updater')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')

let mainWindow = null
let backendProc = null
const BACKEND_PORT = 8788

// 找到本机后端 Python
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
          resolve(j && (j.status === 'ok' || j.app === 'frameweave' || j.ok) ? 'frameweave' : 'other')
        } catch { resolve('other') }
      })
    })
    req.on('error', () => resolve(null)) // 端口未监听或无响应 → 空闲
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
  // 端口占用检测：已是本服务(复用) / 其他程序(友好报错) / 空闲(正常启动)
  const occ = await probePort()
  if (occ === 'frameweave') {
    console.log('[backend] 已检测到运行中的 FrameWeave 后端，直接复用')
    return
  }
  if (occ === 'other') {
    dialog.showErrorBox('拾帧 FrameWeave',
      '端口 ' + BACKEND_PORT + ' 被其他程序占用。\n\n请关闭占用该端口的程序后重启应用。')
    return
  }
  try {
    const { cmd, backendMain } = findBackendCommand()
    backendProc = backendMain
      ? spawn(cmd, ['-u', backendMain], { env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, stdio: 'ignore' })
      : spawn(cmd, [], { env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, stdio: 'ignore' })
    const ok = await waitForBackend()
    if (!ok) {
      dialog.showErrorBox('FrameWeave', '后端服务启动超时，请检查 Python 环境')
    }
  } catch (e) {
    dialog.showErrorBox('FrameWeave', '启动后端失败: ' + e.message)
  }
}

// 无边框窗口控制：仅通过 preload 暴露给渲染进程
ipcMain.on('window:minimize', () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.minimize() })
ipcMain.on('window:toggle-maximize', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (mainWindow.isMaximized()) mainWindow.unmaximize()
  else mainWindow.maximize()
})
ipcMain.on('window:close', () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close() })
ipcMain.handle('window:is-maximized', () => !!(mainWindow && !mainWindow.isDestroyed() && mainWindow.isMaximized()))

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: '拾帧 FrameWeave',
    frame: false,
    resizable: true,
    backgroundColor: '#0a0e18',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  const devUrl = 'http://localhost:5180'
  const distDir = path.join(app.getAppPath(), 'web', 'dist')
  const prodFile = path.join(distDir, 'index.html')

  // 开发模式优先连 Vite dev server；否则加载构建产物
  http.get(devUrl + '/api/health', (res) => {
    res.resume()
    mainWindow.loadURL(devUrl)
  }).on('error', () => {
    if (fs.existsSync(prodFile)) mainWindow.loadFile(prodFile)
    else mainWindow.loadURL(devUrl) // 页面会显示连接失败提示
  })

  mainWindow.on('closed', () => { mainWindow = null })
}

app.whenReady().then(async () => {
  // 先确认本地后端就绪，再加载页面，避免首屏 Failed to fetch
  await startBackend()
  createWindow()
  setupAutoUpdate()
})

// ---- 自动更新（electron-updater） ----
let updateInProgress = false

function setupAutoUpdate() {
  if (!app.isPackaged) {
    console.log('[updater] 开发模式跳过自动更新')
    return
  }
  // 更新源 URL 可被环境变量覆盖（企业部署）
  const feedUrl = process.env.FRAMEWEAVE_UPDATE_URL
  // 未配置真实更新源时跳过检查，避免示例域名 DNS 错误污染启动日志
  if (!feedUrl || /example\.com/i.test(feedUrl)) {
    console.log('[updater] 未配置有效更新源，跳过自动更新')
    return
  }
  autoUpdater.setFeedURL({ provider: 'generic', url: feedUrl })

  autoUpdater.autoDownload = false      // 用户确认后下载
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    const v = info && info.version
    mainWindow && mainWindow.webContents.send('update:available', v)
    if (mainWindow && !updateInProgress) {
      dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: '发现新版本',
        message: '发现新版本 ' + v + '，是否现在下载并安装？',
        buttons: ['立即更新', '稍后'],
        defaultId: 0,
        cancelId: 1,
      }).then(({ response }) => {
        if (response === 0) {
          updateInProgress = true
          autoUpdater.downloadUpdate()
        }
      })
    }
  })

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] 已是最新版本')
  })

  autoUpdater.on('download-progress', (p) => {
    if (mainWindow) {
      mainWindow.webContents.send('update:progress', Math.round((p.percent || 0) * 100) / 100)
    }
  })

  autoUpdater.on('update-downloaded', (info) => {
    updateInProgress = false
    if (mainWindow) {
      dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: '更新就绪',
        message: '新版本 ' + info.version + ' 已下载完成，重启应用完成更新？',
        buttons: ['立即重启', '稍后'],
        defaultId: 0,
      }).then(({ response }) => {
        if (response === 0) autoUpdater.quitAndInstall()
      })
    }
  })

  autoUpdater.on('error', (err) => {
    updateInProgress = false
    console.error('[updater] 更新失败:', err && err.message)
  })

  // 前端可主动触发检查
  ipcMain.on('check-for-update', () => {
    autoUpdater.checkForUpdates().catch((err) => console.error('[updater] 手动检查失败:', err && err.message))
  })

  setTimeout(() => autoUpdater.checkForUpdates().catch((err) => console.error('[updater] 自动检查失败:', err && err.message)), 5000)
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('quit', () => {
  if (backendProc && !backendProc.killed) {
    try { backendProc.kill() } catch { /* ignore */ }
  }
})