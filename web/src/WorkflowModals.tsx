// t10（D-03/D-06）共享工作流弹层：缺失依赖清单 + 导入后「立即打开」
// 供 WorkflowPanel（模板/导入动线）与 Canvas（运行前检查）复用，避免重复实现。
import type { CSSProperties } from 'react'
import { Store, LayoutTemplate, X, ExternalLink, Copy, ArrowRight } from 'lucide-react'

/** 打开商城并携带 type_id 过滤（MarketPage 消费 detail.typeIds；App 只负责打开面板） */
export function openMarketForTypeIds(typeIds: string[]) {
  const ids = Array.from(new Set((typeIds || []).filter(Boolean)))
  window.dispatchEvent(new CustomEvent('fw-open-market', { detail: { typeIds: ids } }))
}

const overlay: CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 2100, background: 'rgba(6,8,16,.62)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
}
const card: CSSProperties = {
  width: 460, maxWidth: '92vw', borderRadius: 8, padding: '18px 20px',
  background: 'var(--ink-800)',
  border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-2)',
}
const btn = (primary = false): CSSProperties => ({
  padding: '6px 13px', borderRadius: 9, fontSize: 12.5, cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 600,
  background: primary ? 'var(--btn-primary-grad)' : 'transparent',
  color: primary ? '#fff' : 'var(--text-dim)',
  border: primary ? 'none' : '1px solid var(--border)',
})

// ---- D-03：缺失依赖清单（模板创建 / 运行前检查 / 画布未安装节点） ----
export function MissingNodesModal({ title, typeIds, note, onContinue, onClose }: {
  title?: string
  typeIds: string[]
  note?: string
  onContinue?: () => void
  onClose: () => void
}) {
  const ids = Array.from(new Set(typeIds || []))
  if (ids.length === 0) return null
  return (
    <div style={overlay} onClick={onClose}>
      <div style={card} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <LayoutTemplate size={16} color="#e0a800" />
          <span style={{ fontSize: 14, fontWeight: 700, flex: 1 }}>{title || '缺少节点依赖'}</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', lineHeight: 0 }}><X size={15} /></button>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6, marginBottom: 12 }}>
          以下节点类型未安装。安装后画布将自动生效，无需重启。
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginBottom: 14 }}>
          {ids.map((tid) => (
            <div key={tid} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px', borderRadius: 4,
              border: '1px solid var(--border)', background: 'var(--bg-panel-2)',
            }}>
              <span style={{ width: 22, height: 22, borderRadius: 7, flexShrink: 0, background: 'rgba(224,168,0,.15)', color: '#e0a800', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 10.5, fontWeight: 800 }}>
                {(tid.split('/').pop() || '?')[0].toUpperCase()}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 650, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tid.split('/').pop()}</div>
                <div style={{ fontSize: 10.5, color: 'var(--text-faint)', fontFamily: 'Consolas, monospace' }}>{tid} · 未安装</div>
              </div>
              <button style={btn(true)} onClick={() => { openMarketForTypeIds([tid]); onClose() }}>
                <><Store size={12} /> 去商城安装</>
              </button>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center' }}>
          {onContinue && (
            <button style={btn(false)} onClick={onContinue}>
              <><ArrowRight size={12} /> 仍然继续</>
            </button>
          )}
          <button style={btn(true)} onClick={() => { openMarketForTypeIds(ids); onClose() }}>
            <><Store size={12} /> 批量去商城安装</>
          </button>
        </div>
        {note && <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 10 }}>{note}</div>}
      </div>
    </div>
  )
}

// ---- D-06：导入/模板后「立即打开 / 创建副本」（toast 去重：弹窗本身即反馈） ----
export function ImportedWorkflowModal({ name, nodeCount, onOpen, onClose }: {
  name: string
  nodeCount?: number
  onOpen: (duplicate: boolean) => void
  onClose: () => void
}) {
  return (
    <div data-testid="workflow-import-modal" style={overlay} onClick={onClose}>
      <div style={card} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <LayoutTemplate size={16} color="var(--success)" />
          <span style={{ fontSize: 14, fontWeight: 700, flex: 1 }}>工作流已导入</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', lineHeight: 0 }}><X size={15} /></button>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6, marginBottom: 16 }}>
          「{name}」{typeof nodeCount === 'number' ? `（${nodeCount} 个节点）` : ''}已导入到模板库，
          可从左侧「我的工作流」再次创建。要现在打开它吗？
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button data-testid="workflow-import-dismiss" style={btn(false)} onClick={onClose}>稍后处理</button>
          <button data-testid="workflow-import-create-copy" style={btn(false)} onClick={() => onOpen(true)}>
            <><Copy size={12} /> 创建副本</>
          </button>
          <button data-testid="workflow-import-open" style={btn(true)} onClick={() => onOpen(false)}>
            <><ExternalLink size={12} /> 立即打开</>
          </button>
        </div>
      </div>
    </div>
  )
}
