// 批量结果面板：读取 batch_render 的 results 资产，展示成功/失败 + 重试 + 打开文件
import { useEffect, useState } from 'react'
import { api } from './api'
import { useAppStore } from './store'

interface BatchItem {
  index: number
  name: string
  ok: boolean
  path?: string
  size?: number
  video_id?: string
  error?: string
}

interface Props {
  workflowId: string
  batchNodeId: string | null   // 选中的 batch_render 节点 id
  onRetry: (nodeId: string) => void
  onClose: () => void
}

export function ResultPanel({ workflowId, batchNodeId, onRetry, onClose }: Props) {
  const dark = useAppStore((s) => s.dark)
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const nodeStatus = useAppStore((s) => s.nodeStatus)
  const [items, setItems] = useState<BatchItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 依据节点输出资产
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

  const failed = items.filter(i => !i.ok)
  const okCount = items.length - failed.length
  const status = batchNodeId ? (nodeStatus[batchNodeId] || '') : ''
  const isRunning = status === 'running' || status === 'queued'

  return (
    <div className="fw-scroll" style={{
      borderTop: '1px solid ' + (dark ? '#262c3d' : '#e4e8f0'),
      background: dark ? '#161a26' : '#fff', padding: '12px 16px', flexShrink: 0,
      maxHeight: 210, overflowY: 'auto',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>📦 批量结果</span>
        {items.length > 0 && (
          <span className="fw-badge" style={{ background: dark ? '#262c3d' : '#eef1f7', color: dark ? '#8b93a9' : '#7a8499' }}>
            {okCount}/{items.length} 成功{failed.length > 0 ? ' · ' + failed.length + ' 失败' : ''}
          </span>
        )}
        {isRunning && <span style={{ fontSize: 12, color: '#f5a524' }}>⏳ 批量执行中…</span>}
        <span style={{ flex: 1 }} />
        {failed.length > 0 && batchNodeId && (
          <button onClick={() => onRetry(batchNodeId)} className="fw-btn fw-btn-danger" style={{ padding: '5px 12px', fontSize: 12 }}>🔄 重试失败项</button>
        )}
        <button onClick={onClose} className="fw-btn fw-btn-ghost" style={{ padding: '5px 9px', fontSize: 13 }}>✕</button>
      </div>
      {loading && <div style={{ fontSize: 12, color: '#8b93a9' }}>读取结果…</div>}
      {error && <div style={{ fontSize: 12, color: '#e5484d' }}>⚠ {error}</div>}
      {!loading && !error && items.length === 0 && !isRunning && (
        <div style={{ fontSize: 12, color: '#8b93a9' }}>暂无结果（连接批量出片节点并执行后展示）</div>
      )}
      {items.map((it) => (
        <div key={it.index} style={{
          display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
          padding: '6px 10px', borderRadius: 9, marginBottom: 3,
          background: dark ? '#1e2230' : '#f5f6fb',
          border: '1px solid ' + (dark ? '#262c3d' : '#e8ebf3'), transition: 'all .15s',
        }}>
          <span style={{ color: it.ok ? '#30a46c' : '#e5484d', width: 18 }}>{it.ok ? '✓' : '✗'}</span>
          <span style={{ fontWeight: 600 }}>{it.name}</span>
          {it.ok ? (
            <>
              <span style={{ color: '#8b93a9' }}>
                {it.size ? ((it.size / 1024 / 1024).toFixed(1) + ' MB') : ''}
              </span>
              <span style={{ flex: 1, color: '#8b93a9', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.path}>
                {it.path}
              </span>
              {it.path && (
                <button onClick={() => api.revealPath(it.path!).catch(() => {})} style={{
                  padding: '2px 8px', borderRadius: 4, fontSize: 11, cursor: 'pointer',
                  border: '1px solid ' + (dark ? '#3a4157' : '#dfe4ee'), background: dark ? '#2c3142' : '#fff',
                  color: dark ? '#e8eaf2' : '#1f2430', flexShrink: 0,
                }}>📂 定位</button>
              )}
            </>
          ) : (
            <span style={{ flex: 1, color: '#e5484d', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.error}>
              {it.error}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
