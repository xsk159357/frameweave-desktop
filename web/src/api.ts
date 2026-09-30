// 后端 API 客户端
import type { NodeSpec, WfMeta, Segment } from './types'

const BASE = 'http://127.0.0.1:8788'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    let msg = resp.statusText
    try { const j = await resp.json(); msg = j.detail || j.message || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return resp.json() as Promise<T>
}

export const api = {
  health: () => req<{ok:boolean}>('/api/health'),

  specs: () => req<NodeSpec[]>('/api/specs'),

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

  activate: (email: string, card: string) =>
    req<{ok:boolean;message?:string;plan?:string;expires_at?:number}>('/api/auth/activate', {
      method: 'POST', body: JSON.stringify({ email, card }),
    }),

  // ---- 商城（M18） ----
  marketItems: (q = '', kind = '', official = '') =>
    req<{items:any[];count:number}>('/api/market/items?q=' + encodeURIComponent(q) + '&kind=' + encodeURIComponent(kind) + '&official=' + encodeURIComponent(official)),

  marketPublish: (body: {kind:string;title:string;description:string;author:string;price:number;download_url:string;tags:string[]}) =>
    req<{ok:boolean;item?:any}>('/api/market/items', { method: 'POST', body: JSON.stringify(body) }),

  marketInstall: (id: string, token: string) =>
    req<{ok:boolean;message?:string}>('/api/market/items/' + id + '/install', { method: 'POST', body: JSON.stringify({ token }) }),

  exportNodeUrl: (typeId: string) => '/api/export/node/' + typeId,

  verify: (token: string, deviceId: string) =>
    req<{ok:boolean;reason?:string;plan?:string;credits?:number;expires_at?:number;device_kick?:boolean}>(
      '/api/auth/verify?token=' + encodeURIComponent(token) + '&device_id=' + encodeURIComponent(deviceId)),

  storeSecret: (name: string, value: string) =>
    req<{ok:boolean}>('/api/secrets/' + encodeURIComponent(name), {
      method: 'PUT', body: JSON.stringify({ value }),
    }),

  revealPath: (path: string) =>
    req<{ok:boolean}>('/api/util/reveal', {
      method: 'POST', body: JSON.stringify({ path }),
    }),

  resetNode: (workflowId: string, nodeId: string, force = true) =>
    req<{ok:boolean}>('/api/workflows/' + workflowId + '/nodes/' + nodeId + '/reset', {
      method: 'POST', body: JSON.stringify({ force }),
    }),
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
