// t43 商城自动刷新浏览器 E2E v3（修正手动刷新语义 + 请求计数 + 防 EBUSY）
// ① 轮询：云端 upsert → 页面 25s 轮询窗口内自动出现（API + 渲染双证据）
// ② 手动刷新：点击 RefreshCw → 立即（≤3s）发起 GET /api/market/items 并拉到最新云端列表
import { chromium } from 'playwright-core'
import { writeFileSync } from 'node:fs'

const BASE = 'http://localhost:5180'
const CLOUD = 'http://127.0.0.1:9789'
const ADMIN_KEY = 'test-admin-key'
const results = []
const out = []
const LINE = (s) => { out.push(s); console.log(s) }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function log(ok, name, detail = '') {
  results.push({ ok, name })
  LINE((ok ? '✅' : '❌') + ' ' + name + (detail ? ' | ' + detail : ''))
}

async function apiJson(url, body) {
  const r = await fetch(url, {
    method: body !== undefined ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return r.json()
}

async function main() {
  const adm = await apiJson(CLOUD + '/api/admin/login', { admin_key: ADMIN_KEY })
  log(adm.ok === true, '云端 admin 登录', adm.token ? 'token ok' : 'no token')
  const adminToken = adm.token

  const browser = await chromium.launch({
    channel: 'msedge', headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900', '--disable-dev-shm-usage'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', (e) => LINE('[pageerror] ' + String(e).slice(0, 200)))

  // 网络监控：统计 /api/market/items 请求
  let marketReqCount = 0
  page.on('request', (req) => { if (req.url().includes('/api/market/items') && req.method() === 'GET') marketReqCount++ })

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 25000 })
  await sleep(1500)

  // 登录
  try {
    await page.locator('input[placeholder="QQ 邮箱"]').first().fill('e2e43@qq.com')
    await page.locator('input[placeholder="密码"]').first().fill('pass1234')
    await page.locator('.lp-btn-main').first().click()
  } catch (e) { LINE('[login fill] ' + String(e).slice(0, 150)) }
  let sessionOk = false
  for (let i = 0; i < 20; i++) {
    await sleep(1000)
    try { if (await page.evaluate(() => !!localStorage.getItem('fw_session_v1'))) { sessionOk = true; break } } catch {}
  }
  log(sessionOk, '登录会话落地 (fw_session_v1)')
  if (!sessionOk) { await browser.close(); writeFileSync('F:/1223/解说工坊/fw_t43_verify/market_ui_e2e_v3.txt', out.join('\n'), 'utf8'); process.exit(1) }

  // 打开商城
  let marketOpened = false
  for (let i = 0; i < 20; i++) {
    try {
      await page.getByRole('button', { name: /商城/ }).first().click()
      await sleep(1000)
      if (await page.locator('button[title="刷新商城"]').count() > 0) { marketOpened = true; break }
    } catch { await sleep(500) }
  }
  log(marketOpened, '商城面板打开（RefreshCw 刷新按钮可见）')
  if (!marketOpened) { await browser.close(); writeFileSync('F:/1223/解说工坊/fw_t43_verify/market_ui_e2e_v3.txt', out.join('\n'), 'utf8'); process.exit(1) }

  // ① 轮询自动出现
  const newId = 'it_ui_' + Date.now().toString(36)
  const ups = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token: adminToken,
    item: { id: newId, title: 'UI轮询自动出现节点', kind: 'node', author: 't43', price: 0, version: '1.0.0', description: 't43 轮询验收' },
  })
  log(ups.ok === true, '云端 upsert 新条目', newId)
  let autoApi = false, autoUi = false, tAuto = 0
  const t0 = Date.now()
  for (let i = 0; i < 30; i++) {
    await sleep(1000)
    tAuto = Date.now() - t0
    try {
      const cur = await (await fetch(BASE + '/api/market/items')).json()
      if ((cur.items || []).some(x => x.id === newId)) autoApi = true
      if ((await page.evaluate(() => document.body.innerText)).includes('UI轮询自动出现节点')) autoUi = true
    } catch {}
    if (autoApi && autoUi) break
  }
  log(autoApi && autoUi, '轮询窗口(≤25s)内新条目自动出现', 'API=' + autoApi + ' UI=' + autoUi + ' t=' + (tAuto / 1000).toFixed(1) + 's')

  // ② 手动刷新即时生效：记录点击前请求数 → 点击 → 3s 内请求数 +1 且新条目拉到
  const newId2 = 'it_ui2_' + Date.now().toString(36)
  const ups2 = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token: adminToken,
    item: { id: newId2, title: '手动刷新第二节点', kind: 'node', author: 't43', price: 0, version: '1.0.0', description: 't43 手动刷新验收' },
  })
  log(ups2.ok === true, '云端再 upsert 第二个条目', newId2)
  const reqBefore = marketReqCount
  const t1 = Date.now()
  await page.locator('button[title="刷新商城"]').first().click()
  let manualUi = false
  for (let i = 0; i < 8; i++) {
    await sleep(400)
    try {
      if ((await page.evaluate(() => document.body.innerText)).includes('手动刷新第二节点')) { manualUi = true; break }
    } catch {}
  }
  const dt = Date.now() - t1
  const reqDelta = marketReqCount - reqBefore
  log(manualUi, 'RefreshCw 手动刷新：新条目立即出现', 't=' + (dt / 1000).toFixed(1) + 's')
  log(reqDelta >= 1, 'RefreshCw 触发即时 GET /api/market/items', '请求增量=' + reqDelta + '（不等 25s 轮询）')

  await page.screenshot({ path: 'F:/1223/解说工坊/fw_t43_verify/market_ui_shot_v3.png' })
  await browser.close().catch(() => {})
  const failed = results.filter((r) => !r.ok).length
  LINE('=== 商城自动刷新浏览器 E2E v3: ' + (failed === 0 ? '全部通过 (' + results.length + ')' : failed + '/' + results.length + ' 失败') + ' ===')
  writeFileSync('F:/1223/解说工坊/fw_t43_verify/market_ui_e2e_v3.txt', out.join('\n'), 'utf8')
  process.exit(failed ? 1 : 0)
}

main().catch((e) => { console.error('E2E 异常:', e); process.exit(1) })