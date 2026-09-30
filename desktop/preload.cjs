// 预加载脚本：最小暴露（安全模型 contextIsolation）
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('frameweave', {
  backendPort: 8788,
  version: '0.1.0',
  // 自动更新桥
  checkForUpdate: () => ipcRenderer.send('check-for-update'),
  onUpdateAvailable: (cb) => ipcRenderer.on('update:available', (_e, v) => cb(v)),
  onUpdateProgress: (cb) => ipcRenderer.on('update:progress', (_e, p) => cb(p)),
});
