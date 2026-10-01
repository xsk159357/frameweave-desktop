// 自定义画布节点组件（v4 节点驱动：类型色端口 / 参数摘要 / 双击内嵌编辑 / 状态整卡）
import { Fragment, memo, useState } from 'react'
import { AlertTriangle, PencilLine, Zap, Lock, Minus } from 'lucide-react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FlowNodeData, PortType } from '../types'
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

// 端口类型 → 颜色（B1：类型可视化）
const portColor: Record<string, string> = {
  VIDEO: '#f87171', IMAGE: '#c084fc', AUDIO: '#4dd0e1',
  SCRIPT: '#34d399', SUBTITLE: '#34d399', SEGMENTS: '#6c8cff',
  TIMELINE: '#6c8cff', JSON: '#a78bfa', STRING: '#9aa6c8',
  INT: '#fbbf24', FLOAT: '#fbbf24', BOOL: '#fbbf24', ANY: '#6b779f',
}
const pc = (t: string) => portColor[t] || portColor.ANY

// 内嵌参数控件（C：节点内编辑，widget 简化集）
function InlineParam({ p, val, onUpdate }: { p: any; val: string; onUpdate: (v: string) => void }) {
  const widget = p.widget || (p.type === 'INT' ? 'number' : 'text')
  if (widget === 'select' || (p.options && p.options.length)) {
    return (
      <select value={String(val)} onChange={(e) => onUpdate(e.target.value)}>
        {(p.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  if (widget === 'number' || p.type === 'INT' || p.type === 'FLOAT') {
    return <input type="number" value={String(val)} onChange={(e) => onUpdate(e.target.value)} />
  }
  if (p.type === 'BOOL') {
    return (
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 7, fontSize: 11.5, cursor: 'pointer' }}>
        <input type="checkbox" checked={String(val) === 'true' || val === true}
          onChange={(e) => onUpdate(String(e.target.checked))} style={{ accentColor: 'var(--accent)', width: 14, height: 14 }} />
        {p.label || p.name}
      </label>
    )
  }
  if (p.name === 'api_key' || (p.name || '').toLowerCase().includes('apikey')) {
    return <input type="password" value={String(val)} placeholder={p.description || '或填 @secret:名称'} onChange={(e) => onUpdate(e.target.value)} />
  }
  return <input type="text" value={String(val)} placeholder={p.description || ''} onChange={(e) => onUpdate(e.target.value)} />
}

function FlowNodeInner({ id, data, selected }: NodeProps) {
  const { type_id, title, category, params, last_params, status, error, progress, asset_ids, spec } = data
  const [editing, setEditing] = useState(false)

  const color = statusColor[status] || statusColor.pending
  const grad = catGrad[category] || 'var(--accent-grad)'
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
  // —— v4.2 性能：状态区高度固定 36px（信息行+状态行），节点尺寸只在编辑/摘要切换时变化，杜绝运行态 ResizeObserver 重排 ——
  const summaryH = summaryItem && !editing ? 20 : 0
  const editH = editing ? paramsList.length * 49 + 24 : 0
  const portH = portRows * 24
  const bodyH = Math.max(editH, portH)
  const statusH = 36
  const nodeH = 44 + summaryH + bodyH + statusH + 12
  const portY = (i: number) => 44 + (editing ? 0 : summaryH) + i * 24
  const statusTop = 44 + summaryH + bodyH + 3

  const updateParam = (name: string, value: string) => {
    ;(window as any).__fwUpdateParams?.(id, { [name]: value })
  }

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id, nodeTitle: (data as FlowNodeData).title })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); setEditing((v) => !v) }}
      style={{
        position: 'relative', width: 240, height: nodeH,
        // v4.2 性能：移除 backdrop-filter（画布 transform 时每帧重采样背层是拖动/缩放卡顿主因），背景提高不透明度保持质感
        background: 'linear-gradient(180deg, rgba(31,38,66,.985), rgba(22,27,48,.985))',
        border: isFailed
          ? '1px solid rgba(248,113,113,.6)'
          : '1px solid ' + (selected ? 'rgba(108,140,255,.95)' : 'rgba(148,168,255,.26)'),
        borderRadius: 16,
        boxShadow: isFailed
          ? '0 0 0 2px rgba(248,113,113,.22), 0 0 18px rgba(248,113,113,.2), var(--shadow-md)'
          : selected
            ? '0 0 0 3px rgba(108,140,255,.22), 0 0 26px rgba(108,140,255,.3), var(--shadow-md)'
            : '0 8px 24px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.08)',
        fontSize: 12, color: 'var(--text)',
        transition: 'box-shadow var(--t-fast) var(--t-ease), border-color var(--t-fast) var(--t-ease)',
      }}
    >
      {/* 顶部状态霓虹条（running 流动） */}
      <div style={{
        position: 'absolute', top: 1, left: 1, right: 1, height: 2.5, pointerEvents: 'none',
        borderRadius: 2,
        background: isRunning ? 'var(--running)' : color,
        opacity: status === 'pending' ? .45 : 1,
        ...(isRunning ? { backgroundImage: 'linear-gradient(90deg, var(--running), #fff3c4, var(--running))', backgroundSize: '200% 100%', animation: 'fw-flow 1.2s linear infinite' } : {}),
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
        {spec?.gpu_required && (
          <span title="需要 GPU" style={{
            display: 'inline-flex', alignItems: 'center', gap: 2, flexShrink: 0,
            fontSize: 8, fontWeight: 700, color: '#ffd47e', background: 'rgba(245,165,36,.14)',
            border: '1px solid rgba(245,165,36,.35)', borderRadius: 999, padding: '1px 5px',
          }}><Zap size={8} />GPU</span>
        )}
        <span style={{
          fontSize: 8.5, padding: '1px 7px', borderRadius: 999, fontWeight: 650, flexShrink: 0,
          background: 'var(--bg-panel-2)', color: 'var(--text-faint)', letterSpacing: .5,
        }}>{category}</span>
      </div>

      {/* ===== 参数摘要行（B2/C3） ===== */}
      {summaryItem && !editing && (
        <div style={{
          margin: '0 12px 2px', padding: '3px 9px', borderRadius: 7,
          background: 'rgba(108,140,255,.09)', border: '1px solid rgba(108,140,255,.16)',
          fontSize: 10, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 5,
          cursor: 'pointer', userSelect: 'none',
        }} onDoubleClick={(e) => { e.stopPropagation(); setEditing(true) }}>
          <span style={{ color: 'var(--accent)', fontWeight: 650, flexShrink: 0 }}>{summaryItem.label}</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{summaryItem.value}</span>
          <PencilLine size={9} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
        </div>
      )}

      {/* ===== 内嵌参数表单（C1：双击展开） ===== */}
      {editing && (
        <div className="fw-node-param" style={{ position: 'relative', margin: '0 12px 4px' }}>
          {paramsList.length === 0 && (
            <div style={{ fontSize: 10.5, color: 'var(--text-faint)', padding: '2px 0 6px' }}>此节点无参数</div>
          )}
          {paramsList.map((pl) => (
            <div key={pl.name}>
              <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 2, display: 'flex', alignItems: 'center', gap: 4 }}>
                {pl.label || pl.name}
                {pl.required && <span style={{ color: 'var(--danger)' }}>*</span>}
              </div>
              <InlineParam p={pl} val={String((params || {})[pl.name] ?? pl.default ?? '')} onUpdate={(v) => updateParam(pl.name, v)} />
            </div>
          ))}
          <div style={{ fontSize: 9.5, color: 'var(--text-faint)', textAlign: 'center', paddingBottom: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
            <Minus size={9} /> 双击收起 <Minus size={9} />
          </div>
        </div>
      )}

      {/* ===== 端口区（左右轨道 + 类型色） ===== */}
      {inputs.map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle
            type="target"
            position={Position.Left}
            id={p.name}
            className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            // v4.3 外观：圆心内移 5px，端口圆点完全收在卡片内，杜绝半圆探出卡边的穿模观感
            style={{ top: portY(i), left: 5, background: pc(p.type), border: '1px solid rgba(10,14,24,.8)' }}
          />
          <span
            style={{
              position: 'absolute', left: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 500,
              display: editing ? 'none' : 'flex', alignItems: 'center', gap: 4,
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
            // v4.3 外观：圆心内移 5px，端口圆点完全收在卡片内
            style={{ top: portY(i), right: 5, background: pc(p.type), border: '1px solid rgba(10,14,24,.8)' }}
          />
          <span
            style={{
              position: 'absolute', right: 9, top: portY(i), transform: 'translateY(-50%)',
              fontSize: 9.5, lineHeight: 1, color: 'var(--text-faint)',
              pointerEvents: 'none', whiteSpace: 'nowrap', maxWidth: 90,
              overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'right', fontWeight: 500,
              display: editing ? 'none' : 'flex', alignItems: 'center', gap: 4,
            }}
          >
            {p.name}
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: pc(p.type), flexShrink: 0 }} />
          </span>
        </Fragment>
      ))}

      {/* ===== 状态区：固定 36px，杜绝运行态 reflow（v4.2） ===== */}
      <div style={{ position: 'absolute', left: 6, right: 6, top: statusTop, height: statusH, fontSize: 10, overflow: 'hidden' }}>
        {/* 行1：错误 > 进度条 > 已改（三选一） */}
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
        {/* 行2：状态行——始终显示（pending 显示「就绪」，避免高度跳变） */}
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
