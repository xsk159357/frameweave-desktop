// 商城页（M18 / t5）：浏览 / 搜索 / 筛选 / 下载安装（进度 + SHA-256 校验 + 错误码 + 重试）/ 回报 / 发布 / 作者页
// t5 完善：流式下载进度、SHA-256 校验展示、安装状态（XHR 上传进度）、服务端错误码展示、
//       云端 item_not_found 容错重试、安装后节点库刷新（reload + /api/specs → setSpecs 广播）。
// t2 完善：搜索输入防抖（300ms）、筛选结果前端缓存（切换 kind/搜索即时响应，不再每次打云端）、
//       下载 60s 无数据看门狗 + 整体重试一次（用户网络不稳时给明确错误而非悬挂）、
//       条目卡片 memo 化（切换过滤/安装进度时避免整页重渲染）。
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Search, X, Package, GitBranch, Download, Upload, Boxes, User, Coins, Loader2, CheckCircle2, ShieldCheck, RefreshCw } from 'lucide-react'
import { api, nodeSpecs, API_BASE, localToken } from './api'
import { useAppStore } from './store'
import { CATEGORY_ORDER, categoryNormal } from './types'

type MarketOpenDetail = { typeIds?: string[]; kind?: string; cat?: string }

// App 在事件到达时才 setShowMarket，MarketPage 可能尚未挂载；暂存最近一次跳转，
// 组件挂载后消费，保证 D-03「点击去商城」不会丢失 typeIds。
let pendingMarketOpen: MarketOpenDetail | null = null
if (typeof window !== 'undefined' && !(window as any).__fwMarketPendingListener) {
  ;(window as any).__fwMarketPendingListener = true
  window.addEventListener('fw-open-market', (ev) => {
    pendingMarketOpen = (ev as CustomEvent<MarketOpenDetail>).detail || {}
  })
}

interface MarketItem {
  id: string
  kind: string
  title: string
  description: string
  author: string
  price: number
  official: boolean
  downloads: number
  installs?: number
  version?: string
  type_id?: string
  sha256?: string
  size?: number
  min_client_version?: string
  status?: string
  tags: string[]
  created_at: number
}

// ---- 服务端/客户端错误码 → 用户可读文案（错误码展示） ----
const CODE_TEXT: Record<string, string> = {
  item_not_found: '条目不存在（云端可能尚未同步，请稍后刷新重试）',
  item_disabled: '条目已下架',
  item_kind_invalid: '条目类型无效',
  client_version_too_old: '当前客户端版本过低，请先更新到新版',
  login_required: '请先登录后再下载付费插件',
  insufficient_credits: '积分不足',
  cloud_unavailable: '无法连接云端授权服务器，请联网后重试',
  cloud_not_configured: '需要云端授权：云端授权服务器未配置，请检查网络后重试',
  cloud_http_404: '云端接口不存在（云端服务可能未同步最新版本，请稍后重试）',
  cloud_http_500: '云端服务器内部错误，请稍后重试',
  cloud_http_502: '云端网关错误，请稍后重试',
  no_download_url: '服务端未返回下载地址',
  download_empty: '下载内容为空',
  size_mismatch: '文件大小与声明不符',
  sha256_mismatch: 'SHA-256 校验失败（下载包可能被篡改）',
  not_zip: '插件包不是有效的 zip',
  pkg_layout: '插件包顶层目录格式不正确',
  bad_manifest: '插件包 manifest 校验失败',
  install_failed: '插件安装失败',
  network: '网络连接异常',
  timeout: '请求超时（大插件请耐心等待或重试）',
}

function codeText(code?: string) {
  return (code && CODE_TEXT[code]) || code || '未知错误'
}

function itKind(items: MarketItem[], id: string) {
  return items.find((item) => item.id === id)?.kind || ''
}

