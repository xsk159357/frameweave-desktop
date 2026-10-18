// 预加载脚本（P2 最小化）：仅暴露渲染层必需桥，保持 contextIsolation + 无 nodeIntegration
// 沙箱模式（webPreferences.sandbox=true）下可用的 API 受限：electron(contextBridge/ipcRenderer)、
// process.argv 等少量属性；本地 API 令牌经 additionalArguments(--fw-local-token=...) 传入，
// 不依赖 process.env（沙箱中不可用），也不落 localStorage。
const { contextBridge, ipcRenderer } = require('electron')

// 从 additionalArguments 取令牌（main.cjs 注入：打包版随机值 / dev 固定 dev-local-token）
function readLocalToken() {
  const argv = (process.argv || [])
  const arg = argv.find((a) => String(a).startsWith('--fw-local-token='))
  const tok = arg ? arg.slice('--fw-local-token='.length) : ''
  return tok || ''   // 取不到时留空，前端回落 dev-local-token（与后端 dev 常量一致）
}

const token = readLocalToken()

contextBridge.exposeInMainWorld('frameweave', {
  backendPort: 8788,
  version: '0.3.12',
  localToken: token,
  // 运行日志（黑匣子）：渲染进程写 debug.log
  log: (level, msg) => ipcRenderer.send('fw-log', { level, msg }),
  // 自动更新桥
  checkForUpdate: () => ipcRenderer.send('check-for-update'),
  downloadUpdate: () => ipcRenderer.send('download-update'),
  installUpdate: () => ipcRenderer.send('install-update'),
  onUpdateAvailable: (cb) => ipcRenderer.on('update:available', (_e, v) => cb(v)),
  onUpdateProgress: (cb) => ipcRenderer.on('update:progress', (_e, p) => cb(p)),
  onUpdateStatus: (cb) => ipcRenderer.on('update:status', (_e, s) => cb(s)),
  // 窗口控制桥（无边框方案A：titleBarOverlay 原生控件 + 渲染层可编程控制）
  window: {
    minimize: () => ipcRenderer.send('window:minimize'),
    toggleMaximize: () => ipcRenderer.send('window:toggle-maximize'),
    close: () => ipcRenderer.send('window:close'),
    isMaximized: () => ipcRenderer.invoke('window:is-maximized'),
    onMaximizeChange: (cb) => {
      const h = (_e, v) => { try { cb(v) } catch { /* 渲染层异常忽略 */ } }
      ipcRenderer.on('window:maximized-changed', h)
      return () => ipcRenderer.removeListener('window:maximized-changed', h)
    },
    // 主题联动：设置原生控件 Overlay 配色（打包版生效；dev/浏览器模式静默）
    setTitlebarTheme: (theme) => ipcRenderer.send('window:set-titlebar-overlay', theme),
  },
})
