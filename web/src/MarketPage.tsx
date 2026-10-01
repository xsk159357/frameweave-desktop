// 商城页（M18）：浏览 / 搜索 / 筛选 / 安装 / 发布 / 作者页
import { useEffect, useState } from 'react'
import { Search, X, Package, GitBranch, Download, Upload, Boxes, User, Star, Coins } from 'lucide-react'
import { api } from './api'
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
  tags: string[]
  created_at: number
}

export function MarketPage({ onClose }: { onClose: () => void }) {
  const session = useAppStore((s) => s.session)
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

  const load = async () => {
    try {
      const r = await api.marketItems(q, kind, '')
      setItems((r.items || []).filter((i: MarketItem) => !author || i.author === author))
    } catch (e: any) { setErr(e.message || '加载失败') }
  }

  useEffect(() => { load() }, [q, kind, author])

  const install = async (id: string) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await api.marketInstall(id, session?.token || '')
      if (r.ok) {
        setMsg(r.message || '安装成功')
        pushToast((r.message || '安装成功') + '，可在左侧节点库使用')
        // 刷新积分
        if (session?.token && session?.deviceId) {
          try { const v = await api.verify(session.token, session.deviceId); if (v.ok && v.credits != null) setCredits(v.credits) } catch { /* ignore */ }
        }
        // F1：立即刷新节点规格，新节点立即可用（不再需要重启）
        try { const s = await api.specs(); setSpecs(s || []) } catch { /* ignore */ }
        load()
      } else setErr(r.message || '安装失败')
    } catch (e: any) { setErr(e.message || '安装失败') }
    finally { setBusy(false) }
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
          <button onClick={() => setShowPublish(true)} style={{ padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 5, border: 'none', background: '#2a3557', color: '#c9d6ff' }}><Upload size={14} /> 发布作品</button>
          <button onClick={onClose} style={{ padding: 6, borderRadius: 8, cursor: 'pointer', border: 'none', background: 'transparent', color: 'var(--text-faint)' }}><X size={18} /></button>
        </div>

        {/* 工具栏 */}
        <div style={{ padding: '12px 18px', display: 'flex', gap: 10, alignItems: 'center', borderBottom: '1px solid ' + ('#22263a') }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, border: '1px solid ' + ('var(--border)'), borderRadius: 9, padding: '7px 11px', background: '#141828' }}>
            <Search size={15} color="var(--text-faint)" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索节点 / 工作流 / 作者…" style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'inherit' }} />
          </div>
          {['', 'node', 'workflow'].map((k) => (
            <button key={k} onClick={() => setKind(k)} style={{ padding: '6px 13px', borderRadius: 999, cursor: 'pointer', fontSize: 12, border: '1px solid ' + (kind === k ? ('#3a4a7a') : ('var(--border)')), background: kind === k ? ('#2a3557') : 'transparent', color: kind === k ? ('#c9d6ff') : 'var(--text-faint)' }}>
              {k === '' ? '全部' : k === 'node' ? '节点' : '工作流'}
            </button>
          ))}
          {author && (
            <button onClick={() => setAuthor('')} style={{ fontSize: 12, color: '#d06a4f', cursor: 'pointer', border: 'none', background: 'transparent' }}>× 作者:{author}</button>
          )}
        </div>

        {/* 消息 */}
        {(msg || err) && (
          <div style={{ padding: '8px 18px', fontSize: 13, color: err ? '#d06a4f' : 'var(--success)', background: err ? ('#3a1c1c') : ('#1c3322') }}>
            {err || msg}
          </div>
        )}

        {/* 列表 */}
        <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
          {items.length === 0 && <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-faint)', fontSize: 13 }}>暂无条目</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {items.map((it) => (
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
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, color: 'var(--text-faint)' }}>
                  <button onClick={() => setAuthor(it.author)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'inherit', display: 'flex', alignItems: 'center', gap: 3, fontSize: 11 }}><User size={12} /> {it.author}</button>
                  <span>↓ {it.downloads}</span>
                  <div style={{ flex: 1 }} />
                  {it.kind === 'node' && (
                    <button onClick={() => install(it.id)} disabled={busy} style={{ padding: '4px 12px', borderRadius: 8, cursor: busy ? 'wait' : 'pointer', fontSize: 12, border: 'none', background: '#2a3557', color: '#c9d6ff', display: 'flex', alignItems: 'center', gap: 4 }}><Download size={13} /> 安装</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 发布表单 */}
        {showPublish && <PublishForm onCancel={() => setShowPublish(false)} onSubmit={publish} />}
      </div>
    </div>
  )
}

function PublishForm({ onCancel, onSubmit }: { onCancel: () => void; onSubmit: (f: any) => void }) {
  const [kind, setKind] = useState('node')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [price, setPrice] = useState('')
  const [downloadUrl, setDownloadUrl] = useState('')
  const [tags, setTags] = useState('')
  const [sending, setSending] = useState(false)

  const input: any = {
    width: '100%', border: '1px solid ' + ('var(--border)'), borderRadius: 8,
    padding: '7px 10px', fontSize: 13, outline: 'none', background: '#141828', color: 'inherit',
    boxSizing: 'border-box', marginBottom: 8,
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 10, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 420, maxWidth: '92vw', border: '1px solid ' + ('var(--border)'), background: 'var(--bg-panel)', borderRadius: 12, padding: 18 }}>
        <b style={{ fontSize: 15, display: 'block', marginBottom: 10 }}>发布作品</b>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          {['node', 'workflow'].map((k) => (
            <button key={k} onClick={() => setKind(k)} style={{ padding: '5px 14px', borderRadius: 999, cursor: 'pointer', fontSize: 12, border: '1px solid ' + (kind === k ? '#3a5bd9' : ('var(--border)')), background: kind === k ? '#e9eefc' : 'transparent', color: kind === k ? '#3a5bd9' : 'var(--text-faint)' }}>
              {k === 'node' ? '节点' : '工作流'}
            </button>
          ))}
        </div>
        <input style={input} placeholder="标题 *" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea style={{ ...input, minHeight: 60, resize: 'vertical' }} placeholder="描述" value={description} onChange={(e) => setDescription(e.target.value)} />
        <input style={input} placeholder="价格（积分，0 = 免费）" type="number" value={price} onChange={(e) => setPrice(e.target.value)} />
        <input style={input} placeholder="网盘直链（百度/123 分享直链，可留空）" value={downloadUrl} onChange={(e) => setDownloadUrl(e.target.value)} />
        <input style={input} placeholder="标签（逗号分隔，如 文案, 视频）" value={tags} onChange={(e) => setTags(e.target.value)} />
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 10 }}>提示：先导出节点/工作流得到 zip，上传到网盘取直链后填在上方；本地桩可留空直链仅作演示。</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onCancel} style={{ padding: '6px 14px', borderRadius: 8, cursor: 'pointer', border: '1px solid ' + ('var(--border)'), background: 'transparent', color: 'var(--text-faint)', fontSize: 13 }}>取消</button>
          <button onClick={() => { setSending(true); onSubmit({ kind, title, description, price, download_url: downloadUrl, tags }) }} disabled={!title || sending} style={{ padding: '6px 16px', borderRadius: 8, cursor: sending || !title ? 'wait' : 'pointer', border: 'none', background: '#3a5bd9', color: '#fff', fontSize: 13 }}>发布</button>
        </div>
      </div>
    </div>
  )
}
