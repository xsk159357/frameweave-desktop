// 开发用静态服务器：服务 web/dist 并代理 /api -> 8788
const http = require('http')
const fs = require('fs')
const path = require('path')
const { createReadStream } = require('fs')

const DIST = path.join(__dirname, 'web', 'dist')
const BACKEND = '127.0.0.1'
const BACKEND_PORT = 8788

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.map': 'application/json',
}

http.createServer((req, res) => {
  const url = req.url || '/'

  // API 代理
  if (url.startsWith('/api/')) {
    const proxy = http.request({
      host: BACKEND, port: BACKEND_PORT, method: req.method,
      path: url, headers: req.headers,
    }, (pRes) => {
      res.writeHead(pRes.statusCode, pRes.headers)
      pRes.pipe(res)
    })
    proxy.on('error', () => { res.writeHead(502); res.end('proxy error') })
    req.pipe(proxy)
    return
  }

  // 静态文件（SPA：非文件路径回 index.html）
  let filePath = path.join(DIST, decodeURIComponent(url.split('?')[0]))
  if (url === '/') filePath = path.join(DIST, 'index.html')
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST, 'index.html')
  }
  const ext = path.extname(filePath).toLowerCase()
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' })
  createReadStream(filePath).pipe(res)
}).listen(5180, '127.0.0.1', () => {
  console.log('static server on http://127.0.0.1:5180 (dist proxy -> 8788)')
})
