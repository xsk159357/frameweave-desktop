// 主画布：ReactFlow 节点图 + 顶部工具栏 + 右侧参数面板
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap,
  addEdge, useNodesState, useEdgesState, type Connection, type Edge,
} from '@xyflow/react'
import { api, connectWS } from './api'
import { useAppStore } from './store'
import { canConnect, type FlowNodeData } from './types'
import { FlowNode } from './nodes/FlowNode'
import { ParamPanel } from './ParamPanel'
import { ResultPanel } from './ResultPanel'

const nodeTypes = { flow: FlowNode }

let nodeSeq = 0

export function Canvas({ workflowId }: { workflowId: string }) {
  const dark = useAppStore((s) => s.dark)
  const specs = useAppStore((s) => s.specs)
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

  const [nodes, setNodes, onNodesChange] = useNodesState<any>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [autoLayout, setAutoLayout] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showResults, setShowResults] = useState(false)
  const [batchNodeId, setBatchNodeId] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number; nodeId: string } | null>(null)
  const savedRef = useRef(false)
  const flowWrapper = useRef<HTMLDivElement>(null)

  // 节点状态注入画布
  useEffect(() => {
    setNodes((nds) => nds.map((n) => ({
      ...n,
      data: {
        ...n.data,
        status: nodeStatus[n.id] || 'pending',
        error: nodeError[n.id] || '',
        progress: nodeProgress[n.id] || 0,
        asset_ids: nodeAssets[n.id] || {},
      },
    })))
  }, [nodeStatus, nodeError, nodeProgress, nodeAssets, setNodes])

  // WebSocket 事件
  useEffect(() => {
    const ws = connectWS(applyNodeEvent)
    return () => ws.close()
  }, [applyNodeEvent])

  // 自动布局
  useEffect(() => {
    if (!autoLayout || nodes.length === 0) return
    // 简单分层布局：按输入依赖深度 y，按序号 x
    const depth = new Map<string, number>()
    const compute = (id: string): number => {
      if (depth.has(id)) return depth.get(id)!
      let d = 0
      for (const e of edges) {
        if (e.target === id) d = Math.max(d, compute(e.source) + 1)
      }
      depth.set(id, d)
      return d
    }
    const width = new Map<string, number>()
    setNodes((nds) => {
      const counts = new Map<number, number>()
      const positioned = nds.map((n) => {
        const d = compute(n.id)
        const c = counts.get(d) || 0
        counts.set(d, c + 1)
        return { ...n, position: { x: 40 + d * 260, y: 40 + c * 140 } }
      })
      return positioned
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoLayout])

  // 添加节点
  const onAddNode = useCallback((typeId: string) => {
    const spec = specs.find((s) => s.type_id === typeId)
    if (!spec) return
    nodeSeq++
    const id = 'n' + nodeSeq + '_' + Date.now().toString(36)
    const data: FlowNodeData = {
      type_id: typeId, title: spec.title, category: spec.category,
      params: Object.fromEntries(spec.params.map(p => [p.name, p.default ?? ''])),
      status: 'pending', progress: 0, asset_ids: {},
      inputs: {}, outputs: {}, spec,
    }
    setNodes((nds) => [...nds, {
      id, type: 'flow', position: { x: 80 + Math.random() * 200, y: 60 + Math.random() * 120 },
      data,
    }])
  }, [specs, setNodes])

  // 连线
  const onConnect = useCallback((conn: Connection) => {
    // 端口类型兼容检查
    const srcNode = nodes.find(n => n.id === conn.source)
    const dstNode = nodes.find(n => n.id === conn.target)
    if (!srcNode || !dstNode) return
    const srcSpec = specs.find(s => s.type_id === srcNode.data.type_id)
    const dstSpec = specs.find(s => s.type_id === dstNode.data.type_id)
    const srcPort = srcSpec?.outputs.find(p => p.name === conn.sourceHandle) || srcSpec?.outputs[0]
    const dstPort = dstSpec?.inputs.find(p => p.name === conn.targetHandle) || dstSpec?.inputs[0]
    if (srcPort && dstPort && !canConnect(srcPort.type, dstPort.type)) return
    setEdges((eds) => addEdge({
      ...conn,
      style: { stroke: '#4f6ef7', strokeWidth: 2 },
      label: srcPort && dstPort ? srcPort.type + '→' + dstPort.type : '',
      labelStyle: { fontSize: 10, fill: '#8b93a9' },
    }, eds))
    // 记录节点输入
    setNodes((nds) => nds.map(n => n.id === conn.target ? {
      ...n, data: { ...n.data, inputs: { ...n.data.inputs, [conn.targetHandle || 'in']: conn.source } },
    } : n))
  }, [nodes, specs, setEdges, setNodes])

  // 更新节点参数
  const updateParams = useCallback((nodeId: string, patch: Record<string, string>) => {
    setNodes((nds) => nds.map((n) => n.id === nodeId ? {
      ...n, data: { ...n.data, params: { ...n.data.params, ...patch } },
    } : n))
    savedRef.current = false
  }, [setNodes])

  // 保存
  const save = useCallback(async () => {
    setSaving(true)
    try {
      const nds = nodes.map((n) => ({
        id: n.id, type: n.data.type_id, x: n.position.x, y: n.position.y,
        params: n.data.params,
      }))
      const eds = edges.map((e) => ({
        source: e.source, target: e.target,
        sourceHandle: e.sourceHandle, targetHandle: e.targetHandle,
      }))
      await api.saveWorkflow(workflowId, nds, eds)
      savedRef.current = true
    } catch (e: any) { console.error('保存失败', e) }
    finally { setSaving(false) }
  }, [nodes, edges, workflowId])

  // 运行
  const run = useCallback(async (mode: string, nodeIds?: string[]) => {
    setRunning(true)
    setLastError('')
    try {
      const nds = nodes.map((n) => ({
        id: n.id, type: n.data.type_id, x: n.position.x, y: n.position.y, params: n.data.params,
      }))
      const eds = edges.map((e) => ({
        source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle,
      }))
      await api.saveWorkflow(workflowId, nds, eds)
      await api.runWorkflow(workflowId, mode, nodeIds || [])
    } catch (e: any) { console.error('运行失败', e); setLastError(String(e.message || e)); setRunning(false) }
    // 成功时 running 由 execution_done 事件置 false
  }, [nodes, edges, workflowId, setRunning, setLastError])

  // 重试单个节点（selection 模式，只重跑该节点，上游 cached 复用）
  const retryNode = useCallback(async (nodeId: string) => {
    setRunning(true)
    setLastError('')
    try {
      await api.runWorkflow(workflowId, 'selection', [nodeId])
    } catch (e: any) {
      console.error('重试失败', e)
      setLastError(String(e.message || e))
      setRunning(false)
    }
  }, [workflowId, setRunning, setLastError])
  // 重置节点状态（右键菜单）
  const resetNode = useCallback(async (nodeId: string) => {
    try {
      await api.resetNode(workflowId, nodeId, true)
    } catch (e: any) {
      console.error('重置失败', e)
      setLastError(String(e.message || e))
    }
  }, [workflowId, setLastError])


  // 打开已有工作流（从 workflowId 加载）
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
            asset_ids: n.asset_ids || {},
            inputs: n.inputs || {}, outputs: n.outputs || {},
            spec,
          } as FlowNodeData,
        }
      })
      const eds = (wf.edges || []).map((e: any) => ({
        id: 'e_' + e.source + '_' + e.target, source: e.source, target: e.target,
        sourceHandle: e.sourceHandle, targetHandle: e.targetHandle,
        style: { stroke: '#4f6ef7', strokeWidth: 2 },
      }))
      setNodes(nds); setEdges(eds)
    }).catch(() => { /* 新工作流为空 */ })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId])

  // 快捷键
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save() }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const el = document.activeElement as HTMLElement
        if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save])

  // 在父级暴露添加节点方法
  useEffect(() => {
    ;(window as any).__fwAddNode = onAddNode
    ;(window as any).__fwNodeMenu = (m: { x: number; y: number; nodeId: string }) => setMenu(m)
  }, [onAddNode])

  // 执行结束（running true→false）自动展示批量结果面板
  const prevRunning = useRef(false)
  useEffect(() => {
    if (prevRunning.current && !running) {
      const bn = nodes.find((n) => n.data.type_id === 'core/batch_render')
      if (bn) { setBatchNodeId(bn.id); setShowResults(true) }
    }
    prevRunning.current = running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  const toolbar = {
    display: 'flex', gap: 8, alignItems: 'center', padding: '8px 12px',
    borderBottom: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
    background: dark ? '#1a1d29' : '#fff', flexShrink: 0,
  }
  const btn = {
    padding: '6px 12px', borderRadius: 6, border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
    background: dark ? '#22263a' : '#f4f6fb', color: dark ? '#e8eaf2' : '#1f2430', fontSize: 13,
    cursor: 'pointer',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', flex: 1 }}>
      <div style={toolbar}>
        <button style={{ ...btn, background: '#4f6ef7', border: 'none', color: '#fff' }} onClick={() => run('all')}>
          {running ? '⏳ 执行中...' : '▶ 一键出片'}
        </button>
        <button style={btn} onClick={() => run('downstream', )}>仅下游</button>
        <button style={btn} onClick={save}>{saving ? '保存中...' : '💾 保存'}</button>
        {running && (() => {
          const vals = Object.values(nodeProgress).filter((v) => v !== undefined && v !== null)
          const avg = vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : 0
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, maxWidth: 260 }}>
              <div style={{ flex: 1, height: 8, background: dark ? '#2c3142' : '#eef1f7', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: (avg * 100) + '%', background: '#4f6ef7', transition: 'width .3s', borderRadius: 4 }} />
              </div>
              <span style={{ fontSize: 11, color: '#8b93a9', whiteSpace: 'nowrap' }}>{(avg * 100).toFixed(0)}%</span>
            </div>
          )
        })()}
        {lastError && (
          <span style={{ fontSize: 11, color: '#e5484d', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={lastError}>
            ⚠ {lastError}
          </span>
        )}
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: dark ? '#e8eaf2' : '#1f2430' }}>
          <input type="checkbox" checked={autoLayout} onChange={(e) => setAutoLayout(e.target.checked)} />
          自动布局
        </label>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#8b93a9' }}>Ctrl+S 保存 · 双击节点打开时间线</span>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div ref={flowWrapper} style={{ flex: 1, position: 'relative', background: dark ? '#12141c' : '#f0f2f8' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, n) => { setSelectedNodeId(n.id); setMenu(null) }}
            onPaneClick={() => { setSelectedNodeId(null); setMenu(null) }}
          >
            <Background gap={20} color={dark ? '#2c3142' : '#e2e6ee'} />
            <Controls />
            <MiniMap pannable zoomable style={{ background: dark ? '#1a1d29' : '#fff' }} />
          </ReactFlow>
        </div>
        {selectedNodeId && (() => {
          const n = nodes.find((x) => x.id === selectedNodeId)
          const spec = specs.find((s) => s.type_id === n?.data?.type_id)
          return <ParamPanel nodeId={selectedNodeId} spec={spec} params={n?.data?.params || {}} run_history={n?.data?.run_history || []} onUpdate={updateParams} />
        })()}
      </div>
      {menu && (() => {
        const n = nodes.find((x) => x.id === menu.nodeId)
        const itemStyle = {
          padding: '6px 12px', fontSize: 13, cursor: 'pointer', color: dark ? '#e8eaf2' : '#1f2430',
        }
        const itemHover = { background: dark ? '#2c3142' : '#eef1f7' }
        return (
          <div
            style={{
              position: 'fixed', left: menu.x, top: menu.y, zIndex: 1000, minWidth: 170,
              background: dark ? '#1a1d29' : '#fff', border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
              borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,.18)', padding: '4px 0',
            }}
            onMouseLeave={() => setMenu(null)}
          >
            <div style={{ padding: '4px 12px', fontSize: 11, color: '#8b93a9' }}>{n?.data?.title || menu.nodeId}</div>
            <div style={{ height: 1, background: dark ? '#2c3142' : '#e2e6ee', margin: '2px 0' }} />
            <div style={{ ...itemStyle, ...itemHover }} onClick={() => { setMenu(null); run('selection', [menu.nodeId]) }}>
              ▶ 单独运行
              <span style={{ fontSize: 10, color: '#8b93a9', marginLeft: 6 }}>仅重跑此节点</span>
            </div>
            <div style={itemStyle} onClick={() => { setMenu(null); run('downstream', [menu.nodeId]) }}
              onMouseEnter={(e) => { e.currentTarget.style.background = itemHover.background }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
              ⬇ 运行下游
              <span style={{ fontSize: 10, color: '#8b93a9', marginLeft: 6 }}>从该节点向后</span>
            </div>
            <div style={itemStyle}
              onClick={() => { setMenu(null); resetNode(menu.nodeId) }}
              onMouseEnter={(e) => { e.currentTarget.style.background = itemHover.background }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
              ↺ 重置状态
              <span style={{ fontSize: 10, color: '#8b93a9', marginLeft: 6 }}>强制下次重算</span>
            </div>
          </div>
        )
      })()}
      {showResults && batchNodeId && (
        <ResultPanel
          workflowId={workflowId}
          batchNodeId={batchNodeId}
          onRetry={retryNode}
          onClose={() => setShowResults(false)}
        />
      )}
    </div>
  )
}
