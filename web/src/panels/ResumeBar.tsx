// 断点续跑 UI（D-12 / t11）：画布顶部阶段条 + 最后检查点
// - 数据：GET /api/workflows/{id}（pipeline_stage/index/stages/checkpoint + 节点终态）
// - 动作：继续运行（缓存命中已成功节点，只重算失败/未跑）、从失败节点重试（reset+selection 重跑）、
//         清除检查点（阶段回起点 + 全部节点重置，需确认）
// - 位置：浮层（不侵入 Canvas.tsx 顶栏），仅在有阶段进度/失败节点时显示
import { useCallback, useEffect, useRef, useState } from 'react'
import { Play, RotateCcw, Trash2, ChevronRight, MapPin, AlertTriangle, X } from 'lucide-react'
import { api } from '../api'
import { useAppStore } from '../store'

interface WfSnapshot {
  pipeline_stage?: string
  pipeline_stage_index?: number
  pipeline_stages?: string[]
  pipeline_checkpoint?: Record<string, unknown>
  nodes?: { id: string; status?: string; error?: string }[]
}

export function ResumeBar() {
  const workflowId = useAppStore((s) => s.workflowId)
  const pushToast = useAppStore((s) => s.pushToast)
  const nodeStatus = useAppStore((s) => s.nodeStatus)
  const [wf, setWf] = useState<WfSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const lastWf = useRef(workflowId)

  const reload = useCallback(async (id: string) => {
    if (!id) return
    try {
      const d = (await api.getWorkflow(id)) as any
      setWf(d || null)
    } catch { /* 工作流可能已删除 */ }
  }, [])

  useEffect(() => {
    if (!workflowId) return
    lastWf.current = workflowId
    void reload(workflowId)
    const it = setInterval(() => { if (lastWf.current) void reload(lastWf.current) }, 8000)
    return () => clearInterval(it)
  }, [workflowId, reload])

  // 运行结束/失败后自动刷新（WS node_update → store 状态变化，600ms 防抖）
  useEffect(() => {
    if (lastWf.current) void reload(lastWf.current)
  }, [nodeStatus, reload])

  const stages = wf?.pipeline_stages || []
  const stageIdx = Math.max(0, wf?.pipeline_stage_index ?? 0)
  const cp = wf?.pipeline_checkpoint || {}
  const cpKeys = Object.keys(cp).filter((k) => cp[k] != null && cp[k] !== '' && cp[k] !== 0)
  const failedIds = (wf?.nodes || []).filter((n) => n.status === 'failed').map((n) => n.id)
  const hasProgress = (stageIdx > 0 || cpKeys.length > 0 || failedIds.length > 0)

  if (!workflowId || !hasProgress) return null

  const runWorkflow = async (mode: string, nodeIds: string[]) => {
    setBusy(true)
    try {
      const r: any = await api.runWorkflow(workflowId, mode, nodeIds)
      pushToast('已入队执行（队列位置 ' + (r.queue_position ?? '—') + '）', 'ok')
    } catch (e: any) { pushToast('运行失败：' + (e.message || e), 'err') }
    finally { setBusy(false) }
  }

  // 继续运行：成功节点命中缓存自动跳过，仅重算失败/未跑节点
  const doResume = async () => {
    setBusy(true)
    try {
      if (failedIds.length > 0) {
        await Promise.all(failedIds.map((nid) => api.resetNode(workflowId, nid, true).catch(() => null)))
      }
      await api.resumePipeline(workflowId).catch(() => null)
      await runWorkflow('all', [])
    } finally { setBusy(false) }
  }

  // 从失败节点重试：reset（清缓存）后仅重跑失败节点集合
  const doRetryFailed = async () => {
    setBusy(true)
    try {
      await Promise.all(failedIds.map((nid) => api.resetNode(workflowId, nid, true).catch(() => null)))
      await runWorkflow('selection', failedIds)
    } finally { setBusy(false) }
  }

  // 清除检查点：阶段回起点 + 全部节点重置（从头开始）
  const doClear = async () => {
    if (!window.confirm('清除检查点将把阶段重置到起点并清空全部节点状态（缓存结果保留），确定继续？')) return
    setBusy(true)
    try {
      if (stages.length > 0) await api.updatePipelineStage(workflowId, stages[0], {}).catch(() => null)
      const allIds = (wf?.nodes || []).map((n) => n.id)
      await Promise.all(allIds.map((nid) => api.resetNode(workflowId, nid, false).catch(() => null)))
      pushToast('检查点已清除，阶段回到起点', 'ok')
      void reload(workflowId)
    } catch (e: any) { pushToast('清除失败：' + (e.message || e), 'err') }
    finally { setBusy(false) }
  }

  return (
    <div className="fw-resume-bar" style={{
      position: 'fixed', top: 62, left: '50%', transform: 'translateX(-50%)', zIndex: 120,
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', maxWidth: 'min(860px, calc(100vw - 60px))',
      border: '1px solid var(--border-strong)', borderRadius: 13,
      background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
      boxShadow: 'var(--elev-3), 0 0 20px rgba(245,165,36,.10)',
      animation: 'fw-pop-in .18s var(--t-ease) both',
      flexWrap: 'wrap' as const,
    }}>
      <MapPin size={13} color="var(--running)" style={{ flexShrink: 0 }} />
      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--running)', flexShrink: 0 }}>断点续跑</span>

      {/* 阶段条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
        {stages.map((s, i) => {
          const done = i < stageIdx
          const cur = i === stageIdx
          return (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <span title={s} style={{
                fontSize: 9.5, padding: '2px 7px', borderRadius: 999, whiteSpace: 'nowrap',
                color: cur ? '#0b1120' : done ? 'var(--success)' : 'var(--text-faint)',
                background: cur ? 'var(--running)' : done ? 'var(--success-soft)' : 'var(--bg-panel-2)',
                border: '1px solid ' + (cur ? 'transparent' : 'var(--border)'),
                fontWeight: cur ? 800 : 600,
              }}>{s}</span>
              {i < stages.length - 1 && <ChevronRight size={9} color="var(--text-faint)" style={{ flexShrink: 0 }} />}
            </div>
          )
        })}
      </div>

      {/* 最后检查点 */}
      {cpKeys.length > 0 && (
        <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-dim)', flexShrink: 0, fontSize: 9.5 }}
          title={cpKeys.map((k) => k + '=' + JSON.stringify(cp[k])).join('\n')}>
          {cpKeys.length} 个检查点数据
        </span>
      )}
      {failedIds.length > 0 && (
        <span className="fw-badge" style={{ background: 'var(--danger-soft)', color: 'var(--danger)', flexShrink: 0, fontSize: 9.5 }}>
          <AlertTriangle size={9} style={{ verticalAlign: -1, marginRight: 2 }} />{failedIds.length} 个节点失败
        </span>
      )}

      <button onClick={doResume} disabled={busy} className="fw-btn fw-btn-primary"
        style={{ fontSize: 11, padding: '5px 12px', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <Play size={11} /> 继续运行
      </button>
      {failedIds.length > 0 && (
        <button onClick={doRetryFailed} disabled={busy} className="fw-btn fw-btn-ghost"
          style={{ fontSize: 11, color: 'var(--running)', display: 'inline-flex', alignItems: 'center', gap: 4, border: '1px solid var(--border)' }}>
          <RotateCcw size={11} /> 从失败节点重试
        </button>
      )}
      <button onClick={doClear} disabled={busy} className="fw-btn fw-btn-ghost" title="清除检查点（从头开始）"
        style={{ fontSize: 11, padding: 4, lineHeight: 0, color: 'var(--text-faint)' }}>
        <Trash2 size={12} />
      </button>
    </div>
  )
}