// 队列面板（D-09 / t11）：运行中 / 等待中 / 历史 三栏
// - 运行中：任务 id、执行模式、节点数、已运行时长、单任务取消
// - 等待中：排队位置、并发上限提示、预计等待占位、单任务出队取消
// - 历史：最近终态任务（成功/失败/取消）状态列表 + 单任务重试
// 数据源：GET /api/queue（2s 轮询）、GET /api/history、DELETE /api/queue/{task_id}
import { memo, useEffect, useRef, useState } from 'react'
import {
  Play, Square, RotateCcw, Loader2, Clock, Layers, ChevronRight,
  CheckCircle2, XCircle, CircleDashed, ListOrdered, Activity,
} from 'lucide-react'
import { panelsApi, type QueueSnapshot, type HistoryEntry } from '../panelsApi'
import { useAppStore } from '../store'

function fmtTime(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function fmtDur(start?: number, end?: number): string {
  if (!start) return '—'
  const end2 = end || Date.now() / 1000
  const s = Math.max(0, Math.round(end2 - start))
  if (s < 60) return s + 's'
  if (s < 3600) return Math.floor(s / 60) + 'm' + (s % 60) + 's'
  return Math.floor(s / 3600) + 'h' + Math.floor((s % 3600) / 60) + 'm'
}

const statusMeta: Record<string, { color: string; icon: typeof CheckCircle2 }> = {
  success: { color: 'var(--success)', icon: CheckCircle2 },
  failed: { color: 'var(--danger)', icon: XCircle },
  cancelled: { color: 'var(--text-faint)', icon: CircleDashed },
}

interface TaskRowProps {
  task: any
  currentWf: string
  showPosition?: boolean
  maxConcurrent?: number
  onCancel: (tid: string) => void
  onRetry?: (task: any) => void
  onOpenWf?: (wid: string) => void
  busyId: string
}

const TaskRow = memo(function TaskRow({ task, currentWf, showPosition, maxConcurrent, onCancel, onRetry, onOpenWf, busyId }: TaskRowProps) {
  const isCur = task.workflow_id === currentWf
  const st = statusMeta[task.status]
  const Icon = st ? st.icon : Activity
  const busy = busyId === task.task_id
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 4,
      border: '1px solid ' + (isCur ? 'var(--accent-strong)' : 'var(--border)'),
      background: isCur ? 'var(--accent-soft)' : 'var(--bg-panel-2)',
      marginBottom: 6, fontSize: 11.5,
    }}>
      <Icon size={13} color={st ? st.color : 'var(--running)'} style={{ flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 650, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={task.workflow_id}>{isCur ? '当前工作流' : (task.workflow_id || '').slice(0, 12)}</span>
          {showPosition && (
            <span className="fw-badge" style={{ background: 'var(--bg-panel)', color: 'var(--text-dim)', flexShrink: 0 }}>
              排队 #{task.position}
            </span>
          )}
          {task.status === 'running' && task.mode && (
            <span className="fw-badge" style={{ background: 'var(--bg-panel)', color: 'var(--text-dim)', flexShrink: 0 }}>{task.mode}</span>
          )}
          {task.mode === 'all' && task.node_ids && task.node_ids.length > 0 && (
            <span style={{ color: 'var(--text-faint)', fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
              <Layers size={9} />{task.node_ids.length} 节点
            </span>
          )}
        </div>
        <div style={{ color: 'var(--text-faint)', fontSize: 10, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {task.status === 'queued'
            ? <>排队 #{task.position}{maxConcurrent ? ` · 并发上限 ${maxConcurrent}` : ''} · 入队 {fmtTime(task.created_at)}</>
            : task.status === 'running'
              ? <>运行中 {fmtDur(task.started_at)} · 开始 {fmtTime(task.started_at)} · 任务 {task.task_id.slice(0, 8)}</>
              : <>{task.status === 'success' ? '完成' : task.status === 'failed' ? '失败' : '已取消'} · {fmtDur(task.started_at, task.finished_at)} · {fmtTime(task.finished_at)}</>}
        </div>
        {task.error && task.status === 'failed' && (
          <div style={{ color: 'var(--danger)', fontSize: 10, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={task.error}>
            {task.error}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        {(task.status === 'running' || task.status === 'queued') && (
          <button
            onClick={() => onCancel(task.task_id)}
            disabled={busy}
            title="取消此任务"
            className="fw-btn fw-btn-ghost"
            style={{ padding: 4, lineHeight: 0, color: 'var(--danger)', display: 'inline-flex', alignItems: 'center' }}
          >{busy ? <Loader2 size={12} className="fw-spin" /> : <Square size={12} />}</button>
        )}
        {onRetry && task.status !== 'running' && task.status !== 'queued' && (
          <button
            onClick={() => onRetry(task)}
            disabled={busy}
            title="重试此任务（按原模式重新入队）"
            className="fw-btn fw-btn-ghost"
            style={{ padding: 4, lineHeight: 0, color: 'var(--accent-strong)', display: 'inline-flex', alignItems: 'center' }}
          >{busy ? <Loader2 size={12} className="fw-spin" /> : <RotateCcw size={12} />}</button>
        )}
        {onOpenWf && (
          <button
            onClick={() => onOpenWf(task.workflow_id)}
            title="打开该工作流"
            className="fw-btn fw-btn-ghost"
            style={{ padding: 4, lineHeight: 0, color: 'var(--text-faint)', display: 'inline-flex', alignItems: 'center' }}
          ><ChevronRight size={12} /></button>
        )}
      </div>
    </div>
  )
})

export function QueuePanel({ onOpenWf }: { onOpenWf?: (wid: string) => void }) {
  const workflowId = useAppStore((s) => s.workflowId)
  const pushToast = useAppStore((s) => s.pushToast)
  const [snap, setSnap] = useState<QueueSnapshot | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [err, setErr] = useState('')
  const [busyId, setBusyId] = useState('')
  const seq = useRef(0)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const s = ++seq.current
      try {
        const [q, h] = await Promise.all([panelsApi.queueSnapshot(), panelsApi.queueHistory(60)])
        if (!alive || s !== seq.current) return
        setSnap(q)
        setHistory(h.history || [])
        setErr('')
      } catch (e: any) {
        if (alive && s === seq.current) setErr(e.message || '队列读取失败')
      }
    }
    tick()
    const it = setInterval(tick, 2000)
    return () => { alive = false; clearInterval(it) }
  }, [])

  const cancel = async (tid: string) => {
    setBusyId(tid)
    try { await panelsApi.queueCancelTask(tid); pushToast('已请求取消任务', 'ok') }
    catch (e: any) { pushToast('取消失败：' + (e.message || e), 'err') }
    finally { setBusyId('') }
  }
  const retry = async (task: any) => {
    setBusyId(task.task_id)
    try { await panelsApi.queueRetryTask(task); pushToast('已重新入队', 'ok') }
    catch (e: any) { pushToast('重试失败：' + (e.message || e), 'err') }
    finally { setBusyId('') }
  }
  const openWf = (wid: string) => {
    if (!wid) return
    if (onOpenWf) { onOpenWf(wid); return }
    // 兜底：切换到该工作流（Canvas 监听 workflowId 变化自动加载）
    useAppStore.getState().setWorkflow(wid, '')
  }

  const running = snap?.running || []
  const pending = snap?.pending || []
  const maxC = snap?.max_concurrent ?? 4

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* 头部：并发上限 + 计数 */}
      <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <Activity size={15} color="var(--accent)" />
        <b style={{ fontSize: 13.5 }}>任务队列</b>
        <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-dim)' }}>并发上限 {maxC}</span>
        {running.length > 0 && <span className="fw-badge" style={{ background: 'var(--running)', color: '#0b1120' }}>{running.length} 运行中</span>}
        {pending.length > 0 && <span className="fw-badge" style={{ background: 'var(--accent-soft)', color: 'var(--accent-strong)' }}>{pending.length} 排队</span>}
        {err && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--danger)' }}>{err}</span>}
      </div>

      {running.length === 0 && pending.length === 0 && (
        <div style={{ textAlign: 'center', padding: 34, color: 'var(--text-faint)', fontSize: 12, lineHeight: 2 }}>
          <Play size={18} style={{ opacity: .5, marginBottom: 4 }} />
          <div>队列空闲</div>
          <div style={{ fontSize: 11 }}>点击「一键出片」或右键节点运行后，任务会显示在这里</div>
        </div>
      )}

      <div className="fw-scroll" style={{ flex: 1, overflow: 'auto', padding: 10, minHeight: 0 }}>
        {/* 运行中 */}
        {running.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px', fontSize: 11, fontWeight: 700, color: 'var(--running)', letterSpacing: .6 }}>
              <Activity size={11} /> 运行中（{running.length}）
            </div>
            {running.map((t) => <TaskRow key={t.task_id} task={t} currentWf={workflowId} maxConcurrent={maxC} onCancel={cancel} onOpenWf={openWf} busyId={busyId} />)}
          </div>
        )}
        {/* 等待中 */}
        {pending.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px', fontSize: 11, fontWeight: 700, color: 'var(--accent-strong)', letterSpacing: .6 }}>
              <ListOrdered size={11} /> 等待中（{pending.length}）· 排队按 FIFO，最多 {maxC} 个并发
            </div>
            {pending.map((t) => <TaskRow key={t.task_id} task={t} currentWf={workflowId} showPosition maxConcurrent={maxC} onCancel={cancel} onOpenWf={openWf} busyId={busyId} />)}
          </div>
        )}
        {/* 历史 */}
        <div style={{ marginTop: running.length || pending.length ? 14 : 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px 0', fontSize: 11, fontWeight: 700, color: 'var(--text-faint)', letterSpacing: .6 }}>
            <Clock size={11} /> 历史（最近 {history.length} 条）
          </div>
          {history.length === 0 && <div style={{ color: 'var(--text-faint)', fontSize: 11, padding: '6px 2px' }}>暂无历史任务</div>}
          {history.map((t) => <TaskRow key={t.task_id} task={t} currentWf={workflowId} onCancel={cancel} onRetry={retry} onOpenWf={openWf} busyId={busyId} />)}
        </div>
      </div>
    </div>
  )
}