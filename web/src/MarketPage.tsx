// 商城页（M18 / t5）：浏览 / 搜索 / 筛选 / 下载安装（进度 + SHA-256 校验 + 错误码 + 重试）/ 回报 / 发布 / 作者页
// t5 完善：流式下载进度、SHA-256 校验展示、安装状态（XHR 上传进度）、服务端错误码展示、
//       云端 item_not_found 容错重试、安装后节点库刷新（reload + /api/specs → setSpecs 广播）。
import { useEffect, useState } from 'react'
import { Search, X, Package, GitBranch, Download, Upload, Boxes, User, Coins, Loader2, CheckCircle2, ShieldCheck } from 'lucide-react'
import { api, API_BASE, localToken } from './api'
import { useAppStore } from './store'

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
  item_not_found: '条目不存在（云端可能未同步，已自动重试/回落本地）',
  item_disabled: '条目已下架',
  item_kind_invalid: '条目类型无效',
  client_version_too_old: '当前客户端版本过低，请先更新到新版',
  login_required: '请先登录后再下载付费插件',
  insufficient_credits: '积分不足',
  cloud_unavailable: '云端授权服务不可用',
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

function fmtSize(n?: number) {
  if (!n) return ''
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

interface InstallStep { label: string; pct?: number; sha?: string }

export function MarketPage({ onClose }: { onClose: () => void }) {
  const session = useAppStore((s) => s.session)
  const specs = useAppStore((s) => s.specs)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const pushToast = useAppStore((s) => s.pushToast)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
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

  // 已安装集合 = 服务端 /api/specs 的 type_id；安装成功 setSpecs 后自动联动（节点库刷新）
  useEffect(() => { setInstalledSet(new Set(specs.map((s) => s.type_id))) }, [specs])

  const load = async () => {
    try {
      const r = await api.marketItems(q, kind, '')
      setItems((r.items || []).filter((i: MarketItem) => !author || i.author === author))
    } catch (e: any) { setErr(e.message || '加载失败') }
  }

  useEffect(() => { load() }, [q, kind, author])

  const report = async (id: string, result: 'ok' | 'failed', error = '') => {
    try { await api.marketInstallReport(id, session?.token || '', __APP_VERSION__, result, error) } catch { /* 旧服务兼容 */ }
  }

  const install = async (id: string) => {
    setBusy(true); setErr(''); setMsg('')
    setActiveId(id)
    let reportId = id
    const fail = (code: string, message: string) => {
      const e: any = new Error(message)
      e.code = code
      return e
    }
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
            try { await load() } catch { /* ignore */ }
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
      // 相对 stub_path（本地桩内联 zip）与绝对直链统一解析
      const url = api.resolveUrl(dl.url || dl.stub_path || '')
      if (!url) throw fail('no_download_url', '下载授权未返回下载地址')

      // ---- 2. 流式下载 + 进度 ----
      setStep({ label: '正在下载插件', pct: 0 })
      // 本地桩直链（/api/export、/api/market/*/file）受本地授权令牌保护：
      // 仅对本地 API 地址附加 X-FW-Local-Token；GitHub Release 等外域直链不加（防 CORS 预检失败）。
      const downloadHeaders: Record<string, string> = {}
      if (url.startsWith(API_BASE)) downloadHeaders['X-FW-Local-Token'] = localToken()
      const resp = await fetch(url, { headers: downloadHeaders })
      if (!resp.ok) throw fail('download_http_' + resp.status, '下载失败: HTTP ' + resp.status)
      const declared = Number(dl.size || 0) || 0
      const len = declared || Number(resp.headers.get('content-length') || 0) || 0
      const reader = resp.body ? resp.body.getReader() : null
      const parts: BlobPart[] = []
      let received = 0
      if (reader) {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          parts.push(value)
          received += value.byteLength
          if (len) setStep({ label: `正在下载插件 ${fmtSize(received)} / ${fmtSize(len)}`, pct: Math.min(99, Math.round((received / len) * 100)) })
          if (declared && received > declared) throw fail('size_mismatch', '下载文件超过声明大小')
        }
      } else {
        const buf = await resp.arrayBuffer()
        parts.push(buf)
        received = buf.byteLength
      }
      if (declared && received !== declared) throw fail('size_mismatch', `文件大小校验失败：收到 ${received}B，声明 ${declared}B`)
      if (received === 0) throw fail('download_empty', '下载内容为空')

      // ---- 3. SHA-256 校验（服务端未给 sha256 时跳过并注明） ----
      const blob = new Blob(parts)
      if (dl.sha256) {
        setStep({ label: '正在计算 SHA-256 校验' })
        const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
        const actual = Array.from(new Uint8Array(digest)).map((x) => x.toString(16).padStart(2, '0')).join('')
        if (actual.toLowerCase() !== dl.sha256.toLowerCase()) throw fail('sha256_mismatch', 'SHA-256 校验失败（下载包可能被篡改）')
        setStep({ label: 'SHA-256 校验通过', pct: 100, sha: actual.slice(0, 16) })
      }

      // ---- 4. 上传安装（XHR 流式上传，10 分钟超时 + 实时进度；大插件适配） ----
      setStep({ label: '正在安装插件', pct: 0 })
      const name = dl.filename || (auth.type_id || id).split('/').pop() + '.zip'
      const file = new File([blob], name, { type: 'application/zip' })
      const installed = await api.installUserNode(file, (p) => setStep({ label: '正在安装插件', pct: p }))
      if (!installed.ok) throw fail((installed as any).code || 'install_failed', installed.message || '插件安装失败')

      // ---- 5. 安装回报（失败容错：旧服务不识别回报时不影响安装成功） ----
      setStep({ label: '回报安装结果' })
      await report(reportId, 'ok')

      // ---- 6. 节点库刷新（reload 重扫 + /api/specs → setSpecs 广播 fw-nodes-installed） ----
      setStep({ label: '刷新节点库' })
      await api.reloadUserNodes()
      try { const s = await api.specs(); setSpecs(s || []) } catch { /* ignore */ }
      if (session?.token && session?.deviceId) {
        try { const v = await api.verify(session.token, session.deviceId); if (v.ok && v.credits != null) setCredits(v.credits) } catch { /* ignore */ }
      }
      setMsg('安装成功，节点已可用')
      pushToast('插件安装成功，节点已可用', 'ok')
      load()
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
      if (r.ok) { setMsg('发布成功'); setShowPublish(false); load() }
      else setErr(r.message || '发布失败')
    } catch (e: any) { setErr(e.message || '发布失败') }
    finally { setBusy(false) }
  }

  const frame = { border: 'var(--glass-border)', background: 'var(--glass-strong)',  WebkitBackdropFilter: 'var(--glass-blur)' }

  const stepText = step
    ? step.label + (step.pct != null ? ` ${step.pct}%` : '') + ((step as any).sha ? `（${(step as any).sha}…）` : '')
    : ''

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 999, background: 'rgba(8,10,18,.72)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...frame, width: 860, maxWidth: '92vw', height: '82vh', borderRadius: 20, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: 'var(--glass-inner), var(--shadow-lg)' }}>
        {/* 头部 */}
        <div style={{ padding: '14px 18px', borderBottom: '1px solid ' + ('var(--border)'), display: 'flex', alignItems: 'center', gap: 12 }}>
          <Boxes size={20} color={'#9fb4ff'} />
          <b style={{ fontSize: 16 }}>节点商城</b>
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>官方插件免费 · 用户节点积分定价</span>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--text-faint)' }}><Coins size={14} color="#e0a800" /> {credits} 积分</span>
          <button onClick={() => setShowPublish(true)} style={{ padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 5, border: 'none', background: 'var(--accent-soft)', color: 'var(--accent-strong)' }}><Upload size={14} /> 发布作品</button>
          <button onClick={onClose} title="关闭商城" style={{ padding: 6, borderRadius: 8, cursor: 'pointer', border: 'none', background: 'transparent', color: 'var(--text-faint)' }}><X size={18} /></button>
        </div>

        {/* 工具栏 */}
        <div style={{ padding: '12px 18px', display: 'flex', gap: 10, alignItems: 'center', borderBottom: '1px solid ' + ('var(--border)') }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, border: '1px solid ' + ('var(--border)'), borderRadius: 9, padding: '7px 11px', background: 'var(--input-bg)' }}>
            <Search size={15} color="var(--text-faint)" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索节点 / 工作流 / 作者…" style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'inherit' }} />
          </div>
          {['', 'node', 'workflow'].map((k) => (
            <button key={k} onClick={() => setKind(k)} style={{ padding: '6px 13px', borderRadius: 999, cursor: 'pointer', fontSize: 12, border: '1px solid ' + (kind === k ? ('#3a4a7a') : ('var(--border)')), background: kind === k ? 'var(--accent-soft)' : 'transparent', color: kind === k ? 'var(--accent-strong)' : 'var(--text-faint)' }}>
              {k === '' ? '全部' : k === 'node' ? '节点' : '工作流'}
            </button>
          ))}
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
          <div style={{ height: 3, background: 'var(--bg-panel-2)' }}>
            <div style={{ height: '100%', width: step.pct + '%', background: 'linear-gradient(90deg,#7c8cff,#54d6a0)', transition: 'width .25s ease' }} />
          </div>
        )}

        {/* 列表 */}
        <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
          {items.length === 0 && <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-faint)', fontSize: 13 }}>暂无条目</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {items.map((it) => {
              const installing = activeId === it.id && !!step
              const installed = !!it.type_id && installedSet.has(it.type_id)
              return (
                <div key={it.id} style={{ ...frame, borderRadius: 10, padding: 13, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {it.kind === 'node' ? <Package size={16} color={'#9fb4ff'} /> : <GitBranch size={16} color="var(--success)" />}
                    <b style={{ fontSize: 14, flex: 1 }}>{it.title}</b>
                    {it.official && <span style={{ fontSize: 11, color: '#b8860b', border: '1px solid #e8d48b', borderRadius: 999, padding: '1px 7px', background: '#fff8e1' }}>官方</span>}
                    {it.price > 0
                      ? <span style={{ fontSize: 12, color: '#e0a800', fontWeight: 600 }}><Coins size={12} style={{ verticalAlign: -1 }} /> {it.price}</span>
                      : <span style={{ fontSize: 12, color: 'var(--success)' }}>免费</span>}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-faint)', minHeight: 30 }}>{it.description}</div>
                  {/* 校验元信息展示（SHA-256 短摘要 / 包大小 / 最低版本 / 已安装） */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5, color: 'var(--text-faint)', flexWrap: 'wrap' }}>
                    {it.sha256 && <span title={'SHA-256: ' + it.sha256} style={{ fontFamily: 'var(--font-mono)' }}><ShieldCheck size={10} style={{ verticalAlign: -1 }} /> SHA-256 {it.sha256.slice(0, 12)}…</span>}
                    {it.size ? <span>{fmtSize(it.size)}</span> : null}
                    {it.min_client_version ? <span style={{ color: '#e0a800' }}>需 v{it.min_client_version}+</span> : null}
                    {installed && <span style={{ color: 'var(--success)', marginLeft: 'auto' }}><CheckCircle2 size={11} style={{ verticalAlign: -1 }} /> 已安装</span>}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, color: 'var(--text-faint)' }}>
                    <button onClick={() => setAuthor(it.author)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'inherit', display: 'flex', alignItems: 'center', gap: 3, fontSize: 11 }}><User size={12} /> {it.author}</button>
                    <span>↓ {it.downloads}</span>
                    {typeof it.installs === 'number' && <span>✓ {it.installs}</span>}
                    <div style={{ flex: 1 }} />
                    {it.kind === 'node' && (
                      <button onClick={() => install(it.id)} disabled={busy || installed}
                        title={installed ? '该节点已安装' : (it.type_id ? '下载并安装 ' + it.type_id : '下载并安装')}
                        style={{
                          padding: '4px 12px', borderRadius: 8, cursor: busy || installed ? 'default' : 'pointer', fontSize: 12,
                          border: 'none', display: 'flex', alignItems: 'center', gap: 4,
                          background: installed ? 'var(--success-soft)' : 'var(--accent-soft)',
                          color: installed ? 'var(--success)' : 'var(--accent-strong)',
                        }}>
                        {installing ? (
                          <><Loader2 size={13} className="fw-spin" /> {step ? step.label : '安装中'}{step && step.pct != null ? ` ${step.pct}%` : ''}</>
                        ) : installed ? (
                          <><CheckCircle2 size={13} /> 已安装</>
                        ) : (
                          <><Download size={13} /> 下载并安装</>
                        )}
                      </button>
                    )}
                  </div>
                  {installing && step && step.pct != null && (
                    <div style={{ height: 4, borderRadius: 999, background: 'var(--bg-panel-2)', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: step.pct + '%', background: 'linear-gradient(90deg,#7c8cff,#54d6a0)', transition: 'width .25s ease' }} />
                    </div>
                  )}
                </div>
              )
            })}
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
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 10 }}>提示：上方可直接导出所选节点/工作流 zip，上传到网盘取直链后填入下方；本地桩可留空直链仅作演示。</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onCancel} style={{ padding: '6px 14px', borderRadius: 8, cursor: 'pointer', border: '1px solid ' + ('var(--border)'), background: 'transparent', color: 'var(--text-faint)', fontSize: 13 }}>取消</button>
          <button onClick={() => { setSending(true); onSubmit({ kind, title, description, price, download_url: downloadUrl, tags }) }} disabled={!title || sending} style={{ padding: '6px 16px', borderRadius: 8, cursor: sending || !title ? 'wait' : 'pointer', border: 'none', background: 'var(--btn-primary-grad)', color: '#fff', fontSize: 13 }}>发布</button>
        </div>
      </div>
    </div>
  )
}