// Zustand 画布状态存储（v4：+toast 系统 / 时间线 / 未保存标志）
import { create } from 'zustand'
import type { FlowNodeData, NodeSpec, NodeStatus } from './types'

interface SessionInfo {
  token: string
  email: string
  expiresAt: number
  plan?: string      // trial | member
  credits?: number   // 积分
  deviceId?: string  // 本机设备标识
}

export interface ToastItem { id: number; msg: string; kind: 'ok' | 'err' | 'info' }

export interface AppSettings {
  theme: 'dark' | 'light'
  dragEnabled: boolean
}
interface AppState {
  session: SessionInfo | null
  setSession: (s: SessionInfo | null) => void

  specs: NodeSpec[]
  setSpecs: (s: NodeSpec[]) => void

  workflowId: string
  workflowName: string
  setWorkflow: (id: string, name: string) => void

  running: boolean
  setRunning: (r: boolean) => void
  lastError: string
  setLastError: (m: string) => void

  nodeStatus: Record<string, NodeStatus>
  nodeError: Record<string, string>
  nodeProgress: Record<string, number>
  nodeAssets: Record<string, Record<string, string>>
  applyNodeEvent: (ev: any) => void

  selectedNodeId: string | null
  setSelectedNodeId: (id: string | null) => void

  // v4：底部时间线独立开关（双击有 segments 资产才开）
  timelineNodeId: string | null
  setTimelineNodeId: (id: string | null) => void

  // v4：未保存标志（改参数置脏，保存成功清）
  dirty: boolean
  setDirty: (d: boolean) => void

  // v4：toast 轻提示
  toasts: ToastItem[]
  pushToast: (msg: string, kind?: 'ok' | 'err' | 'info') => void
  removeToast: (id: number) => void

  settings: AppSettings
  setSettings: (p: Partial<AppSettings>) => void
}

const storageKey = 'fw_session_v1'
const settingsKey = 'fw_settings_v1'

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(settingsKey)
    if (raw) {
      const p = JSON.parse(raw)
      return { theme: p.theme === 'light' ? 'light' : 'dark', dragEnabled: p.dragEnabled !== false }
    }
    const legacy = localStorage.getItem('fw_theme')
    if (legacy === 'light') return { theme: 'light', dragEnabled: true }
  } catch { /* 忽略损坏 */ }
  // 新用户默认亮色；用户切换后通过 fw_settings_v1 持久化
  return { theme: 'light', dragEnabled: true }
}

function loadSession(): SessionInfo | null {
  try {
    const raw = localStorage.getItem(storageKey)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

let toastSeq = 1

export const useAppStore = create<AppState>((set, get) => ({
  session: loadSession(),
  setSession: (s) => {
    if (s) localStorage.setItem(storageKey, JSON.stringify(s))
    else localStorage.removeItem(storageKey)
    set({ session: s })
  },

  specs: [],
  setSpecs: (specs) => set({ specs }),

  workflowId: '',
  workflowName: '',
  setWorkflow: (id, name) => set({ workflowId: id, workflowName: name || '未命名工作流' }),

  running: false,
  setRunning: (r) => set({ running: r }),
  lastError: '',
  setLastError: (m) => set({ lastError: m }),

  nodeStatus: {},
  nodeError: {},
  nodeProgress: {},
  nodeAssets: {},
  applyNodeEvent: (ev) => {
    const t = ev?.type
    if (t === 'node_update') {
      set((s) => ({
        nodeStatus: { ...s.nodeStatus, [ev.node_id]: ev.status },
        nodeError: ev.error !== undefined ? { ...s.nodeError, [ev.node_id]: ev.error } : s.nodeError,
        nodeProgress: ev.progress !== undefined ? { ...s.nodeProgress, [ev.node_id]: ev.progress } : s.nodeProgress,
        nodeAssets: ev.asset_ids ? { ...s.nodeAssets, [ev.node_id]: ev.asset_ids } : s.nodeAssets,
      }))
    } else if (t === 'execution_done') {
      set({ running: false })
    } else if (t === 'graph_error') {
      set({ running: false, lastError: ev.message || '执行出错' })
    }
    if (t === 'node_update' && ev.status === 'failed') {
      set((s) => ({ lastError: s.nodeError[ev.node_id] || s.lastError }))
    }
  },

  selectedNodeId: null,
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  timelineNodeId: null,
  setTimelineNodeId: (id) => set({ timelineNodeId: id }),

  dirty: false,
  setDirty: (d) => set({ dirty: d }),

  toasts: [],
  pushToast: (msg, kind = 'ok') => {
    const id = Date.now() + toastSeq++
    set((s) => ({ toasts: [...s.toasts, { id, msg, kind }] }))
    setTimeout(() => get().removeToast(id), 4200)
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  settings: loadSettings(),
  setSettings: (p) => {
    const next = { ...get().settings, ...p }
    try { localStorage.setItem(settingsKey, JSON.stringify(next)) } catch { /* 忽略 */ }
    set({ settings: next })
  },
}))
