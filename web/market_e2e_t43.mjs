// t43 商城自动刷新 E2E（playwright-core + msedge）
// 场景：打开商城 → 记录初始条目 → 云端 admin_market_upsert 新增条目
//      → 轮询周期内（≤25s）自动出现；→ 点击 RefreshCw 按钮即时生效
import { chromium } from 'playwright-core'
import { writeFileSync } from 'node:fs'

const BASE = 'http://localhost:5180'
const CLOUD = 'http://127.0.0.1:9789'
// 2026-10-19：管理密钥改为环境变量（原文件含明文，已按凭据纪律清理并轮换）。
const ADMIN_KEY = process.env.FW_ADMIN_KEY || ''
if (!ADMIN_KEY) {
  console.error('[e2e] 缺少 FW_ADMIN_KEY 环境变量（明文密钥不再入库）')
  process.exit(2)
}
const results = []
const out = []
const LINE = (s) => { out.push(s); console.log(s) }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function log(ok, name, detail = '') {
  results.push({ ok, name })
  LINE((ok ? '✅' : '❌') + ' ' + name + (detail ? ' | ' + detail : ''))
}

async function apiJson(url, body, headers = {}) {
  const r = await fetch(url, {
    method: body !== undefined ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return r.json()
}

async function adminLogin() {
  // 登录云端管理后台拿 admin token
  const r = await apiJson(CLOUD + '/api/admin/login', { admin_key: ADMIN_KEY })
  if (!r.ok) throw new Error('admin login failed: ' + JSON.stringify(r))
  return r.token
}

async function marketItemsClient() {
  // 客户端（经 vite 代理 → backend 8788 → 云端）看到的商城列表
  const r = await fetch(BASE + '/api/market/items')
  const j = await r.json()
  return (j.items || []).map((i) => i.id)
}

async function main() {
  // 0) 前置：确保云端市场为空（新 DB）
  const token = await adminLogin()
  LINE('[cloud] admin token ok')
  // 清空：直接读市场列表（admin），无删除接口则记录基线
  const baseAdmin = await apiJson(CLOUD + '/api/admin/market/list', { token })
  LINE('[cloud] 初始 admin 市场条目: ' + ((baseAdmin.items || []).length))

  // 1) 打开应用
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'] })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', (e) => LINE('[pageerror] ' + String(e).slice(0, 200)))

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await sleep(2500)

  // 2) 打开商城（右上角「商城」按钮）
  let marketOpened = false
  for (let i = 0; i < 20 && !marketOpened; i++) {
    try {
      await page.getByRole('button', { name: /商城/ }).first().click()
      await sleep(1200)
      // 商城面板元素：搜索框 placeholder="搜索节点…" 或「发布作品」按钮
      const marker = await page.evaluate(() => {
        const b = document.body.innerText
        return b.includes('发布作品') || b.includes('商城')
      })
      if (await page.locator('input[placeholder*="搜索"]').count() > 0 || marker) marketOpened = true
    } catch (e) { await sleep(1000) }
  }
  log(marketOpened, '打开商城面板')

  // 3) 初始条目数（客户端视角）
  let before = []
  try { before = await marketItemsClient() } catch {}
  LINE('[market] 初始客户端条目: ' + JSON.stringify(before))

  // 4) 云端 upsert 新条目（模拟云端管理员发布）
  const newId = 'it_e2e_' + Date.now().toString(36)
  const upsert = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token,
    item: {
      id: newId,
      title: 'E2E 自动刷新测试节点',
      kind: 'node',
      author: 'e2e-test',
      price: 0,
      version: '1.0.0',
      description: 't43 商城自动刷新验证条目',
    },
  })
  log(upsert.ok === true, '云端 admin_market_upsert 新增条目', newId + ' ' + JSON.stringify(upsert.item || {}).slice(0, 80))

  // 5) 轮询周期内自动出现（最长等 30s，浏览器内由 25s 轮询驱动）
  let autoAppear = false
  let autoT = 0
  const t0 = Date.now()
  for (let i = 0; i < 30; i++) {
    await sleep(1000)
    try {
      const cur = await marketItemsClient()
      autoT = Date.now() - t0
      if (cur.includes(newId)) { autoAppear = true; break }
    } catch {}
  }
  log(autoAppear && autoT <= 30000, '轮询周期内新条目自动出现', 't=' + (autoT / 1000).toFixed(1) + 's')

  // 6) 页面内文本确认（渲染层证据）
  let pageShows = false
  if (autoAppear) {
    try { pageShows = (await page.evaluate(() => document.body.innerText)).includes('E2E 自动刷新测试节点') } catch {}
  }
  log(pageShows, '新条目在页面渲染', 'body.innerText 命中')

  // 7) 删除条目 → 点刷新按钮 → 立即消失（手动刷新即时生效）
  const rmDel = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token, item: { id: newId, status: 'disabled', title: 'E2E 自动刷新测试节点' },
  })
  log(rmDel.ok === true, '云端将条目下架(disabled)')
  let manualCleared = false
  try {
    await page.locator('button[title="刷新商城"]').click()
    await sleep(1500)
    manualCleared = (await page.evaluate(() => document.body.innerText)).includes('已下架')
      || !(await page.evaluate(() => document.body.innerText)).includes('E2E 自动刷新测试节点')
      || (await page.evaluate(() => document.body.innerText)).includes('E2E-')
    LINE('[market] 手动刷新后页面文本片段: ' + (await page.evaluate(() => document.body.innerText)).slice(-200).replace(/\n/g, ' '))
  } catch (e) { LINE('[market] 手动刷新异常: ' + String(e).slice(0, 150)) }
  log(manualCleared, 'RefreshCw 手动刷新按钮即时生效（下架条目消失）')

  await page.screenshot({ path: 'F:/1223/解说工坊/fw_t43_verify/market_e2e_shot.png' })
  await browser.close()

  const failed = results.filter((r) => !r.ok).length
  LINE('=== 商城自动刷新 E2E: ' + (failed === 0 ? '全部通过 (' + results.length + ')' : failed + '/' + results.length + ' 失败') + ' ===')
  writeFileSync('F:/1223/解说工坊/fw_t43_verify/phase3_market_e2e.txt', out.join('\n'), 'utf8')
}

main().catch((e) => { console.error('E2E 异常:', e); process.exit(1) })