// 左侧工作流列表（v1.2：替代节点库侧栏；节点添加移入画布右键菜单）
import { useEffect, useState } from 'react'
import { Plus, Search, FileText, Download, Trash2, Puzzle, Layers } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

interface Props {
  onOpenPlugin: () => void
}

export function WorkflowPanel({ onOpenPlugin }: Props) {
  const workflowId = useAppStore((s) => s.workflowId)
  const setWorkflow = useAppStore((s) => s.setWorkflow)
  const dirty = useAppStore((s) => s.dirty)
  const setTimelineNodeId = useAppStore((s) => s.setTimelineNodeId)
  const pushToast = useAppStore((s) => s.pushToast)
  const [list, setList] = useState<{ id: string; name: string; updated_at: number }[]>([])
  const [query, setQuery] = useState('')
  const [plugCount, setPlugCount] = useState(0)
  const [menu, setMenu] = useState<{ x: number; y: number; id: string; name: string } | null>(null)

  const refresh = async () => {
    try { const l = await api.listWorkflows(); setList(l || []) } catch { /* ignore */ }
    try { const u = await api.userNodes(); setPlugCount((u.nodes || []).length) } catch { /* ignore */ }
  }
  useEffect(() => { refresh() }, [workflowId])

  const fmt = (ts: number) => {
    const d = Date.now() / 1000 - ts
    if (d < 60) return '刚刚'
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前'
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前'
    return Math.floor(d / 86400) + ' 天前'
  }
  const items = list.filter(w => !query || (w.name || w.id).toLowerCase().includes(query.toLowerCase()))

  const open = (id: string, name: string) => { setWorkflow(id, name); setTimelineNodeId(null); setMenu(null) }

  const del = async (id: string) => {
    setMenu(null)
    if (!window.confirm('删除该工作流？不可恢复。')) return
    try {
      await api.deleteWorkflow(id)
      refresh()
      if (id === workflowId) {
        const l = await api.listWorkflows()
        if (l.length > 0) open(l[0].id, (l[0] as any).name || '')
        else pushToast('没有工作流了，点 + 新建', 'info')
      }
    } catch (e: any) { pushToast('删除失败: ' + (e.message || e), 'err') }
  }

  return (
    <div style={{
      width: 232, flexShrink: 0, display: 'flex', flexDirection: 'column',
      background: 'var(--bg)', borderRight: '1px solid var(--border)', minHeight: 0,
    }}>
      {/* 头部：标题 + 插件入口 */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '12px 12px 8px', gap: 6 }}>
        <Layers size={14} color="var(--accent)" style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: .6, color: 'var(--text-dim)', flex: 1 }}>工作流</span>
        <button title="节点插件" onClick={onOpenPlugin}
          style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 7px', borderRadius: 8, color: 'var(--text-faint)', fontSize: 11 }}>
          <Puzzle size={12} /> {plugCount > 0 ? plugCount : ''}
        </button>
      </div>
      {/* 新建主按钮 */}
      <div style={{ padding: '2px 12px 8px' }}>
        <button className="fw-btn fw-btn-primary" onClick={async () => {
          try {
            const c = await api.createWorkflow('新工作流')
            setWorkflow((c as any).id, '新工作流')
            setTimelineNodeId(null)
          } catch (e: any) { pushToast('新建失败: ' + (e.message || e), 'err') }
        }} style={{ width: '100%', padding: '7px 10px', justifyContent: 'center', fontSize: 12.5 }}>
          <><Plus size={13} /> 新建工作流</>
        </button>
      </div>
      {/* 搜索 */}
      <div style={{ padding: '0 12px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--panel-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '5px 9px' }}>
          <Search size={12} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索工作流"
            style={{ background: 'none', border: 'none', outline: 'none', color: 'var(--text)', fontSize: 12, width: '100%' }} />
        </div>
      </div>
      {/* 列表 */}
      <div className="fw-scroll" style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
        {items.length === 0 && (
          <div style={{ padding: '26px 10px', textAlign: 'center', color: 'var(--text-faint)', fontSize: 12, lineHeight: 1.8 }}>
            {query ? '没有匹配的工作流' : '还没有工作流，点击上方「新建工作流」开始'}
          </div>
        )}
        {items.map((w) => {
          const cur = w.id === workflowId
          return (
            <div key={w.id} onClick={() => open(w.id, w.name)}
              onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, id: w.id, name: w.name }) }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '8px 9px', borderRadius: 10, cursor: 'pointer',
                background: cur ? 'var(--accent-soft)' : 'transparent', border: '1px solid ' + (cur ? 'rgba(139,147,255,.18)' : 'transparent'),
                marginBottom: 2, transition: 'background var(--t-fast)',
              }}
              onMouseEnter={(e) => { if (!cur) e.currentTarget.style.background = 'var(--hover)' }}
              onMouseLeave={(e) => { if (!cur) e.currentTarget.style.background = 'transparent' }}>
              <FileText size={13} style={{ color: cur ? 'var(--accent)' : 'var(--text-faint)', flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: cur ? 650 : 500, color: cur ? 'var(--text)' : 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {w.name || w.id}
                  {cur && dirty && <span style={{ color: 'var(--running)', marginLeft: 5 }}>●</span>}
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 1 }}>{fmt(w.updated_at || 0)}</div>
              </div>
            </div>
          )
        })}
      </div>
      {/* 底部占位（插件入口已置顶） */}
      {menu && (
        <div style={{ position: 'fixed', left: menu.x, top: menu.y, zIndex: 60, minWidth: 168,
          background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 12, boxShadow: 'var(--shadow-lg)', padding: 5 }}
          onMouseLeave={() => setMenu(null)}>
          <div style={{ padding: '5px 10px', fontSize: 11, color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 200 }}>{menu.name}</div>
          <div style={{ height: 1, background: 'var(--border)', margin: '3px 4px' }} />
          <div style={{ padding: '6px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
            onClick={() => window.open('http://127.0.0.1:8788/api/export/workflow/' + menu.id, '_blank')}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
            <Download size={12} /> 导出工作流 zip
          </div>
          <div style={{ padding: '6px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: 7 }}
            onClick={() => del(menu.id)}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(248,113,113,.1)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
            <Trash2 size={12} /> 删除
          </div>
        </div>
      )}
    </div>
  )
}
