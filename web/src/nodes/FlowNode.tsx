// 自定义画布节点组件（v3.2 规整三段式：标题行 / 端口区 / 状态行）
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

  // 三段式高度：标题行 40 + 端口区（取输入输出较多者）+ 状态信息行
  const inputs = spec?.inputs || []
  const outputs = spec?.outputs || []
  const portRows = Math.max(inputs.length, outputs.length, 1)
  const statusRows = (hasDiff ? 1 : 0) + (error ? 1 : 0)
    + (status === 'running' && progress !== undefined ? 1 : 0)
    + (status !== 'pending' && status !== 'running' ? 1 : 0)
  const nodeH = 58 + portRows * 24 + statusRows * 18
  const portY = (i: number) => 44 + i * 24

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); useAppStore.getState().setSelectedNodeId(id) }}
      style={{
        position: 'relative', width: 240, height: nodeH,
        background: 'linear-gradient(180deg, rgba(30,37,64,.88), rgba(22,27,48,.92))',
        backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        border: '1px solid ' + (selected ? 'rgba(108,140,255,.9)' : 'rgba(148,168,255,.2)'),
        borderRadius: 16,
        boxShadow: selected
          ? '0 0 0 3px rgba(108,140,255,.22), 0 0 26px rgba(108,140,255,.3), var(--shadow-md)'
          : '0 8px 24px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.07)',
        fontSize: 12, color: 'var(--text)',
        transition: 'box-shadow var(--t-fast) var(--t-ease), border-color var(--t-fast) var(--t-ease)',
      }}
    >
      {/* 顶部状态霓虹条 */}
      <div style={{
        position: 'absolute', top: 1, left: 1, right: 1, height: 2.5, pointerEvents: 'none',
        borderRadius: 2, background: color, opacity: status === 'pending' ? .5 : 1,
      }} />
      {/* 类别饰条 */}
      <div style={{ position: 'absolute', left: 1, top: 12, bottom: 12, width: 3, borderRadius: 3, background: grad, pointerEvents: 'none' }} />

      {/* ===== 标题行 ===== */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 9px 6px 12px' }}>
        <div style={{
          width: 24, height: 24, borderRadius: 7, flexShrink: 0,
          background: grad, color: '#fff', fontSize: 11.5, fontWeight: 800,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 2px 8px rgba(70,90,200,.4), inset 0 1px 0 rgba(255,255,255,.3)',
        }}>{iconChar}</div>
        <span style={{
          fontWeight: 650, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap', flex: 1, minWidth: 0,
        }}>{title}</span>
        <span style={{
          fontSize: 8.5, padding: '1px 7px', borderRadius: 999, fontWeight: 650, flexShrink: 0,
          background: 'var(--bg-panel-2)', color: 'var(--text-faint)', letterSpacing: .5,
        }}>{category}</span>
      </div>

      {/* ===== 端口区（左右轨道 + 中间留白） ===== */}
      {inputs.map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle
            type="target"
            position={Position.Left}
            id={p.name}
            className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            style={{ top: portY(i) }}
          />
          <span
            style={{
              position: 'absolute', left: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 500,
            }}
          >{p.name}</span>
        </Fragment>
      ))}
      {outputs.map((p, i) => (
        <Fragment key={'out-' + p.name}>
          <Handle
            type="source"
            position={Position.Right}
            id={p.name}
            className="fw-handle out"
            title={'输出: ' + p.name + ' · ' + p.type}
            style={{ top: portY(i) }}
          />
          <span
            style={{
              position: 'absolute', right: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'right', fontWeight: 500,
            }}
          >{p.name}</span>
        </Fragment>
      ))}

      {/* ===== 状态信息行 ===== */}
      <div style={{ position: 'absolute', left: 6, right: 6, top: nodeH - 16 - statusRows * 18 + 4, fontSize: 10 }}>
        {hasDiff && (
          <div style={{ color: 'var(--running)', marginBottom: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, verticalAlign: '-1px' }}><PencilLine size={10} /> 已改</span>: {diffKeys.join(', ')}
          </div>
        )}
        {error && (
          <div style={{ color: 'var(--danger)', marginBottom: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            <><AlertTriangle size={10} style={{ verticalAlign: '-2px', marginRight: 2 }} /> {error}</>
          </div>
        )}
        {status === 'running' && progress !== undefined && (
          <div style={{ height: 4, background: 'var(--bg-panel-2)', borderRadius: 2, overflow: 'hidden', marginBottom: 3 }}>
            <div style={{ height: '100%', width: (progress * 100) + '%', background: color, boxShadow: '0 0 8px ' + color + 'aa', transition: 'width .3s' }} />
          </div>
        )}
        {status !== 'pending' && status !== 'running' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: '0 0 6px ' + color + 'cc', flexShrink: 0 }} />
            <span style={{ color: 'var(--text-faint)' }}>{statusText[status] || status}</span>
            {Object.keys(asset_ids).length > 0 && (
              <span className="fw-badge" style={{ marginLeft: 'auto', background: 'var(--bg-panel)', color: 'var(--text-dim)' }}>
                {Object.keys(asset_ids).length} 输出
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export const FlowNode = memo(FlowNodeInner)
