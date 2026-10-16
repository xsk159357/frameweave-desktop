// S1-T7（R-平台-7）：插件 web 目录注入机制（EXTENSION_WEB_DIRS 等价）
//
// 数据链：
//   GET /api/extensions            → [{pkg, type_id, version, entry, capabilities, url}]
//   <script src=API_BASE+url>      → 插件脚本执行，调用 window.fwExtensions.register({...})
//   loadWebExtensions() 挂载       → 每个扩展的 mount(host, ctx) 收到画布宿主容器
//                                   （#fw-ext-root 内的专属 slot），在画布注入自定义 UI 组件。
//
// 平台侧约定（插件 web 目录契约，见 frameweave-plugins/docs/plugin-development-spec.md）：
//   - manifest.web.entry 必须指向包内 web/*.js|mjs|css，平台静态服务 /extensions/<pkg>/...
//   - 插件脚本经 window.fwExtensions.register() 自我注册；无 mount 的扩展仅加载资源。
//   - sandbox/capabilities 由 manifest 声明，加载器仅按其挂载到画布宿主，不扩大权限。
import { api, type FwWebExt } from './api'

export interface FwExtensionCtx {
  api: typeof api
  /** 本扩展专属挂载容器（#fw-ext-root 内，class=fw-ext-slot） */
  host: HTMLElement
  /** 当前节点规格清单（/api/specs 快照） */
  getSpecs: () => any[]
}

export interface FwExtension {
  pkg: string
  type_id: string
  version: string
  /** 挂载到画布宿主容器；返回值/异常由加载器捕获并记日志（不阻断其它扩展） */
  mount?: (host: HTMLElement, ctx: FwExtensionCtx) => void | Promise<void>
  /** 卸载/插件移除时清理（可选） */
  unmount?: (host: HTMLElement) => void
}

declare global {
  interface Window {
    fwExtensions?: {
      register: (ext: FwExtension) => void
      list: () => FwExtension[]
    }
  }
}

const loaded = new Set<string>()        // key = pkg@version（已注入脚本）
const mounted = new Set<string>()       // key = pkg@version（已挂载）
const registered: FwExtension[] = []    // 插件脚本注册的扩展（去重按 pkg）
let bridgeReady = false
let hostReady = false
let reloadTimer: ReturnType<typeof setTimeout> | null = null

function ensureBridge(): void {
  if (bridgeReady) return
  bridgeReady = true
  ;(window as any).fwExtensions = {
    register: (ext: FwExtension) => {
      if (!ext || typeof ext !== 'object' || !ext.type_id) return
      const key = ext.pkg || ext.type_id
      const i = registered.findIndex((r) => (r.pkg || r.type_id) === key)
      if (i >= 0) registered[i] = ext
      else registered.push(ext)
      // 宿主已就绪且脚本在挂载窗口外注册 → 立即补挂载
      if (hostReady) mountExt(ext)
    },
    list: () => registered.slice(),
  }
}

function waitForHost(timeoutMs = 8000): Promise<HTMLElement | null> {
  const start = Date.now()
  return new Promise((resolve) => {
    const tick = () => {
      const el = document.getElementById('fw-ext-root')
      if (el) return resolve(el)
      if (Date.now() - start > timeoutMs) return resolve(null)
      setTimeout(tick, 200)
    }
    tick()
  })
}

function mountExt(ext: FwExtension): void {
  if (!ext.mount) return
  const key = (ext.pkg || ext.type_id) + '@' + (ext.version || '')
  if (mounted.has(key)) return
  const host = document.getElementById('fw-ext-root')
  if (!host) return
  const slot = document.createElement('div')
  slot.className = 'fw-ext-slot'
  slot.dataset.pkg = ext.pkg || ''
  slot.dataset.typeId = ext.type_id || ''
  host.appendChild(slot)
  mounted.add(key)
  const ctx: FwExtensionCtx = {
    api,
    host: slot,
    getSpecs: () => ((window as any).__fwSpecs as any[]) || [],
  }
  try {
    Promise.resolve(ext.mount(slot, ctx)).catch((e) => console.error('[web-ext] mount 失败', ext.type_id, e))
  } catch (e) {
    console.error('[web-ext] mount 异常', ext.type_id, e)
  }
}

function injectScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('脚本加载失败: ' + src))
    document.head.appendChild(s)
  })
}

export async function loadWebExtensions(root?: HTMLElement): Promise<void> {
  ensureBridge()
  const host = root || (await waitForHost())
  if (host) hostReady = true
  let list: FwWebExt[] = []
  try {
    const r = await api.extensions()
    list = r.extensions || []
  } catch (e) {
    console.error('[web-ext] 清单获取失败（后端未就绪/无扩展）', e)
    return
  }
  // 清理已卸载或版本变化的包：卸载槽位 + 允许重注入新版本脚本
  for (const k of Array.from(loaded)) {
    const at = k.lastIndexOf('@')
    const pkg = at > 0 ? k.slice(0, at) : k
    const cur = list.find((e) => e.pkg === pkg)
    if (cur && cur.version === (at > 0 ? k.slice(at + 1) : '')) continue
    loaded.delete(k)
    mounted.delete(k)
    if (host) {
      const slot = host.querySelector<HTMLElement>('.fw-ext-slot[data-pkg="' + pkg + '"]')
      const ext = registered.find((r) => (r.pkg || r.type_id) === pkg)
      if (ext?.unmount && slot) {
        try { ext.unmount(slot) } catch (e) { console.error('[web-ext] unmount 异常', pkg, e) }
      }
      if (slot) slot.remove()
    }
  }
  // 注入每个扩展的入口脚本（按 pkg@version 去重）
  for (const ext of list) {
    const key = ext.pkg + '@' + (ext.version || '')
    if (loaded.has(key)) continue
    loaded.add(key)
    try {
      await injectScript(api.extensionUrl(ext.url))
    } catch (e) {
      console.error('[web-ext] 注入失败', ext.pkg, ext.url, e)
    }
  }
  // 补挂载：宿主就绪后注册的扩展 / 无 mount 的扩展跳过
  for (const ext of registered) mountExt(ext)
}

/** 注册全局事件：节点库变化（插件安装/升级/卸载）后延迟重载 web 扩展 */
export function initWebExtensions(): void {
  if (reloadTimer !== null) return // 幂等（HMR/重复调用安全）
  window.addEventListener('fw-nodes-installed', () => {
    if (reloadTimer) window.clearTimeout(reloadTimer)
    reloadTimer = window.setTimeout(() => {
      reloadTimer = null
      loadWebExtensions().catch(() => { /* 后端暂不可达时静默，下个事件再试 */ })
    }, 300)
  })
}
