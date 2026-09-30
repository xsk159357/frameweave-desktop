// 自定义画布节点组件
import { memo } from 'react'
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
        position: 'relative', width: 200, background: dark ? '#1a1d29' : '#fff',
        border: '1px solid ' + (selected ? '#4f6ef7' : (dark ? '#2c3142' : '#e2e6ee')),
        borderTop: '3px solid ' + color,
        borderRadius: 8, boxShadow: selected ? '0 0 0 2px rgba(79,110,247,.25)' : '0 1px 3px rgba(0,0,0,.08)',
        padding: 10, fontSize: 12, color: dark ? '#e8eaf2' : '#1f2430',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
        <span>{title}
          {hasDiff && (
            <span title={'参数与上次运行不同：' + diffKeys.join(', ')}
                  style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 9, fontSize: 10,
                           background: '#f5a524', color: '#1f2430', fontWeight: 600, cursor: 'help' }}>
              参数改动
            </span>
          )}
        </span>
        <span style={{ fontSize: 10, color: '#8b93a9' }}>{category}</span>
      </div>
      {hasDiff && (
        <div style={{ fontSize: 10, color: '#b8860b', marginBottom: 4, wordBreak: 'break-all', lineHeight: 1.5 }}>
          ⚑ 已改: {diffKeys.join(', ')}
        </div>
      )}
      {error && (
        <div style={{ color: '#e5484d', fontSize: 11, marginBottom: 4, wordBreak: 'break-all' }}>
          ⚠ {error}
        </div>
      )}
      {status === 'running' && progress !== undefined && (
        <div style={{ height: 4, background: dark ? '#2c3142' : '#eef1f7', borderRadius: 2, overflow: 'hidden', marginBottom: 4 }}>
          <div style={{ height: '100%', width: (progress * 100) + '%', background: color, transition: 'width .3s' }} />
        </div>
      )}
      {status !== 'pending' && status !== 'running' && (
        <div style={{ fontSize: 10, color: '#8b93a9' }}>
          {statusText[status] || status}
          {Object.keys(asset_ids).length > 0 ? ' · ' + Object.keys(asset_ids).length + ' 输出' : ''}
        </div>
      )}
      {/* 输入端口：多端口垂直分布 + hover 放大 + 类型提示 */}
      {(spec?.inputs || []).map((p, i) => (
        <Handle
          key={'in-' + p.name}
          type="target"
          position={Position.Left}
          id={p.name}
          className="fw-handle in"
          title={'输入: ' + p.name + ' · ' + p.type}
          style={{ top: 24 + i * 26 }}
        />
      ))}
      {/* 输出端口 */}
      {(spec?.outputs || []).map((p, i) => (
        <Handle
          key={'out-' + p.name}
          type="source"
          position={Position.Right}
          id={p.name}
          className="fw-handle out"
          title={'输出: ' + p.name + ' · ' + p.type}
          style={{ top: 24 + i * 26 }}
        />
      ))}
    </div>
  )
}

export const FlowNode = memo(FlowNodeInner)
