// 自定义画布节点组件（v4 节点驱动：类型色端口 / 参数摘要 / 双击打开右侧编辑面板）
// v046：移除卡片内嵌编辑（表单撑高节点会覆盖相邻节点=穿模），双击统一打开右侧 ParamPanel，节点尺寸恒定
import { Fragment, memo } from 'react'
import { AlertTriangle, PencilLine, Zap, Minus } from 'lucide-react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FlowNodeData, PortType } from '../types'

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

const catSolid: Record<string, string> = {
  输入: '#7c8cf8',
  语义: '#a78bfa',
  分析: '#34d399',
  控制: '#fbbf24',
  输出: '#f87171',
}

// 端口类型 → 颜色（B1：类型可视化）
const portColor: Record<string, string> = {
  VIDEO: '#f87171', IMAGE: '#c084fc', AUDIO: '#4dd0e1',
  SCRIPT: '#34d399', SUBTITLE: '#34d399', SEGMENTS: '#6c8cff',
  TIMELINE: '#6c8cff', JSON: '#a78bfa', STRING: '#9aa6c8',
  INT: '#fbbf24', FLOAT: '#fbbf24', BOOL: '#fbbf24', ANY: '#6b779f',
}
const pc = (t: string) => portColor[t] || portColor.ANY

function FlowNodeInner({ id, data, selected }: NodeProps) {
  const { type_id, title, category, params, last_params, status, error, progress, asset_ids, spec } = data

  const color = statusColor[status] || statusColor.pending
  const solid = catSolid[category] || 'var(--accent)'
  const isFailed = status === 'failed'
  const isRunning = status === 'running'

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

  const inputs = spec?.inputs || []
  const outputs = spec?.outputs || []
  const paramsList = spec?.params || []
  const portRows = Math.max(inputs.length, outputs.length, 1)
  // 参数摘要行：第一个非空参数（B2）
  const summaryItem = (() => {
    for (const pl of paramsList) {
      const v = (params || {})[pl.name]
      if (v !== undefined && v !== null && String(v) !== '') {
        const s = String(v)
        return { label: pl.label || pl.name, value: s.length > 30 ? s.slice(0, 29) + '…' : s }
      }
    }
    return null
  })()
  // —— v046：节点高度恒定（不随编辑变化，杜绝表单撑高覆盖相邻节点的穿模）；状态区固定 36px ——
  const summaryH = summaryItem ? 20 : 0
  const portH = portRows * 24
  const bodyH = portH
  const statusH = 36
  const nodeH = 44 + summaryH + bodyH + statusH + 12
  const portY = (i: number) => 44 + summaryH + i * 24
  const statusTop = 44 + summaryH + bodyH + 3

  const openEdit = () => { ;(window as any).__fwOpenEdit?.(id) }

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id, nodeTitle: (data as FlowNodeData).title })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); openEdit() }}
      style={{
        position: 'relative', width: 240, height: nodeH,
        background: 'var(--panel)',
        border: isFailed
          ? '1.5px solid rgba(248,113,113,.9)'
          : '1px solid ' + (selected ? '#8b93ff' : 'var(--border)'),
        borderRadius: 16,
        boxShadow: isFailed
          ? '0 0 0 2px rgba(248,113,113,.3), 0 0 20px rgba(248,113,113,.28), var(--shadow-md)'
          : selected
            ? '0 0 0 3px rgba(139,147,255,.2), 0 0 26px rgba(139,147,255,.28), var(--shadow-md)'
            : '0 8px 24px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.08)',
        fontSize: 12, color: 'var(--text)',
        transition: 'box-shadow var(--t-fast) var(--t-ease), border-color var(--t-fast) var(--t-ease)',
        ...(isRunning ? { animation: 'fw-node-pulse 2.2s ease-in-out infinite' } : {}),
      }}
    >
      {/* ===== 端口区（左右轨道 + 类型色，v4.3：圆心内嵌 5px 全收卡内） ===== */}
      {inputs.map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle
            type="target"
            position={Position.Left}
            id={p.name}
            className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            style={{ top: portY(i), left: 5, background: pc(p.type), border: '1px solid rgba(10,14,24,.8)' }}
          />
          <span
            style={{
              position: 'absolute', left: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: pc(p.type), flexShrink: 0 }} />
            {p.name}
          </span>
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
            style={{ top: portY(i), right: 5, background: pc(p.type), border: '1px solid rgba(10,14,24,.8)' }}
          />
          <span
            style={{
              position: 'absolute', right: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'right', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            {p.name}
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: pc(p.type), flexShrink: 0 }} />
          </span>
        </Fragment>
      ))}

      {/* ===== 状态区：固定 36px，杜绝运行态 reflow（v4.2） ===== */}
      <div style={{ position: 'absolute', left: 6, right: 6, top: statusTop, height: statusH, fontSize: 10, overflow: 'hidden' }}>
        {error && (
          <div style={{ color: 'var(--danger)', height: 17, lineHeight: '17px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={error}>
            <><AlertTriangle size={10} style={{ verticalAlign: '-2px', marginRight: 2 }} /> {error}</>
          </div>
        )}
        {!error && isRunning && progress !== undefined && (
          <div style={{ height: 17, display: 'flex', alignItems: 'center' }}>
            <div style={{ flex: 1, height: 4, background: 'var(--bg-panel-2)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: (progress * 100) + '%', background: color, boxShadow: '0 0 8px ' + color + 'aa', transition: 'width .3s' }} />
            </div>
            <span style={{ marginLeft: 6, fontSize: 9, color: 'var(--text-faint)', fontVariantNumeric: 'tabular-nums' }}>{(progress * 100).toFixed(0)}%</span>
          </div>
        )}
        {!error && !isRunning && hasDiff && (
          <div style={{ color: 'var(--running)', height: 17, lineHeight: '17px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={diffKeys.join(', ')}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, verticalAlign: '-1px' }}><PencilLine size={10} /> 已改</span>: {diffKeys.join(', ')}
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, height: 17, lineHeight: '17px' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: '0 0 6px ' + color + 'cc', flexShrink: 0 }} />
          <span style={{ color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {statusText[status] || status}
          </span>
          {Object.keys(asset_ids).length > 0 && (
            <span className="fw-badge" style={{ marginLeft: 'auto', background: 'var(--bg-panel)', color: 'var(--text-dim)' }}>
              {Object.keys(asset_ids).length} 输出
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export const FlowNode = memo(FlowNodeInner)
