// 主画布（v4 画布优先）：浮层参数面板 + 拖拽添加 + 复制粘贴 + 撤销重做 + 连线右键 + 删除确认 + 导出导入 + 取消运行
import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent as ReactDragEvent, type ReactNode } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, useReactFlow,
  type Connection, type Edge,
} from '@xyflow/react'
import { Play, Loader2, ArrowDown, Save, RotateCcw, Square, Download, Upload, HelpCircle, X, Film, Trash2, Search, Puzzle, Map as MapIcon, Store, RefreshCw } from 'lucide-react'
import { api, connectWS } from './api'
import { useAppStore } from './store'
import { canConnect, type FlowNodeData, type NodeSpec } from './types'
import { FlowNode } from './nodes/FlowNode'
import { ParamPanel } from './ParamPanel'
import { ResultPanel } from './ResultPanel'

const portDot = (t: string) => (({ VIDEO: '#4f8ef7', IMAGE: '#7c8cf8', AUDIO: '#34d399', SUBTITLE: '#fbbf24', SEGMENTS: '#a78bfa', TIMELINE: '#f472b6', SCRIPT: '#f87171', ANY: '#8b93a9' } as Record<string, string>)[t] || 'var(--accent)')

const nodeTypes = { flow: FlowNode }
let nodeSeq = 0

