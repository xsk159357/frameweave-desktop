// 全局类型定义

// 13 种端口类型：与后端 app/ports.py PortType 一一对应（单一真源，类型映射/端口渲染共用）
export const PORT_TYPES = [
  'VIDEO', 'IMAGE', 'AUDIO', 'SCRIPT', 'SUBTITLE', 'SEGMENTS', 'TIMELINE',
  'JSON', 'STRING', 'INT', 'FLOAT', 'BOOL', 'ANY',
] as const

export type PortType = (typeof PORT_TYPES)[number]

export interface PortSpec {
  name: string
  type: PortType
  label: string
  required: boolean
  default?: unknown
  widget: string
  options: string[]
  description: string
}

export interface NodeSpec {
  type_id: string
  title: string
  category: string
  description: string
  version: string
  inputs: PortSpec[]
  outputs: PortSpec[]
  params: PortSpec[]
  gpu_required: boolean
  gpu_recommended: boolean
  streaming: boolean
}

export type NodeStatus = 'pending' | 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | 'cached'

export interface FlowNodeData {
  type_id: string
  title: string
  category: string
  params: Record<string, unknown>
  last_params?: Record<string, unknown>
  run_history?: { time: number; status: string; params?: Record<string, unknown>; cache_key?: string; error?: string }[]
  status: NodeStatus
  error?: string
  progress: number
  asset_ids: Record<string, string>
  inputs: Record<string, string>
  outputs: Record<string, string>
  spec?: NodeSpec
}

// 资产协议：与后端 app/assets.py Asset 字段对齐（kind/path/meta/fingerprint）
export interface AssetMeta {
  id: string
  kind: string
  path?: string
  meta: Record<string, unknown>
  fingerprint?: string
  node_id?: string
  created_at?: number
  size?: number
}

export interface Segment {
  index: number
  start: number
  end: number
  duration: number
  thumbnail?: string
}

export interface WfMeta { id: string; name: string; updated_at: number }

// 连线兼容性判断（与后端 can_connect 对齐）
export function canConnect(src: PortType, dst: PortType): boolean {
  if (src === 'ANY' || dst === 'ANY' || src === dst) return true
  if (src === 'JSON') return ['JSON','SCRIPT','SEGMENTS','SUBTITLE','STRING'].includes(dst)
  if (dst === 'JSON') return true
  return false
}
