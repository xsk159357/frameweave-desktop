// 自定义画布节点组件（v3 霓虹标签节点）
import { Fragment, memo } from 'react'
import { AlertTriangle, PencilLine } from 'lucide-react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FlowNodeData } from '../types'
import { useAppStore } from '../store'

const statusColor: Record<string, string> = {
  pending: '#6b779f',
  queued: '#8fa3c8',
  running: '#fbbf24',
  success: '#34d399',
  failed: '#f87171',
  cancelled: '#8b93a9',
  cached: '#22d3ee',
}

const statusText: Record<string, string> = {
  pending: '待运行', queued: '排队中', running: '运行中',
  success: '完成', failed: '失败', cancelled: '已取消', cached: '缓存命中',
}

const catGrad: Record<string, string> = {
  输入: 'linear-gradient(135deg,#6c8cff,#8b5cf6)',
  语义: 'linear-gradient(135deg,#8b5cf6,#c084fc)',
  分析: 'linear-gradient(135deg,#0ea5a4,#34d399)',
  控制: 'linear-gradient(135deg,#f59e0b,#f97316)',
  输出: 'linear-gradient(135deg,#f87171,#fb7185)',
}

function FlowNodeInner({ id, data, selected }: NodeProps) {
  const { type_id, title, category, params, last_params, status, error, progress, asset_ids, spec } = data

  const color = statusColor[status] || statusColor.pending
  const grad = catGrad[category] || 'var(--accent-grad)'

  const diffKeys = (() => {
    if (!last_params || Object.keys(last_params).length === 0) return []
    const cur = params || {}
    return Object.keys({ ...cur, ...last_params }).filter((k) =>
      JSON.stringify(cur[k] ?? '') !== JSON.stringify(last_params[k] ?? ''))
  })()
  const hasDiff = diffKeys.length > 0
  const iconChar = (() => {
    const last = String(type_id || '').split('/').pop() || ''
    const ch = last.replace(/[^a-zA-Z0-9]/g, '')[0]
    return ch ? ch.toUpperCase() : (title || '?').slice(0, 1)
  })()

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); useAppStore.getState().setSelectedNodeId(id) }}
      style={{
        position: 'relative', width: 224, background: 'linear-gradient(180deg, rgba(30,37,64,.86), rgba(22,27,48,.9))',
        backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        border: '1px solid ' + (selected ? 'rgba(108,140,255,.9)' : 'rgba(148,168,255,.2)'),
        borderRadius: 16,
        boxShadow: selected
          ? '0 0 0 3px rgba(108,140,255,.22), 0 0 24px rgba(108,140,255,.28), var(--shadow-md)'
          : '0 8px 24px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.07)',
        padding: '12px 13px 14px', fontSize: 12, color: 'var(--text)',
        overflow: 'visible', transition: 'box-shadow var(--t-fast) var(--t-ease), border-color var(--t-fast) var(--t-ease)',
      }}
    >
      {/* 顶部状态霓虹条 */}
      <div style={{
        position: 'absolute', top: -1, left: 12, right: 12, height: 2.5, pointerEvents: 'none',
        borderRadius: 2, background: color,
        boxShadow: '0 0 10px ' + color + 'cc',
        opacity: status === 'pending' ? .45 : .95,
      }} />
      {/* 类别霓虹图标块（左侧饰条） */}
      <div style={{ position: 'absolute', left: 0, top: 18, bottom: 18, width: 3, borderRadius: 3, background: grad, boxShadow: '0 0 10px rgba(139,120,255,.5)', pointerEvents: 'none' }} />

      {/* 标题行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '3px 0 7px 6px' }}>
        <div style={{
          width: 26, height: 26, borderRadius: 8, flexShrink: 0,
          background: grad, color: '#fff', fontSize: 12, fontWeight: 800,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 2px 8px rgba(70,90,200,.4), inset 0 1px 0 rgba(255,255,255,.3)',
        }}
      >{iconChar}</div>
        <span style={{ fontWeight: 650, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{title}</span>
        <span style={{
          fontSize: 8.5, padding: '1px 7px', borderRadius: 999, fontWeight: 650,
          background: 'var(--bg-panel-2)', color: 'var(--text-faint)', letterSpacing: .5, flexShrink: 0,
        }}>{category}</span>
      </div>

      {hasDiff && (
        <div style={{ fontSize: 10, color: 'var(--running)', margin: '0 0 5px 6px', wordBreak: 'break-all', lineHeight: 1.5 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}><PencilLine size={10} /> 已改</span>: {diffKeys.join(', ')}
        </div>
      )}
      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 11, margin: '0 0 5px 6px', wordBreak: 'break-all' }}>
          <><AlertTriangle size={11} style={{ verticalAlign: '-2px', marginRight: 2 }} /> {error}</>
        </div>
      )}
      {status === 'running' && progress !== undefined && (
        <div style={{ height: 4, background: 'var(--bg-panel-2)', borderRadius: 2, overflow: 'hidden', margin: '0 0 6px 6px' }}>
          <div style={{ height: '100%', width: (progress * 100) + '%', background: color, boxShadow: '0 0 8px ' + color + 'aa', transition: 'width .3s' }} />
        </div>
      )}
      {status !== 'pending' && status !== 'running' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--text-faint)', marginLeft: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: '0 0 6px ' + color + 'cc', flexShrink: 0 }} />
          <span>{statusText[status] || status}</span>
          {Object.keys(asset_ids).length > 0 && (
            <span className="fw-badge" style={{ marginLeft: 'auto', background: 'var(--bg-panel)', color: 'var(--text-dim)' }}>
              {Object.keys(asset_ids).length} 输出
            </span>
          )}
        </div>
      )}

      {/* 输入端口：霓虹圆点 + 名称标签 */}
      {(spec?.inputs || []).map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle
            type="target"
            position={Position.Left}
            id={p.name}
            className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            style={{ top: 30 + i * 26 }}
          />
          <span
            style={{
              position: 'absolute', left: 9, top: 30 + i * 26, transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis',
              fontWeight: 500,
            }}
          >{p.name}</span>
        </Fragment>
      ))}
      {/* 输出端口 */}
      {(spec?.outputs || []).map((p, i) => (
        <Fragment key={'out-' + p.name}>
          <Handle
            type="source"
            position={Position.Right}
            id={p.name}
            className="fw-handle out"
            title={'输出: ' + p.name + ' · ' + p.type}
            style={{ top: 30 + i * 26 }}
          />
          <span
            style={{
              position: 'absolute', right: 9, top: 30 + i * 26, transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis',
              textAlign: 'right', fontWeight: 500,
            }}
          >{p.name}</span>
        </Fragment>
      ))}
    </div>
  )
}

export const FlowNode = memo(FlowNodeInner)
