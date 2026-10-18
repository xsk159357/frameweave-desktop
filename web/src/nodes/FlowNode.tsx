// 画布节点（墨织 v2）：取景框式卡片 · 分类色细条 · 类型色端口 · 固定高度
// 交互契约保持不变：双击打开右侧检查器；展开参数为卡片内覆盖层（高度仍由 Canvas estNodeH 计算）。
import { Fragment, memo, useEffect, useRef, useState } from 'react'
import { AlertTriangle, PencilLine, Zap, ChevronDown, ChevronUp, Unplug, Copy, X } from 'lucide-react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FlowNodeData, PortType } from '../types'
import { categoryColor, categoryNormal } from '../types'

type FlowNodeDataType = FlowNodeData & Record<string, unknown>

const statusColor: Record<string, string> = {
  pending: '#79746A',
  queued: '#8FAEC4',
  running: '#D2A45E',
  success: '#8FB996',
  failed: '#D47B70',
  cancelled: '#8E8B83',
  cached: '#9BB7C4',
}

const statusText: Record<string, string> = {
  pending: '待运行', queued: '排队中', running: '运行中',
  success: '完成', failed: '失败', cancelled: '已取消', cached: '缓存命中',
}

function catSolid(category: string): string {
  return categoryColor(category)
}

// 端口类型 → 颜色（低饱和，仅作识别）
const portColor: Record<string, string> = {
  VIDEO: '#C08077', IMAGE: '#A08FBE', AUDIO: '#7FA9AC',
  SCRIPT: '#86A98B', SUBTITLE: '#86A98B', SEGMENTS: '#7E93B8',
  TIMELINE: '#7E93B8', JSON: '#A08FBE', STRING: '#9B968B',
  INT: '#C2A05E', FLOAT: '#C2A05E', BOOL: '#C2A05E', ANY: '#8E8B83',
}
const pc = (t: string) => portColor[t] || portColor.ANY

