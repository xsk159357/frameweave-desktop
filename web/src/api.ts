// 后端 API 客户端
import type { NodeSpec, WfMeta, Segment } from './types'

const BASE = 'http://127.0.0.1:8788'

// H3 修复：本地 API 鉴权令牌 —— 打包版从 preload 桥取随机值，dev 回落固定值
function localToken(): string {
  const w = (window as any).frameweave
  if (w && w.localToken) return w.localToken
  const saved = localStorage.getItem('fw_local_token')
  if (saved) return saved
  return 'dev-local-token'
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 8000)
  try {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', 'X-FW-Local-Token': localToken(), ...(init?.headers || {}) },
    signal: ctrl.signal,
    ...init,
  })
  if (!resp.ok) {
    let msg = resp.statusText
    try { const j = await resp.json(); msg = j.detail || j.message || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return resp.json() as Promise<T>
  } catch (e: any) {
    if (e?.name === 'AbortError') throw new Error('请求超时：云端授权服务暂时无响应，请检查网络后重试')
    if (e instanceof TypeError && /fetch|network|failed/i.test(String(e.message || e))) throw new Error('无法连接本地服务，请稍后重试')
    throw e
  } finally { clearTimeout(timer) }
}

async function reqMulti<T>(path: string, fd: FormData): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 20000)
  try {
    const resp = await fetch(BASE + path, { method: 'POST', body: fd, signal: ctrl.signal, headers: { 'X-FW-Local-Token': localToken() } })
    if (!resp.ok) {
      let msg = resp.statusText
      try { const j = await resp.json(); msg = j.detail || j.message || msg } catch { /* ignore */ }
      throw new Error(msg)
    }
    return resp.json() as Promise<T>
  } finally { clearTimeout(timer) }
}

