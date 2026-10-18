// Zustand 画布状态存储（v4：+toast 系统 / 时间线 / 未保存标志）
import { create } from 'zustand'
import type { FlowNodeData, NodeSpec, NodeStatus } from './types'
import type { RunErrorItem } from './api'

interface SessionInfo {
  token: string
  email: string
  expiresAt: number
  plan?: string      // trial | member
  credits?: number   // 积分
  deviceId?: string  // 本机设备标识
}

// 云端 expires_at 为 Unix 秒（epoch seconds）；历史会话可能存过秒。
// 统一归一化为毫秒（>1e12 视为已是 ms，幂等），供 UI/过期判断一致使用。
export function expiryMs(v?: number): number | undefined {
  if (v == null || Number.isNaN(v)) return undefined
  return v > 1e12 ? v : v * 1000
}

// 节点库变化签名：type_id@version 排序连接串（顺序无关、幂等）——
// 供 setSpecs 判定“真实变化”，避免同内容重复写入触发 fw-nodes-installed 自续环。
function specsSignature(specs: NodeSpec[]): string {
  return specs.map((s) => s.type_id + '@' + (s.version || '')).sort().join('|')
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

  // t10（D-05/D-13）：一次运行的结构化诊断与耗时（ResultPanel/ErrorPanel 消费）
  runErrors: RunErrorItem[] | null
  setRunErrors: (e: RunErrorItem[] | null) => void
  runInfo: { startedAt?: number; endedAt?: number; mode?: string } | null
  setRunInfo: (r: { startedAt?: number; endedAt?: number; mode?: string } | null) => void

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
  // 流光织机默认深色；用户切换后通过 fw_settings_v1 持久化
  return { theme: 'dark', dragEnabled: true }
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
  setSpecs: (specs) => {
    // 仅当节点库「真实变化」才 set + 广播 fw-nodes-installed（t3 根因修复）：
    // 画布监听该事件后会 nodeSpecs()→setSpecs，若无条件广播会形成
    // set → 广播 → 拉取 → set 的自续环；规格数组身份每轮变化又触发工作流
    // 整体重拉取（旧 Canvas useEffect 依赖 specs），把新建工作流中刚添加的
    // 节点清掉。签名按 type_id@version 排序，顺序无关，幂等。
    const prev = get().specs
    if (specsSignature(specs) === specsSignature(prev)) return
    set({ specs })
    try { window.dispatchEvent(new Event('fw-nodes-installed')) } catch { /* 浏览器环境忽略 */ }
  },

  workflowId: '',
  workflowName: '',
  setWorkflow: (id, name) => set({ workflowId: id, workflowName: name || '未命名工作流' }),

  running: false,
  setRunning: (r) => set({ running: r }),
  lastError: '',
  setLastError: (m) => set({ lastError: m }),
  runErrors: null,
  setRunErrors: (e) => set({ runErrors: e }),
  runInfo: null,
  setRunInfo: (r) => set({ runInfo: r }),

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
        runInfo: ev.status === 'running' && !s.runInfo?.startedAt
          ? { startedAt: Date.now(), mode: s.runInfo?.mode }
          : s.runInfo,
      }))
    } else if (t === 'execution_done') {
      set((s) => ({ running: false, runInfo: s.runInfo ? { ...s.runInfo, endedAt: Date.now() } : null }))
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