function FlowNodeInner({ id, data, selected }: NodeProps) {
  const d = data as FlowNodeDataType
  const { type_id, title, category, params, last_params, status, error, progress, asset_ids, spec } = d

  const color = statusColor[status] || statusColor.pending
  const solid = catSolid(category)
  const isFailed = status === 'failed'
  const isRunning = status === 'running'
  const isCached = status === 'cached'

  const convertedInputs: Record<string, boolean> = (d.forceInputs || ((window as any).__fw_param_inputs?.[id] || {})) as Record<string, boolean>
  const forceInputParams = (spec?.params || []).filter((p: any) => p.forceInput)
  const activeInputParams = forceInputParams.filter((p: any) => convertedInputs[p.name])
  const [errOpen, setErrOpen] = useState(false)
  const errLong = !!error && error.length > 80

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

  const expanded = !!d.expanded
  const cardRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!expanded) return
    const h = (e: MouseEvent) => {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) {
        ;(window as any).__fwSetNodeExpanded?.(id, false)
      }
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [expanded, id])

  const paramUpd = (patch: Record<string, string>) => { ;(window as any).__fwUpdateParams?.(id, patch) }
  const renderCompact = (p: any, val: any) => {
    const widget = p.widget || (p.type === 'INT' || p.type === 'FLOAT' ? 'number' : 'text')
    const label = p.label || p.name
    const base = {
      width: '100%', padding: '4px 7px', borderRadius: 4, fontSize: 11.5, marginTop: 2,
      border: '1px solid var(--line)', background: 'var(--ink-600)', color: 'var(--paper)',
      outline: 'none', boxSizing: 'border-box' as const,
    }
    if (widget === 'select' || (p.options && p.options.length)) {
      return (
        <div key={p.name} style={{ marginBottom: 5 }}>
          <div style={{ fontSize: 10, color: 'var(--paper-faint)', display: 'flex', alignItems: 'center', gap: 4 }}>
            {label}
            {p.forceInput && <Unplug size={8} color="var(--ink-300)" title="可转为输入端口：连接后此参数由上游注入" style={{ flexShrink: 0 }} />}
          </div>
          <select className="nodrag nopan" style={base} value={String(val ?? '')} onChange={(e) => paramUpd({ [p.name]: e.target.value })}>
            {(p.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      )
    }
    if (p.type === 'BOOL' || widget === 'toggle') {
      return (
        <div key={p.name} style={{ marginBottom: 5, display: 'flex', alignItems: 'center', gap: 7 }}>
          <input className="nodrag nopan" type="checkbox" checked={String(val) === 'true' || val === true}
            onChange={(e) => paramUpd({ [p.name]: String(e.target.checked) })}
            style={{ accentColor: 'var(--brass)', width: 13, height: 13, margin: 0 }} />
          <div style={{ fontSize: 10, color: 'var(--paper-faint)', display: 'flex', alignItems: 'center', gap: 4 }}>
            {label}
            {p.forceInput && <Unplug size={8} color="var(--ink-300)" title="可转为输入端口：连接后此参数由上游注入" style={{ flexShrink: 0 }} />}
          </div>
        </div>
      )
    }
    const isSecret = /key|token|secret|api/.test((p.name || '').toLowerCase())
    return (
      <div key={p.name} style={{ marginBottom: 5 }}>
        <div style={{ fontSize: 10, color: 'var(--paper-faint)', display: 'flex', alignItems: 'center', gap: 4 }}>
          {label}
          {p.forceInput && <Unplug size={8} color="var(--ink-300)" title="可转为输入端口：连接后此参数由上游注入" style={{ flexShrink: 0 }} />}
        </div>
        <input className="nodrag nopan" style={base} type={isSecret ? 'password' : 'text'} value={String(val ?? '')}
          placeholder={p.description || ''} onChange={(e) => paramUpd({ [p.name]: e.target.value })} />
      </div>
    )
  }

  const inputs = spec?.inputs || []
  const outputs = spec?.outputs || []
  const paramsList = spec?.params || []
  const portRows = Math.max(inputs.length + activeInputParams.length, outputs.length, 1)
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
  const summaryH = !expanded && summaryItem ? 28 : 0
  const cfgItems = (spec?.params || []).length
  const cfgH = expanded ? 24 + cfgItems * 46 + 12 : 0
  const portH = portRows * 24
  const statusH = 36
  const nodeH = 44 + summaryH + portH + cfgH + statusH + 12
  const portY = (i: number) => 44 + summaryH + i * 24
  const statusTop = 44 + summaryH + portH + cfgH + 3

  const openEdit = () => { ;(window as any).__fwOpenEdit?.(id) }

  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault(); e.stopPropagation()
        ;(window as any).__fwNodeMenu && (window as any).__fwNodeMenu({ x: e.clientX, y: e.clientY, nodeId: id, nodeTitle: d.title })
      }}
      onDoubleClick={(e) => { e.stopPropagation(); openEdit() }}
      ref={cardRef}
      className={'fw-node' + (selected ? ' selected' : '') + (isRunning ? ' running' : '') + (isFailed ? ' failed' : '') + (isCached ? ' cached' : '')}
      style={{
        position: 'relative', width: 240, height: nodeH,
        zIndex: expanded ? 60 : (selected ? 40 : 1),
        ['--node-accent' as any]: solid,
      }}
    >
      {/* 选中 / 运行 顶部指示线 */}
      <span className="fw-node-edge" aria-hidden="true" />

      {/* 标题行 */}
      <div className="fw-node-head">
        <div className="fw-node-icon" style={{ color: solid, borderColor: solid + '55', background: solid + '1a' }}>{iconChar}</div>
        <span className="fw-node-title">{title || type_id}</span>
        {spec?.gpu_required && (
          <span className="fw-node-tag gpu" title="需要 GPU"><Zap size={8} />GPU</span>
        )}
        {forceInputParams.length > 0 && (
          <span className="fw-node-tag port" title={'可转为输入端口（forceInput）：' + forceInputParams.map((p: any) => p.name).join('、') + ' 可由上游连线注入'}>
            <Unplug size={8} />{forceInputParams.length}
          </span>
        )}
        {category && <span className="fw-node-cat">{category}</span>}
        {paramsList.length > 0 && (
          <button className="nodrag nopan fw-node-expand" onClick={(e) => { e.stopPropagation(); ;(window as any).__fwSetNodeExpanded?.(id, !expanded) }}
            title={expanded ? '收起配置' : '展开配置'}>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
      </div>

      {/* 参数摘要 */}
      {summaryItem && !expanded && (
        <div className="nodrag nopan fw-node-summary" onClick={() => { if (paramsList.length > 0) { ;(window as any).__fwSetNodeExpanded?.(id, !expanded) } }}
          title={paramsList.length > 0 ? '点击展开配置' : ''}>
          <span>{summaryItem.label}</span>{summaryItem.value}
        </div>
      )}

      {/* 端口 */}
      {inputs.map((p, i) => (
        <Fragment key={'in-' + p.name}>
          <Handle type="target" position={Position.Left} id={p.name} className="fw-handle in"
            title={'输入: ' + p.name + ' · ' + p.type}
            style={{ top: portY(i), left: 5, background: pc(p.type) }} />
          <span className="fw-port-label in" style={{ top: portY(i) }}>
            <span className="fw-port-dot" style={{ background: pc(p.type) }} />{p.name}
          </span>
        </Fragment>
      ))}
      {activeInputParams.map((p: any, i: number) => (
        <Fragment key={'param-in-' + p.name}>
          <Handle type="target" position={Position.Left} id={p.name}
            className="fw-handle in fw-force-input-handle"
            title={'已转为输入: ' + p.name + ' · ' + p.type}
            style={{ top: portY(inputs.length + i), left: 5, background: pc(p.type) }} />
          <span className="fw-port-label in forced" style={{ top: portY(inputs.length + i) }}>↳ {p.name}</span>
        </Fragment>
      ))}
      {outputs.map((p, i) => (
        <Fragment key={'out-' + p.name}>
          <Handle type="source" position={Position.Right} id={p.name} className="fw-handle out"
            title={'输出: ' + p.name + ' · ' + p.type}
            style={{ top: portY(i), right: 5, background: pc(p.type) }} />
          <span className="fw-port-label out" style={{ top: portY(i) }}>
            {p.name}<span className="fw-port-dot" style={{ background: pc(p.type) }} />
          </span>
        </Fragment>
      ))}

      {expanded && (
        <div className="fw-node-config" style={{ marginTop: portH + 6 }}>
          <div className="fw-node-config-head">
            <span>参数配置</span>
            <span className="num">{paramsList.length} 项</span>
          </div>
          {paramsList.length === 0 && <div className="fw-node-config-empty">此节点无参数</div>}
          {paramsList.map((p: any) => renderCompact(p, (params || {})[p.name] ?? p.default))}
        </div>
      )}

      {/* 状态区 */}
      <div className="fw-node-status" style={{ top: statusTop }}>
        {error && (
          <div className="fw-node-error-line">
            <span title={error}><AlertTriangle size={10} /> {error}</span>
            {errLong && (
              <button className="nodrag nopan" onClick={(e) => { e.stopPropagation(); setErrOpen(true) }} title="展开错误详情">
                <ChevronDown size={10} />
              </button>
            )}
          </div>
        )}
        {!error && isRunning && progress !== undefined && (
          <div className="fw-node-progress">
            <div className="track"><div className="fill" style={{ width: (progress * 100) + '%', background: color }} /></div>
            <span>{(progress * 100).toFixed(0)}%</span>
          </div>
        )}
        {!error && !isRunning && hasDiff && (
          <div className="fw-node-diff" title={diffKeys.join(', ')}>
            <PencilLine size={10} /> 已改 · {diffKeys.join(', ')}
          </div>
        )}
        <div className="fw-node-status-line">
          <span className={'dot' + (isRunning ? ' pulse' : '')} style={{ background: color }} />
          <span>{statusText[status] || status}</span>
          {Object.keys(asset_ids).length > 0 && (
            <span className="fw-node-outputs">{Object.keys(asset_ids).length} 输出</span>
          )}
        </div>
      </div>

      {errOpen && error && (
        <div className="nodrag nopan fw-node-errdetail">
          <div className="head">
            <AlertTriangle size={11} color="var(--err)" />
            <b>节点错误详情</b>
            <span style={{ flex: 1 }} />
            <button onClick={() => { try { navigator.clipboard.writeText(error); } catch { /* ignore */ } }} title="复制错误文本"><Copy size={11} /></button>
            <button onClick={() => setErrOpen(false)} title="收起"><X size={11} /></button>
          </div>
          <div className="body fw-scroll">{error}</div>
        </div>
      )}
    </div>
  )
}

export const FlowNode = memo(FlowNodeInner)
