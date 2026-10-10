// 左侧节点面板：搜索 + 分类 + 收藏（v3 霓虹行式）
import { useMemo, useState, type ReactNode } from 'react'
import { Star, Puzzle, Zap } from 'lucide-react'
import { useAppStore } from './store'

export function Sidebar({ onAddNode }: { onAddNode: (typeId: string) => void }) {
  const specs = useAppStore((s) => s.specs)
  const [query, setQuery] = useState('')
  const [favs, setFavs] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('fw_favs') || '[]') } catch { return [] }
  })

  const categories = useMemo(() => {
    const map = new Map<string, typeof specs>()
    for (const s of specs) {
      if (query && !s.title.includes(query) && !s.type_id.includes(query) && !s.description.includes(query)) continue
      if (!map.has(s.category)) map.set(s.category, [])
      map.get(s.category)!.push(s)
    }
    return [...map.entries()]
  }, [specs, query])

  const catGrad: Record<string, string> = {
    输入: 'linear-gradient(135deg,#6c8cff,#8b5cf6)',
    语义: 'linear-gradient(135deg,#8b5cf6,#c084fc)',
    分析: 'linear-gradient(135deg,#0ea5a4,#34d399)',
    控制: 'linear-gradient(135deg,#f59e0b,#f97316)',
    输出: 'linear-gradient(135deg,#f87171,#fb7185)',
  }
  const catColor: Record<string, string> = {
    输入: '#6c8cff',
    语义: '#a78bfa',
    分析: '#34d399',
    控制: '#fbbf24',
    输出: '#f87171',
  }
  const short = (s: any) => {
    const last = String(s.type_id || '').split('/').pop() || ''
    const ch = last.replace(/[^a-zA-Z0-9]/g, '')[0]
    return ch ? ch.toUpperCase() : (s.title || '?').slice(0, 1)
  }

  const toggleFav = (typeId: string) => {
    const next = favs.includes(typeId) ? favs.filter(f => f !== typeId) : [...favs, typeId]
    setFavs(next)
    localStorage.setItem('fw_favs', JSON.stringify(next))
  }

  const renderItem = (s: any) => (
    <div
      key={s.type_id}
      draggable
      onDragStart={(e) => { e.dataTransfer.setData('application/fw-node', s.type_id); e.dataTransfer.effectAllowed = 'copy' }}
      onClick={() => onAddNode(s.type_id)}
      title={s.description || s.title}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
        padding: '8px 10px', marginBottom: 2, borderRadius: 12,
        border: '1px solid transparent',
        position: 'relative',
        transition: 'background var(--t-fast) var(--t-ease), transform var(--t-fast) var(--t-ease), border-color var(--t-fast)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'var(--accent-soft)'
        e.currentTarget.style.borderColor = 'rgba(139,147,255,.16)'
        e.currentTarget.style.transform = 'translateX(2px)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.borderColor = 'transparent'
        e.currentTarget.style.transform = 'translateX(0)'
      }}
    >
      <div style={{
        width: 30, height: 30, borderRadius: 9, flexShrink: 0,
        background: catGrad[s.category] || 'var(--accent-grad)', color: '#fff',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13.5, fontWeight: 700,
        boxShadow: '0 2px 10px rgba(70,90,200,.35), inset 0 1px 0 rgba(255,255,255,.3)',
      }}>{short(s)}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
          <button
            onClick={(e) => { e.stopPropagation(); toggleFav(s.type_id) }}
            style={{
              background: 'none', border: 'none', padding: 2, lineHeight: 0, cursor: 'pointer', flexShrink: 0,
              color: favs.includes(s.type_id) ? '#fbbf24' : 'var(--text-faint)',
              opacity: favs.includes(s.type_id) ? 1 : 0.5,
              transition: 'opacity var(--t-fast), color var(--t-fast)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = '#fbbf24' }}
            onMouseLeave={(e) => { e.currentTarget.style.opacity = favs.includes(s.type_id) ? '1' : '0.5'; e.currentTarget.style.color = favs.includes(s.type_id) ? '#fbbf24' : 'var(--text-faint)' }}
            title={favs.includes(s.type_id) ? '取消收藏' : '收藏'}
          ><Star size={13} fill={favs.includes(s.type_id) ? '#fbbf24' : 'none'} color={favs.includes(s.type_id) ? '#fbbf24' : 'var(--text-faint)'} /></button>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 1, fontFamily: 'Consolas, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.type_id}</span>
          {s.gpu_required && (
            <span title="需要 GPU" style={{ display: 'inline-flex', alignItems: 'center', gap: 2, flexShrink: 0, fontSize: 8, fontWeight: 700, color: '#ffd47e', background: 'rgba(245,165,36,.14)', border: '1px solid rgba(245,165,36,.35)', borderRadius: 999, padding: '0 5px' }}><Zap size={7} />GPU</span>
          )}
        </div>
      </div>
    </div>
  )

  const SectionTitle = ({ children, extra, color }: { children?: ReactNode; extra?: string; color?: string }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 10px 8px' }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 1, color: color || 'var(--text-faint)', textTransform: 'uppercase' }}>{children}</span>
      {extra && <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-faint)' }}>{extra}</span>}
      <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
    </div>
  )

  return (
    <div style={{ width: 248, borderRight: 'var(--glass-border)',
      background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
      boxShadow: 'var(--glass-inner)', display: 'flex', flexDirection: 'column', flexShrink: 0, position: 'relative', zIndex: 5 }}>
      <div style={{ padding: '14px 14px 10px', borderBottom: 'var(--glass-border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ fontWeight: 700, fontSize: 13.5, display: 'inline-flex', alignItems: 'center', gap: 7, letterSpacing: .3 }}><Puzzle size={14} color="var(--accent)" /> 节点库</span>
          <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-faint)' }}>{specs.length}</span>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索节点…"
          className="fw-input"
          style={{ paddingLeft: 30, backgroundImage: "url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2214%22 height=%2214%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%236b779f%22 stroke-width=%222%22><circle cx=%2211%22 cy=%2211%22 r=%227%22/><path d=%22M21 21l-4.3-4.3%22/></svg>')", backgroundRepeat: 'no-repeat', backgroundPosition: '10px center' }}
        />
      </div>
      <div className="fw-scroll" style={{ overflow: 'auto', padding: '4px 8px 14px', flex: 1 }}>
        {favs.length > 0 && (
          <div>
            <SectionTitle color="#fbbf24"><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Star size={10} fill="#fbbf24" /> 常用节点</span></SectionTitle>
            {specs.filter(s => favs.includes(s.type_id)).map(renderItem)}
          </div>
        )}
        {categories.map(([cat, items]) => (
          <div key={cat}>
            <SectionTitle extra={String(items.length)} color={catColor[cat]}>{cat}</SectionTitle>
            {items.map(renderItem)}
          </div>
        ))}
        {categories.length === 0 && (
          <div style={{ color: 'var(--text-faint)', fontSize: 12, padding: '20px 10px', textAlign: 'center', lineHeight: 1.7 }}>
            {specs.length === 0
              ? <>节点库为空。<br />请到「商城」安装节点后自动出现。</>
              : '无匹配节点'}
          </div>
        )}
      </div>
    </div>
  )
}