export const api = {
  health: () => req<{ok:boolean}>('/api/health'),

  specs: () => req<NodeSpec[]>('/api/specs'),

  userNodes: () => req<{ nodes: string[]; dir: string }>('/api/user_nodes'),

  installUserNode: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return reqMulti<{ ok: boolean; message?: string; nodes: string[] }>('/api/user_nodes/install', fd)
  },

  reloadUserNodes: () => req<{ ok: boolean; loaded: number; nodes: string[] }>('/api/user_nodes/reload', { method: 'POST' }),


  listWorkflows: () => req<WfMeta[]>('/api/workflows'),

  createWorkflow: (name: string) => req<Record<string, unknown>>('/api/workflows', {
    method: 'POST', body: JSON.stringify({ name }),
  }),

  listTemplates: () => req<{id:string;title:string;description:string;nodes:number}[]>('/api/templates'),

  createFromTemplate: (tplId: string) =>
    req<Record<string, unknown>>('/api/workflows/from-template/' + tplId, {
      method: 'POST',
    }),

  getWorkflow: (id: string) => req<Record<string, unknown>>('/api/workflows/' + id),

  deleteWorkflow: (id: string) =>
    req<{ok:boolean}>('/api/workflows/' + id, { method: 'DELETE' }),
  renameWorkflow: (id: string, name: string) =>
    req<Record<string, unknown>>('/api/workflows/' + id, {
      method: 'PUT', body: JSON.stringify({ name }),
    }),

  saveWorkflow: (id: string, nodes: unknown[], edges: unknown[]) =>
    req<Record<string, unknown>>('/api/workflows/' + id, {
      method: 'PUT', body: JSON.stringify({ nodes, edges }),
    }),

  runWorkflow: (id: string, mode = 'all', nodeIds?: string[]) =>
    req<{ok:boolean}>('/api/workflows/' + id + '/run', {
      method: 'POST', body: JSON.stringify({ mode, node_ids: nodeIds || [] }),
    }),

  asset: (aid: string) => req<{id:string;kind:string;content?:unknown}>('/api/assets/' + aid + '/content'),

  login: (email: string, password: string, deviceId: string) =>
    req<{ok:boolean;session?:{token:string;email:string;expires_at:number;plan?:string;credits?:number;device_id?:string};device_kick?:{message:string};message?:string}>('/api/auth/login', {
      method: 'POST', body: JSON.stringify({ email, password, device_id: deviceId }),
    }),

  sendEmailCode: (email: string, scene: 'register' | 'reset_password') =>
    req<{ok:boolean;message?:string;expires_in?:number;retry_after?:number}>('/api/auth/email/send-code', {
      method: 'POST', body: JSON.stringify({ email, scene }),
    }),

  register: (email: string, code: string, password: string, deviceId: string) =>
    req<{ok:boolean;session?:{token:string;email:string;expires_at:number;plan?:string;credits?:number;device_id?:string};message?:string}>('/api/auth/register', {
      method: 'POST', body: JSON.stringify({ email, code, password, device_id: deviceId }),
    }),

  resetPassword: (email: string, code: string, newPassword: string) =>
    req<{ok:boolean;message?:string}>('/api/auth/password/reset', {
      method: 'POST', body: JSON.stringify({ email, code, new_password: newPassword }),
    }),

  activate: (email: string, card: string) =>
    req<{ok:boolean;message?:string;plan?:string;expires_at?:number}>('/api/auth/activate', {
      method: 'POST', body: JSON.stringify({ email, card }),
    }),

  // ---- 商城（M18） ----
  marketItems: (q = '', kind = '', official = '') =>
    req<{items:any[];count:number}>('/api/market/items?q=' + encodeURIComponent(q) + '&kind=' + encodeURIComponent(kind) + '&official=' + encodeURIComponent(official)),

  marketPublish: (body: {kind:string;title:string;description:string;author:string;price:number;download_url:string;tags:string[]}) =>
    req<{ok:boolean;item?:any;message?:string}>('/api/market/items', { method: 'POST', body: JSON.stringify(body) }),

  marketInstall: (id: string, token: string) =>
    req<{ok:boolean;message?:string}>('/api/market/items/' + id + '/install', { method: 'POST', body: JSON.stringify({ token }) }),

  marketDownloadAuth: (id: string, token: string, clientVersion: string) =>
    req<{ok:boolean;status?:string;code?:string;message?:string;item_id?:string;title?:string;version?:string;kind?:string;type_id?:string;price?:number;download?:{filename?:string;size?:number;sha256?:string;url:string};direct?:boolean}>('/api/market/items/' + encodeURIComponent(id) + '/download-auth', {
      method: 'POST', body: JSON.stringify({ item_id: id, token, client_version: clientVersion }),
    }),

  marketInstallReport: (id: string, token: string, clientVersion: string, result: 'ok' | 'failed', error = '') =>
    req<{ok:boolean;recorded?:boolean}>('/api/market/items/' + encodeURIComponent(id) + '/install-report', {
      method: 'POST', body: JSON.stringify({ item_id: id, token, client_version: clientVersion, result, error }),
    }),

  exportNodeUrl: (typeId: string) => '/api/export/node/' + typeId,

  verify: (token: string, deviceId: string) =>
    req<{ok:boolean;reason?:string;plan?:string;credits?:number;expires_at?:number;device_kick?:boolean}>(
      '/api/auth/verify', { method: 'POST', body: JSON.stringify({ token, device_id: deviceId }) }),

  storeSecret: (name: string, value: string) =>
    req<{ok:boolean}>('/api/secrets/' + encodeURIComponent(name), {
      method: 'PUT', body: JSON.stringify({ value }),
    }),

  revealPath: (path: string) =>
    req<{ok:boolean}>('/api/util/reveal', {
      method: 'POST', body: JSON.stringify({ path }),
    }),

  cancelWorkflow: (id: string) =>
    req<{ok:boolean}>('/api/workflows/' + id + '/cancel', { method: 'POST' }),

  resetNode: (workflowId: string, nodeId: string, force = true) =>
    req<{ok:boolean}>('/api/workflows/' + workflowId + '/nodes/' + nodeId + '/reset', {
      method: 'POST', body: JSON.stringify({ force }),
    }),

  // ---- 导出（M18 补全：节点 zip / 工作流 zip，blob 下载） ----
  exportNodeZip: async (typeId: string) => {
    const resp = await fetch(BASE + '/api/export/node/' + encodeURIComponent(typeId), {
      headers: { 'X-FW-Local-Token': localToken() },
    })
    if (!resp.ok) throw new Error('导出失败: HTTP ' + resp.status)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = String(typeId).split('/').pop() + '.zip'; a.click()
    URL.revokeObjectURL(url)
  },
  exportWorkflowZip: async (id: string) => {
    const resp = await fetch(BASE + '/api/export/workflow/' + encodeURIComponent(id), {
      headers: { 'X-FW-Local-Token': localToken() },
    })
    if (!resp.ok) throw new Error('导出失败: HTTP ' + resp.status)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'workflow-' + id + '.zip'; a.click()
    URL.revokeObjectURL(url)
  },
}

// WebSocket 事件订阅
export function connectWS(onEvent: (ev: any) => void): WebSocket {
  // 打包版 file:// 下 location.host 为空 → 必须用绝对地址（与 API BASE 同源）
  const ws = new WebSocket('ws://127.0.0.1:8788/ws')
  ws.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)) } catch { /* ignore */ }
  }
  ws.onclose = () => {
    // 自动重连
    setTimeout(() => connectWS(onEvent), 2000)
  }
  return ws
}
