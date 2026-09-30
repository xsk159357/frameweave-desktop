// 左侧节点面板：搜索 + 分类 + 收藏
import { useMemo, useState } from 'react'
import { Star, Puzzle } from 'lucide-react'
import { useAppStore } from './store'

export function Sidebar({ onAddNode }: { onAddNode: (typeId: string) => void }) {
  const specs = useAppStore((s) => s.specs)
  const dark = useAppStore((s) => s.dark)
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
    输入: 'linear-gradient(135deg,#5b6cf7,#8a5cf6)',
    语义: 'linear-gradient(135deg,#8a5cf6,#c26df5)',
    分析: 'linear-gradient(135deg,#2fa26b,#3ecb85)',
    控制: 'linear-gradient(135deg,#f5a524,#f07b3f)',
    输出: 'linear-gradient(135deg,#e5484d,#ff7a7d)',
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
      onClick={() => onAddNode(s.type_id)}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
        padding: '8px 9px', marginBottom: 3, borderRadius: 12,
        transition: 'background var(--t-fast) var(--t-ease), transform var(--t-fast) var(--t-ease)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'var(--hover-bg)'
        e.currentTarget.style.transform = 'translateX(2px)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.transform = 'translateX(0)'
      }}
    >
      <div style={{
        width: 30, height: 30, borderRadius: 9, flexShrink: 0,
        background: catGrad[s.category] || 'var(--accent-grad)', color: '#fff',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 13.5, fontWeight: 700,
        boxShadow: '0 2px 8px rgba(60,72,140,.26), inset 0 1px 0 rgba(255,255,255,.28)',
      }}>{short(s)}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
          <button
            onClick={(e) => { e.stopPropagation(); toggleFav(s.type_id) }}
            style={{
              background: 'none', border: 'none', padding: 2, lineHeight: 0, cursor: 'pointer', flexShrink: 0,
              color: favs.includes(s.type_id) ? '#f5a524' : dark ? '#4a5264' : '#b6bdd0',
              opacity: favs.includes(s.type_id) ? 1 : 0.55,
              transition: 'opacity var(--t-fast), color var(--t-fast)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = '#f5a524' }}
            onMouseLeave={(e) => { e.currentTarget.style.opacity = favs.includes(s.type_id) ? '1' : '0.55'; e.currentTarget.style.color = favs.includes(s.type_id) ? '#f5a524' : (dark ? '#4a5264' : '#b6bdd0') }}
            title={favs.includes(s.type_id) ? '取消收藏' : '收藏'}
          ><Star size={13} fill={favs.includes(s.type_id) ? '#f5a524' : 'none'} color={favs.includes(s.type_id) ? '#f5a524' : (dark ? '#4a5264' : '#b6bdd0')} /></button>
        </div>
        <div style={{ fontSize: 10.5, color: dark ? '#5d6579' : '#98a1b6', marginTop: 1, fontFamily: 'Consolas, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.type_id}</div>
      </div>
    </div>
  )

  const SectionTitle = ({ children, extra }: { children: React.ReactNode; extra?: string }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 12px 7px' }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: .8, color: dark ? '#8b93a9' : '#7c869c', textTransform: 'uppercase' }}>{children}</span>
      {extra && <span className="fw-badge" style={{ background: dark ? '#262c3d' : '#eef1f7', color: dark ? '#8b93a9' : '#7a8499' }}>{extra}</span>}
      <span style={{ flex: 1, height: 1, background: dark ? '#1d2130' : '#e8ebf3' }} />
    </div>
  )

  return (
    <div style={{ width: 246, borderRight: 'var(--glass-border)',
      background: 'linear-gradient(180deg, var(--glass-strong), var(--glass))', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
      boxShadow: 'var(--glass-inner), inset -1px 0 0 rgba(255,255,255,.35)', display: 'flex', flexDirection: 'column', flexShrink: 0, position: 'relative', zIndex: 5 }}>
      <div style={{ padding: '14px 14px 10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ fontWeight: 700, fontSize: 13.5, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Puzzle size={14} color="var(--accent)" /> 节点库</span>
          <span className="fw-badge" style={{ background: dark ? '#262c3d' : '#eef1f7', color: dark ? '#8b93a9' : '#7a8499' }}>{specs.length}</span>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索节点…"
          className="fw-input"
          style={{ paddingLeft: 30, backgroundImage: "url('data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2214%22 height=%2214%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%238b93a9%22 stroke-width=%222%22><circle cx=%2211%22 cy=%2211%22 r=%227%22/><path d=%22M21 21l-4.3-4.3%22/></svg>')", backgroundRepeat: 'no-repeat', backgroundPosition: '10px center' }}
        />
      </div>
      <div className="fw-scroll" style={{ overflow: 'auto', padding: '0 8px 14px', flex: 1 }}>
        {favs.length > 0 && (
          <div>
            <SectionTitle><span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Star size={10} fill="#f5a524" /> 常用节点</span></SectionTitle>
            {specs.filter(s => favs.includes(s.type_id)).map(renderItem)}
          </div>
        )}
        {categories.map(([cat, items]) => (
          <div key={cat}>
            <SectionTitle extra={String(items.length)}>{cat}</SectionTitle>
            {items.map(renderItem)}
          </div>
        ))}
        {categories.length === 0 && (
          <div style={{ color: dark ? '#5d6579' : '#9aa2b5', fontSize: 12, padding: '20px 4px', textAlign: 'center' }}>
            无匹配节点
          </div>
        )}
      </div>
    </div>
  )
}
