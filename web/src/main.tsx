import React from 'react'
import ReactDOM from 'react-dom/client'
import '@xyflow/react/dist/style.css'
import './index.css'
import App from './App'

// 全局错误捕获 → 写本地运行日志（黑匣子 debug.log）
function fwErr(level: string, where: string, err: unknown) {
  try {
    const w = window as any
    const msg = where + ': ' + (err instanceof Error ? (err.stack || err.message) : String(err))
    w.frameweave?.log?.(level, msg)
  } catch { /* 忽略 */ }
}
window.addEventListener('error', (e) => fwErr('error', 'window.onerror', e.error || e.message))
window.addEventListener('unhandledrejection', (e) => fwErr('error', 'unhandledrejection', e.reason))

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>
)
