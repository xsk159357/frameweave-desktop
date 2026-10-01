// 右侧参数面板：参数表单 + 执行状态 + 输出资产预览
import { useState } from 'react'
import { Lock, Check, Zap, X, Eye } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import type { NodeSpec } from './types'

interface Props {
  nodeId: string
  spec: NodeSpec | undefined
  params: Record<string, string>
  run_history?: { time: number; status: string; params?: Record<string, unknown>; cache_key?: string; error?: string }[]
  onUpdate: (nodeId: string, patch: Record<string, string>) => void
  onClose?: () => void
}

export function ParamPanel({ nodeId, spec, params, run_history, onUpdate, onClose }: Props) {
  const dark = useAppStore((s) => s.dark)
  const nodeStatus = useAppStore((s) => s.nodeStatus)
  const nodeError = useAppStore((s) => s.nodeError)
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const [preview, setPreview] = useState<any>(null)
  const [previewErr, setPreviewErr] = useState('')

  const loadPreview = async (aid: string) => {
    setPreviewErr('')
    try {
      const r = await api.asset(aid)
      setPreview(r)
    } catch (e: any) { setPreviewErr(e.message); setPreview(null) }
  }

  const assetIds = nodeAssets[nodeId] || {}
  const status = nodeStatus[nodeId] || 'pending'
  const error = nodeError[nodeId] || ''

  const styles = {
    panel: {
      width: '100%', height: '100%', borderLeft: 'var(--glass-border)', boxSizing: 'border-box' as const,
      background: 'var(--glass-strong)',       boxShadow: 'none', padding: 16, overflowY: 'auto', flexShrink: 0,
    },
    label: { fontSize: 11, fontWeight: 700, letterSpacing: .4, color: 'var(--text-faint)', margin: '14px 0 7px', textTransform: 'uppercase' as const },
    input: {
      width: '100%', padding: '8px 11px', borderRadius: 10, fontSize: 13,
      border: 'var(--glass-border)', background: 'var(--input-bg)', color: 'var(--text)',
            boxShadow: 'var(--glass-inner)', marginBottom: 10, boxSizing: 'border-box' as const, outline: 'none',
      transition: 'border-color .2s, box-shadow .2s',
    },
  }

  const renderParam = (p: any) => {
    const val = params[p.name] ?? p.default ?? ''
    const widget = p.widget || (p.type === 'INT' ? 'number' : 'text')
    if (widget === 'select' || (p.options && p.options.length)) {
      return (
        <select
          style={styles.input}
          value={String(val)}
          onChange={(e) => onUpdate(nodeId, { [p.name]: e.target.value })}
        >
          {(p.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
        </select>
      )
    }
    if (widget === 'number' || p.type === 'INT' || p.type === 'FLOAT') {
      return (
        <input
          style={styles.input} type="number" value={String(val)}
          onChange={(e) => onUpdate(nodeId, { [p.name]: e.target.value })}
        />
      )
    }
    if (p.type === 'BOOL') {
      return (
        <input
          type="checkbox" checked={String(val) === 'true' || val === true}
          onChange={(e) => onUpdate(nodeId, { [p.name]: String(e.target.checked) })}
          style={{ marginBottom: 10 }}
        />
      )
    }
    if (p.name === 'api_key' || (p.name || '').toLowerCase().includes('apikey')) {
      const secName = (p.name === 'api_key' && nodeId) ? 'fw_api_' + nodeId : p.name
      return (
        <div>
          <input
            style={styles.input} type="password" value={String(val)}
            placeholder={p.description || '或填 @secret:名称 引用保险箱'}
            onChange={(e) => onUpdate(nodeId, { [p.name]: e.target.value })}
          />
          <button
            onClick={async () => {
              try {
                await api.storeSecret(secName, String(val))
                onUpdate(nodeId, { [p.name]: '@secret:' + secName })
                alert('已加密存入本机保险箱，参数已改为引用 ' + '@secret:' + secName)
              } catch (e: any) { alert('存入失败: ' + e.message) }
            }}
            style={{ ...styles.input, background: 'var(--border)', cursor: 'pointer',
                     marginTop: -4, textAlign: 'center', fontSize: 12, width: '100%', fontWeight: 600,
                     borderColor: 'var(--border-strong)', transition: 'all .18s' }}
          >
            <><Lock size={12} /> 加密存入本机保险箱</>
          </button>
        </div>
      )
    }
    return (
      <input
        style={styles.input} type="text" value={String(val)}
        placeholder={p.description || ''}
        onChange={(e) => onUpdate(nodeId, { [p.name]: e.target.value })}
      />
    )
  }

  return (
    <div style={styles.panel}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
        <span style={{ fontSize: 15, fontWeight: 700 }}>{spec?.title || '节点'}</span>
        <span className="fw-badge" style={{ background: 'var(--border)', color: 'var(--text-faint)', fontFamily: 'Consolas, monospace' }}>#{nodeId.slice(0, 12)}</span>
        <span style={{ flex: 1 }} />
        {onClose && (
          <button onClick={onClose} title="关闭" style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 4, borderRadius: 7, lineHeight: 0 }}><X size={15} /></button>
        )}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 4, lineHeight: 1.5 }}>
        {spec?.type_id}
      </div>
      {spec?.description && (
        <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginBottom: 12, lineHeight: 1.55 }}>{spec.description}</div>
      )}

      {/* 执行状态 */}
      <div style={styles.label}>执行状态</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%',
          background: status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)'
            : status === 'running' ? 'var(--running)' : status === 'cached' ? 'var(--cached)' : 'var(--text-faint)',
          boxShadow: '0 0 0 3px ' + (status === 'success' ? 'var(--success-soft)' : status === 'failed' ? 'var(--danger-soft)' : 'transparent') }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)' : status === 'running' ? 'var(--running)' : status === 'cached' ? 'var(--cached)' : 'var(--text)' }}>
          {status}
        </span>
      </div>
      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 10, whiteSpace: 'pre-wrap', background: 'rgba(229,72,77,.1)', borderRadius: 9, padding: '8px 10px', lineHeight: 1.5 }}>{error}</div>
      )}

      {/* 参数表单 */}
      {spec && spec.params.length > 0 && (
        <>
          <div style={{ ...styles.label, marginTop: 4 }}>参数</div>
          {spec.params.map((p) => (
            <div key={p.name}>
              <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 2 }}>
                {p.label} {p.description ? '· ' + p.description : ''}
              </div>
              {renderParam(p)}
            </div>
          ))}
        </>
      )}

      {/* 运行历史 */}
      {run_history && run_history.length > 0 && (
        <div>
          <div style={{ ...styles.label, marginTop: 8 }}>运行历史（{run_history.length}）</div>
          <div style={{ maxHeight: 180, overflow: 'auto', marginBottom: 10 }}>
            {[...run_history].reverse().map((h, i) => {
              const hColor = h.status === 'success' ? 'var(--success)' : h.status === 'cached' ? 'var(--cached)' : 'var(--danger)'
              const t = h.time ? new Date(h.time * 1000).toLocaleString('zh-CN', { hour12: false }) : ''
              const paramSummary = h.params ? Object.entries(h.params).filter(([k, v]) => String(v ?? '') !== '').slice(0, 3).map(([k, v]) => k + '=' + String(v).slice(0, 14)).join(' ') : ''
              return (
                <div key={i} style={{
                  border: '1px solid ' + ('var(--border)'),
                  borderRadius: 6, padding: '6px 8px', marginBottom: 6, fontSize: 11,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: hColor, fontWeight: 600 }}>
                      {h.status === 'success' ? <><Check size={11} style={{ verticalAlign: '-2px' }} /> 成功</> : h.status === 'cached' ? <><Zap size={11} style={{ verticalAlign: '-2px' }} /> 缓存命中</> : <><X size={11} style={{ verticalAlign: '-2px' }} /> {h.status || '失败'}</>}
                    </span>
                    <span style={{ color: 'var(--text-faint)' }}>{t}</span>
                  </div>
                  {paramSummary && <div style={{ color: 'var(--text-faint)', marginTop: 2 }}>{paramSummary}</div>}
                  {h.error && <div style={{ color: 'var(--danger)', marginTop: 2, wordBreak: 'break-all' }}>{String(h.error).slice(0, 120)}</div>}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {/* 输出资产 */}
      <div style={{ ...styles.label, marginTop: 8 }}>输出资产</div>
      {Object.entries(assetIds).length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-faint)', marginBottom: 10 }}>尚未产出（运行后显示）</div>
      )}
      {Object.entries(assetIds).map(([port, aid]) => (
        <div key={port} style={{ marginBottom: 8 }}>
          <button
            onClick={() => loadPreview(aid)}
            style={{
              padding: '8px 10px', borderRadius: 9, width: '100%', textAlign: 'left', cursor: 'pointer',
              border: '1px solid ' + ('var(--border-strong)'), background: 'var(--bg-panel-2)',
              color: 'var(--text)', fontSize: 12, transition: 'all .18s',
            }}
          >
            <><Eye size={13} style={{ verticalAlign: '-2px', marginRight: 2 }} /> 预览 {port}（{aid.slice(0, 8)}）</>
          </button>
        </div>
      ))}

      {previewErr && <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 8 }}>{previewErr}</div>}

      {preview && (
        <div style={{ marginTop: 4, fontSize: 12, wordBreak: 'break-all' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>预览内容（{preview.kind}）</div>
          {preview.kind === 'SEGMENTS' && Array.isArray(preview.content) && (
            <div>
              <div style={{ color: 'var(--text-faint)', marginBottom: 6 }}>共 {preview.content.length} 个片段</div>
              {preview.content.slice(0, 10).map((s: any) => (
                <div key={s.index} style={{ padding: '4px 0', borderBottom: '1px solid ' + ('var(--border)') }}>
                  #{s.index} {s.start}s → {s.end}s（{s.duration}s）
                </div>
              ))}
            </div>
          )}
          {preview.kind === 'SUBTITLE' && Array.isArray(preview.content) && (
            <div>
              <div style={{ color: 'var(--text-faint)', marginBottom: 6 }}>共 {preview.content.length} 条字幕</div>
              {preview.content.slice(0, 8).map((s: any, i: number) => (
                <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid ' + ('var(--border)') }}>
                  {s.start}s → {s.end}s {s.text}
                </div>
              ))}
            </div>
          )}
          {preview.kind === 'JSON' && preview.content && (
            <pre style={{ whiteSpace: 'pre-wrap', background: '#0a0e18',
              padding: 8, borderRadius: 6, maxHeight: 300, overflow: 'auto', fontSize: 11 }}>
              {JSON.stringify(preview.content, null, 2).slice(0, 2500)}
            </pre>
          )}
          {preview.kind === 'STRING' && preview.content && (
            <pre style={{ whiteSpace: 'pre-wrap', background: '#0a0e18',
              padding: 8, borderRadius: 6, maxHeight: 300, overflow: 'auto', fontSize: 11 }}>
              {String((preview.content as any).text || JSON.stringify(preview.content)).slice(0, 2500)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
