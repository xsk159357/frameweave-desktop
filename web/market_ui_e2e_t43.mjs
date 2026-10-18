// t43 商城自动刷新浏览器 E2E v2（带阶段日志 + 总超时护栏；playwright-core + msedge）
// 流程：登录（云端桩）→ 打开商城 → 记录初始条目 → 云端 upsert → 轮询 ≤25s 自动出现
//      → 删除/下架条目 → 点击 RefreshCw 手动刷新 → 立即消失
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
const LINE = (s) => { out.push(s); console.log(s); }
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

async function main() {
  const deadline = Date.now() + 180000 // 总护栏 3 分钟
  const withTimeout = (p, ms, tag) => Promise.race([p, sleep(ms).then(() => { throw new Error('timeout:' + tag) })])

  // 0) 云端 admin 登录
  const adm = await apiJson(CLOUD + '/api/admin/login', { admin_key: ADMIN_KEY })
  log(adm.ok === true, '云端 admin 登录', adm.token ? 'token ok' : 'no token')
  const adminToken = adm.token

  // 1) 启动浏览器
  LINE('[stage] launch browser')
  const browser = await withTimeout(chromium.launch({
    channel: 'msedge', headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900', '--disable-dev-shm-usage'],
  }), 40000, 'launch')
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  page.on('pageerror', (e) => LINE('[pageerror] ' + String(e).slice(0, 200)))
  page.on('console', (m) => { if (m.type() === 'error') LINE('[console.error] ' + m.text().slice(0, 160)) })

  // 2) 打开应用（登录页）
  LINE('[stage] goto ' + BASE)
  await withTimeout(page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 25000 }), 30000, 'goto')
  await sleep(1500)
  const hasLogin = await page.evaluate(() => document.body.innerText.includes('登录'))
  LINE('[login page] hasLogin=' + hasLogin)

  // 3) 登录：填邮箱/密码 → 提交（走云端）
  try {
    await page.locator('input[placeholder="QQ 邮箱"]').first().fill('e2e43@qq.com')
    await page.locator('input[placeholder="密码"]').first().fill('pass1234')
    // 提交按钮 = .lp-btn-main（避免命中「登 录」seg 切换按钮）
    await page.locator('.lp-btn-main').first().click()
  } catch (e) { LINE('[login fill] ' + String(e).slice(0, 150)) }
  let sessionOk = false
  for (let i = 0; i < 20; i++) {
    await sleep(1000)
    try { if (await page.evaluate(() => !!localStorage.getItem('fw_session_v1'))) { sessionOk = true; break } } catch {}
  }
  log(sessionOk, '登录会话落地 (fw_session_v1)')

  // 4) 打开商城
  let marketOpened = false
  for (let i = 0; i < 20; i++) {
    try {
      await page.getByRole('button', { name: /商城/ }).first().click()
      await sleep(1000)
      if (await page.locator('button[title="刷新商城"], button[title="刷新"]').count() > 0
        || (await page.evaluate(() => document.body.innerText.includes('发布作品')))) { marketOpened = true; break }
    } catch (e) { await sleep(500) }
  }
  log(marketOpened, '商城面板打开（可见 RefreshCw 刷新按钮/发布作品）')

  // 5) 初始条目（客户端视角 + 页面渲染）
  let before = []
  try { before = await (await fetch(BASE + '/api/market/items')).json().then(j => (j.items || []).map(i => i.id)) } catch {}
  LINE('[market] before=' + JSON.stringify(before))
  const pageBefore = await page.evaluate(() => document.body.innerText)
  const newId = 'it_ui_' + Date.now().toString(36)

  // 6) 云端 upsert 新条目
  const ups = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token: adminToken,
    item: { id: newId, title: 'UI自动刷新验证节点', kind: 'node', author: 't43', price: 0, version: '1.0.0',
            description: 't43 商城自动刷新浏览器验收' },
  })
  log(ups.ok === true, '云端 upsert 新条目', newId)

  // 7) 轮询周期（≤25s）内自动出现：客户端 API + 页面渲染
  let autoApi = false, autoUi = false, tAuto = 0
  const t0 = Date.now()
  for (let i = 0; i < 30; i++) {
    await sleep(1000)
    tAuto = Date.now() - t0
    try {
      const cur = await (await fetch(BASE + '/api/market/items')).json()
      if ((cur.items || []).some(x => x.id === newId)) autoApi = true
      const txt = await page.evaluate(() => document.body.innerText)
      if (txt.includes('UI自动刷新验证节点')) autoUi = true
    } catch {}
    if (autoApi && autoUi) break
  }
  log(autoApi, '轮询内新条目客户端 API 自动出现', 't=' + (tAuto / 1000).toFixed(1) + 's')
  log(autoUi, '轮询内新条目页面渲染自动出现', 't=' + (tAuto / 1000).toFixed(1) + 's')

  // 8) 云端下架 → 点击 RefreshCw 手动刷新 → 立即消失
  const dis = await apiJson(CLOUD + '/api/admin/market/upsert', {
    token: adminToken,
    item: { id: newId, title: 'UI自动刷新验证节点', status: 'disabled', kind: 'node' },
  })
  log(dis.ok === true, '云端将条目下架(disabled)')
  let manual = false, tManual = 0
  try {
    const t1 = Date.now()
    await page.locator('button[title="刷新商城"], button[title="刷新"]').first().click()
    // 等待刷新完成（旋转态结束/内容更新）
    for (let i = 0; i < 15; i++) {
      await sleep(500)
      const txt = await page.evaluate(() => document.body.innerText)
      const still = txt.includes('UI自动刷新验证节点')
      if (!still) { tManual = Date.now() - t1; manual = true; break }
    }
  } catch (e) { LINE('[refresh] ' + String(e).slice(0, 150)) }
  log(manual, 'RefreshCw 手动刷新即时生效（下架条目消失）', 't=' + (tManual / 1000).toFixed(1) + 's')

  await page.screenshot({ path: 'F:/1223/解说工坊/fw_t43_verify/market_ui_shot.png' })
  await browser.close().catch(() => {})

  const failed = results.filter((r) => !r.ok).length
  LINE('=== 商城自动刷新浏览器 E2E: ' + (failed === 0 ? '全部通过 (' + results.length + ')' : failed + '/' + results.length + ' 失败') + ' ===')
  writeFileSync('F:/1223/解说工坊/fw_t43_verify/phase3_market_ui_e2e.txt', out.join('\n'), 'utf8')
  process.exit(failed ? 1 : 0)
}

main().catch((e) => { console.error('E2E 异常:', e); process.exit(1) })