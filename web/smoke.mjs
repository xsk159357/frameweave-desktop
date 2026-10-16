// FrameWeave UI 冒烟测试 v6（playwright-core + 系统 Edge）
// 前置漂移修复（A2 报告）：
//  1) 本地 API 写操作补 X-FW-Local-Token（dev 回落常量 dev-local-token）
//  2) 工作流选择：不再硬取 wfs[0]（其可能是空工作流），改为选有 >=3 节点的首个工作流（优先离线最小链）
//  3) 登录：纯云化（QQ 邮箱+密码），无云端测试账号时用 FW_E2E_SEED_SESSION=1 种子会话 + 拦截 /api/auth/verify
//     （保证画布与 S1-T7 web 目录注入链可在真实浏览器验证；云登录本身需真实凭据，如实注明）
//  4) 登录选择器对齐当前 LoginPage（placeholder="QQ 邮箱" + 主按钮 .lp-btn-main）
import { chromium } from 'playwright-core'
import { writeFileSync } from 'node:fs'

const BASE = 'http://localhost:5180'
const API = 'http://127.0.0.1:8788'
const TOKEN = 'dev-local-token'
const EMAIL = process.env.FW_E2E_EMAIL || 'smoker@test.local'
const PASS = process.env.FW_E2E_PASS || 'smoke1234'
const SEED = process.env.FW_E2E_SEED_SESSION !== '0'   // 默认种子会话（云登录无凭据）
const hdr = { 'Content-Type': 'application/json', 'X-FW-Local-Token': TOKEN }
const results = []
const out = []
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const LINE = (s) => { out.push(s); console.log(s) }

function log(ok, name, detail = '') {
  results.push({ ok, name })
  LINE((ok ? '✅' : '❌') + ' ' + name + (detail ? ' | ' + detail : ''))
}

async function pickWorkflow() {
  // 优先离线最小链，其次任意有 >=3 节点的工作流；无则创建 4 节点链
  const pref = ['265a8c12a785']
  const list = await (await fetch(API + '/api/workflows', { headers: hdr })).json()
  for (const id of pref) {
    if (list.some(w => w.id === id)) {
      const w = await (await fetch(API + '/api/workflows/' + id, { headers: hdr })).json()
      if ((w.nodes || []).length >= 3) return w
    }
  }
  for (const w of list) {
    const g = await (await fetch(API + '/api/workflows/' + w.id, { headers: hdr })).json()
    if ((g.nodes || []).length >= 3) return g
  }
  // 兜底：创建 4 节点最小链
  const mk = await fetch(API + '/api/workflows', {
    method: 'POST', headers: hdr,
    body: JSON.stringify({ name: 'e2e-min', nodes: [
      { id: 'n1', type: 'core/text_input', x: 60, y: 300, params: { value: 'hello world 1234' } },
      { id: 'n2', type: 'core/text_length', x: 260, y: 300, params: {} },
      { id: 'n3', type: 'core/json_output', x: 460, y: 300, params: {} },
      { id: 'n4', type: 'core/console_output', x: 660, y: 300, params: {} },
    ], edges: [
      { from: 'n1', from_port: 'text', to: 'n2', to_port: 'text' },
      { from: 'n2', from_port: 'length', to: 'n3', to_port: 'data' },
      { from: 'n3', from_port: 'json', to: 'n4', to_port: 'data' },
    ] }),
  })
  const created = await mk.json()
  const g = await (await fetch(API + '/api/workflows/' + created.id, { headers: hdr })).json()
  return g
}