function fmtSize(n?: number) {
  if (!n) return ''
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

interface InstallStep { label: string; pct?: number; sha?: string }

// 构造带错误码的异常（前端统一 codeText 文案 + 按 code 重试）
function fail(code: string, message: string) {
  const e: any = new Error(message)
  e.code = code
  return e
}

// 条目卡片（memo 化）：仅当本卡 props（条目/已装态/安装态/忙碌态）变化才重渲染，
// 切换 kind/搜索、其他条目安装进度更新时不再整页重渲染。
// t11（D-18）：卡片首屏只保留用途/依赖徽章/安装状态/CTA；SHA-256/体积/最低版本等
// 技术校验信息折叠进「详情」（toggleExpand）；依赖徽章由 tags/描述关键词推导。
const MarketCard = memo(function MarketCard({ item, installed, installing, step, busy, onInstall, onAuthor }: {
  item: MarketItem
  installed: boolean
  installing: boolean
  step: InstallStep | null
  busy: boolean
  onInstall: (id: string) => void
  onAuthor: (a: string) => void
}) {
  const [detail, setDetail] = useState(false)
  const frame = { border: '1px solid var(--line)', background: 'var(--ink-800)' }

  // D-18：依赖徽章（ffmpeg / 模型 / API Key / GPU / 网络 / 剪映等，从 tags+描述关键词推导）
  const deps: { label: string; tone: 'warn' | 'accent' | 'ok' }[] = []
  const hay = ((item.tags || []).join(' ') + ' ' + (item.description || '')).toLowerCase()
  if (/ffmpeg|编码|解码|转码/.test(hay)) deps.push({ label: 'FFmpeg', tone: 'warn' })
  if (/模型|whisper|asr|tts|checkpoint|权重/.test(hay)) deps.push({ label: '模型文件', tone: 'warn' })
  if (/api|key|密钥|token|联网|cloud/.test(hay)) deps.push({ label: 'API Key', tone: 'warn' })
  if (/gpu|cuda|显卡/.test(hay)) deps.push({ label: 'GPU', tone: 'accent' })
  if (/剪映|premiere|prxml|draft/.test(hay)) deps.push({ label: '剪映/Premiere', tone: 'ok' })

  return (
    <div style={{ ...frame, borderRadius: 6, padding: 12, display: 'flex', flexDirection: 'column', gap: 7 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {item.kind === 'node' ? <Package size={14} color={'var(--brass)'} /> : <GitBranch size={14} color="var(--info)" />}
        <b style={{ fontSize: 13.5, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.title}>{item.title}</b>
        {item.official && <span style={{ fontSize: 9.5, color: 'var(--brass-hi)', border: '1px solid rgba(210,164,94,.35)', borderRadius: 3, padding: '0 5px', background: 'var(--brass-soft)', flexShrink: 0, letterSpacing: '.06em' }}>官方</span>}
        {item.price > 0
          ? <span style={{ fontSize: 11, color: 'var(--brass)', fontWeight: 600, flexShrink: 0, fontFamily: 'var(--font-mono)' }}><Coins size={11} style={{ verticalAlign: -1 }} /> {item.price}</span>
          : <span style={{ fontSize: 11.5, color: 'var(--success)', flexShrink: 0 }}>免费</span>}
      </div>
      {/* 首屏：用途（两行截断） */}
      <div style={{
        fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.5,
        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', minHeight: 34,
      }}>{item.description || '（无描述）'}</div>
      {/* 首屏：依赖徽章（安装前须知） */}
      {deps.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {deps.map((d) => (
            <span key={d.label} title={'安装前需准备：' + d.label}
              style={{
                fontSize: 9, fontWeight: 650, padding: '1px 6px', borderRadius: 3,
                color: d.tone === 'warn' ? 'var(--gold)' : d.tone === 'accent' ? 'var(--accent-strong)' : 'var(--success)',
                background: d.tone === 'warn' ? 'var(--warn-soft)' : d.tone === 'accent' ? 'var(--brass-soft)' : 'var(--ok-soft)',
                border: '1px solid var(--line)',
              }}>{d.label}</span>
          ))}
        </div>
      )}
      {/* 首屏：安装状态 + CTA */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5, color: 'var(--text-faint)' }}>
        <button onClick={() => onAuthor(item.author)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'inherit', display: 'flex', alignItems: 'center', gap: 3, fontSize: 10.5, padding: 0 }}><User size={11} /> {item.author}</button>
        {item.kind === 'node' && installed && <span style={{ color: 'var(--success)' }}><CheckCircle2 size={11} style={{ verticalAlign: -1 }} /> 已安装</span>}
        <span style={{ flex: 1 }} />
        {/* D-18：技术校验信息折叠到详情 */}
        <button onClick={() => setDetail((v) => !v)}
          style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', fontSize: 10.5, display: 'inline-flex', alignItems: 'center', gap: 3, padding: 0 }}>
          {detail ? '收起详情' : '详情'} {detail ? <ChevronUpIcon size={10} /> : <ChevronDownIcon size={10} />}
        </button>
        {item.kind === 'workflow' && (
          <button onClick={() => onInstall(item.id)} disabled={busy}
            title="下载并导入工作流"
            style={{ padding: '3px 9px', borderRadius: 4, cursor: busy ? 'default' : 'pointer', fontSize: 10.5, border: '1px solid rgba(210,164,94,.3)', display: 'flex', alignItems: 'center', gap: 4, background: 'var(--brass-soft)', color: 'var(--brass-hi)' }}>
            <Download size={12} /> 下载并导入
          </button>
        )}
        {item.kind === 'node' && (
          <button onClick={() => onInstall(item.id)} disabled={busy || installed}
            title={installed ? '该节点已安装' : (item.type_id ? '下载并安装 ' + item.type_id : '下载并安装')}
            style={{
              padding: '3px 9px', borderRadius: 4, cursor: busy || installed ? 'default' : 'pointer', fontSize: 10.5,
              border: '1px solid ' + (installed ? 'rgba(143,185,150,.3)' : 'rgba(210,164,94,.35)'), display: 'flex', alignItems: 'center', gap: 4,
              background: installed ? 'var(--ok-soft)' : 'var(--brass-soft)',
              color: installed ? 'var(--ok)' : 'var(--brass-hi)',
            }}>
            {installing ? (
              <><Loader2 size={12} className="fw-spin" /> {step ? step.label : '安装中'}{step && step.pct != null ? ` ${step.pct}%` : ''}</>
            ) : installed ? (
              <><CheckCircle2 size={12} /> 已安装</>
            ) : (
              <><Download size={12} /> 下载并安装</>
            )}
          </button>
        )}
      </div>
      {/* 折叠区：技术校验信息（SHA-256 / 体积 / 最低版本 / 下载安装统计） */}
      {detail && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 7, marginTop: 2, animation: 'fw-pop-in .15s var(--t-ease) both' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', fontSize: 10, color: 'var(--text-faint)' }}>
            {item.sha256 && <span title={'SHA-256: ' + item.sha256} style={{ fontFamily: 'var(--font-mono)' }}><ShieldCheck size={10} style={{ verticalAlign: -1 }} /> SHA-256 {item.sha256.slice(0, 16)}…</span>}
            {item.size ? <span>{fmtSize(item.size)}</span> : null}
            {item.min_client_version ? <span style={{ color: '#e0a800' }}>需 v{item.min_client_version}+</span> : null}
            {item.type_id ? <span style={{ fontFamily: 'var(--font-mono)' }}>{item.type_id}</span> : null}
            <span>↓ {item.downloads}</span>
            {typeof item.installs === 'number' && <span>✓ {item.installs}</span>}
            {item.version ? <span>v{item.version}</span> : null}
          </div>
          {item.tags && item.tags.length > 0 && (
            <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 9.5, color: 'var(--text-faint)' }}>
              {item.tags.map((t) => <span key={t} className="fw-badge" style={{ background: 'var(--bg-panel-2)' }}>#{t}</span>)}
            </div>
          )}
        </div>
      )}
      {installing && step && step.pct != null && (
        <div style={{ height: 4, borderRadius: 999, background: 'var(--bg-panel-2)', overflow: 'hidden' }}>
          <div style={{ height: '100%', width: step.pct + '%', background: 'var(--brass)', transition: 'width .25s ease' }} />
        </div>
      )}
    </div>
  )
})
const ChevronUpIcon = ({ size = 10 }: { size?: number }) => <span style={{ display: 'inline-flex' }}>⌃</span>
const ChevronDownIcon = ({ size = 10 }: { size?: number }) => <span style={{ display: 'inline-flex' }}>⌄</span>