function CanvasInner({ workflowId, readOnly = false }: { workflowId: string; readOnly?: boolean }) {
  const specs = useAppStore((s) => s.specs)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const nodeStatus = useAppStore((s) => s.nodeStatus)
  const nodeError = useAppStore((s) => s.nodeError)
  const nodeProgress = useAppStore((s) => s.nodeProgress)
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const applyNodeEvent = useAppStore((s) => s.applyNodeEvent)
  const running = useAppStore((s) => s.running)
  const setRunning = useAppStore((s) => s.setRunning)
  const lastError = useAppStore((s) => s.lastError)
  const setLastError = useAppStore((s) => s.setLastError)
  const selectedNodeId = useAppStore((s) => s.selectedNodeId)
  const setSelectedNodeId = useAppStore((s) => s.setSelectedNodeId)
  const setTimelineNodeId = useAppStore((s) => s.setTimelineNodeId)
  const setDirty = useAppStore((s) => s.setDirty)
  const pushToast = useAppStore((s) => s.pushToast)

  const [nodes, setNodes, onNodesChangeRaw] = useNodesState<any>([])
  // 节点被手动拖动后标记 _manual：整列重排尊重手动位置（不再自动拉回），自动推挤的收起时缝合回来
  const onNodesChange = useCallback((changes: any[]) => {
    const posIds = new Set(changes.filter((ch: any) => ch.type === 'position' && ch.position).map((ch: any) => ch.id))
    if (posIds.size > 0) {
      setNodes((nds) => nds.map((n) => posIds.has(n.id) && !n.data._manual ? { ...n, data: { ...n.data, _manual: true } } : n))
    }
    onNodesChangeRaw(changes)
  }, [onNodesChangeRaw, setNodes])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [autoLayout, setAutoLayout] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showResults, setShowResults] = useState(false)
  const [batchNodeId, setBatchNodeId] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number; nodeId?: string; nodeTitle?: string; edgeId?: string } | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'nodes'; ids: string[] } | null>(null)
  const [showHelp, setShowHelp] = useState(false)
  const [addMenu, setAddMenu] = useState<{ x: number; y: number } | null>(null)
  const [addQuery, setAddQuery] = useState('')
  const [connMenu, setConnMenu] = useState<{ x: number; y: number; source: string; sourceHandle: string; srcType: string } | null>(null)
  const [showMiniMap, setShowMiniMap] = useState(false)
  const lastConnectRejected = useRef(false)
  const dragEnabled = useAppStore((s) => s.settings.dragEnabled)
  const savedRef = useRef(false)
  const flowWrapper = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const copyRef = useRef<{ nodes: any[]; edges: Edge[] } | null>(null)
  const paramTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { screenToFlowPosition } = useReactFlow()
  const guarded = useCallback((action: () => void) => {
    if (readOnly) { pushToast('订阅已到期，请续费后继续使用', 'info'); return }
    action()
  }, [readOnly, pushToast])

  // ---- 撤销 / 重做（D3） ----
  const historyRef = useRef<{ nodes: any[]; edges: Edge[] }[]>([])
  const futureRef = useRef<{ nodes: any[]; edges: Edge[] }[]>([])
  const pushHistory = useCallback(() => {
    historyRef.current.push({ nodes, edges })
    if (historyRef.current.length > 40) historyRef.current.shift()
    futureRef.current = []
  }, [nodes, edges])
  const undo = useCallback(() => {
    const prev = historyRef.current.pop()
    if (!prev) return
    futureRef.current.push({ nodes, edges })
    setNodes(prev.nodes); setEdges(prev.edges)
    setDirty(false)
  }, [nodes, edges, setNodes, setEdges, setDirty])
  const redo = useCallback(() => {
    const next = futureRef.current.pop()
    if (!next) return
    historyRef.current.push({ nodes, edges })
    setNodes(next.nodes); setEdges(next.edges)
    setDirty(false)
  }, [nodes, edges, setNodes, setEdges, setDirty])

  // 节点状态注入画布（v4.2 性能：引用相同则保持原节点对象，零处置节点不触发重渲染）
  useEffect(() => {
    setNodes((nds) => nds.map((n) => {
      const st = nodeStatus[n.id] || 'pending'
      const er = nodeError[n.id] || ''
      const pr = nodeProgress[n.id] || 0
      const as = nodeAssets[n.id] || {}
      const d = n.data
      if (d.status === st && d.error === er && d.progress === pr && d.asset_ids === as) return n
      return { ...n, data: { ...d, status: st, error: er, progress: pr, asset_ids: as } }
    }))
  }, [nodeStatus, nodeError, nodeProgress, nodeAssets, setNodes])

  // 节点安装后自动刷新 specs（商城/插件面板安装成功时触发 fw-nodes-installed）
  useEffect(() => {
    const h = () => {
      api.specs().then(setSpecs).catch(() => {})
    }
    window.addEventListener('fw-nodes-installed', h)
    return () => window.removeEventListener('fw-nodes-installed', h)
  }, [setSpecs])

  // WebSocket 事件
  useEffect(() => {
    const ws = connectWS(applyNodeEvent)
    return () => ws.close()
  }, [applyNodeEvent])

  // 自动布局
  useEffect(() => {
    if (!autoLayout || nodes.length === 0) return
    const depth = new Map<string, number>()
    const compute = (id: string): number => {
      if (depth.has(id)) return depth.get(id)!
      let d = 0
      for (const e of edges) { if (e.target === id) d = Math.max(d, compute(e.source) + 1) }
      depth.set(id, d)
      return d
    }
    setNodes((nds) => {
      const counts = new Map<number, number>()
      return nds.map((n) => {
        const d = compute(n.id)
        const c = counts.get(d) || 0
        counts.set(d, c + 1)
        return { ...n, position: { x: 40 + d * 260, y: 40 + c * 230 } }
      })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoLayout])

  // ---- 创建节点 ----
  const createNode = useCallback((typeId: string, x: number, y: number) => {
    const spec = specs.find((s) => s.type_id === typeId)
    if (!spec) return
    pushHistory()
    nodeSeq++
    const id = 'n' + nodeSeq + '_' + Date.now().toString(36)
    const data: FlowNodeData = {
      type_id: typeId, title: spec.title, category: spec.category,
      params: Object.fromEntries(spec.params.map(p => [p.name, p.default ?? ''])),
      status: 'pending', progress: 0, asset_ids: {}, inputs: {}, outputs: {}, spec,
    }
    setNodes((nds) => [...nds, { id, type: 'flow', position: { x, y }, data }])
  }, [specs, pushHistory, setNodes])

  // 点击添加（v047：级联错开，杜绝随机落点互相重叠的穿模）
  const addPosRef = useRef<{ x: number; y: number } | null>(null)
  const addNodeAt = useCallback((typeId: string, sx: number, sy: number) => {
    const pos = screenToFlowPosition({ x: sx, y: sy })
    createNode(typeId, pos.x - 120, pos.y - 30)
  }, [createNode, screenToFlowPosition])

  const menuIconChar = (typeId: string) => {
    const last = String(typeId || '').split('/').pop() || ''
    const ch = last.replace(/[^a-zA-Z0-9]/g, '')[0]
    return ch ? ch.toUpperCase() : '?'
  }

  // 添加菜单分组（类别顺序）
  const addGroups = useMemo(() => {
    const order = ['输入', '语义', '分析', '控制', '输出', '用户节点']
    const map = new Map<string, NodeSpec[]>()
    for (const s of specs) {
      if (addQuery && !s.title.includes(addQuery) && !s.type_id.includes(addQuery)) continue
      const cat = s.category || '其他'
      if (!map.has(cat)) map.set(cat, [])
      map.get(cat)!.push(s)
    }
    const keys = [...map.keys()].sort((a, b) => {
      const ia = order.indexOf(a); const ib = order.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
    return keys.map(k => ({ category: k, items: map.get(k)! }))
  }, [specs, addQuery])

const onAddNode = useCallback((typeId: string) => {
    const base = addPosRef.current || { x: 60, y: 60 }
    let x = base.x + 56, y = base.y + 210
    if (y > 900) { y = 60; x += 280 }
    if (x > 1200) { x = 60; y = 60 }
    addPosRef.current = { x, y }
    createNode(typeId, x, y)
  }, [createNode])

  // ---- 拖拽添加（D1） ----
  const onDragOver = useCallback((e: ReactDragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }, [])
  const onDrop = useCallback((e: ReactDragEvent) => {
    e.preventDefault()
    const typeId = e.dataTransfer.getData('application/fw-node')
    if (!typeId) return
    const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY })
    createNode(typeId, pos.x - 120, pos.y - 40)
  }, [createNode, screenToFlowPosition])

  // ---- 连线 ----
  const onConnect = useCallback((conn: Connection) => {
    const srcNode = nodes.find(n => n.id === conn.source)
    const dstNode = nodes.find(n => n.id === conn.target)
    if (!srcNode || !dstNode) return
    const srcSpec = specs.find(s => s.type_id === srcNode.data.type_id)
    const dstSpec = specs.find(s => s.type_id === dstNode.data.type_id)
    const srcPort = srcSpec?.outputs.find(p => p.name === conn.sourceHandle) || srcSpec?.outputs[0]
    const dstPort = dstSpec?.inputs.find(p => p.name === conn.targetHandle) || dstSpec?.inputs[0]
    if (srcPort && dstPort && !canConnect(srcPort.type, dstPort.type)) {
      // 落在不兼容端口上：标记拒绝，让 onConnectEnd 弹可链接列表（而不是什么都不显示）
      lastConnectRejected.current = true
      return
    }
    lastConnectRejected.current = false
    pushHistory()
    setEdges((eds) => addEdge({
      ...conn,
      style: { stroke: 'var(--edge-stroke)', strokeWidth: 2.2 },
      label: srcPort && dstPort ? srcPort.type + '→' + dstPort.type : '',
      labelStyle: { fontSize: 10, fill: '#6b779f' },
    } as Edge, eds))
    setNodes((nds) => nds.map(n => n.id === conn.target ? {
      ...n, data: { ...n.data, inputs: { ...n.data.inputs, [conn.targetHandle || 'in']: conn.source } },
    } : n))
  }, [nodes, specs, pushHistory, setEdges, setNodes])

  // ---- 建边（拖线成功 / 连接列表点击共用） ----
  const makeConn = useCallback((source: string, sourceHandle: string, target: string, targetHandle: string) => {
    const srcNode = nodes.find(n => n.id === source)
    const dstNode = nodes.find(n => n.id === target)
    if (!srcNode || !dstNode || source === target) return
    const srcSpec = specs.find(s => s.type_id === srcNode.data.type_id)
    const dstSpec = specs.find(s => s.type_id === dstNode.data.type_id)
    const srcPort = srcSpec?.outputs.find(p => p.name === sourceHandle) || srcSpec?.outputs[0]
    const dstPort = dstSpec?.inputs.find(p => p.name === targetHandle) || dstSpec?.inputs[0]
    if (srcPort && dstPort && !canConnect(srcPort.type, dstPort.type)) return
    pushHistory()
    setEdges((eds) => addEdge({
      source, sourceHandle, target, targetHandle,
      style: { stroke: 'var(--edge-stroke)', strokeWidth: 2.2 },
      label: srcPort && dstPort ? srcPort.type + '→' + dstPort.type : '',
      labelStyle: { fontSize: 10, fill: '#6b779f' },
    } as Edge, eds))
    setNodes((nds) => nds.map(n => n.id === target ? { ...n, data: { ...n.data, inputs: { ...n.data.inputs, [targetHandle || 'in']: source } } } : n))
  }, [nodes, specs, pushHistory, setEdges, setNodes])

  // 拖线落空（未连到有效端口）→ 弹出可链接列表（v1.3）
  const onConnectEnd = useCallback((event: any, connState: any) => {
    const from = connState?.fromNode
    const fromHandle = connState?.fromHandle
    if (!from) return
    const rejected = lastConnectRejected.current
    lastConnectRejected.current = false
    // 落空（toNode 无）或落在不兼容端口上（onConnect 已拒绝）→ 弹可链接列表
    if (connState?.toNode && !rejected) return
    // 源输出端口类型：三层解析（spec 匹配 → 事件 target 的 .fw-handle title → 放弃并清旧菜单）
    const hId = fromHandle?.id || ''
    let srcType = ''
    const fromSpec = (from.data?.spec as any) || specs.find((s) => s.type_id === from.data?.type_id)
    const srcPort = fromSpec?.outputs?.find((p: any) => p.name === hId)
    if (srcPort) srcType = srcPort.type
    if (!srcType) {
      const tEl = (event as any)?.target as HTMLElement | null
      const handleEl = tEl?.closest ? tEl.closest('.fw-handle') : null
      const title = (handleEl || tEl)?.getAttribute?.('title') || ''
      const m = /\u00b7\s*([A-Z]+)\s*$/.exec(title)
      if (m) srcType = m[1]
    }
    if (!srcType) { setConnMenu(null); return } // 解析不到不再残留旧菜单
    setConnMenu({
      x: Math.max(8, (event as any)?.clientX ?? 0), y: Math.max(8, (event as any)?.clientY ?? 0),
      source: from.id, sourceHandle: hId, srcType,
    })
  }, [specs])

  // 可新建节点类型候选：源类型 → 输入兼容的节点类型（按类别分组）
  const connGroups = useMemo(() => {
    if (!connMenu) return []
    const order = ['输入', '语义', '分析', '控制', '输出', '用户节点']
    const groups = new Map<string, { spec: any; ports: any[] }[]>()
    for (const sp of specs) {
      const ports = (sp.inputs || []).filter((p: any) => canConnect(connMenu.srcType as any, p.type as any))
      if (ports.length === 0) continue
      const cat = order.includes(sp.category) ? sp.category : '其他'
      if (!groups.has(cat)) groups.set(cat, [])
      groups.get(cat)!.push({ spec: sp, ports })
    }
    return [...groups.entries()].map(([category, items]) => ({ category, items }))
  }, [connMenu, specs])
  const connSrcNode = connMenu ? nodes.find((n) => n.id === connMenu.source) : null

  // 从端口牵线 → 空白 → 选节点类型：创建新节点并自动连线（Blender/Figma 式）
  const createAndConnect = useCallback((typeId: string, x: number, y: number) => {
    if (!connMenu) return
    const sp = specs.find((s) => s.type_id === typeId)
    const tPort = sp?.inputs?.find((p: any) => canConnect(connMenu.srcType as any, p.type as any))
    if (!sp || !tPort) return
    pushHistory()
    nodeSeq++
    const id = 'n' + nodeSeq + '_' + Date.now().toString(36)
    const data: FlowNodeData = {
      type_id: typeId, title: sp.title, category: sp.category,
      params: Object.fromEntries((sp.params || []).map((p: any) => [p.name, p.default ?? ''])),
      status: 'pending', progress: 0, asset_ids: {}, outputs: {},
      inputs: { [tPort.name]: connMenu.source },
      spec: sp,
    }
    setNodes((nds) => [...nds, { id, type: 'flow', position: { x: x - 60, y: y - 14 }, data }])
    setEdges((eds) => addEdge({
      source: connMenu.source, sourceHandle: connMenu.sourceHandle, target: id, targetHandle: tPort.name,
      style: { stroke: 'var(--edge-stroke)', strokeWidth: 2.2 },
      label: connMenu.srcType + '→' + tPort.type,
      labelStyle: { fontSize: 10, fill: '#6b779f' },
    } as Edge, eds))
    setConnMenu(null)
  }, [connMenu, specs, pushHistory, setEdges, setNodes])

  // ---- 更新节点参数 ----
  const updateParams = useCallback((nodeId: string, patch: Record<string, string>) => {
    if (readOnly) { pushToast('订阅已到期，请续费后继续使用', 'info'); return }
    if (!paramTimer.current) { pushHistory(); paramTimer.current = setTimeout(() => { paramTimer.current = null }, 600) }
    setNodes((nds) => nds.map((n) => n.id === nodeId ? {
      ...n, data: { ...n.data, params: { ...n.data.params, ...patch } },
    } : n))
    savedRef.current = false
    setDirty(true)
  }, [pushHistory, setNodes, setDirty])

  // 暴露给节点卡 / 侧栏（全局桥：内嵌编辑 / 右键菜单 / 点击添加）
  useEffect(() => {
    ;(window as any).__fwUpdateParams = updateParams
    ;(window as any).__fwNodeMenu = (info: { x: number; y: number; nodeId: string; nodeTitle: string }) => setMenu(info)
    ;(window as any).__fwAddNode = (typeId: string) => onAddNode(typeId)
    ;(window as any).__fwOpenEdit = (id: string) => { setSelectedNodeId(id); setMenu(null) }
    // 展开/收起：更新节点展开态后对整个相关列做垂直重排（防遮挡 + 收缝）
    ;(window as any).__fwSetNodeExpanded = (nodeId: string, exp: boolean) => {
      setNodes((nds) => reflowColumn(
        nds.map((n) => n.id === nodeId ? { ...n, data: { ...n.data, expanded: exp } } : n),
        nodeId,
      ))
    }
  }, [updateParams, onAddNode, setSelectedNodeId, setNodes])

  // ---- 保存 ----
  const save = useCallback(async () => {
    setSaving(true)
    try {
      const nds = nodes.map((n) => ({ id: n.id, type: n.data.type_id, x: n.position.x, y: n.position.y, params: n.data.params }))
      const eds = edges.map((e) => ({ source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle }))
      await api.saveWorkflow(workflowId, nds, eds)
      savedRef.current = true
      setDirty(false)
    } catch (e: any) { console.error('保存失败', e); pushToast('保存失败: ' + (e.message || e), 'err') }
    finally { setSaving(false) }
  }, [nodes, edges, workflowId, pushToast, setDirty])

  // 运行
  const run = useCallback(async (mode: string, nodeIds?: string[]) => {
    setRunning(true)
    setLastError('')
    try {
      const nds = nodes.map((n) => ({ id: n.id, type: n.data.type_id, x: n.position.x, y: n.position.y, params: n.data.params }))
      const eds = edges.map((e) => ({ source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle }))
      await api.saveWorkflow(workflowId, nds, eds)
      await api.runWorkflow(workflowId, mode, nodeIds || [])
      setDirty(false)
    } catch (e: any) {
      console.error('运行失败', e)
      setLastError(String(e.message || e)); setRunning(false)
      pushToast('运行失败: ' + String(e.message || e), 'err')
    }
  }, [nodes, edges, workflowId, setRunning, setLastError, pushToast, setDirty])

  // ---- 取消运行（F3） ----
  const cancelRun = useCallback(async () => {
    try {
      await api.cancelWorkflow(workflowId)
      pushToast('已发送取消请求', 'info')
    } catch (e: any) { pushToast('取消失败: ' + (e.message || e), 'err') }
  }, [workflowId, pushToast])

  // 重试单个节点
  const retryNode = useCallback(async (nodeId: string) => {
    setRunning(true); setLastError('')
    try { await api.runWorkflow(workflowId, 'selection', [nodeId]) }
    catch (e: any) { console.error('重试失败', e); setLastError(String(e.message || e)); setRunning(false) }
  }, [workflowId, setRunning, setLastError])
  // 重置节点状态
  const resetNode = useCallback(async (nodeId: string) => {
    try { await api.resetNode(workflowId, nodeId, true); pushToast('节点状态已重置', 'info') }
    catch (e: any) { console.error('重置失败', e); setLastError(String(e.message || e)); pushToast('重置失败: ' + (e.message || e), 'err') }
  }, [workflowId, setLastError, pushToast])

  // 打开已有工作流
  useEffect(() => {
    if (!workflowId) return
    let alive = true
    api.getWorkflow(workflowId).then((wf: any) => {
      if (!alive) return
      const nds = (wf.nodes || []).map((n: any) => {
        const spec = specs.find((s) => s.type_id === n.type) || specs[0]
        return {
          id: n.id, type: 'flow', position: { x: n.x, y: n.y },
          data: {
            type_id: n.type, title: spec?.title || n.type, category: spec?.category || '',
            params: n.params || {}, last_params: n.last_params || {},
            run_history: n.run_history || [],
            status: n.status || 'pending', error: n.error || '', progress: n.progress || 0,
            asset_ids: n.asset_ids || {}, inputs: n.inputs || {}, outputs: n.outputs || {}, spec,
          } as FlowNodeData,
        }
      })
      const eds = (wf.edges || []).map((e: any) => ({
        id: 'e_' + e.source + '_' + e.target + '_' + Math.random().toString(36).slice(2, 6), source: e.source, target: e.target,
        sourceHandle: e.sourceHandle, targetHandle: e.targetHandle,
        style: { stroke: 'var(--edge-stroke)', strokeWidth: 2.2 }, labelStyle: { fontSize: 10, fill: '#6b779f' },
      }))
      setNodes(nds); setEdges(eds)
      if (wf.name) useAppStore.getState().setWorkflow(workflowId, wf.name)
    }).catch(() => { /* 新工作流为空 */ })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId])

  // ---- 复制 / 粘贴（D2） ----
  const copySelection = useCallback(() => {
    const sel = nodes.filter(n => n.selected)
    if (!sel.length) return
    const ids = new Set(sel.map(n => n.id))
    const es = edges.filter(ed => ids.has(ed.source) && ids.has(ed.target))
    copyRef.current = { nodes: sel, edges: es }
    pushToast('已复制 ' + sel.length + ' 个节点', 'info')
  }, [nodes, edges, pushToast])

  const paste = useCallback(() => {
    const src = copyRef.current
    if (!src || !src.nodes.length) return
    pushHistory()
    const idMap = new Map<string, string>()
    const now = Date.now().toString(36)
    const newNodes = src.nodes.map(n => {
      const nid = 'n' + (++nodeSeq) + '_' + now + Math.floor(Math.random() * 1000).toString()
      idMap.set(n.id, nid)
      return { ...n, id: nid, position: { x: n.position.x + 40, y: n.position.y + 40 }, selected: true }
    })
    const newEdges = src.edges.map(e => ({
      ...e,
      id: 'e_' + Math.random().toString(36).slice(2, 10),
      source: idMap.get(e.source)!, target: idMap.get(e.target)!,
      selected: false,
    }))
    setNodes(nds => [...nds, ...newNodes])
    setEdges(eds => [...eds, ...newEdges])
    setNodes(nds => nds.map(n => (n.selected ? { ...n, selected: false } : n)))
    setTimeout(() => {
      setNodes(nds => nds.map(n => (idMap.has(n.id) ? { ...n, selected: true } : n)))
    }, 30)
    pushToast('已粘贴 ' + newNodes.length + ' 个节点', 'ok')
  }, [pushHistory, setNodes, setEdges, pushToast])

  // ---- 导出 / 导入（F2） ----
  const exportWorkflow = useCallback(() => {
    const doc = {
      app: 'FrameWeave', format: 1, exportedAt: new Date().toISOString(),
      workflowId, workflowName: useAppStore.getState().workflowName,
      nodes: nodes.map(n => ({ id: n.id, type: n.data.type_id, x: Math.round(n.position.x), y: Math.round(n.position.y), params: n.data.params })),
      edges: edges.map(e => ({ source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle })),
    }
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'FrameWeave-工作流-' + new Date().toISOString().slice(0, 10) + '.fwflow.json'; a.click()
    URL.revokeObjectURL(url)
    pushToast('已导出 ' + nodes.length + ' 个节点', 'ok')
  }, [nodes, edges, workflowId, pushToast])

  const importWorkflow = useCallback(async (file: File) => {
    try {
      const text = await file.text()
      const doc = JSON.parse(text)
      if (!Array.isArray(doc.nodes)) throw new Error('无效的工作流文件（缺少 nodes）')
      const idMap = new Map<string, string>()
      const now = Date.now().toString(36)
      const nds = doc.nodes.map((n: any) => {
        const nid = 'n' + (++nodeSeq) + '_' + now + Math.floor(Math.random() * 1e4).toString()
        idMap.set(String(n.id), nid)
        const spec = specs.find(s => s.type_id === n.type)
        return {
          id: nid, type: 'flow', position: { x: n.x ?? 120 + Math.random() * 60, y: n.y ?? 120 + Math.random() * 60 },
          data: {
            type_id: n.type, title: spec?.title || n.type, category: spec?.category || '',
            params: n.params || {}, last_params: {}, run_history: [], status: 'pending', error: '', progress: 0,
            asset_ids: {}, inputs: {}, outputs: {}, spec,
          } as FlowNodeData,
        }
      })
      const eds = (doc.edges || []).map((ed: any) => ({
        id: 'e_' + Math.random().toString(36).slice(2, 10),
        source: idMap.get(String(ed.source)) || ed.source,
        target: idMap.get(String(ed.target)) || ed.target,
        sourceHandle: ed.sourceHandle, targetHandle: ed.targetHandle,
        style: { stroke: 'var(--edge-stroke)', strokeWidth: 2.2 }, labelStyle: { fontSize: 10, fill: '#6b779f' },
      }))
      pushHistory()
      setNodes(nds); setEdges(eds)
      setDirty(true)
      pushToast('已导入 ' + nds.length + ' 个节点（保存后生效）', 'ok')
    } catch (e: any) { pushToast('导入失败: ' + (e.message || e), 'err') }
  }, [specs, pushHistory, setNodes, setEdges, setDirty, pushToast])

  // 执行结束自动展示批量结果
  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && !running) {
      const bn = nodes.find((n) => n.data.type_id === 'core/batch_render')
      if (bn) { setBatchNodeId(bn.id); setShowResults(true) }
    }
    prevRunning.current = running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  // ---- 连线右键（D4）：全局监听 contextmenu 命中 .react-flow__edge ----
  useEffect(() => {
    const onWinCtx = (e: globalThis.MouseEvent) => {
      const t = e.target as HTMLElement
      if (t.closest('.react-flow__node')) return
      const edgeEl = t.closest('.react-flow__edge')
      if (edgeEl) {
        e.preventDefault()
        let id = edgeEl.getAttribute('data-id') || ''
        if (!id) { const gid = edgeEl.getAttribute('id') || ''; id = gid.split('edge-')[1] || '' }
        if (id) setMenu({ x: e.clientX, y: e.clientY, edgeId: id })
      }
    }
    window.addEventListener('contextmenu', onWinCtx)
    return () => window.removeEventListener('contextmenu', onWinCtx)
  }, [])

  // ---- 键盘（D5 删除确认 / D2 D3 D6） ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement
      const inInput = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (!readOnly) save(); else pushToast('订阅已到期，请续费后继续使用', 'info'); return }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return }
      if ((e.ctrlKey || e.metaKey) && e.key === 'c') { if (!inInput) copySelection(); return }
      if ((e.ctrlKey || e.metaKey) && e.key === 'v') { if (!inInput) paste(); return }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (inInput) return
        const sel = nodes.filter(n => n.selected)
        const selE = edges.filter(ed => ed.selected)
        if (sel.length > 0) { e.preventDefault(); setConfirm({ kind: 'nodes', ids: sel.map(n => n.id) }); return }
        if (selE.length > 0) { e.preventDefault(); pushHistory(); const rm = new Set(selE.map(ed => ed.id)); setEdges(eds => eds.filter(ed => !rm.has(ed.id))); setDirty(true); return }
      }
      if (e.key === '?') { if (!inInput) setShowHelp(v => !v) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, undo, redo, copySelection, paste, nodes, edges, pushHistory, setEdges])

  // 删除确认执行
  const doDelete = useCallback(() => {
    if (!confirm) return
    pushHistory()
    const ids = new Set(confirm.ids)
    const newNodes = nodes.filter(n => !ids.has(n.id))
    const newEdges = edges.filter(e => !ids.has(e.source) && !ids.has(e.target))
    setNodes(newNodes); setEdges(newEdges)
    setConfirm(null)
    setSelectedNodeId(null)
    setDirty(true)
    pushToast('已删除 ' + ids.size + ' 个节点', 'info')
  }, [confirm, pushHistory, nodes, edges, setNodes, setEdges, setSelectedNodeId, setDirty, pushToast])

  const toolbar = {
    display: 'flex', gap: 8, alignItems: 'center', padding: '9px 14px',
    borderBottom: 'var(--glass-border)', background: 'var(--glass-strong)',
    backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
    boxShadow: 'var(--glass-inner)', flexShrink: 0, position: 'relative' as const, zIndex: 9,
  }
  const btn = {
    padding: '7px 12px', borderRadius: 11, border: 'var(--glass-border)',
    background: 'var(--glass)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
    boxShadow: 'var(--glass-inner)', color: 'var(--text)', fontSize: 13,
    cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
    transition: 'all .2s ease',
  }
  const iconBtn = { ...btn, padding: '7px 9px' }
  const sep = { width: 1, alignSelf: 'stretch', margin: '4px 2px', background: 'var(--border)' }

  const menuItem = (icon: ReactNode, label: string, sub: string, onClick: () => void) => (
    <div
      style={{ padding: '6px 12px', fontSize: 13, cursor: 'pointer', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 7 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover-bg)' }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
      onClick={onClick}
    >
      {icon}
      <span>{label}</span>
      <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-faint)' }}>{sub}</span>
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', flex: 1, position: 'relative' }}>
      {/* ===== 工具栏（A5 瘦身） ===== */}
      <div style={toolbar}>
        <button
          onClick={() => guarded(() => running ? cancelRun() : run('all'))}
          style={{
            ...btn, border: 'none',
            background: running ? 'rgba(248,113,113,.9)' : 'var(--btn-primary-grad)',
            color: '#fff', fontWeight: 700, padding: '6px 15px',
            boxShadow: running ? '0 2px 10px rgba(248,113,113,.4)' : '0 2px 12px rgba(108,140,255,.42)',
          }}
        >
          {running ? <><Square size={13} /> 停止</> : <><Play size={15} /> 一键出片</>}
        </button>
        <button style={btn} onClick={() => guarded(() => run('downstream'))} disabled={readOnly}><ArrowDown size={13} /> 仅下游</button>
        <button style={btn} onClick={() => guarded(save)} disabled={readOnly}>{saving ? '保存中…' : <><Save size={13} /> 保存</>}</button>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-dim)', cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={autoLayout} onChange={(e) => setAutoLayout(e.target.checked)} style={{ accentColor: 'var(--accent)', width: 14, height: 14 }} />
          自动布局
        </label>
        {running && (() => {
          const vals = Object.values(nodeProgress).filter((v: any) => v !== undefined && v !== null)
          const avg = vals.length > 0 ? (vals as number[]).reduce((a, b) => a + b, 0) / vals.length : 0
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 4 }}>
              <div style={{ width: 110, height: 7, background: 'var(--bg-panel-2)', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: (avg * 100) + '%', background: 'var(--accent-grad)', transition: 'width .3s', borderRadius: 999 }} />
              </div>
              <span style={{ fontSize: 11, color: 'var(--text-faint)', fontVariantNumeric: 'tabular-nums' }}>{(avg * 100).toFixed(0)}%</span>
            </div>
          )
        })()}
        <div style={{ flex: 1 }} />
        <div style={sep} />
        <button style={iconBtn} title="导入工作流 (JSON)" onClick={() => fileInput.current?.click()}><Upload size={14} /></button>
        <button style={iconBtn} title="导出工作流 (JSON)" onClick={exportWorkflow}><Download size={14} /></button>
        <button style={iconBtn} title="快捷键 (?)" onClick={() => setShowHelp(v => !v)}><HelpCircle size={14} /></button>
        <input ref={fileInput} type="file" accept=".json,.fwflow.json,.fwflow" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) importWorkflow(f); e.target.value = '' }} />
      </div>

      {/* ===== 画布 + 浮层参数面板 ===== */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0, position: 'relative' }}>
        <div
          ref={flowWrapper} style={{ flex: 1, position: 'relative', background: 'var(--bg-grad)', backgroundAttachment: 'fixed' }}
          onDragOver={onDragOver} onDrop={onDrop}
        >
          {nodes.length === 0 && specs.length > 0 && (
            <div className="fw-empty-canvas" aria-label="空工作流引导">
              <div className="fw-empty-canvas-mark"><span>＋</span></div>
              <div className="fw-empty-canvas-title">从一个节点开始编排</div>
              <div className="fw-empty-canvas-text">右键画布添加节点，或从左侧工作流面板管理项目</div>
              <button className="fw-btn fw-btn-primary" onClick={() => onAddNode(specs[0].type_id)}>
                添加第一个节点
              </button>
              <div className="fw-empty-canvas-hint">支持拖拽、连线与一键出片</div>
            </div>
          )}
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            nodesDraggable={!readOnly}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd}
            nodesConnectable={dragEnabled && !readOnly}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ maxZoom: 0.95, padding: 0.35 }}
            minZoom={0.2}
            maxZoom={1.6}
            proOptions={{ hideAttribution: true }}
            onlyRenderVisibleElements={nodes.length > 40}
            onNodeClick={(_, n) => { setSelectedNodeId(n.id); setMenu(null) }}
            onPaneClick={() => { setSelectedNodeId(null); setMenu(null); setShowHelp(false); setAddMenu(null); setConnMenu(null) }}
            onPaneContextMenu={(e) => { e.preventDefault(); setMenu(null); setAddMenu({ x: e.clientX, y: e.clientY }) }}
          >
            <Background gap={22} color="var(--canvas-dot)" />
            <Controls />
            {/* 右下角隐藏式地图：按钮点击从下方升起（条件渲染 + 入场动画；显式 absolute 相对画布容器右下） */}
            {showMiniMap && (
              <MiniMap pannable zoomable maskColor="var(--vignette)"
                style={{ position: 'absolute', right: 16, bottom: 16, top: 'auto', left: 'auto', zIndex: 22,
                  width: 208, height: 150, animation: 'fw-minimap-up .32s var(--t-ease)',
                  background: 'var(--panel-2)', border: '1px solid var(--border-strong)', borderRadius: 12, boxShadow: 'var(--shadow-lg)' }} />
            )}
            <button onClick={() => setShowMiniMap(v => !v)}
              title={showMiniMap ? '收起地图' : '展开地图'}
              style={{ position: 'absolute', right: 14, bottom: 14, zIndex: 23, display: 'flex', alignItems: 'center', gap: 5,
                background: showMiniMap ? 'var(--accent-soft)' : 'var(--panel-2)', color: 'var(--text)', border: '1px solid ' + (showMiniMap ? 'var(--accent)' : 'var(--border-strong)'),
                borderRadius: 999, padding: '5px 11px', fontSize: 11.5, fontWeight: 600, cursor: 'pointer', boxShadow: 'var(--shadow-md)', transition: 'background .16s var(--t-ease)' }}>
              <MapIcon size={12} style={{ color: 'var(--accent-strong)' }} />
              地图
            </button>
          </ReactFlow>

          {/* 空态引导：本体未安装任何节点（节点来自商城/插件市场） */}
          {specs.length === 0 && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
              pointerEvents: 'none',
            }}>
              <div style={{
                pointerEvents: 'auto', maxWidth: 420, textAlign: 'center', padding: '30px 34px',
                background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
                border: 'var(--glass-border)', borderRadius: 20, boxShadow: 'var(--glass-inner), var(--shadow-lg)',
              }}>
                <div style={{ fontSize: 15, fontWeight: 750, color: 'var(--text)', marginBottom: 6 }}>
                  <span style={{ color: 'var(--accent)', fontWeight: 800 }}>FrameWeave</span> 节点库为空
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.7, marginBottom: 16 }}>
                  本安装包只包含软件本体（画布引擎 / 授权 / 商城）。<br />
                  节点能力通过<strong>「商城」</strong>安装——安装后自动出现在左侧节点库与右键菜单。
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
                  <button className="fw-btn fw-btn-primary" onClick={() => { window.dispatchEvent(new Event('fw-open-market')) }}>
                    <><Store size={13} /> 去商城安装节点</>
                  </button>
                  <button className="fw-btn fw-btn-ghost" onClick={() => { window.dispatchEvent(new Event('fw-open-plugins')) }}>
                    <><Puzzle size={13} /> 导入节点插件</>
                  </button>
                  <button className="fw-btn fw-btn-ghost" onClick={async () => { await api.reloadUserNodes(); setSpecs(await api.specs()); pushToast('节点库已刷新', 'ok') }}>
                    <><RefreshCw size={13} /> 刷新节点</>
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 视口 vignette */}
          <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'radial-gradient(120% 100% at 50% 40%, transparent 62%, var(--vignette))', zIndex: 1 }} />

          {/* 拖线落空：新建节点类型列表（v1.3：点击=创建新节点并自动连线） */}
          {connMenu && (
            <div style={{ position: 'fixed', left: Math.min(connMenu.x, innerWidth - 312), top: Math.min(connMenu.y, innerHeight - 420), zIndex: 56, width: 296,
              background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 12, boxShadow: 'var(--shadow-lg)',
              display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
                <span style={{ color: 'var(--text-faint)' }}>新建节点</span>
                {connSrcNode && <span style={{ color: 'var(--text-dim)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 110 }}>{connSrcNode.data.title || connSrcNode.data.type_id}</span>}
                <span style={{ color: 'var(--accent-strong)' }}>{connMenu.srcType}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--text-faint)' }}>→ {connGroups.reduce((a, g) => a + g.items.length, 0)}</span>
              </div>
              <div className="fw-scroll" style={{ flex: 1, overflowY: 'auto', padding: 6 }}>
                {connGroups.length === 0 && <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--text-faint)' }}>没有接收 {connMenu.srcType} 的节点类型</div>}
                {connGroups.map((g) => (
                  <div key={g.category}>
                    <div style={{ padding: '5px 8px 3px', fontSize: 10.5, fontWeight: 700, letterSpacing: .8, color: 'var(--text-faint)' }}>{g.category}</div>
                    {g.items.map((it: any) => (
                      <button key={it.spec.type_id}
                        onClick={() => { createAndConnect(it.spec.type_id, connMenu.x - 150, connMenu.y - 20) }}
                        title={it.spec.description || ''}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', padding: '6px 8px', borderRadius: 8,
                          background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text)', fontSize: 12 }}>
                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: portDot(it.ports[0].type), flexShrink: 0 }} />
                        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 550 }}>
                          {it.spec.title}
                        </span>
                        <span style={{ color: 'var(--text-faint)', fontSize: 10.5, flexShrink: 0 }}>+{it.ports.length} 口</span>
                      </button>
                    ))}
                  </div>
                ))}
              </div>
              <div style={{ borderTop: '1px solid var(--border)', padding: '5px 8px', fontSize: 10, color: 'var(--text-faint)', textAlign: 'center' }}>点击即创建节点并自动连线 · 点画布空白关闭</div>
            </div>
          )}

          {/* 空白右键：添加节点菜单（v1.2） */}
          {addMenu && (
            <div style={{ position: 'fixed', left: addMenu.x, top: addMenu.y, zIndex: 55, width: 272, maxHeight: 430,
              background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 12, boxShadow: 'var(--shadow-lg)',
              display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
              onMouseLeave={() => setAddMenu(null)}>
              <div style={{ padding: 8, borderBottom: '1px solid var(--border)', display: 'flex', gap: 6, alignItems: 'center' }}>
                <Search size={12} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
                <input autoFocus value={addQuery} onChange={(e) => setAddQuery(e.target.value)}
                  placeholder="搜索节点…" style={{ background: 'none', border: 'none', outline: 'none', color: 'var(--text)', fontSize: 12.5, width: '100%' }} />
              </div>
              <div className="fw-scroll" style={{ flex: 1, overflowY: 'auto', padding: 6 }}>
                {addGroups.length === 0 && <div style={{ padding: 14, textAlign: 'center', fontSize: 12, color: 'var(--text-faint)' }}>没有匹配的节点</div>}
                {addGroups.map((g) => (
                  <div key={g.category}>
                    <div style={{ padding: '5px 8px 3px', fontSize: 10.5, fontWeight: 700, letterSpacing: .8, color: 'var(--text-faint)' }}>{g.category}</div>
                    {g.items.map((s: any) => (
                      <div key={s.type_id} title={s.description || ''}
                        onClick={() => { setAddMenu(null); setAddQuery(''); addNodeAt(s.type_id, addMenu.x + 150, addMenu.y + 14) }}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, cursor: 'pointer', color: 'var(--text)' }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)' }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                        <div style={{ width: 20, height: 20, borderRadius: 6, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, flexShrink: 0 }}>
                          {menuIconChar(s.type_id)}
                        </div>
                        <span style={{ fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{s.title}</span>
                        {s.gpu_required && <span className="fw-pill" style={{ marginLeft: 'auto', fontSize: 9, flexShrink: 0 }}>GPU</span>}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div style={{ borderTop: '1px solid var(--border)', padding: 6 }}>
                <div style={{ padding: '6px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 12, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
                  onClick={() => { setAddMenu(null); window.dispatchEvent(new Event('fw-open-plugins')) }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                  <Puzzle size={12} /> 导入节点插件…
                </div>
              </div>
            </div>
          )}

          {/* 快捷键面板（D6） */}
          {showHelp && (
            <div className="fw-shortcut">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <b style={{ fontSize: 13 }}>快捷键</b>
                <button onClick={() => setShowHelp(false)} style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', lineHeight: 0 }}><X size={13} /></button>
              </div>
              {[
                ['Ctrl + S', '保存工作流'], ['Ctrl + Z / Ctrl+Shift+Z', '撤销 / 重做'],
                ['Ctrl + C / Ctrl + V', '复制 / 粘贴节点'], ['Delete / Backspace', '删除选中节点'],
                ['双击节点', '打开参数面板'], ['右键节点', '运行 / 时间线 / 重置'],
                ['右键画布空白', '添加节点'],
                ['右键连线', '删除连线'], ['?', '本面板'],
                              ].map(([k, v]) => (
                <div className="row" key={k}><span style={{ color: 'var(--text-dim)' }}>{v}</span><kbd>{k}</kbd></div>
              ))}
            </div>
          )}
        </div>

        {/* 浮层参数面板（A1：不挤画布） */}
        {selectedNodeId && (() => {
          const n = nodes.find((x) => x.id === selectedNodeId)
          const spec = specs.find((s) => s.type_id === n?.data?.type_id)
          return (
            <div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 328, zIndex: 40, boxShadow: '-8px 0 24px rgba(0,0,0,.28)' }}>
              <ParamPanel
                nodeId={selectedNodeId}
                spec={spec}
                params={n?.data?.params || {}}
                run_history={n?.data?.run_history || []}
                onUpdate={updateParams}
                onClose={() => setSelectedNodeId(null)}
              />
            </div>
          )
        })()}
      </div>

      {/* ===== 右键菜单 ===== */}
      {menu && (
        <div
          style={{
            position: 'fixed', left: menu.x, top: menu.y, zIndex: 1000, minWidth: 192,
            background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
            border: 'var(--glass-border)', borderRadius: 14, boxShadow: 'var(--glass-inner), var(--shadow-lg)',
            padding: '6px 0',
          }}
          onMouseLeave={() => setMenu(null)}
        >
          {menu.nodeId ? (
            <>
              <div style={{ padding: '4px 12px', fontSize: 11, color: 'var(--text-faint)' }}>{menu.nodeTitle || menu.nodeId}</div>
              <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
              {menuItem(<Play size={13} />, '单独运行', '仅重跑此节点', () => { setMenu(null); run('selection', [menu.nodeId!]) })}
              {menuItem(<ArrowDown size={13} />, '运行下游', '从该节点向后', () => { setMenu(null); run('downstream', [menu.nodeId!]) })}
              {menuItem(<Film size={13} />, '查看时间线', '', () => { setMenu(null); setTimelineNodeId(menu.nodeId!) })}
              {menuItem(<RotateCcw size={13} />, '重置状态', '强制重算', () => { setMenu(null); resetNode(menu.nodeId!) })}
              <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
              {menuItem(<Trash2 size={13} />, '删除节点', 'Delete', () => { setMenu(null); setConfirm({ kind: 'nodes', ids: [menu.nodeId!] }) })}
            </>
          ) : menu.edgeId ? (
            <>
              <div style={{ padding: '4px 12px', fontSize: 11, color: 'var(--text-faint)' }}>连线</div>
              <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
              {menuItem(<Trash2 size={13} />, '删除连线', '', () => {
                const id = menu.edgeId
                setMenu(null)
                if (!id) return
                pushHistory()
                setEdges(eds => eds.filter(ed => ed.id !== id))
                setDirty(true)
              })}
            </>
          ) : null}
        </div>
      )}

      {/* ===== 删除确认（D5） ===== */}
      {confirm && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(6,8,16,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setConfirm(null)}>
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 360, maxWidth: '88vw', borderRadius: 16, padding: '18px 20px',
              background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
              border: '1px solid var(--border-strong)', boxShadow: 'var(--glass-inner), var(--shadow-lg)',
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Trash2 size={15} color="var(--danger)" /> 确认删除
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-dim)', marginBottom: 16, lineHeight: 1.6 }}>
              将删除 {confirm.ids.length} 个节点及其相连的全部连线。此操作可通过 Ctrl+Z 撤销。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="fw-btn fw-btn-ghost" onClick={() => setConfirm(null)}>取消</button>
              <button className="fw-btn fw-btn-danger" onClick={doDelete}><><Trash2 size={13} /> 删除</></button>
            </div>
          </div>
        </div>
      )}

      {showResults && batchNodeId && (
        <ResultPanel workflowId={workflowId} batchNodeId={batchNodeId} onRetry={retryNode} onClose={() => setShowResults(false)} />
      )}
    </div>
  )
}

// ===== 节点整列重排（内镶展开防遮挡：矩形判交 + 列压缩 + 收起缝合） =====
const NODE_W = 240
const GAP = 14
// 节点渲染高度估算（与 FlowNode 内 nodeH 一致：44 标题 + 摘要20 + 内镶表单cfgH + 端口 + 36 状态 + 12）
const estNodeH = (d: any): number => {
  const p = d?.spec?.params || []
  const hasVal = d?.params && p.some((pl: any) => { const v = (d.params || {})[pl.name]; return v !== undefined && v !== null && String(v) !== '' })
  // 与 FlowNode.summaryH 保持一致，避免自动排布高度与实际节点不一致
  const sumH = !d?.expanded && hasVal ? 28 : 0
  const cfgH = d?.expanded ? 24 + p.length * 46 + 12 : 0
  const ins = d?.spec?.inputs?.length || 0
  const outs = d?.spec?.outputs?.length || 0
  return 44 + sumH + Math.max(ins, outs, 1) * 24 + cfgH + 36 + 12
}
// 整列垂直重排：节点展开/收起后，把其下方水平区间相交的节点按原纵向顺序依次压紧（链式一次解决），
// 收起时下方节点自动上移缝合间隙。
const reflowColumn = (nds: any[], id: string): any[] => {
  const self = nds.find((n) => n.id === id)
  if (!self) return nds
  const sH = estNodeH(self.data)
  const below = nds
    .filter((n) => n.id !== id
      && n.position.y > self.position.y
      && n.position.x < self.position.x + NODE_W
      && n.position.x + NODE_W > self.position.x)
    .sort((a, b) => a.position.y - b.position.y || a.id.localeCompare(b.id))
  const out = nds.map((n) => ({ ...n }))
  // cursor 贴紧式：自动推挤过的节点收起时缝合（不留洞）；手动拖动过的节点(_manual)作为障碍推进、保持原位
  let cursor = self.position.y + sH + GAP
  for (const n of below) {
    const rn = out.find((o) => o.id === n.id)!
    const nH = estNodeH(rn.data)
    if (rn.data?._manual) {
      cursor = Math.max(cursor, rn.position.y + nH + GAP)
      continue
    }
    if (rn.position.y !== cursor) rn.position = { ...rn.position, y: cursor }
    cursor = rn.position.y + nH + GAP
  }
  return out
}

export const Canvas = memo(function Canvas(props: { workflowId: string; readOnly?: boolean }) {
  return (
    <ReactFlowProvider children={<CanvasInner {...props} />} />
  )
})