async function main() {
  // 0. 准备：选工作流（有节点）+ 造首节点运行历史（selection run + force reset）
  let wf
  try { wf = await pickWorkflow() } catch (e) { LINE('[准备] 选工作流失败: ' + String(e).slice(0, 120)) }
  const wid = wf ? wf.id : ''
  const targetId = wf && wf.nodes[0] ? wf.nodes[0].id : 'n1'
  LINE('[准备] 目标工作流: ' + wid + ' 节点数=' + (wf ? wf.nodes.length : 0) + ' 目标节点=' + targetId)
  if (wid) {
    try {
      // 重置全部节点（force），确保一键出片走真实重算而非缓存直通（worker 模式可观测「执行中」）
      for (const n of wf.nodes) {
        await fetch(API + '/api/workflows/' + wid + '/nodes/' + n.id + '/reset', {
          method: 'POST', headers: hdr, body: JSON.stringify({ force: true }),
        })
      }
      LINE('[准备] 已重置 ' + wf.nodes.length + ' 节点')
      const r = await fetch(API + '/api/workflows/' + wid + '/run', {
        method: 'POST', headers: hdr,
        body: JSON.stringify({ node_ids: [targetId], mode: 'selection' }),
      })
      LINE('[准备] 首节点重跑触发: ' + r.status)
      for (let i = 0; i < 20; i++) {
        await sleep(2000)
        const g = await (await fetch(API + '/api/workflows/' + wid, { headers: hdr })).json()
        const n1 = g.nodes.find(n => n.id === targetId)
        if (n1 && (n1.run_history || []).length > 0 && n1.status === 'success') {
          LINE('[准备] 首节点已有历史 ' + n1.run_history.length + ' 条 status=' + n1.status)
          break
        }
      }
    } catch (e) { LINE('[准备] 造历史失败: ' + String(e).slice(0, 120)) }
  }

  const browser = await chromium.launch({
    channel: 'msedge', headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,900'],
  })
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 } })
  if (SEED) {
    // e2e 种子会话：本地注入会话 + 拦截 /api/auth/verify（云登录需真实凭据，此处只验证画布与注入链）
    await context.addInitScript(() => {
      localStorage.setItem('fw_device_id', 'e2e-device-0001')
      localStorage.setItem('fw_session_v1', JSON.stringify({
        token: 'e2e-local-token', email: 'smoker@test.local', expiresAt: Date.now() + 86400e3,
        plan: 'trial', credits: 0, deviceId: 'e2e-device-0001',
      }))
    })
    await context.route('**/api/auth/verify**', (route) => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, verified: true, mode: 'offline_cache', plan: 'trial', credits: 0, expires_at: Math.floor(Date.now() / 1000) + 86400 }),
    }))
  }
  const page = await context.newPage()
  page.on('pageerror', e => LINE('[pageerror] ' + String(e).slice(0, 300)))

  // 1. 登录页 / 已注入会话直接进入工作区
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await sleep(2500)
  if (SEED) {
    const entered = await page.evaluate(() => !document.body.innerText.includes('欢迎回来'))
    log(entered, '会话注入生效（跳过云登录）')
    if (!entered) { const body = await page.evaluate(() => document.body.innerText); LINE('[page text] ' + body.slice(0, 300)) }
  } else {
    const hasLogin = await page.evaluate(() => document.body.innerText.includes('登录'))
    log(hasLogin, '登录页渲染')
    try {
      await page.locator('input[placeholder="QQ 邮箱"]').fill(EMAIL)
      await page.locator('input[placeholder="密码"]').fill(PASS)
      log(true, '表单填写')
    } catch (e) { log(false, '表单填写', String(e).slice(0, 120)) }
    try {
      await page.locator('.lp-btn-main').click()
      log(true, '提交登录')
    } catch (e) { log(false, '提交登录', String(e).slice(0, 120)) }
    let sessionOk = false
    for (let i = 0; i < 15; i++) {
      await sleep(1000)
      try { if (await page.evaluate(() => !!localStorage.getItem('fw_session_v1'))) { sessionOk = true; break } } catch {}
    }
    log(sessionOk, '登录会话落地')
  }

  // 2. 主界面
  let entered = false
  for (let i = 0; i < 20; i++) {
    await sleep(1000)
    try { if (await page.getByRole('button', { name: /一键出片/ }).count() > 0) { entered = true; break } } catch {}
  }
  log(entered, '进入主界面')
  if (!entered) {
    const body = await page.evaluate(() => document.body.innerText)
    LINE('[page text] ' + body.slice(0, 400))
    try { await page.screenshot({ path: 'F:/1223/解说工坊/FrameWeave/.tmp/smoke_fail3.png' }) } catch {}
    await browser.close()
    LINE('=== 冒烟测试结果: ' + results.filter(r => !r.ok).length + '/' + results.length + ' 失败 ===')
    writeFileSync('F:/1223/解说工坊/FrameWeave/.tmp/smoke_result.txt', out.join('\n'), 'utf8')
    return
  }

  // 2.5 切到目标工作流（App 默认打开 list[0] 可能是空工作流）
  if (wid) {
    try {
      const item = page.locator('.fw-scroll > div').filter({ hasText: (wf && wf.name) || wid })
      await item.first().click({ force: true })
      await sleep(1800)
      LINE('[切流] 已点击目标工作流 ' + ((wf && wf.name) || wid))
    } catch (e) { LINE('[切流] 失败: ' + String(e).slice(0, 120)) }
  }

  // 3. 画布节点
  await sleep(2500)
  const nodeCount = await page.locator('.react-flow__node').count()
  log(nodeCount >= 3, '画布节点数 >= 3', '实际=' + nodeCount)

  // 4. S1-T7（R-平台-7）：插件 web 目录注入（EXTENSION_WEB_DIRS 等价）
  try {
    const extResp = await fetch(API + '/api/extensions')
    const extJ = await extResp.json()
    const demo = (extJ.extensions || []).find((e) => e.type_id === 'user/web_demo')
    log(!!demo, '扩展清单包含 user/web_demo', demo ? (demo.url + ' v' + demo.version) : '清单缺失')
    let injected = false
    for (let i = 0; i < 24; i++) {
      await sleep(500)
      try {
        if (await page.locator('#fw-ext-root .fw-ext-demo').count() > 0) { injected = true; break }
      } catch { /* 宿主未渲染/未注入 */ }
    }
    log(injected, '画布注入自定义组件（.fw-ext-demo）')
    if (injected) {
      await page.locator('#fw-ext-root .fw-ext-demo-btn').click()
      await sleep(300)
      const n = await page.locator('#fw-ext-root .fw-ext-demo-count').innerText()
      log(n === '1', '注入组件交互（点击计数=1）', '实际=' + n)
    }
    const cssResp = await fetch(API + '/extensions/user_web_demo/web/style.css')
    const cssTxt = await cssResp.text()
    log(cssResp.status === 200 && /fw-ext-panel/.test(cssTxt), '插件 web 静态资源可访问（style.css）', 'HTTP ' + cssResp.status)
  } catch (e) { log(false, 'S1-T7 web 目录注入', String(e).slice(0, 160)) }

  // 5. 双击首节点 → 参数面板 + 运行历史
  try {
    await page.locator('.react-flow__node').first().dblclick({ force: true })
    await sleep(1500)
    const hasHistory = await page.evaluate(() => document.body.innerText.includes('运行历史'))
    const hasPanel = await page.evaluate(() => document.body.innerText.includes('执行状态'))
    log(hasHistory, '运行历史区块')
    log(hasPanel, '参数面板')
  } catch (e) { log(false, '双击节点', String(e).slice(0, 120)) }

  // 6. 一键出片 → 执行中（快速路径容忍：L0 内置节点 in-process 直通，窗口 <350ms；
  //     WS 运行态已由 test_ws_events 覆盖，此处验证 UI 确实触发了真实重算）
  let sawRunning = false
  let ran = false
  try {
    const totalHist = async () => {
      const g = await (await fetch(API + '/api/workflows/' + wid, { headers: hdr })).json()
      return (g.nodes || []).reduce((s, n) => s + ((n.run_history || []).length), 0)
    }
    const histBefore = await totalHist()
    await page.getByRole('button', { name: /一键出片/ }).click()
    for (let i = 0; i < 8; i++) {
      await sleep(350)
      try {
        const txt = await page.evaluate(() => [...document.querySelectorAll('button')].map(b => b.innerText).join('|'))
        if (txt.includes('执行中')) { sawRunning = true; break }
      } catch {}
    }
    for (let i = 0; i < 100; i++) {
      await sleep(3000)
      try {
        const t = await page.evaluate(() => document.body.innerText)
        if (!t.includes('执行中')) { break }
      } catch {}
    }
    const histAfter = await totalHist()
    ran = histAfter > histBefore
    log(sawRunning || ran, '运行触发（执行中或历史增长）',
      sawRunning ? '执行中可见' : (ran ? ('快速路径直通，全节点历史 ' + histBefore + '→' + histAfter) : '历史未增 ' + histBefore + '→' + histAfter))
  } catch (e) { log(false, '触发一键出片', String(e).slice(0, 120)) }
  try {
    const g = await fetch(API + '/api/workflows/' + wid, { headers: hdr })
    const gj = await g.json()
    const statList = gj.nodes.map(n => n.id + ':' + (n.status || '?')).join(' ')
    LINE('[节点状态] ' + statList)
    const allDone = gj.nodes.every(n => ['success', 'failed', 'cached', 'cancelled'].includes(n.status))
    log(allDone, '节点全部落定')
  } catch (e) { log(false, 'API 节点状态确认', String(e).slice(0, 80)) }

  try { await page.screenshot({ path: 'F:/1223/解说工坊/FrameWeave/.tmp/smoke_shot.png' }) } catch {}
  await browser.close()

  const failed = results.filter(r => !r.ok).length
  LINE('=== 冒烟测试结果: ' + (failed === 0 ? '全部通过 (' + results.length + ')' : failed + '/' + results.length + ' 失败') + ' ===')
  writeFileSync('F:/1223/解说工坊/FrameWeave/.tmp/smoke_result.txt', out.join('\n'), 'utf8')
}

main().catch(e => {
  console.error('冒烟测试异常:', e)
  process.exit(1)
})
