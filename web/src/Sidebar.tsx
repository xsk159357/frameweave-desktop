// 左侧节点面板：搜索 + 分类 + 收藏
import { useMemo, useState } from 'react'
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
        padding: '8px 10px', marginBottom: 4, borderRadius: 6, cursor: 'pointer',
        background: dark ? '#22263a' : '#f4f6fb', border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
        transition: 'all .15s',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#4f6ef7' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = dark ? '#2c3142' : '#e2e6ee' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600 }}>{s.title}</span>
        <button
          onClick={(e) => { e.stopPropagation(); toggleFav(s.type_id) }}
          style={{ background: 'none', border: 'none', fontSize: 14, color: favs.includes(s.type_id) ? '#f5a524' : '#8b93a9' }}
          title="收藏"
        >★</button>
      </div>
      <div style={{ fontSize: 11, color: '#8b93a9', marginTop: 2 }}>{s.type_id}</div>
    </div>
  )

  return (
    <div style={{ width: 260, borderRight: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
      background: dark ? '#12141c' : '#f8f9fb', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      <div style={{ padding: 12 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="🔍 搜索节点..."
          style={{
            width: '100%', padding: '8px 10px', borderRadius: 6,
            border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
            background: dark ? '#1a1d29' : '#fff', color: dark ? '#e8eaf2' : '#1f2430',
          }}
        />
      </div>
      <div style={{ overflow: 'auto', padding: '0 12px 12px', flex: 1 }}>
        {favs.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: '#8b93a9', fontWeight: 600, marginBottom: 6 }}>★ 常用节点</div>
            {specs.filter(s => favs.includes(s.type_id)).map(renderItem)}
          </div>
        )}
        {categories.map(([cat, items]) => (
          <div key={cat} style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: '#8b93a9', fontWeight: 600, marginBottom: 6 }}>{cat}</div>
            {items.map(renderItem)}
          </div>
        ))}
        {categories.length === 0 && <div style={{ color: '#8b93a9', fontSize: 12, padding: 12 }}>无匹配节点</div>}
      </div>
    </div>
  )
}
