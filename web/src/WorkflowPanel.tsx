// 左侧工作流列表（v1.2：替代节点库侧栏；节点添加移入画布右键菜单）
import { useEffect, useRef, useState } from 'react'
import { Plus, Search, FileText, Download, Upload, Trash2, Puzzle, Layers, PencilLine, LayoutTemplate } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import { MissingNodesModal, ImportedWorkflowModal } from './WorkflowModals'

interface Props {
  onOpenPlugin: () => void
  readOnly?: boolean
}

export function WorkflowPanel({ onOpenPlugin, readOnly = false }: Props) {
  const workflowId = useAppStore((s) => s.workflowId)
  const setWorkflow = useAppStore((s) => s.setWorkflow)
  const dirty = useAppStore((s) => s.dirty)
  const specs = useAppStore((s) => s.specs)
  const setTimelineNodeId = useAppStore((s) => s.setTimelineNodeId)
  const pushToast = useAppStore((s) => s.pushToast)
  const [list, setList] = useState<{ id: string; name: string; updated_at: number }[]>([])
  const [query, setQuery] = useState('')
  const [plugCount, setPlugCount] = useState(0)
  const [templates, setTemplates] = useState<{ id: string; title: string; description: string; nodes: number; type_ids?: string[] }[]>([])
  const [showTemplates, setShowTemplates] = useState(false)
  const [menu, setMenu] = useState<{ x: number; y: number; id: string; name: string } | null>(null)
  const [nameBox, setNameBox] = useState<null | { mode: 'create' } | { mode: 'rename'; id: string; name: string }>(null)
  const [nameInput, setNameInput] = useState('')
  const shareInput = useRef<HTMLInputElement>(null)
  // t10（D-03/D-06）：模板缺节点弹层 + 导入后「立即打开」弹层
  const [missingModal, setMissingModal] = useState<{ title: string; typeIds: string[] } | null>(null)
  const [importModal, setImportModal] = useState<{ tplId: string; name: string; nodeCount?: number } | null>(null)

  const refresh = async () => {
    try { const l = await api.listWorkflows(); setList(l || []) } catch { /* ignore */ }
    try { const u = await api.userNodes(); setPlugCount((u.nodes || []).length) } catch { /* ignore */ }
    try { const t = await api.listTemplates(); setTemplates(t || []) } catch { /* ignore */ }
  }
  useEffect(() => { refresh() }, [workflowId])

  // t10（D-06 联动）：商城下载导入工作流后（frontend-pro MarketPage 派发），
  // 与左侧「导入分享」共用同一个「立即打开」弹层 + 单条反馈（toast 去重）。
  useEffect(() => {
    const h = (ev: Event) => {
      const d = (ev as CustomEvent).detail || {}
      if (d && d.tpl_id) setImportModal({ tplId: d.tpl_id, name: d.name || '导入的工作流', nodeCount: d.node_count })
    }
    window.addEventListener('fw-workflow-imported', h)
    return () => window.removeEventListener('fw-workflow-imported', h)
  }, [])

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

  const importSharedWorkflow = async (file: File) => {
    try {
      const imported = await api.importWorkflowZip(file)
      // D-06：导入成功弹「立即打开 / 创建副本」——弹窗即反馈，不再重复 toast
      setImportModal({
        tplId: (imported as any).tpl_id || '',
        name: (imported as any).name || '导入的工作流',
        nodeCount: (imported as any).node_count,
      })
      await refresh()
    } catch (e: any) { pushToast('导入工作流失败: ' + (e.message || e), 'err') }
  }

  // D-06：从模板创建工作流（导入后「立即打开/创建副本」共用）
  const openImportedTemplate = async (tplId: string, name: string, duplicate: boolean) => {
    const tpl = importModal
    setImportModal(null)
    if (!tplId) { pushToast('模板 id 缺失，请从左侧「我的工作流」创建', 'err'); return }
    try {
      const created = await api.createFromTemplate(tplId)
      const id = (created as any).id
      const n = (duplicate ? (name || '工作流') + ' 副本' : (created as any).name || name || '模板工作流')
      setWorkflow(id, n)
      setTimelineNodeId(null)
      await refresh()
      const missing = (created as any).missing_type_ids
      if (Array.isArray(missing) && missing.length > 0) {
        setMissingModal({ title: '工作流「' + n + '」缺少节点', typeIds: missing })
      } else {
        pushToast('已创建：' + n, 'ok')
      }
    } catch (e: any) { pushToast('创建模板失败: ' + (e.message || e), 'err') }
  }

  const newFromTemplate = async (tplId: string) => {
    setShowTemplates(false)
    try {
      const created = await api.createFromTemplate(tplId)
      const id = (created as any).id
      const name = (created as any).name || '模板工作流'
      setWorkflow(id, name)
      setTimelineNodeId(null)
      refresh()
      // D-03：模板所需节点未安装 → 弹缺失依赖清单（去商城安装/批量安装，装完自动生效），
      // 不阻断打开画布；无缺失时给出「已创建」明确反馈（D-07）
      const missing = (created as any).missing_type_ids
      if (Array.isArray(missing) && missing.length > 0) {
        setMissingModal({ title: '模板「' + name + '」缺少节点', typeIds: missing })
      } else {
        pushToast('已创建：' + name, 'ok')
      }
    } catch (e: any) { pushToast('创建模板失败: ' + (e.message || e), 'err') }
  }

  const submitName = async () => {
    if (!nameBox) return
    const name = nameInput.trim() || '未命名工作流'
    const nb = nameBox
    setNameBox(null)
    try {
      if (nb.mode === 'create') {
        const c = await api.createWorkflow(name)
        setWorkflow((c as any).id, name)
        setTimelineNodeId(null)
        // D-07：新建工作流成功后明确反馈（空白画布 + toast「已创建」）
        pushToast('已创建：' + name, 'ok')
        refresh()
      } else {
        await api.renameWorkflow(nb.id, name)
        refresh()
        if (nb.id === workflowId) setWorkflow(nb.id, name)
        pushToast('已重命名', 'ok')
      }
    } catch (e: any) { pushToast('失败: ' + (e.message || e), 'err') }
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
        <button className="fw-btn fw-btn-primary" disabled={readOnly} onClick={() => { setNameInput(''); setNameBox({ mode: 'create' }) }}
          style={{ width: '100%', padding: '7px 10px', justifyContent: 'center', fontSize: 12.5 }}>
          <><Plus size={13} /> 新建工作流</>
        </button>
      </div>
      {/* 模板操作：导入分享包 / 从模板新建 */}
      <div style={{ padding: '0 12px 8px', display: 'flex', gap: 6 }}>
        <button disabled={readOnly} onClick={() => shareInput.current?.click()} className="fw-btn fw-btn-ghost" style={{ flex: 1, padding: '6px 6px', justifyContent: 'center', fontSize: 11.5 }} title="导入别人分享的工作流 zip">
          <Upload size={12} /> 导入分享
        </button>
        <button disabled={readOnly || templates.length === 0} onClick={() => setShowTemplates(true)} className="fw-btn fw-btn-ghost" style={{ flex: 1, padding: '6px 6px', justifyContent: 'center', fontSize: 11.5 }}>
          <LayoutTemplate size={12} /> 我的工作流
        </button>
        <input ref={shareInput} type="file" accept=".zip" hidden onChange={(e) => { const file = e.target.files?.[0]; if (file) void importSharedWorkflow(file); e.target.value = '' }} />
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
      {showTemplates && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(8,10,18,.46)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowTemplates(false)}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 520, maxWidth: '90vw', borderRadius: 'var(--radius-xl)', padding: 18, background: 'var(--card-grad)', border: '1px solid var(--border-strong)', boxShadow: 'var(--glass-inner), var(--elev-3)' }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}><LayoutTemplate size={15} color="var(--accent)" /> 我的工作流</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {templates.map((t) => {
                // D-06：模板卡片标注「缺少节点」状态（服务端 type_ids 已返回，未安装时提示）
                const missingCount = Array.isArray((t as any).type_ids)
                  ? (t as any).type_ids.filter((tid: string) => !specs.some((s) => s.type_id === tid)).length
                  : 0
                return (
                <button key={t.id} onClick={() => newFromTemplate(t.id)} style={{ textAlign: 'left', padding: '10px 12px', borderRadius: 12, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-panel)', color: 'var(--text)' }}>
                  <div style={{ fontSize: 13, fontWeight: 650, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title}</span>
                    {missingCount > 0 && (
                      <span className="fw-badge" style={{ background: 'rgba(224,168,0,.15)', color: '#e0a800', flexShrink: 0 }}>缺 {missingCount} 节点</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>{t.description} · {t.nodes} 节点</div>
                </button>
                )
              })}
              {templates.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>暂无模板，请先从商城安装工作流模板。</div>}
            </div>
          </div>
        </div>
      )}
      {/* 底部占位（插件入口已置顶） */}
      {menu && (
        <div style={{ position: 'fixed', left: menu.x, top: menu.y, zIndex: 60, minWidth: 168,
          background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 12, boxShadow: 'var(--shadow-lg)', padding: 5 }}
          onMouseLeave={() => setMenu(null)}>
          <div style={{ padding: '5px 10px', fontSize: 11, color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 200 }}>{menu.name}</div>
          <div style={{ height: 1, background: 'var(--border)', margin: '3px 4px' }} />
          <div style={{ padding: '6px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
            onClick={() => { if (!readOnly) { setNameInput(menu.name); setNameBox({ mode: 'rename', id: menu.id, name: menu.name }); setMenu(null) } }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
            <PencilLine size={12} /> 重命名
          </div>
          <div style={{ padding: '6px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
            onClick={() => {
              const id = menu.id
              setMenu(null)
              api.exportWorkflowZip(id)
                .then(() => pushToast('工作流已导出 zip', 'ok'))
                .catch((e: any) => pushToast('导出失败: ' + (e.message || e), 'err'))
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
            <Download size={12} /> 导出工作流 zip
          </div>
          <div style={{ padding: '6px 10px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: 7 }}
            onClick={() => { if (!readOnly) del(menu.id) }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(248,113,113,.1)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
            <Trash2 size={12} /> 删除
          </div>
        </div>
      )}

      {/* 新建/重命名命名弹层 */}
      {nameBox && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 150, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setNameBox(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 320, background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 14, boxShadow: 'var(--shadow-lg)', padding: '14px 16px' }}>
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>{nameBox.mode === 'create' ? '新建工作流' : '重命名工作流'}</div>
            <input autoFocus value={nameInput} onChange={(e) => setNameInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitName() }}
              placeholder="输入工作流名称" style={{ width: '100%', padding: '7px 10px', borderRadius: 9, fontSize: 12.5, border: '1px solid var(--border)', background: 'var(--input-bg)', color: 'var(--text)', outline: 'none', boxSizing: 'border-box' }} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <button onClick={() => setNameBox(null)} style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 9, padding: '5px 14px', fontSize: 12, color: 'var(--text-dim)', cursor: 'pointer' }}>取消</button>
              <button onClick={submitName} className="fw-btn fw-btn-primary" style={{ padding: '5px 16px', fontSize: 12 }}>确定</button>
            </div>
          </div>
        </div>
      )}

      {/* t10（D-03）：模板缺节点依赖弹层（去商城安装 / 批量安装） */}
      {missingModal && (
        <MissingNodesModal
          title={missingModal.title}
          typeIds={missingModal.typeIds}
          note="安装完成后节点库自动刷新，画布重新加载即生效。"
          onClose={() => setMissingModal(null)}
        />
      )}

      {/* t10（D-06）：导入后「立即打开 / 创建副本」 */}
      {importModal && (
        <ImportedWorkflowModal
          name={importModal.name}
          nodeCount={importModal.nodeCount}
          onOpen={(dup) => void openImportedTemplate(importModal.tplId, importModal.name, dup)}
          onClose={() => setImportModal(null)}
        />
      )}
    </div>
  )
}
