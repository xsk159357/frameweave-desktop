// 画布节点搜索菜单（D-15 / D-16 / t11）
// - D-15：画布空白 onDoubleClick 触发（document 级 dblclick 监听 .react-flow__pane，
//   不侵入 Canvas.tsx）；搜索框自动聚焦，↑/↓ 选择，Enter 添加，Esc 关闭
// - D-16：统一搜索词元 = title + type_id + description + search_aliases
//   （types.specMatchesQuery），大小写/空格不敏感，支持中英文别名
// - D-17：结果按 CATEGORY_ORDER 共享常量排序（与 manifest 一致）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { Search, X, CornerDownLeft } from 'lucide-react'
import { useAppStore } from '../store'
import { specMatchesQuery, categoryNormal, categoryColor, CATEGORY_ORDER } from '../types'

const BLOCK = '.react-flow__node, .fw-pro-search, .react-flow__controls, .react-flow__minimap, .react-flow__panel, .react-flow__handle'
const OUTER = '.fw-pro-drawer, .fw-onboarding, .fw-resume-bar, .fw-auth-modal, .fw-update-overlay, .fw-profile-modal'

function isCanvasPane(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !(el instanceof HTMLElement)) return false
  if (el.closest(BLOCK)) return false
  if (el.closest(OUTER)) return false
  // 画布舞台内的「非节点/非控件」区域都视为空白：含 ReactFlow pane/背景网格，
  // 以及空态/加载覆盖层（真实点击落在覆盖层元素上时同样要触发，D-15 双视角验证））
  return !!el.closest('.fw-canvas-stage')
}

export function NodeSearchMenu() {
  const specs = useAppStore((s) => s.specs)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // D-15：画布空白双击 → 打开菜单（自动聚焦）
  useEffect(() => {
    const onDbl = (e: MouseEvent) => {
      if (!isCanvasPane(e.target)) return
      e.preventDefault()
      setQ('')
      setActive(0)
      setPos({ x: e.clientX, y: e.clientY })
    }
    document.addEventListener('dblclick', onDbl, true)
    return () => document.removeEventListener('dblclick', onDbl, true)
  }, [])

  // 聚焦 + 关闭快捷键
  useEffect(() => {
    if (!pos) return
    requestAnimationFrame(() => inputRef.current?.focus())
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPos(null)
    }
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement
      if (el && !el.closest('.fw-pro-search')) setPos(null)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown) }
  }, [pos])

  // 程序化入口（供 Canvas/其他面板调用，如快捷键 'N' / 添加菜单统一）
  useEffect(() => {
    ;(window as any).__fwOpenNodeSearch = (x: number, y: number) => {
      setQ('')
      setActive(0)
      setPos({ x, y })
    }
    return () => { delete (window as any).__fwOpenNodeSearch }
  }, [])

  // D-16/D-17：统一搜索 + 分类顺序
  const results = useMemo(() => {
    if (!pos) return []
    const hit = specs
      .filter((s) => !s.deprecated && specMatchesQuery(s, q))
      .sort((a, b) => {
        const ca = CATEGORY_ORDER.indexOf(categoryNormal(a.category))
        const cb = CATEGORY_ORDER.indexOf(categoryNormal(b.category))
        if (ca !== cb) return (ca < 0 ? 99 : ca) - (cb < 0 ? 99 : cb)
        return a.title.localeCompare(b.title, 'zh-CN')
      })
    return hit.slice(0, 40)
  }, [specs, q, pos])

  useEffect(() => {
    setActive(0)
    if (listRef.current) listRef.current.scrollTop = 0
  }, [q, pos])

  if (!pos) return null

  const add = (typeId: string) => {
    ;(window as any).__fwAddNode?.(typeId)
    setPos(null)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (results[active]) add(results[active].type_id) }
    else if (e.key === 'Escape') { e.preventDefault(); setPos(null) }
  }

  // 视口内钳制（避免菜单溢出窗口底部/右侧）
  const W = 300, H = Math.min(360, 26 + results.length * 34 + 20)
  const x = Math.min(pos.x, window.innerWidth - W - 12)
  const yTop = pos.y + 8
  const y = yTop + H > window.innerHeight - 10 ? Math.max(8, pos.y - H - 8) : yTop

  return (
    <div className="fw-pro-search" style={{
      position: 'fixed', left: x, top: y, width: W, zIndex: 3000,
      border: '1px solid var(--line-strong)', borderRadius: 6,
      background: 'var(--ink-700)',
      boxShadow: 'var(--shadow-2)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
      animation: 'fw-pop-in .16s var(--t-ease) both',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 10px', borderBottom: '1px solid var(--line)' }}>
        <Search size={13} color="var(--text-faint)" />
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="搜索节点（标题 / 类型 / 别名）…"
          style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', color: 'var(--text)', fontSize: 12.5 }}
        />
        <button onClick={() => setPos(null)} style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', padding: 2, lineHeight: 0 }}><X size={13} /></button>
      </div>
      <div ref={listRef} className="fw-scroll" style={{ maxHeight: H - 44, overflow: 'auto', padding: 5 }}>
        {results.length === 0 && (
          <div style={{ padding: '18px 10px', textAlign: 'center', color: 'var(--text-faint)', fontSize: 11.5 }}>
            {q ? '无匹配节点（试试中文名 / type_id / 别名）' : '输入关键词搜索节点…'}
          </div>
        )}
        {results.map((s, i) => {
          const col = categoryColor(s.category)
          return (
            <div key={s.type_id}
              onMouseEnter={() => setActive(i)}
              onClick={() => add(s.type_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 4, cursor: 'pointer',
                background: i === active ? 'var(--brass-soft)' : 'transparent',
                border: i === active ? '1px solid rgba(210,164,94,.35)' : '1px solid transparent',
                fontSize: 12,
              }}>
              <span style={{
                width: 22, height: 22, borderRadius: 4, flexShrink: 0, background: col + '22', color: col,
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, border: '1px solid ' + col + '44',
              }}>
                {(s.type_id.split('/').pop() || s.title || '?').replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '')[0]?.toUpperCase() || '?'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</div>
                <div style={{ fontSize: 9.5, color: 'var(--text-faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.type_id}{s.search_aliases && s.search_aliases.length > 0 ? ' · ' + s.search_aliases.slice(0, 3).join(' / ') : ''}
                </div>
              </div>
              {i === active && <CornerDownLeft size={11} color="var(--accent-strong)" style={{ flexShrink: 0 }} />}
            </div>
          )
        })}
      </div>
      <div style={{ padding: '5px 10px', borderTop: '1px solid var(--line)', fontSize: 9, color: 'var(--text-faint)', display: 'flex', gap: 10, fontFamily: 'var(--font-mono)' }}>
        <span>↑↓ 选择</span><span>Enter 添加</span><span>Esc 关闭</span>
      </div>
    </div>
  )
}