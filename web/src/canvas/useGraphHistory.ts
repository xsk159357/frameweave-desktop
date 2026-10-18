/**
 * 画布撤销 / 重做（D3）——2026-10-19 从 Canvas.tsx 拆出（审阅 P2-9）。
 *
 * 行为与拆分前完全一致：
 *  - 历史栈上限 40 步（超出丢弃最旧）；
 *  - 每次新编辑清空重做栈；
 *  - undo/redo 后把 dirty 置回 false（与服务端已保存状态对齐）。
 * editSeqRef 一并托管：它是「工作流异步加载窗口内用户已编辑」的序号标记，
 * 由调用方在加载完成时比对，避免异步 getWorkflow 覆盖用户刚添加的节点。
 */
import { useCallback, useRef } from 'react'
import type { Edge } from '@xyflow/react'

export interface GraphSnapshot {
  nodes: any[]
  edges: Edge[]
}

export function useGraphHistory(params: {
  nodes: any[]
  edges: Edge[]
  setNodes: (n: any[]) => void
  setEdges: (e: Edge[]) => void
  setDirty: (v: boolean) => void
  limit?: number
}) {
  const { nodes, edges, setNodes, setEdges, setDirty, limit = 40 } = params
  const historyRef = useRef<GraphSnapshot[]>([])
  const futureRef = useRef<GraphSnapshot[]>([])
  const editSeqRef = useRef(0)

  const pushHistory = useCallback(() => {
    historyRef.current.push({ nodes, edges })
    if (historyRef.current.length > limit) historyRef.current.shift()
    futureRef.current = []
  }, [nodes, edges, limit])

  const undo = useCallback(() => {
    const prev = historyRef.current.pop()
    if (!prev) return
    futureRef.current.push({ nodes, edges })
    setNodes(prev.nodes)
    setEdges(prev.edges)
    setDirty(false)
  }, [nodes, edges, setNodes, setEdges, setDirty])

  const redo = useCallback(() => {
    const next = futureRef.current.pop()
    if (!next) return
    historyRef.current.push({ nodes, edges })
    setNodes(next.nodes)
    setEdges(next.edges)
    setDirty(false)
  }, [nodes, edges, setNodes, setEdges, setDirty])

  const resetHistory = useCallback(() => {
    historyRef.current = []
    futureRef.current = []
  }, [])

  return { pushHistory, undo, redo, resetHistory, editSeqRef }
}
