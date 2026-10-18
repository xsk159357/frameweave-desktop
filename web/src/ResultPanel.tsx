// 运行结果卡（t10/D-13）：状态 / 耗时 / 输出文件数 / 输出目录 / 打开文件夹 / 复制路径 + 导出中心。
// 失败时内嵌错误诊断卡（t10/D-05：节点→端口→原因→建议动作，traceback 折叠「技术详情」）。
// 批量出片节点（batch_render）同时保留逐项成功/失败 + 重试失败项。
import { useEffect, useMemo, useState } from 'react'
import { Package, Loader2, RotateCcw, X, FolderOpen, AlertCircle, Check, XCircle, Copy, CheckCircle2, Store, Clock, FileVideo, Captions, FileJson2, FileArchive, FileAudio2 } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import { openMarketForTypeIds } from './WorkflowModals'

interface BatchItem {
  index: number
  name: string
  ok: boolean
  path?: string
  size?: number
  video_id?: string
  error?: string
}

interface OutRow {
  aid: string
  kind: string
  path: string
  name: string
  size?: number
}

interface Props {
  workflowId: string
  batchNodeId: string | null   // 选中的 batch_render 节点 id
  onRetry: (nodeId: string) => void
  onClose: () => void
}

// ---- D-05：Python traceback / 堆栈 → 「用户摘要 + 技术详情折叠」 ----
function splitTrace(msg: string): { summary: string; detail?: string } {
  const m = String(msg || '').trim()
  if (!m) return { summary: '未知错误' }
  const idx = m.search(/\n\s*(Traceback|File "|  File |\s+at\s)/)
  if (idx > 0) return { summary: m.slice(0, idx).trim(), detail: m.slice(idx).trim() }
  if (m.length > 160) return { summary: m.slice(0, 160).trim() + '…', detail: m }
  return { summary: m }
}

// ---- D-05：错误码 → 建议动作 ----
function suggestFor(code: string, item: any): { text: string; market?: string[] } {
  switch (code) {
    case 'node_type_unknown': {
      const tid = item?.type_id || item?.typeId
      return { text: '该节点类型未安装', market: tid ? [String(tid)] : undefined }
    }
    case 'required_input_missing':
      return { text: '必需输入未连接：请从上游节点连线，或填入 @资产引用' }
    case 'port_type_incompatible':
      return { text: `端口类型不兼容：${item?.source_type || ''} → ${item?.target_type || ''}，请检查连线` }
    case 'source_node_missing':
      return { text: '连线指向不存在的源节点，请删除该连线' }
    case 'target_node_missing':
      return { text: '连线指向不存在的目标节点，请删除该连线' }
    case 'source_handle_invalid':
    case 'target_handle_invalid':
      return { text: '连线端口不存在（节点协议已更新？），请重新连线' }
    case 'edge_invalid':
      return { text: '连线数据无效，请删除后重新连接' }
    case 'graph_invalid':
      return { text: '工作流图校验失败，见下方错误清单' }
    default:
      return { text: '' }
  }
}

const KIND_LABEL: Record<string, string> = {
  VIDEO: 'MP4 / 视频', AUDIO: '音频', IMAGE: '图片', SUBTITLE: '字幕',
  JSON: 'JSON 数据', PACKAGE: '工程包', FILE: '文件',
}

function rowLabel(r: OutRow): string {
  const p = (r.path || r.name || '').toLowerCase()
  if (r.kind === 'JSON' || r.kind === 'PACKAGE') {
    if (p.endsWith('.xml') || p.endsWith('.fcpxml')) return 'Premiere XML'
    if (p.includes('draft') || p.includes('剪映') || p.includes('草稿')) return '剪映草稿'
    if (r.kind === 'PACKAGE' || p.endsWith('.zip')) return '工程包'
    return 'JSON 数据'
  }
  return KIND_LABEL[r.kind] || r.kind || '文件'
}

export function ResultPanel({ workflowId, batchNodeId, onRetry, onClose }: Props) {
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const nodeStatus = useAppStore((s) => s.nodeStatus)
  const nodeError = useAppStore((s) => s.nodeError)
  const lastError = useAppStore((s) => s.lastError)
  const runErrors = useAppStore((s) => s.runErrors)
  const runInfo = useAppStore((s) => s.runInfo)
  const pushToast = useAppStore((s) => s.pushToast)
  const [items, setItems] = useState<BatchItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [outRows, setOutRows] = useState<OutRow[]>([])
  const [copied, setCopied] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  // 依据节点输出资产（批量结果）
  const resultsId = batchNodeId ? (nodeAssets[batchNodeId] || {}).results : undefined

  useEffect(() => {
    if (!resultsId) { setItems([]); return }
    let alive = true
    setLoading(true); setError('')
    api.asset(resultsId).then((r: any) => {
      if (!alive) return
      const content = Array.isArray(r?.content?.items) ? r.content.items : (Array.isArray(r?.content) ? r.content : [])
      setItems(content.map((it: any) => ({
        index: it.index ?? 0, name: it.name || '', ok: !!it.ok,
        path: it.path || '', size: it.size || 0, video_id: it.video_id,
        error: it.error || '',
      })))
    }).catch((e: any) => { if (alive) setError(String(e.message || e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [resultsId])

  // D-13：聚合本次运行全部输出资产（输出文件数/目录/导出中心）
  useEffect(() => {
    const aids = new Set<string>()
    for (const perNode of Object.values(nodeAssets || {})) {
      for (const aid of Object.values(perNode || {})) if (aid) aids.add(String(aid))
    }
    if (aids.size === 0) { setOutRows([]); return }
    let alive = true
    const rows: OutRow[] = []
    ;(async () => {
      for (const aid of Array.from(aids).slice(0, 30)) {
        try {
          const m = await api.assetMeta(aid)
          if (!alive) return
          rows.push({ aid, kind: m.kind || 'FILE', path: m.path || '', name: (m.path || aid).split(/[\\/]/).pop() || aid, size: m.size })
        } catch { /* 单条失败跳过 */ }
      }
      if (alive) setOutRows(rows)
    })()
    return () => { alive = false }
  }, [nodeAssets])

  const failed = items.filter(i => !i.ok)
  const okCount = items.length - failed.length
  const status = batchNodeId ? (nodeStatus[batchNodeId] || '') : ''
  const isRunning = status === 'running' || status === 'queued'

  const failedNodes = Object.entries(nodeStatus).filter(([, st]) => st === 'failed')
  const successNodes = Object.entries(nodeStatus).filter(([, st]) => st === 'success')
  const hasError = !!lastError || (runErrors?.length || 0) > 0 || failedNodes.length > 0
  const statusLabel = isRunning ? '运行中' : hasError ? '失败' : successNodes.length > 0 ? '成功' : '未运行'

  // 耗时
  const durText = useMemo(() => {
    const s = runInfo?.startedAt
    if (!s) return ''
    const end = runInfo?.endedAt || Date.now()
    const sec = Math.max(0, Math.round((end - s) / 1000))
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
  }, [runInfo])
  void workflowId

  // 输出目录 = 第一个带路径资产的目录
  const outDir = outRows.find(r => r.path)?.path.split(/[\\/]/).slice(0, -1).join('/') || ''

  const copyText = async (text: string, label: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(label); setTimeout(() => setCopied(''), 1500) }
    catch { pushToast('复制失败，请手动复制', 'err') }
  }

  // 错误卡条目：结构化 runErrors 优先；否则取节点级失败错误
  const errEntries: { nodeId: string; handle?: string; code?: string; message: string; typeId?: string }[] = []
  if (runErrors && runErrors.length > 0) {
    for (const it of runErrors) {
      errEntries.push({
        nodeId: it.node_id || '', handle: it.handle || '', code: it.code || '',
        message: it.message || '', typeId: (it.type_id as string) || '',
      })
    }
  } else if (lastError) {
    errEntries.push({ nodeId: '', message: lastError })
  }
  for (const [nid, st] of failedNodes) {
    if (errEntries.some((e) => e.nodeId === nid)) continue
    errEntries.push({ nodeId: nid, message: nodeError[nid] || '节点执行失败' })
  }

  const errIcon = (c: string) => ({ color: c })
  const actionBtn = { padding: '2px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer', border: '1px solid #3a4157', background: 'var(--border)', color: 'var(--text)', flexShrink: 0 }

  return (
    <div className="fw-scroll" style={{
      borderTop: 'var(--glass-border)', background: 'var(--glass-strong)',
      boxShadow: 'none', padding: '12px 16px', flexShrink: 0,
      maxHeight: 320, overflowY: 'auto',
    }}>
      {/* 头部：状态 / 耗时 / 统计 / 重试 / 关闭 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Package size={14} /> 运行结果</span>
        <span className="fw-badge" style={{ background: hasError ? 'rgba(248,113,113,.15)' : 'var(--success-soft)', color: hasError ? 'var(--danger)' : 'var(--success)', fontWeight: 700 }}>{statusLabel}</span>
        {durText && (
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Clock size={11} /> {durText}
          </span>
        )}
        {outRows.length > 0 && (
          <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)' }}>
            {outRows.length} 个输出文件
          </span>
        )}
        {items.length > 0 && (
          <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)' }}>
            {okCount}/{items.length} 成功{failed.length > 0 ? ' · ' + failed.length + ' 失败' : ''}
          </span>
        )}
        {isRunning && <span style={{ fontSize: 12, color: 'var(--running)', display: 'inline-flex', alignItems: 'center', gap: 5 }}><Loader2 size={12} className="fw-spin" /> 批量执行中…</span>}
        <span style={{ flex: 1 }} />
        {failed.length > 0 && batchNodeId && (
          <button onClick={() => onRetry(batchNodeId)} className="fw-btn fw-btn-danger" style={{ padding: '5px 12px', fontSize: 12 }}><><RotateCcw size={12} /> 重试失败项</></button>
        )}
        <button onClick={onClose} className="fw-btn fw-btn-ghost" style={{ padding: '5px 9px', fontSize: 13 }}><X size={14} /></button>
      </div>

      {/* ===== D-05：错误诊断卡（节点→端口→原因→建议动作；traceback 折叠） ===== */}
      {errEntries.length > 0 && (
        <div style={{ border: '1px solid rgba(248,113,113,.32)', background: 'rgba(248,113,113,.07)', borderRadius: 10, padding: '9px 11px', marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: 'var(--danger)', marginBottom: 6 }}>
            <AlertCircle size={13} /> 错误诊断
            <span style={{ fontSize: 10.5, fontWeight: 400, color: 'var(--text-faint)' }}>点击「技术详情」查看堆栈，问题多为节点/连线配置</span>
          </div>
          {errEntries.map((en, i) => {
            const sug = en.code ? suggestFor(en.code, en) : { text: '' }
            const sp = splitTrace(en.message)
            const key = i + '_' + (en.nodeId || '') + '_' + (en.handle || '')
            const isOpen = expanded === key
            return (
              <div key={key} style={{ borderTop: i > 0 ? '1px solid rgba(248,113,113,.14)' : 'none', paddingTop: i > 0 ? 6 : 0, marginTop: i > 0 ? 6 : 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, flexWrap: 'wrap' }}>
                  {en.nodeId && <span className="fw-badge" style={{ background: 'rgba(248,113,113,.14)', color: 'var(--danger)', fontFamily: 'Consolas, monospace', fontSize: 10.5 }}>#{en.nodeId.slice(0, 14)}</span>}
                  {en.handle && <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)', fontSize: 10.5 }}>端口 {en.handle}</span>}
                  {en.code && <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)', fontFamily: 'Consolas, monospace', fontSize: 10.5 }}>{en.code}</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text)', marginTop: 3, wordBreak: 'break-word' }}>{sp.summary}</div>
                {sug.text && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, color: 'var(--text-dim)', marginTop: 3, flexWrap: 'wrap' }}>
                    <span style={{ color: '#e0a800' }}>建议：{sug.text}</span>
                    {sug.market && (
                      <button style={actionBtn} onClick={() => openMarketForTypeIds(sug.market!)}>
                        <><Store size={11} style={errIcon('#e0a800')} /> 去商城安装</>
                      </button>
                    )}
                  </div>
                )}
                {sp.detail && (
                  <details open={isOpen} onToggle={(e) => setExpanded(isOpen ? null : key)} style={{ marginTop: 3 }}>
                    <summary style={{ fontSize: 11, color: 'var(--text-faint)', cursor: 'pointer', userSelect: 'none' }}>技术详情（堆栈）</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--panel-2)', borderRadius: 6, padding: 6, fontSize: 10.5, color: 'var(--text-dim)', maxHeight: 140, overflow: 'auto', marginTop: 4 }}>{sp.detail}</pre>
                  </details>
                )}
              </div>
            )
          })}
        </div>
      )}

      {loading && <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>读取结果…</div>}
      {error && <div style={{ fontSize: 12, color: 'var(--danger)' }}><><AlertCircle size={12} style={{ verticalAlign: '-2px', marginRight: 2 }} /> {error}</></div>}
      {!loading && !error && items.length === 0 && !isRunning && !hasError && outRows.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>本次运行未产出结果（连接批量出片/输出节点并执行后展示）</div>
      )}

      {items.map((it) => (
        <div key={it.index} style={{
          display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
          padding: '6px 10px', borderRadius: 9, marginBottom: 3,
          background: 'var(--glass)', border: 'var(--glass-border)', transition: 'all .15s',
        }}>
          <span style={{ color: it.ok ? 'var(--success)' : 'var(--danger)', width: 18, display: 'inline-flex', justifyContent: 'center' }}>{it.ok ? <Check size={13} /> : <XCircle size={13} />}</span>
          <span style={{ fontWeight: 600 }}>{it.name}</span>
          {it.ok ? (
            <>
              <span style={{ color: 'var(--text-faint)' }}>
                {it.size ? ((it.size / 1024 / 1024).toFixed(1) + ' MB') : ''}
              </span>
              <span style={{ flex: 1, color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.path}>
                {it.path}
              </span>
              {it.path && (
                <>
                  <button onClick={() => api.revealPath(it.path!).catch(() => {})} style={actionBtn}><><FolderOpen size={11} /> 定位</></button>
                  <button onClick={() => copyText(it.path!, 'item')} style={actionBtn}><>{copied === 'item' ? <CheckCircle2 size={11} color="var(--success)" /> : <Copy size={11} />} {copied === 'item' ? '已复制' : '复制'}</></button>
                </>
              )}
            </>
          ) : (
            <span style={{ flex: 1, color: 'var(--danger)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.error}>
              {it.error}
            </span>
          )}
        </div>
      ))}

      {/* ===== D-13：输出汇总 + 打开文件夹/复制路径 ===== */}
      {outRows.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginTop: 6, padding: '6px 10px', borderRadius: 9, background: 'var(--bg-panel-2)', border: '1px solid var(--border)', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 650 }}>输出文件</span>
          <span style={{ color: 'var(--text-faint)', flex: 1, minWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={outDir}>{outDir || outRows[0].path}</span>
          {outDir && (
            <>
              <button style={actionBtn} onClick={() => api.revealPath(outDir).catch(() => pushToast('无法打开输出目录（路径不存在）', 'err'))}><><FolderOpen size={11} /> 打开文件夹</></button>
              <button style={actionBtn} onClick={() => copyText(outDir, 'dir')}><>{copied === 'dir' ? <CheckCircle2 size={11} color="var(--success)" /> : <Copy size={11} />} {copied === 'dir' ? '已复制' : '复制路径'}</></button>
            </>
          )}
        </div>
      )}

      {/* ===== D-13：导出中心（MP4/字幕/剪映草稿/Premiere XML/工程包） ===== */}
      {outRows.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: .5, color: 'var(--text-faint)', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}>
            导出中心 <span style={{ fontWeight: 400 }}>按资产类型分组，可定位/复制路径</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {outRows.map((r) => {
              const label = rowLabel(r)
              const icon = label.includes('MP4') ? <FileVideo size={12} /> : label.includes('字幕') ? <Captions size={12} /> : label.includes('XML') ? <FileJson2 size={12} /> : label.includes('草稿') ? <FileJson2 size={12} /> : label.includes('工程') ? <FileArchive size={12} /> : <FileAudio2 size={12} />
              return (
                <div key={r.aid} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '5px 9px', borderRadius: 8, background: 'var(--glass)', border: 'var(--glass-border)' }}>
                  <span style={{ color: 'var(--accent-strong)', display: 'inline-flex', width: 16 }}>{icon}</span>
                  <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)', flexShrink: 0 }}>{label}</span>
                  <span style={{ flex: 1, minWidth: 0, color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.path}>{r.name}</span>
                  {r.path && (
                    <>
                      <button style={actionBtn} onClick={() => api.revealPath(r.path).catch(() => {})}><><FolderOpen size={11} /> 打开</></button>
                      <button style={actionBtn} onClick={() => copyText(r.path, r.aid)}><>{copied === r.aid ? <CheckCircle2 size={11} color="var(--success)" /> : <Copy size={11} />} {copied === r.aid ? '已复制' : '复制路径'}</></button>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
