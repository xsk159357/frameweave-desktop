// 预加载脚本：最小暴露（安全模型 contextIsolation）
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('frameweave', {
  backendPort: 8788,
  version: '0.2.12',
  // H3 修复：本地 API 鉴权令牌（打包版主进程注入随机值；dev 回落常量）
  localToken: process.env.FRAMEWEAVE_LOCAL_TOKEN || 'dev-local-token',
  // 运行日志（黑匣子）：渲染进程写 debug.log
  log: (level, msg) => ipcRenderer.send('fw-log', { level, msg }),
  // 自动更新桥
  checkForUpdate: () => ipcRenderer.send('check-for-update'),
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
});
