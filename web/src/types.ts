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
  search_aliases?: string[]
  deprecated?: boolean
}

// ---- S1-T4（R-平台-4）：/api/object_info 等价语义（前端渲染单一来源） ----
export interface ObjectInfo {
  name: string
  display_name: string
  description: string
  category: string
  version: string
  input: {
    required: Record<string, [PortType, Record<string, unknown>]>
    optional: Record<string, [PortType, Record<string, unknown>]>
    hidden: Record<string, unknown>
  }
  input_order: { required: string[]; optional: string[] }
  is_input_list: Record<string, boolean>
  output: PortType[]
  output_name: string[]
  output_is_list: boolean[]
  search_aliases: string[]
  deprecated: boolean
  kind?: string
  pkg?: string
  gpu_required?: boolean
  gpu_recommended?: boolean
  streaming?: boolean
}

/** object_info 端口元组 [type, meta] → PortSpec（单一来源适配器） */
function portFromTuple(name: string, typ: PortType, meta: Record<string, unknown>): PortSpec {
  return {
    name,
    type: typ,
    label: String(meta.label ?? name),
    required: Boolean(meta.required ?? true),
    default: meta.default,
    widget: String(meta.widget ?? (typ === 'INT' || typ === 'FLOAT' ? 'number' : 'text')),
    options: Array.isArray(meta.options) ? (meta.options as string[]) : [],
    description: String(meta.description ?? ''),
  }
}

/** /object_info 全量映射 → NodeSpec[]（单一来源；/api/specs 数组不再作为唯一来源） */
export function objectInfoToSpecs(oi: Record<string, ObjectInfo>): NodeSpec[] {
  const PRIM = ['STRING', 'INT', 'FLOAT', 'BOOL']
  const out: NodeSpec[] = []
  for (const tid of Object.keys(oi)) {
    const v = oi[tid]
    const required = v.input?.required || {}
    const optional = v.input?.optional || {}
    const order = v.input_order || { required: [], optional: [] }
    const orderedNames = [...(order.required || []), ...(order.optional || [])]
    const merged: Record<string, [PortType, Record<string, unknown>]> = { ...optional, ...required }

    // 保存声明顺序（input_order）+ 按 port_role 还原 FW 的连线/参数区分：
    //   port_role==='input' → 连线输入端口（画布左柄）；port_role==='param' → 表单参数。
    //   兼容旧后端（无 port_role）：按类型启发——非 STRING/INT/FLOAT/BOOL 视为连线输入。
    const inputs: PortSpec[] = []
    const params: PortSpec[] = []
    const pushSplit = (name: string, typ: PortType, meta: Record<string, unknown>) => {
      const role = meta.port_role
      const spec = portFromTuple(name, typ, meta)
      if (role === 'input' || (role == null && !PRIM.includes(typ))) inputs.push(spec)
      else params.push(spec)
    }
    for (const name of orderedNames) {
      if (merged[name]) pushSplit(name, merged[name][0], merged[name][1])
    }
    for (const name of Object.keys(merged)) {
      if (!orderedNames.includes(name)) pushSplit(name, merged[name][0], merged[name][1])
    }
    out.push({
      type_id: tid,
      title: v.display_name || v.name || tid,
      category: v.category || '用户节点',
      description: v.description || '',
      version: v.version || '1.0.0',
      inputs,
      outputs: (v.output_name || []).map((n, i) => portFromTuple(n, v.output[i] || 'ANY' as PortType, {})),
      params,
      gpu_required: v.gpu_required || false,
      gpu_recommended: v.gpu_recommended || false,
      streaming: v.streaming || false,
      search_aliases: v.search_aliases || [],
      deprecated: v.deprecated || false,
    })
  }
  return out
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
