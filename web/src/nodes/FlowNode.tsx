// 自定义画布节点组件
import { Fragment, memo } from 'react'
import { AlertTriangle, PencilLine } from 'lucide-react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FlowNodeData } from '../types'
import { useAppStore } from '../store'

const statusColor: Record<string, string> = {
  pending: '#9aa4b8',
  queued: '#8fa3c8',
  running: '#f5a524',
  success: '#30a46c',
  failed: '#e5484d',
  cancelled: '#8b93a9',
  cached: '#6d8aff',
}

const statusText: Record<string, string> = {
  pending: '待运行', queued: '排队中', running: '运行中',
  success: '完成', failed: '失败', cancelled: '已取消', cached: '缓存命中',
}

function FlowNodeInner({ id, data, selected }: NodeProps) {
  const { type_id, title, category, params, last_params, status, error, progress, asset_ids, spec } = data
  const dark = useAppStore((s) => s.dark)
  const dbl = useAppStore((s) => s.setSelectedNodeId)

  const color = statusColor[status] || statusColor.pending

  // 参数差异高亮：当前参数 vs 上次运行参数快照（仅成功/缓存节点有意义）
  const diffKeys = (() => {
    if (!last_params || Object.keys(last_params).length === 0) return []
    const cur = params || {}
    return Object.keys({ ...cur, ...last_params }).filter((k) =>
      JSON.stringify(cur[k] ?? '') !== JSON.stringify(last_params[k] ?? ''))
  })()
  const hasDiff = diffKeys.length > 0

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); dbl(id) }}
      style={{
        position: 'relative', width: 216, background: 'var(--glass)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        border: selected ? '1.5px solid var(--accent)' : 'var(--glass-border)',
        borderRadius: 16, boxShadow: selected ? '0 0 0 4px var(--input-focus-ring), var(--glass-inner), var(--shadow-md)' : 'var(--glass-inner), var(--shadow-sm)',
        padding: '13px 13px 15px', fontSize: 12, color: 'var(--text)',
        overflow: 'hidden', transition: 'box-shadow var(--t-fast) var(--t-ease), border-color var(--t-fast) var(--t-ease)',
      }}
    >
      {/* 顶部渐变状态条 */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: 'linear-gradient(90deg, ' + color + ' 0%, ' + color + '88 100%)', opacity: .9, pointerEvents: 'none' }} />
      <div style={{ fontWeight: 650, marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12.5 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}
          {hasDiff && (
            <span title={'参数与上次运行不同：' + diffKeys.join(', ')}
                  style={{ marginLeft: 6, padding: '1px 7px', borderRadius: 999, fontSize: 9.5,
                           background: 'var(--running-soft)', color: 'var(--running)', fontWeight: 650, cursor: 'help' }}>
              参数改动
            </span>
          )}
        </span>
        <span className="fw-badge" style={{ background: dark ? '#262c3d' : '#eef1f7', color: dark ? '#8b93a9' : '#7c869c' }}>{category}</span>
      </div>
      {hasDiff && (
        <div style={{ fontSize: 10, color: '#b8860b', marginBottom: 4, wordBreak: 'break-all', lineHeight: 1.5 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}><PencilLine size={10} /> 已改</span>: {diffKeys.join(', ')}
        </div>
      )}
      {error && (
        <div style={{ color: '#e5484d', fontSize: 11, marginBottom: 4, wordBreak: 'break-all' }}>
          <><AlertTriangle size={11} style={{ verticalAlign: '-2px', marginRight: 2 }} /> {error}</>
        </div>
      )}
      {status === 'running' && progress !== undefined && (
        <div style={{ height: 4, background: dark ? '#2c3142' : '#eef1f7', borderRadius: 2, overflow: 'hidden', marginBottom: 4 }}>
          <div style={{ height: '100%', width: (progress * 100) + '%', background: color, transition: 'width .3s' }} />
        </div>
      )}
      {status !== 'pending' && status !== 'running' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: dark ? '#8b93a9' : '#7a8499' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, flexShrink: 0 }} />
          <span>{statusText[status] || status}</span>
          {Object.keys(asset_ids).length > 0 && (
            <span className="fw-badge" style={{ marginLeft: 'auto', background: 'var(--glass)', color: 'var(--text-dim)' }}>
              {Object.keys(asset_ids).length} 输出
            </span>
          )}
        </div>
      )}
      {/* 输入端口：多端口垂直分布 + 名称标签 + hover 放大 + 类型提示 */}
      {(spec?.inputs || []).map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle
            type="target"
            position={Position.Left}
            id={p.name}
            className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            style={{ top: 26 + i * 26 }}
          />
          <span
            style={{
              position: 'absolute', left: 8, top: 26 + i * 26, transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: dark ? '#8b93a9' : '#6d768c',
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
            style={{ top: 26 + i * 26 }}
          />
          <span
            style={{
              position: 'absolute', right: 8, top: 26 + i * 26, transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: dark ? '#8b93a9' : '#6d768c',
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
