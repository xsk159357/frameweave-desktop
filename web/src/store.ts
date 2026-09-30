// Zustand 画布状态存储
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

interface AppState {
  // 会话
  session: SessionInfo | null
  setSession: (s: SessionInfo | null) => void

  // 节点规格
  specs: NodeSpec[]
  setSpecs: (s: NodeSpec[]) => void

  // 工作流
  workflowId: string
  workflowName: string
  setWorkflow: (id: string, name: string) => void

  // 执行状态
  running: boolean
  setRunning: (r: boolean) => void
  lastError: string
  setLastError: (m: string) => void

  // 节点状态（后端实时推送）
  nodeStatus: Record<string, NodeStatus>
  nodeError: Record<string, string>
  nodeProgress: Record<string, number>
  nodeAssets: Record<string, Record<string, string>>
  applyNodeEvent: (ev: any) => void

  // 选中节点（双击展开时间线）
  selectedNodeId: string | null
  setSelectedNodeId: (id: string | null) => void

  // 主题
  dark: boolean
  toggleDark: () => void
}

const storageKey = 'fw_session_v1'

function loadSession(): SessionInfo | null {
  try {
    const raw = localStorage.getItem(storageKey)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

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
  workflowName: '未命名工作流',
  setWorkflow: (id, name) => set({ workflowId: id, workflowName: name }),

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

  dark: localStorage.getItem('fw_theme') === 'dark',
  toggleDark: () => {
    const d = !get().dark
    localStorage.setItem('fw_theme', d ? 'dark' : 'light')
    set({ dark: d })
  },
}))
