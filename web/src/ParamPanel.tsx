// 右侧参数面板：参数表单 + 执行状态 + 输出资产预览
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Lock, Check, Zap, X, Eye, Settings2, CircleDot, FolderOpen, Loader2 } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import type { NodeSpec } from './types'

interface Props {
  nodeId: string
  spec: NodeSpec | undefined
  params: Record<string, string>
  run_history?: { time: number; status: string; params?: Record<string, unknown>; cache_key?: string; error?: string }[]
  onUpdate: (nodeId: string, patch: Record<string, string>) => void
  /** D-14：forceInput 参数状态由画布保存到 node.forceInputs */
  forceInputs?: Record<string, boolean>
  onForceInputChange?: (nodeId: string, paramName: string, enabled: boolean) => void
  onClose?: () => void
}

export function ParamPanel({ nodeId, spec, params, run_history, onUpdate, forceInputs = {}, onForceInputChange, onClose }: Props) {
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
      width: '100%', height: '100%', borderLeft: 'none', boxSizing: 'border-box' as const,
      background: 'var(--ink-850)', boxShadow: 'none', padding: '14px 16px 20px', overflowY: 'auto', flexShrink: 0,
    },
    label: { fontSize: 10, fontWeight: 700, letterSpacing: '.14em', color: 'var(--text-faint)', margin: '18px 0 8px', textTransform: 'uppercase' as const, display: 'flex', alignItems: 'center', gap: 5 },
    input: {
      width: '100%', height: 30, padding: '0 9px', borderRadius: 4, fontSize: 12,
      border: '1px solid var(--line-strong)', background: 'var(--ink-600)', color: 'var(--text)',
      marginBottom: 9, boxSizing: 'border-box' as const, outline: 'none',
      transition: 'border-color .15s, box-shadow .15s',
    },
  }

  const STATUS_TEXT: Record<string, string> = {
    pending: '待运行', queued: '排队中', running: '运行中', success: '已完成',
    failed: '失败', cancelled: '已取消', cached: '缓存命中',
  }

  const renderParam = (p: any) => {
    const val = params[p.name] ?? p.default ?? ''
    const widget = p.widget || (p.type === 'INT' ? 'number' : 'text')
    // D-02（P0）：FILE 类参数渲染「选择文件」——选中后回填路径并显示时长/分辨率/缩略图「已就绪」
    if (widget === 'file') {
      return <FileParamInput nodeId={nodeId} param={p} value={String(val)} onUpdate={onUpdate} />
    }
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2, paddingBottom: 12, borderBottom: '1px solid var(--line)' }}>
        <span style={{ width: 24, height: 24, borderRadius: 4, flexShrink: 0, background: 'var(--brass-soft)', color: 'var(--brass-hi)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
          {((spec?.type_id || '').split('/').pop() || 'N').replace(/[^a-zA-Z0-9]/g, '')[0]?.toUpperCase() || 'N'}
        </span>
        <span style={{ fontSize: 13.5, fontWeight: 650 }}>{spec?.title || '节点'}</span>
        <span className="fw-badge" style={{ background: 'var(--ink-600)', color: 'var(--text-faint)', fontFamily: 'var(--font-mono)', height: 16, borderRadius: 3 }}>#{nodeId.slice(0, 8)}</span>
        <span style={{ flex: 1 }} />
        {onClose && (
          <button onClick={onClose} title="关闭" style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 4, borderRadius: 4, lineHeight: 0 }}><X size={14} /></button>
        )}
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--text-faint)', marginTop: 8, lineHeight: 1.5, fontFamily: 'var(--font-mono)', letterSpacing: '.02em' }}>
        {spec?.type_id}
      </div>
      {spec?.description && (
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 4, marginBottom: 4, lineHeight: 1.6 }}>{spec.description}</div>
      )}

      {/* 执行状态 */}
      <div style={styles.label}><CircleDot size={10} /> 执行状态</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%',
          background: status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)'
            : status === 'running' ? 'var(--running)' : status === 'cached' ? 'var(--cached)' : 'var(--text-faint)' }} />
        <span style={{ fontSize: 12, fontWeight: 550, color: status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)' : status === 'running' ? 'var(--running)' : status === 'cached' ? 'var(--cached)' : 'var(--text-dim)' }}>
          {STATUS_TEXT[status] || status}
        </span>
      </div>
      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 10, whiteSpace: 'pre-wrap', background: 'rgba(229,72,77,.1)', borderRadius: 9, padding: '8px 10px', lineHeight: 1.5 }}>{error}</div>
      )}

      {/* 参数表单 */}
      {spec && spec.params.length > 0 && (
        <>
          <div style={{ ...styles.label, marginTop: 2 }}><Settings2 size={11} /> 参数</div>
          {spec.params.map((p) => (
            <div key={p.name}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-dim)', marginBottom: 2 }}>
                <span style={{ flex: 1 }}>{p.label} {p.description ? '· ' + p.description : ''}</span>
                {p.forceInput && onForceInputChange && (
                  <button type="button" title={forceInputs[p.name] ? '已转为输入端口，点击恢复控件' : '允许上游连线注入此参数'} onClick={() => onForceInputChange(nodeId, p.name, !forceInputs[p.name])}
                    style={{ border: '1px solid ' + (forceInputs[p.name] ? 'var(--accent)' : 'var(--border)'), background: forceInputs[p.name] ? 'var(--accent-soft)' : 'transparent', color: forceInputs[p.name] ? 'var(--accent-strong)' : 'var(--text-faint)', borderRadius: 6, padding: '2px 6px', fontSize: 10.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    {forceInputs[p.name] ? '输入端口已启用' : '转为输入'}
                  </button>
                )}
              </div>
              {forceInputs[p.name] ? <div style={{ border: '1px dashed var(--accent)', borderRadius: 8, padding: '7px 9px', color: 'var(--accent-strong)', fontSize: 11.5, marginBottom: 10 }}>已转为输入端口，连接后由上游注入（控件已锁定）</div> : renderParam(p)}
            </div>
          ))}
        </>
      )}

      {/* 运行历史 */}
      {run_history && run_history.length > 0 && (
        <div>
          <div style={{ ...styles.label, marginTop: 4 }}>运行历史（{run_history.length}）</div>
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
                  {h.error && <div style={{ color: 'var(--danger)', marginTop: 2, wordBreak: 'break-all' }}><span style={{ display: 'inline-block', fontSize: 10, color: 'var(--text-faint)', border: '1px solid var(--border)', borderRadius: 4, padding: '1px 4px', marginRight: 4 }}>历史记录</span>{String(h.error).slice(0, 120)}</div>}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {/* 输出资产 */}
      <div style={{ ...styles.label, marginTop: 4 }}>输出资产</div>
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
            <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--panel-2)',
              padding: 8, borderRadius: 6, maxHeight: 300, overflow: 'auto', fontSize: 11 }}>
              {JSON.stringify(preview.content, null, 2).slice(0, 2500)}
            </pre>
          )}
          {preview.kind === 'STRING' && preview.content && (
            <pre style={{ whiteSpace: 'pre-wrap', background: 'var(--panel-2)',
              padding: 8, borderRadius: 6, maxHeight: 300, overflow: 'auto', fontSize: 11 }}>
              {String((preview.content as any).text || JSON.stringify(preview.content)).slice(0, 2500)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ---- D-02（t10）：FILE 类参数文件选择 —— 选中回填路径 + 时长/分辨率/缩略图「已就绪」 ----
const FILE_ACCEPT = 'video/*,audio/*,image/*,.srt,.ass,.vtt,.json,.xml,.zip'

interface FileReadyMeta {
  name: string
  path: string
  duration?: string
  resolution?: string
  thumbnail?: string
}

function FileParamInput({ nodeId, param, value, onUpdate }: {
  nodeId: string
  param: any
  value: string
  onUpdate: (nodeId: string, patch: Record<string, string>) => void
}) {
  const pushToast = useAppStore((s) => s.pushToast)
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [meta, setMeta] = useState<FileReadyMeta | null>(null)

  const probeMeta = async (file: File, path: string) => {
    const base: FileReadyMeta = { name: file.name, path }
    const ext = file.name.split('.').pop()?.toLowerCase() || ''
    try {
      if (['mp4', 'mov', 'mkv', 'webm', 'avi', 'flv', 'm4v', 'ts'].includes(ext)) {
        const url = URL.createObjectURL(file)
        const v = document.createElement('video')
        v.preload = 'metadata'
        v.muted = true
        v.src = url
        await new Promise<void>((res, rej) => {
          v.onloadedmetadata = () => res()
          v.onerror = () => rej(new Error('读取视频元数据失败'))
          setTimeout(() => rej(new Error('读取视频超时')), 8000)
        })
        const dur = v.duration || 0
        const min = Math.floor(dur / 60)
        const sec = Math.floor(dur % 60)
        base.duration = `${min}:${String(sec).padStart(2, '0')}`
        if (v.videoWidth) base.resolution = `${v.videoWidth}×${v.videoHeight}`
        try {
          v.currentTime = Math.min(1, dur / 2)
          await new Promise<void>((res, rej) => {
            v.onseeked = () => res()
            v.onerror = () => rej(new Error('seek'))
            setTimeout(() => rej(new Error('seek 超时')), 5000)
          })
          if (v.videoWidth) {
            const c = document.createElement('canvas')
            c.width = v.videoWidth
            c.height = v.videoHeight
            c.getContext('2d')?.drawImage(v, 0, 0, c.width, c.height)
            base.thumbnail = c.toDataURL('image/jpeg', 0.72)
          }
        } catch { /* 缩略图失败不阻断，元数据仍可用 */ }
        URL.revokeObjectURL(url)
      } else if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(ext)) {
        const url = URL.createObjectURL(file)
        const img = new Image()
        img.onload = () => {
          base.resolution = `${img.width}×${img.height}`
          try {
            const c = document.createElement('canvas')
            c.width = img.width
            c.height = img.height
            c.getContext('2d')?.drawImage(img, 0, 0)
            base.thumbnail = c.toDataURL('image/jpeg', 0.72)
          } catch { /* 忽略 */ }
          URL.revokeObjectURL(url)
          setMeta({ ...base })
        }
        img.onerror = () => { URL.revokeObjectURL(url); setMeta(base) }
        img.src = url
        return
      }
    } catch { /* 元数据读取失败仍可回填路径 */ }
    setMeta(base)
  }

  const onPick = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    try {
      // Electron 下 File.path 为真实绝对路径；浏览器/headless 模式走后端素材登记返回路径
      const p = (file as any).path
      if (typeof p === 'string' && p) {
        onUpdate(nodeId, { [param.name]: p })
        void probeMeta(file, p)
      } else {
        const r = await api.ingestAsset(file)
        onUpdate(nodeId, { [param.name]: r.path })
        void probeMeta(file, r.path)
      }
    } catch (e: any) {
      pushToast('选择文件失败: ' + (e?.message || e), 'err')
    } finally {
      setBusy(false)
    }
  }

  const shortPath = (v: string) => {
    if (!v) return ''
    const seg = v.split(/[\\/]/)
    return seg.length > 1 ? seg.slice(-2).join('/') : v
  }

  const boxStyle: CSSProperties = {
    width: '100%', padding: '8px 11px', borderRadius: 10, fontSize: 13, boxSizing: 'border-box',
    border: 'var(--glass-border)', background: 'var(--input-bg)', color: 'var(--text)',
    boxShadow: 'var(--glass-inner)', outline: 'none',
  }

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 7, alignItems: 'stretch' }}>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          title={param.description || '选择本地媒体文件'}
          style={{ ...boxStyle, width: 'auto', flexShrink: 0, cursor: busy ? 'wait' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600, color: 'var(--accent-strong)', background: 'var(--accent-soft)', borderColor: 'rgba(139,147,255,.35)' }}
        >
          {busy ? <Loader2 size={13} className="fw-spin" /> : <FolderOpen size={13} />}
          {busy ? '登记中…' : '选择文件'}
        </button>
        <input
          data-testid="media-file-input" ref={inputRef} type="file" accept={FILE_ACCEPT}
          style={{ display: 'none' }}
          onChange={(e) => { const f = e.target.files?.[0]; void onPick(f); e.target.value = '' }}
        />
        <div style={{ ...boxStyle, flex: 1, fontSize: 12, color: 'var(--text-faint)', display: 'flex', alignItems: 'center', overflow: 'hidden' }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }} title={value || param.name}>
            {value ? shortPath(value) : (meta?.name || '未选择文件')}
          </span>
          {(meta || value) && (
            <span style={{ flexShrink: 0, marginLeft: 6, display: 'inline-flex', alignItems: 'center', gap: 3, color: 'var(--success)', fontSize: 11, fontWeight: 600 }}>
              <Check size={11} /> 已就绪
            </span>
          )}
        </div>
      </div>
      {(meta?.duration || meta?.resolution) && (
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 6, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {meta.duration && <span>时长 {meta.duration}</span>}
          {meta.resolution && <span>分辨率 {meta.resolution}</span>}
          {meta.thumbnail && (
            <img src={meta.thumbnail} alt="视频缩略图" style={{ width: 96, borderRadius: 8, border: '1px solid var(--border)', marginTop: 4, objectFit: 'cover', aspectRatio: '16/9' }} />
          )}
        </div>
      )}
    </div>
  )
}
