#!/usr/bin/env node
// P2 asar 完整性清单生成器（electron-builder 之前运行）
//
// 作用：对最终会进入 app.asar 的关键文件计算 SHA-256 预期值，写入 desktop/integrity.json。
// main.cjs 启动时（仅打包版）按同一清单重新哈希比对，不匹配即拒绝启动，
// 防止攻击者解包 asar 替换 main.cjs / preload.cjs / web/dist 前端后绕过授权校验。
//
// 注意：清单刻意不包含 package.json —— electron-builder 打包时会改写它
// （removePackageScripts 删除 scripts、注入版本等），源 package.json 的哈希
// 与 asar 内最终内容不一致；其余被列入 files 的文件均原样拷贝，哈希稳定。
'use strict'

const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')          // desktop/
const webDist = path.resolve(root, '..', 'web', 'dist')

function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

// 递归收集目录下全部文件的相对路径 -> sha256
function collectDir(dir, prefix) {
  const out = {}
  if (!fs.existsSync(dir)) return out
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    const rel = prefix ? prefix + '/' + entry.name : entry.name
    if (entry.isDirectory()) Object.assign(out, collectDir(full, rel))
    else out[rel] = sha256File(full)
  }
  return out
}

const files = {}
files['main.cjs'] = sha256File(path.join(root, 'main.cjs'))
files['preload.cjs'] = sha256File(path.join(root, 'preload.cjs'))

if (!fs.existsSync(path.join(webDist, 'index.html'))) {
  console.error('[embed-integrity] 缺少 web/dist —— 请先执行前端构建（npm run build:web）')
  process.exit(1)
}
Object.assign(files, collectDir(webDist, 'web/dist'))

const manifest = {
  v: 1,
  generated_at: new Date().toISOString(),
  files,
}

fs.writeFileSync(path.join(root, 'integrity.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log('[embed-integrity] integrity.json 已生成：' + Object.keys(files).length + ' 个文件')
for (const rel of Object.keys(files)) console.log('  - ' + rel + '  ' + files[rel].slice(0, 16) + '...')
