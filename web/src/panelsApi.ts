// 专业面板 API 封装（t11 / frontend-pro）
//
// 归因说明：D-09/D-11/D-12 要求封装 /api/queue、/api/history、DELETE /api/queue/{task_id}、
// POST /api/workflows/{wfid}/resume 与资产/LLM 端点。为避免与 frontend-dev 正在并行的
// api.ts 类型扩展冲突，端点集中在本独立文件（共用 api.ts 的 API_BASE / localToken，
// 不改写 api.ts）。后续合流时可直接把这些函数并入 api.ts（纯追加，无行为重叠）。
import { API_BASE, localToken } from './api'

// ---- 队列（D-09；后端 TaskQueueManager.snapshot / finished_history） ----
export interface QueueTask {
  task_id: string
  workflow_id: string
  mode: string
  node_ids: string[]
  priority: number
  status: string            // queued | running | success | failed | cancelled
  created_at?: number
  started_at?: number
  finished_at?: number
  error?: string
  /** pending 任务的服务端排队位置（1 起） */
  position?: number
}

export interface QueueSnapshot {
  ok: boolean
  running: QueueTask[]
  pending: QueueTask[]
  max_concurrent: number
  queue_running?: QueueTask[]
  queue_pending?: QueueTask[]
}

export interface HistoryEntry {
  task_id: string
  workflow_id: string
  mode: string
  node_ids: string[]
  priority: number
  status: string
  created_at?: number
  started_at?: number
  finished_at?: number
  error?: string
}

// ---- 资产（D-10；/api/assets/* 与 /api/util/reveal，字段对齐 backend assets.Asset） ----
export interface AssetInfo {
  id: string
  kind: string            // VIDEO/AUDIO/IMAGE/SCRIPT/SEGMENTS/TIMELINE/JSON...
  path?: string
  meta: Record<string, unknown>
  fingerprint?: string
  node_id?: string
  created_at?: number
  size?: number
}

/** D-10：GET /api/assets 列表条目（path 相对 assets 根用于展示；path_abs 绝对路径供 reveal/复制） */
export interface AssetListEntry {
  id: string
  kind: string
  path?: string
  path_abs?: string
  size?: number
  created_at?: number
  node_id?: string
  fingerprint?: string
}

/** D-10：DELETE /api/assets/{aid} 409 引用保护响应 */
export interface AssetDeleteRef {
  workflow_id: string
  workflow_name?: string
  node_id: string
  port: string
}

export interface ResumeInfo {
  ok: boolean
  workflow_id: string
  stage: string
  stage_index: number
  stages: string[]
  checkpoint: Record<string, unknown>
}

// ---- LLM 设置（D-11 模型中心复用 /api/settings/llm 与 /api/llm/probe） ----
export interface LlmSettings {
  base_url?: string
  api_key?: string
  model?: string
  provider?: string
}

async function req<T>(path: string, init?: RequestInit, timeoutMs = 10000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const resp = await fetch(API_BASE + path, {
      headers: { 'Content-Type': 'application/json', 'X-FW-Local-Token': localToken(), ...(init?.headers || {}) },
      signal: ctrl.signal,
      ...init,
    })
    if (!resp.ok) {
      let msg = resp.statusText
      let code = ''
      try { const j = await resp.json(); msg = j.detail || j.message || msg; code = j.code || '' } catch { /* ignore */ }
      const e: any = new Error(msg)
      e.code = code
      e.status = resp.status
      throw e
    }
    return resp.json() as Promise<T>
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      const err: any = new Error('请求超时，请稍后重试')
      err.code = 'timeout'
      throw err
    }
    throw e
  } finally { clearTimeout(timer) }
}

