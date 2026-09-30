import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// 版本号以 desktop/package.json 为唯一真源（打包链路 web 构建 → electron-builder 共用）
const appVersion = JSON.parse(readFileSync(join(__dirname, '..', 'desktop', 'package.json'), 'utf8')).version || '0.0.0'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // base 用相对路径：打包版经 Electron loadFile(file://) 加载，绝对路径 /assets 会指向磁盘根导致白屏
  base: './',
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(appVersion) },
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8788',
      '/ws': { target: 'ws://127.0.0.1:8788', ws: true }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
