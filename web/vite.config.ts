import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // base 用相对路径：打包版经 Electron loadFile(file://) 加载，绝对路径 /assets 会指向磁盘根导致白屏
  base: './',
  plugins: [react()],
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