export const panelsApi = {
  /** D-09：队列快照（运行中 + 等待中含排队位置 + 并发上限） */
  queueSnapshot: () =>
    req<QueueSnapshot>('/api/queue', undefined, 15000),

  /** D-09：执行历史（最近 N 条终态任务） */
  queueHistory: (limit = 100) =>
    req<{ ok: boolean; history: HistoryEntry[]; count: number }>('/api/history?limit=' + limit),

  /** D-09：逐任务取消（等待中出队 / 运行中请求取消） */
  queueCancelTask: (taskId: string) =>
    req<{ ok: boolean; task_id: string; status?: string }>('/api/queue/' + encodeURIComponent(taskId), { method: 'DELETE' }),

  /** D-09：单任务重试 = 按任务快照对原工作流重新入队（mode+node_ids 原样） */
  queueRetryTask: (task: { workflow_id: string; mode?: string; node_ids?: string[] }) =>
    req<{ ok: boolean; task_id?: string; queue_position?: number; message?: string }>('/api/workflows/' + encodeURIComponent(task.workflow_id) + '/run', {
      method: 'POST', body: JSON.stringify({ mode: task.mode || 'all', node_ids: task.node_ids || [] }),
    }),

  /** D-10:单个资产详情 */
  asset: (aid: string) =>
    req<AssetInfo>('/api/assets/' + encodeURIComponent(aid)),

  /** D-10：素材中心列表（kind 过滤；path 相对 + path_abs 绝对路径） */
  listAssets: (kind = '', limit = 500) =>
    req<{ ok: boolean; assets: AssetListEntry[]; count: number }>(
      '/api/assets?kind=' + encodeURIComponent(kind) + '&limit=' + encodeURIComponent(String(limit))),

  /** D-10：重命名资产磁盘文件（仅改主干名；422 = 名称非法/同名存在） */
  renameAsset: (aid: string, name: string) =>
    req<{ ok: boolean } & AssetListEntry>('/api/assets/' + encodeURIComponent(aid), {
      method: 'PATCH', body: JSON.stringify({ name }),
    }),

  /** D-10：删除资产。被工作流引用时后端返回 409 code=asset_in_use（references 清单），
   *  需 force=true 确认删除。 */
  deleteAsset: (aid: string, force = false) =>
    req<{ ok: boolean; asset_id?: string; code?: string; message?: string; references?: AssetDeleteRef[] }>(
      '/api/assets/' + encodeURIComponent(aid) + (force ? '?force=1' : ''), { method: 'DELETE' }),
}

/** D-10：媒体文件预览地址（video/audio/image 直接可 <img>/<video> 引用） */
export function assetFileUrl(aid: string): string {
  return API_BASE + '/api/assets/' + encodeURIComponent(aid) + '/file'
}

/** D-10：资源管理器定位（Windows explorer /select） */
export async function revealAsset(path?: string): Promise<boolean> {
  if (!path) return false
  try { await req<{ ok: boolean }>('/api/util/reveal', { method: 'POST', body: JSON.stringify({ path }) }); return true }
  catch { return false }
}

/** D-11：LLM 设置读取/写入与连通性探测（模型中心 LLM 分组复用） */
export const llmApi = {
  get: () => req<LlmSettings>('/api/settings/llm'),
  put: (s: LlmSettings) => req<{ ok: boolean }>('/api/settings/llm', { method: 'PUT', body: JSON.stringify(s) }),
  probe: () => req<{ ok: boolean; message?: string; latency_ms?: number }>('/api/llm/probe', { method: 'POST', body: JSON.stringify({}) }),
}

// ---- 模型中心（D-11；GET /api/models/status 只读快照，复用 plugin.deps.resolve_model） ----
export interface ModelStatusEntry {
  id: string
  name: string
  ready: boolean
  source: string          // env | data_dir | registry | manual
  path: string
  warnings: string[]
  size_bytes: number | null
  weight_file: string | null
  download: string[]
}

export interface ModelCatalogEntry extends ModelStatusEntry {
  type?: string
  installed?: boolean
  compatible_nodes?: string[]
  offline?: boolean
  progress?: number
  size?: number
}

export const modelsApi = {
  status: () =>
    req<{ ok: boolean; models: ModelStatusEntry[]; count: number; registry_count: number; note?: string }>(
      '/api/models/status', undefined, 15000),
  /** t17 D-11：模型目录（兼容 models/items 两种响应容器） */
  list: () => req<{ ok: boolean; models?: ModelCatalogEntry[]; items?: ModelCatalogEntry[]; count?: number }>('/api/models', undefined, 15000),
  /** t17 D-11：下载模型；进度由面板轮询目录/状态展示 */
  download: (id: string) => req<{ ok: boolean; model?: ModelCatalogEntry; task_id?: string; status?: string; message?: string }>('/api/models/download', {
    method: 'POST', body: JSON.stringify({ id }),
  }, 120000),
  /** t17 D-11：删除模型；后端引用保护时返回 model_in_use/409 */
  remove: (id: string) => req<{ ok: boolean; id?: string; status?: string; references?: string[] }>('/api/models/' + encodeURIComponent(id), { method: 'DELETE' }),
}