export function MarketPage({ onClose }: { onClose: () => void }) {
  const session = useAppStore((s) => s.session)
  const specs = useAppStore((s) => s.specs)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const pushToast = useAppStore((s) => s.pushToast)
  const [q, setQ] = useState('')                    // 搜索框即时输入值
  const [debouncedQ, setDebouncedQ] = useState('')  // 防抖后的查询条件（300ms 停顿后生效）
  const [kind, setKind] = useState('')
  const [cat, setCat] = useState('')                 // D-17：同分类筛选（与节点库 manifest 顺序一致）
  // t16（D-03）：外部缺失节点跳转的精确 type_id 过滤（不依赖商城全文搜索是否索引 type_id）
  const [targetTypeIds, setTargetTypeIds] = useState<string[]>([])
  const [items, setItems] = useState<MarketItem[]>([])
  const [author, setAuthor] = useState('')           // 作者页过滤
  const [showPublish, setShowPublish] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [credits, setCredits] = useState(session?.credits || 0)
  const [activeId, setActiveId] = useState('')        // 正在安装的条目 id
  const [step, setStep] = useState<InstallStep | null>(null)
  const [installedSet, setInstalledSet] = useState<Set<string>>(new Set())
  const [refreshing, setRefreshing] = useState(false)
  const loadSeq = useRef(0)
  const listCache = useRef<Map<string, MarketItem[]>>(new Map())  // 筛选结果前端缓存：(q|kind) → 云端条目

  // D-17：分类选项 = 已安装节点规格分类（CATEGORY_ORDER 顺序）∪ 条目关键词可识别分类
  const catOptions = useMemo(() => {
    const fromSpecs = new Set(specs.map((s) => categoryNormal(s.category)))
    const fromItems = new Set<string>()
    for (const it of items) {
      const c = categoryOfItem(it)
      if (c) fromItems.add(c)
    }
    const all = new Set([...fromSpecs, ...fromItems])
    return [...all].sort((a, b) => {
      const ia = CATEGORY_ORDER.indexOf(a)
      const ib = CATEGORY_ORDER.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
  }, [specs, items])

  // 条目分类：已安装节点按 type_id 查规格；未安装按 tags/标题/描述关键词识别
  function categoryOfItem(it: MarketItem): string {
    if (it.kind === 'node' && it.type_id) {
      const s = specs.find((x) => x.type_id === it.type_id)
      if (s) return categoryNormal(s.category)
    }
    const hay = ((it.tags || []).join(' ') + ' ' + it.title + ' ' + (it.description || '')).toLowerCase()
    for (const c of CATEGORY_ORDER) {
      const kws: Record<string, string[]> = {
        输入: ['导入', '加载', '输入', 'load', 'ingest', '素材导入'],
        分析: ['分析', '检测', '识别', '解析', 'analy', 'detect', 'scene'],
        语义: ['语义', '理解', '意图', 'semantic', '嵌入', 'embedding'],
        音频: ['音频', '语音', '音轨', 'audio', 'voice'],
        剪辑: ['剪辑', '拼接', '合成', '编辑', 'edit', 'concat', 'mix'],
        输出: ['输出', '导出', '渲染', 'export', 'render', '字幕输出'],
        控制: ['控制', '流程', '条件', 'control', 'branch', 'if'],
        用户节点: ['用户', '自定义', 'user', 'custom'],
      }
      if ((kws[c] || []).some((k) => hay.includes(k))) return c
    }
    return ''
  }

  // 已安装集合 = 服务端 /api/specs 的 type_id；安装成功 setSpecs 后自动联动（节点库刷新）
  useEffect(() => { setInstalledSet(new Set(specs.map((s) => s.type_id))) }, [specs])

  // t16（D-03/D-06）：跨面板事件契约
  // - fw-open-market.detail = { typeIds?: string[], kind?: string, cat?: string }
  //   缺失节点 modal/右键点击后由 App 打开商城；这里精确过滤 type_id，并支持重复触发更新。
  // - fw-workflow-imported.detail = { tpl_id|workflowId, name, node_count }。
  //   WorkflowPanel 已注册同一事件并负责「立即打开/创建副本」modal；商城收到后关闭自己，避免 modal 被商城遮挡。
  useEffect(() => {
    const applyMarketOpen = (d: MarketOpenDetail) => {
      const ids = Array.isArray(d.typeIds) ? d.typeIds.filter(Boolean).map(String) : []
      setTargetTypeIds(ids)
      setQ('')
      if (d.kind != null) setKind(String(d.kind))
      if (d.cat != null) setCat(String(d.cat))
    }
    if (pendingMarketOpen) {
      applyMarketOpen(pendingMarketOpen)
      pendingMarketOpen = null
    }
    const onOpenMarket = (ev: Event) => {
      applyMarketOpen((ev as CustomEvent<MarketOpenDetail>).detail || {})
    }
    const onWorkflowImported = (ev: Event) => {
      const d = (ev as CustomEvent<{ tpl_id?: string; workflowId?: string; name?: string }>).detail || {}
      if (d.tpl_id || d.workflowId) onClose()
    }
    window.addEventListener('fw-open-market', onOpenMarket)
    window.addEventListener('fw-workflow-imported', onWorkflowImported)
    return () => {
      window.removeEventListener('fw-open-market', onOpenMarket)
      window.removeEventListener('fw-workflow-imported', onWorkflowImported)
    }
  }, [onClose])

  // 搜索输入防抖：停顿 300ms 后才更新查询条件（避免每次按键都请求后端+云端）
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])

  const load = async (manual = false, opts: { useCache?: boolean } = {}) => {
    const seq = ++loadSeq.current
    if (manual) setRefreshing(true)
    const key = debouncedQ + '|' + kind
    try {
      // 筛选结果前端缓存：同一 (搜索, kind) 组合直接复用，切换过滤即时响应
      if (opts.useCache !== false) {
        const hit = listCache.current.get(key)
        if (hit) {
          setItems(hit.filter((i: MarketItem) => !author || i.author === author))
          setErr('')
          return
        }
      }
      const r = await api.marketItems(debouncedQ, kind, '')
      if (seq !== loadSeq.current) return
      const list = r.items || []
      listCache.current.set(key, list)
      setItems(list.filter((i: MarketItem) => !author || i.author === author))
      setErr('')
    } catch (e: any) {
      // 后台加载/轮询失败静默（保留旧列表），手动刷新才给明确错误
      if (manual) setErr(e.message || '加载失败，请点击刷新重试')
    } finally {
      if (manual) setRefreshing(false)
    }
  }

  // 筛选变化（防抖后查询/类型/作者）触发加载：优先命中前端缓存；
  // 每 25s 轮询走网络刷新（不命中缓存），页面可见性恢复时也刷新一次。
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null
    const stop = () => { if (timer) { clearInterval(timer); timer = null } }
    const start = () => { stop(); if (document.visibilityState === 'visible') timer = setInterval(() => { void load(false, { useCache: false }) }, 25000) }
    void load(false, { useCache: true })
    start()
    const onVisibility = () => {
      if (document.visibilityState === 'visible') { void load(false, { useCache: true }); start() } else stop()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [debouncedQ, kind, author])

  // 稳定回调（供 memo 卡片使用）：install/setAuthor 经 ref 取最新闭包，props 引用不变
  const installRef = useRef<(id: string) => void>(() => {})
  installRef.current = (id: string) => { void install(id) }
  const onInstall = useCallback((id: string) => { installRef.current(id) }, [])
  const authorRef = useRef<(a: string) => void>(() => {})
  authorRef.current = (a: string) => setAuthor(a)
  const onAuthor = useCallback((a: string) => { authorRef.current(a) }, [])

  const report = async (id: string, result: 'ok' | 'failed', error = '') => {
    try { await api.marketInstallReport(id, session?.token || '', __APP_VERSION__, result, error) } catch { /* 旧服务兼容 */ }
  }

  // 直链插件包下载：60s 无数据看门狗（网络黑洞中断给明确 timeout 错误而非悬挂），
  // 网络/超时类失败返回带 code 的异常，由调用方整体重试一次。
  const downloadPackage = async (url: string, headers: Record<string, string>, declared: number, onStep: (recv: number, len: number) => void): Promise<{ parts: BlobPart[]; received: number }> => {
    const ctrl = new AbortController()
    let inactivity: ReturnType<typeof setTimeout> | null = null
    const arm = () => {
      if (inactivity) clearTimeout(inactivity)
      inactivity = setTimeout(() => ctrl.abort(), 60000)
    }
    arm()
    try {
      const resp = await fetch(url, { headers, signal: ctrl.signal })
      if (!resp.ok) throw fail('download_http_' + resp.status, '下载失败: HTTP ' + resp.status)
      const len = declared || Number(resp.headers.get('content-length') || 0) || 0
      const reader = resp.body ? resp.body.getReader() : null
      const parts: BlobPart[] = []
      let received = 0
      if (reader) {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          arm()  // 有数据到达 → 重置看门狗
          parts.push(value)
          received += value.byteLength
          if (len) onStep(received, len)
          if (declared && received > declared) throw fail('size_mismatch', '下载文件超过声明大小')
        }
      } else {
        const buf = await resp.arrayBuffer()
        parts.push(buf)
        received = buf.byteLength
      }
      if (declared && received !== declared) throw fail('size_mismatch', `文件大小校验失败：收到 ${received}B，声明 ${declared}B`)
      if (received === 0) throw fail('download_empty', '下载内容为空')
      return { parts, received }
    } catch (e: any) {
      if (e && e.name === 'AbortError') throw fail('timeout', '下载中断（超过 60 秒无数据），请检查网络后重试')
      if (e && (e.code === 'network' || e.code === 'timeout')) throw e
      if (e instanceof TypeError || /fetch|network|failed|terminated/i.test(String(e.message || e))) {
        throw fail('network', '下载连接失败：' + (e.message || '网络异常'))
      }
      throw e
    } finally {
      if (inactivity) clearTimeout(inactivity)
    }
  }

  const install = async (id: string) => {
    setBusy(true); setErr(''); setMsg('')
    setActiveId(id)
    let reportId = id
    try {
      // ---- 1. 下载授权（云端 item_not_found → 刷新列表重试一次；瞬时网络/超时 → 延时重试一次） ----
      let auth: any = null
      setStep({ label: '获取下载授权' })
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          setStep({ label: attempt > 0 ? '获取下载授权（重试）' : '获取下载授权' })
          auth = await api.marketDownloadAuth(id, session?.token || '', __APP_VERSION__)
          if (auth.ok) break
          if (auth.code === 'item_not_found' && attempt === 0) {
            // 云端条目未同步（迁移期）：刷新列表后再试一次
            setStep({ label: '云端条目未同步，刷新列表后重试' })
            try { await load(false, { useCache: false }) } catch { /* ignore */ }
            if (!items.some((i) => i.id === id)) {
              throw fail(auth.code || 'item_not_found', auth.message || '条目不存在')
            }
            await delay(600)
            continue
          }
          throw fail(auth.code || 'auth_failed', auth.message || '下载授权失败')
        } catch (e: any) {
          if (attempt === 0 && (e.code === 'network' || e.code === 'timeout')) {
            await delay(800)
            continue
          }
          throw e
        }
      }
      reportId = auth.item_id || id
      const dl = auth.download || {}
      // 相对 stub_path（云端直链/相对路径）与绝对直链统一解析
      const url = api.resolveUrl(dl.url || dl.stub_path || '')
      if (!url) throw fail('no_download_url', '下载授权未返回下载地址')
      const declared = Number(dl.size || 0) || 0

      // ---- 2. 流式下载 + 进度（网络不稳：60s 无数据看门狗 + 整体重试一次） ----
      setStep({ label: '正在下载插件', pct: 0 })
      // 直链（/api/export、/api/market/*/file）受本地授权令牌保护：
      // 仅对本地 API 地址附加 X-FW-Local-Token；GitHub Release 等外域直链不加（防 CORS 预检失败）。
      const downloadHeaders: Record<string, string> = {}
      if (url.startsWith(API_BASE)) downloadHeaders['X-FW-Local-Token'] = localToken()
      let parts: BlobPart[] = []
      let received = 0
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          if (attempt > 0) setStep({ label: '下载连接异常，正在重试下载…', pct: 0 })
          const dlRes = await downloadPackage(url, downloadHeaders, declared, (recv, len) => {
            setStep({ label: `正在下载插件 ${fmtSize(recv)} / ${fmtSize(len)}`, pct: Math.min(99, Math.round((recv / len) * 100)) })
          })
          parts = dlRes.parts
          received = dlRes.received
          break
        } catch (e: any) {
          if (attempt === 0 && (e.code === 'network' || e.code === 'timeout')) {
            await delay(1000)
            continue
          }
          throw e
        }
      }

      // ---- 3. SHA-256 校验（服务端未给 sha256 时跳过并注明） ----
      const blob = new Blob(parts)
      if (dl.sha256) {
        setStep({ label: '正在计算 SHA-256 校验' })
        const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
        const actual = Array.from(new Uint8Array(digest)).map((x) => x.toString(16).padStart(2, '0')).join('')
        if (actual.toLowerCase() !== dl.sha256.toLowerCase()) throw fail('sha256_mismatch', 'SHA-256 校验失败（下载包可能被篡改）')
        setStep({ label: 'SHA-256 校验通过', pct: 100, sha: actual.slice(0, 16) })
      }

      // ---- 4. 按类型导入：节点安装 / 工作流模板导入 ----
      const name = dl.filename || (auth.type_id || id).split('/').pop() + '.zip'
      const file = new File([blob], name, { type: 'application/zip' })
      const isWorkflow = auth.kind === 'workflow' || itKind(items, id) === 'workflow'
      if (isWorkflow) {
        setStep({ label: '正在导入工作流', pct: 0 })
        const imported = await api.importWorkflowZip(file)
        if (!imported.ok) throw fail('workflow_import_failed', imported.message || '工作流导入失败')
        // t16（D-06）：交给 WorkflowPanel 的 ImportedWorkflowModal 统一处理立即打开/创建副本，
        // 商城自身监听该事件并关闭，避免重复 toast 与弹层遮挡。
        window.dispatchEvent(new CustomEvent('fw-workflow-imported', {
          detail: { tpl_id: imported.tpl_id, workflowId: imported.tpl_id, name: imported.name, node_count: imported.node_count },
        }))
        setStep({ label: '工作流已导入', pct: 100 })
      } else {
        setStep({ label: '正在安装节点', pct: 0 })
        const installed = await api.installUserNode(file, (p) => setStep({ label: '正在安装节点', pct: p }))
        if (!installed.ok) throw fail((installed as any).code || 'install_failed', installed.message || '节点安装失败')
      }

      // ---- 5. 安装回报（失败容错：旧服务不识别回报时不影响安装成功） ----
      setStep({ label: '回报安装结果' })
      await report(reportId, 'ok')

      // ---- 6. 节点刷新；工作流导入后由模板列表自动读取 ----
      if (!isWorkflow) {
        setStep({ label: '刷新节点库' })
        await api.reloadUserNodes()
        try { const s = await nodeSpecs(); setSpecs(s || []) } catch { /* ignore */ }
      }
      if (session?.token && session?.deviceId) {
        try { const v = await api.verify(session.token, session.deviceId); if (v.ok && v.credits != null) setCredits(v.credits) } catch { /* ignore */ }
      }
      const successText = isWorkflow ? '工作流已导入，可从左侧模板入口创建' : '节点安装成功，节点已可用'
      setMsg(successText)
      pushToast(successText, 'ok')
      load(false, { useCache: false })
    } catch (e: any) {
      const code = e.code || ''
      const message = e.message || '安装失败'
      setErr(code ? `[${code}] ${message}` : message)
      await report(reportId, 'failed', message)
      pushToast((code ? codeText(code) + '：' : '') + message, 'err')
    } finally {
      setActiveId(''); setStep(null); setBusy(false)
    }
  }

  const publish = async (form: {kind:string;title:string;description:string;price:string;download_url:string;tags:string}) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await api.marketPublish({
        kind: form.kind, title: form.title, description: form.description,
        author: session?.email || 'anonymous', price: Number(form.price) || 0,
        download_url: form.download_url,
        tags: form.tags.split(/[,，\s]+/).filter(Boolean),
      })
      if (r.ok) { setMsg('发布成功'); setShowPublish(false); await load(true, { useCache: false }) }
      else setErr(r.message || '发布失败')
    } catch (e: any) { setErr(e.message || '发布失败') }
    finally { setBusy(false) }
  }

  const frame = { border: '1px solid var(--line)', background: 'var(--ink-800)' }

  // D-17：分类过滤后的可见条目（空态区分「无条目」与「该分类无条目」）
  const visibleItems = items.filter((it) => {
    if (targetTypeIds.length > 0) {
      return targetTypeIds.includes(String(it.type_id || '')) || targetTypeIds.some((id) =>
        String(it.title || '').toLowerCase().includes(id.toLowerCase()) || String(it.description || '').toLowerCase().includes(id.toLowerCase()))
    }
    return !cat || categoryOfItem(it) === cat
  })

  const stepText = step
    ? step.label + (step.pct != null ? ` ${step.pct}%` : '') + ((step as any).sha ? `（${(step as any).sha}…）` : '')
    : ''

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 999, background: 'var(--modal-backdrop)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...frame, width: 880, maxWidth: '92vw', height: '82vh', borderRadius: 10, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: 'var(--glass-inner), var(--shadow-lg)' }}>
        {/* 头部 */}
        <div style={{ padding: '14px 18px', borderBottom: '1px solid ' + ('var(--border)'), display: 'flex', alignItems: 'center', gap: 12 }}>
          <Boxes size={18} color={'var(--brass)'} />
          <b style={{ fontSize: 14, letterSpacing: '.02em' }}>节点商城</b>
          <span style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', letterSpacing: '.06em' }}>MARKET · 官方插件免费 · 用户节点积分定价</span>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--text-faint)' }}><Coins size={13} color="var(--brass)" /> {credits} 积分</span>
          <button onClick={() => setShowPublish(true)} style={{ padding: '5px 10px', borderRadius: 4, cursor: 'pointer', fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5, border: '1px solid var(--line-strong)', background: 'transparent', color: 'var(--paper-dim)' }}><Upload size={14} /> 发布作品</button>
          <button onClick={onClose} title="关闭商城" style={{ padding: 5, borderRadius: 4, cursor: 'pointer', border: 'none', background: 'transparent', color: 'var(--text-faint)' }}><X size={17} /></button>
        </div>

        {/* 工具栏 */}
        <div style={{ padding: '12px 18px', display: 'flex', gap: 10, alignItems: 'center', borderBottom: '1px solid ' + ('var(--border)'), flexWrap: 'wrap' }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, border: '1px solid ' + ('var(--border)'), borderRadius: 4, padding: '5px 10px', background: 'var(--ink-600)', minWidth: 200 }}>
            <button onClick={() => void load(true, { useCache: false })} disabled={refreshing} title="刷新商城" style={{ padding: 3, border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: refreshing ? 'default' : 'pointer' }}><RefreshCw size={15} className={refreshing ? 'fw-spin' : ''} /></button>
            <Search size={15} color="var(--text-faint)" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索节点 / 工作流 / 作者…" style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'inherit' }} />
          </div>
          {['', 'node', 'workflow'].map((k) => (
            <button key={k} onClick={() => setKind(k)} style={{ padding: '5px 11px', borderRadius: 4, cursor: 'pointer', fontSize: 11.5, border: '1px solid ' + (kind === k ? 'var(--brass)' : 'var(--line)'), background: kind === k ? 'var(--brass-soft)' : 'transparent', color: kind === k ? 'var(--brass-hi)' : 'var(--paper-faint)' }}>
              {k === '' ? '全部' : k === 'node' ? '节点' : '工作流'}
            </button>
          ))}
          {/* D-17：同分类筛选（与节点库 manifest 分类顺序一致） */}
          {catOptions.length > 0 && (
            <select value={cat} onChange={(e) => setCat(e.target.value)} title="按分类筛选（与节点库一致）"
              style={{ padding: '5px 9px', borderRadius: 4, fontSize: 11.5, border: '1px solid ' + (cat ? 'var(--brass)' : 'var(--line)'), background: cat ? 'var(--brass-soft)' : 'transparent', color: cat ? 'var(--brass-hi)' : 'var(--paper-faint)', cursor: 'pointer', outline: 'none' }}>
              <option value="">全部分类</option>
              {catOptions.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          {cat && (
            <button onClick={() => setCat('')} style={{ fontSize: 12, color: '#d06a4f', cursor: 'pointer', border: 'none', background: 'transparent' }}>× 分类:{cat}</button>
          )}
          {author && (
            <button onClick={() => setAuthor('')} style={{ fontSize: 12, color: '#d06a4f', cursor: 'pointer', border: 'none', background: 'transparent' }}>× 作者:{author}</button>
          )}
        </div>

        {/* 消息 + 安装步骤进度 */}
        {(msg || err || step) && (
          <div style={{ padding: '9px 18px', fontSize: 13, color: err ? '#d06a4f' : 'var(--success)', background: err ? 'var(--danger-soft)' : 'var(--success-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
            {step && !err && <Loader2 size={13} className="fw-spin" color="var(--accent)" />}
            <span style={{ flex: 1 }}>{err || (step ? stepText : msg)}</span>
            {step && step.pct != null && (
              <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text-faint)' }}>{step.pct}%</span>
            )}
          </div>
        )}
        {step && step.pct != null && (
          <div style={{ height: 2, background: 'var(--ink-500)' }}>
            <div style={{ height: '100%', width: step.pct + '%', background: 'var(--brass)', transition: 'width .25s ease' }} />
          </div>
        )}

        {/* 列表（D-18：紧凑自适应网格；D-17：分类客户端过滤） */}
        <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
          {visibleItems.length === 0 && (
            <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-faint)', fontSize: 13 }}>
              {items.length === 0 ? '暂无条目' : (cat ? '该分类下暂无条目' : '无匹配条目')}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
            {visibleItems.map((it) => (
              <MarketCard
                key={it.id}
                item={it}
                installed={!!it.type_id && installedSet.has(it.type_id)}
                installing={activeId === it.id && !!step}
                step={activeId === it.id ? step : null}
                busy={busy}
                onInstall={onInstall}
                onAuthor={onAuthor}
              />
            ))}
          </div>
        </div>

        {/* 发布表单 */}
        {showPublish && <PublishForm onCancel={() => setShowPublish(false)} onSubmit={publish} push={pushToast} />}
      </div>
    </div>
  )
}

function PublishForm({ onCancel, onSubmit, push }: { onCancel: () => void; onSubmit: (f: any) => void; push: (m: string, k?: 'ok' | 'err') => void }) {
  const [kind, setKind] = useState('node')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [price, setPrice] = useState('')
  const [downloadUrl, setDownloadUrl] = useState('')
  const [tags, setTags] = useState('')
  const [sending, setSending] = useState(false)
  const [nodes, setNodes] = useState<string[]>([])
  const [wfs, setWfs] = useState<{ id: string; name: string }[]>([])
  const [pick, setPick] = useState('')

  useEffect(() => {
    api.userNodes().then((r) => setNodes(r.nodes || [])).catch(() => { /* ignore */ })
    api.listWorkflows().then((r) => setWfs(r || [])).catch(() => { /* ignore */ })
  }, [])
  useEffect(() => { setPick('') }, [kind])

  const exportZip = async () => {
    if (!pick) { push('请先选择要导出的' + (kind === 'node' ? '节点' : '工作流'), 'err'); return }
    try {
      if (kind === 'node') await api.exportNodeZip(pick)
      else await api.exportWorkflowZip(pick)
      push('已导出 ' + pick + '（zip 可上传网盘取直链）', 'ok')
    } catch (e: any) { push('导出失败: ' + (e.message || e), 'err') }
  }

  const input: any = {
    width: '100%', border: '1px solid ' + ('var(--border)'), borderRadius: 8,
    padding: '7px 10px', fontSize: 13, outline: 'none', background: 'var(--input-bg)', color: 'inherit',
    boxSizing: 'border-box', marginBottom: 8,
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 10, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 420, maxWidth: '92vw', border: '1px solid ' + ('var(--border)'), background: 'var(--bg-panel)', borderRadius: 12, padding: 18 }}>
        <b style={{ fontSize: 15, display: 'block', marginBottom: 10 }}>发布作品</b>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          {['node', 'workflow'].map((k) => (
            <button key={k} onClick={() => setKind(k)} style={{ padding: '5px 14px', borderRadius: 999, cursor: 'pointer', fontSize: 12, border: '1px solid ' + (kind === k ? 'var(--accent)' : ('var(--border)')), background: kind === k ? 'var(--accent-soft)' : 'transparent', color: kind === k ? 'var(--accent-strong)' : 'var(--text-faint)' }}>
              {k === 'node' ? '节点' : '工作流'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <select style={{ ...input, marginBottom: 0, flex: 1, color: 'inherit', background: 'var(--input-bg)' }} value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">{kind === 'node' ? '选择要导出的节点…' : '选择要导出的工作流…'}</option>
            {(kind === 'node' ? nodes.map((n) => ({ id: n, name: n })) : wfs).map((it) => (
              <option key={it.id} value={it.id}>{it.name}</option>
            ))}
          </select>
          <button onClick={exportZip} style={{ padding: '7px 12px', borderRadius: 8, cursor: 'pointer', border: '1px solid ' + ('var(--border)'), background: 'var(--accent-soft)', color: 'var(--accent-strong)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
            <Download size={13} /> 导出 {kind === 'node' ? '节点' : '工作流'} zip
          </button>
        </div>
        <input style={input} placeholder="标题 *" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea style={{ ...input, minHeight: 60, resize: 'vertical' }} placeholder="描述" value={description} onChange={(e) => setDescription(e.target.value)} />
        <input style={input} placeholder="价格（积分，0 = 免费）" type="number" value={price} onChange={(e) => setPrice(e.target.value)} />
        <input style={input} placeholder="网盘直链（百度/123 分享直链，可留空）" value={downloadUrl} onChange={(e) => setDownloadUrl(e.target.value)} />
        <input style={input} placeholder="标签（逗号分隔，如 文案, 视频）" value={tags} onChange={(e) => setTags(e.target.value)} />
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 10 }}>提示：可直接导出所选节点/工作流 zip，上传到网盘/Release 取直链后填入下方（纯云端发布，需联网）。</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onCancel} style={{ padding: '6px 14px', borderRadius: 8, cursor: 'pointer', border: '1px solid ' + ('var(--border)'), background: 'transparent', color: 'var(--text-faint)', fontSize: 13 }}>取消</button>
          <button onClick={() => { setSending(true); onSubmit({ kind, title, description, price, download_url: downloadUrl, tags }) }} disabled={!title || sending} style={{ padding: '6px 16px', borderRadius: 8, cursor: sending || !title ? 'wait' : 'pointer', border: 'none', background: 'var(--btn-primary-grad)', color: '#fff', fontSize: 13 }}>发布</button>
        </div>
      </div>
    </div>
  )
}
