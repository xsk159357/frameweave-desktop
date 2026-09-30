// FrameWeave M13 UI 冒烟测试 v5（playwright-core + 系统 Edge）
import { chromium } from 'playwright-core'
import { writeFileSync } from 'node:fs'

const BASE = 'http://localhost:5180'
const API = 'http://127.0.0.1:8788'
const EMAIL = 'smoker@test.local'
const PASS = 'smoke1234'
const results = []
const out = []
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const LINE = (s) => { out.push(s); console.log(s) }

function log(ok, name, detail = '') {
  results.push({ ok, name })
  LINE((ok ? '✅' : '❌') + ' ' + name + (detail ? ' | ' + detail : ''))
}

async function main() {
  // 0. API 准备：对 list[0] 的轻节点 n1 造运行历史（force reset + selection run）
  const wfResp = await fetch(API + '/api/workflows')
  const wfs = await wfResp.json()
  const wid = wfs[0].id
  LINE('[准备] 目标工作流: ' + wid)
  try {
    await fetch(API + '/api/workflows/' + wid + '/nodes/n1/reset', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: true }),
    })
    const r = await fetch(API + '/api/workflows/' + wid + '/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_ids: ['n1'], mode: 'selection' }),
    })
    LINE('[准备] n1 重跑触发: ' + r.status)
    for (let i = 0; i < 20; i++) {
      await sleep(2000)
      const g = await fetch(API + '/api/workflows/' + wid)
      const gj = await g.json()
      const n1 = gj.nodes.find(n => n.id === 'n1')
      if (n1 && (n1.run_history || []).length > 0 && n1.status === 'success') {
        LINE('[准备] n1 已有历史 ' + n1.run_history.length + ' 条 status=' + n1.status)
        break
      }
    }
  } catch (e) { LINE('[准备] n1 造历史失败: ' + String(e).slice(0, 120)) }

  const browser = await chromium.launch({
    channel: 'msedge', headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,900'],
  })
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
  page.on('pageerror', e => LINE('[pageerror] ' + String(e).slice(0, 300)))

  // 1. 登录页
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await sleep(2000)
  const hasLogin = await page.evaluate(() => document.body.innerText.includes('登录'))
  log(hasLogin, '登录页渲染')

  // 2. 填表单
  try {
    await page.locator('input[placeholder="邮箱"]').fill(EMAIL)
    await page.locator('input[placeholder="密码"]').fill(PASS)
    log(true, '表单填写')
  } catch (e) { log(false, '表单填写', String(e).slice(0, 120)) }

  // 3. 点「登 录」（精确文本，排除 mode tab）
  try {
    await page.locator('button').filter({ hasText: '登 录' }).first().click()
    log(true, '提交登录')
  } catch (e) { log(false, '提交登录', String(e).slice(0, 120)) }

  // 4. 会话落地
  let sessionOk = false
  for (let i = 0; i < 15; i++) {
    await sleep(1000)
    try { if (await page.evaluate(() => !!localStorage.getItem('fw_session_v1'))) { sessionOk = true; break } } catch {}
  }
  log(sessionOk, '登录会话落地')

  // 5. 主界面
  let entered = false
  for (let i = 0; i < 20; i++) {
    await sleep(1000)
    try { if (await page.getByRole('button', { name: /一键出片/ }).count() > 0) { entered = true; break } } catch {}
  }
  log(entered, '进入主界面')
  if (!entered) {
    const body = await page.evaluate(() => document.body.innerText)
    LINE('[page text] ' + body.slice(0, 400))
    await page.screenshot({ path: 'F:/1223/解说工坊/FrameWeave/.tmp/smoke_fail3.png' })
    await browser.close()
    LINE('=== 冒烟测试结果: ' + results.filter(r => !r.ok).length + '/' + results.length + ' 失败 ===')
    writeFileSync('F:/1223/解说工坊/FrameWeave/.tmp/smoke_result.txt', out.join('\n'), 'utf8')
    return
  }

  // 6. 画布节点
  await sleep(2500)
  const nodeCount = await page.locator('.react-flow__node').count()
  log(nodeCount >= 3, '画布节点数 >= 3', '实际=' + nodeCount)

  // 7. 双击 n1（第一个节点）→ 参数面板 + 运行历史（M11）
  try {
    await page.locator('.react-flow__node').first().dblclick({ force: true })
    await sleep(1500)
    const hasHistory = await page.evaluate(() => document.body.innerText.includes('运行历史'))
    const hasPanel = await page.evaluate(() => document.body.innerText.includes('执行状态'))
    log(hasHistory, '运行历史区块（M11）')
    log(hasPanel, '参数面板')
  } catch (e) { log(false, '双击节点', String(e).slice(0, 120)) }

  // 8. 一键出片 → 立即轮询「执行中」（HTTP 往返窗口必显示；缓存直通可能很快消失）
  let sawRunning = false
  try {
    await page.getByRole('button', { name: /一键出片/ }).click()
    for (let i = 0; i < 8; i++) {
      await sleep(350)
      try {
        const txt = await page.evaluate(() => [...document.querySelectorAll('button')].map(b => b.innerText).join('|'))
        if (txt.includes('执行中')) { sawRunning = true; break }
      } catch {}
    }
    log(sawRunning, '运行状态切换（执行中）')
  } catch (e) { log(false, '触发一键出片', String(e).slice(0, 120)) }

  // 9. 等完成（轮询按钮文本不再「执行中」+ 结果面板）
  let finished = false
  for (let i = 0; i < 100; i++) {
    await sleep(3000)
    try {
      const t = await page.evaluate(() => document.body.innerText)
      // 执行完成 = 「执行中」消失（本流无 batch_render，批量结果面板专属含 batch_render 流）
      if (!t.includes('执行中')) { finished = true; break }
    } catch {}
    if (i === 99) LINE('[timeout] 300s 后未完成')
  }
  log(finished, '执行完成（running→false）')
  // 用 API 确认节点状态
  try {
    const g = await fetch('http://127.0.0.1:8788/api/workflows/' + wid)
    const gj = await g.json()
    const statList = gj.nodes.map(n => n.id + ':' + (n.status || '?')).join(' ')
    LINE('[节点状态] ' + statList)
    const allDone = gj.nodes.every(n => ['success', 'failed', 'cached', 'cancelled'].includes(n.status))
    log(allDone, '节点全部落定')
  } catch (e) { log(false, 'API 节点状态确认', String(e).slice(0, 80)) }

  await page.screenshot({ path: 'F:/1223/解说工坊/FrameWeave/.tmp/smoke_shot.png' })
  await browser.close()

  const failed = results.filter(r => !r.ok).length
  LINE('=== 冒烟测试结果: ' + (failed === 0 ? '全部通过 (' + results.length + ')' : failed + '/' + results.length + ' 失败') + ' ===')
  writeFileSync('F:/1223/解说工坊/FrameWeave/.tmp/smoke_result.txt', out.join('\n'), 'utf8')
}

main().catch(e => {
  console.error('冒烟测试异常:', e)
  process.exit(1)
})
