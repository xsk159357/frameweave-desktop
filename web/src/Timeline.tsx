// 时间线 v0：多轨（视频/音频/字幕）+ 缩放 + 播放头联动
// 数据源：选中节点的 SEGMENTS 资产（片段列表），M0 以只读预览为主
import { useEffect, useRef, useState } from 'react'
import { Play, Pause, X, Film } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

interface TimelineClip {
  index: number; start: number; end: number; duration: number
}

export function Timeline({ nodeId }: { nodeId: string }) {
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const [clips, setClips] = useState<TimelineClip[]>([])
  const [scale, setScale] = useState(20) // px per second
  const [playhead, setPlayhead] = useState(0)
  const [totalDur, setTotalDur] = useState(0)
  const playingRef = useRef<number | null>(null)

  useEffect(() => {
    const assetIds = nodeAssets[nodeId] || {}
    const segAid = Object.entries(assetIds).find(([port]) => port === 'segments')?.[1]
    if (!segAid) return
    api.asset(segAid).then((r: any) => {
      const c = r.content
      if (Array.isArray(c)) {
        setClips(c)
        const last = c[c.length - 1]
        setTotalDur(last ? last.end : 0)
      }
    }).catch(() => {})
  }, [nodeId, nodeAssets])

  const togglePlay = () => {
    if (playingRef.current) {
      clearInterval(playingRef.current); playingRef.current = null; return
    }
    playingRef.current = window.setInterval(() => {
      setPlayhead((p) => {
        const next = p + 0.1
        return next > totalDur ? 0 : next
      })
    }, 100)
  }

  const px = (t: number) => t * scale
  const seconds = Math.ceil(totalDur || 60)

  return (
    <div style={{ height: 176, borderTop: '1px solid var(--line)',
      background: 'var(--ink-900)', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      {/* 工具栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 12px',
        borderBottom: '1px solid var(--line)', background: 'transparent' }}>
        <button onClick={() => useAppStore.getState().setTimelineNodeId(null)} title="关闭时间线" style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 3, lineHeight: 0 }}><X size={13} /></button>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.14em', color: 'var(--text-faint)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Film size={11} color="var(--brass)" /> 时间线 · TIMELINE
        </span>
        <button onClick={togglePlay} className="fw-btn" style={{ height: 24, padding: '0 10px', color: 'var(--text)', fontSize: 11 }}>
          {playingRef.current ? <><Pause size={12} /> 暂停</> : <><Play size={12} /> 播放</>}
        </button>
        <input type="range" min={10} max={80} value={scale}
          onChange={(e) => setScale(+e.target.value)}
          style={{ width: 110, accentColor: 'var(--brass)' }} />
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{scale}px/s</span>
        <span style={{ flex: 1 }} />
        <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-faint)' }}>
          {nodeId} · {clips.length} 片段 · {totalDur.toFixed(1)}s
        </span>
      </div>

      {/* 标尺 + 轨道 */}
      <div style={{ flex: 1, overflowX: 'auto', position: 'relative', paddingLeft: 56 }}>
        <div style={{ width: px(seconds) + 40, minWidth: '100%', position: 'relative' }}>
          {/* 标尺 */}
          <div style={{ height: 20, borderBottom: '1px solid var(--line)',
            display: 'flex', position: 'sticky', top: 0, background: 'var(--ink-850)', zIndex: 2 }}>
            {Array.from({ length: seconds }).map((_, i) => (
              <div key={i} style={{ position: 'absolute', left: px(i), fontSize: 9, color: 'var(--text-faint)' }}>
                {i}s
              </div>
            ))}
          </div>

          {/* 视频轨 */}
          <div style={{ height: 52, margin: '3px 0', position: 'relative', background: 'var(--ink-850)', border: '1px solid var(--line)', borderRadius: 4 }}>
            {clips.map((c) => (
              <div key={c.index} title={`#${c.index} ${c.start}s-${c.end}s`}
                style={{
                  position: 'absolute', left: px(c.start), width: Math.max(px(c.duration), 8),
                  top: 7, bottom: 7, borderRadius: 6, cursor: 'pointer',
                  background: 'var(--brass-soft)',
                  border: '1px solid rgba(210,164,94,.4)', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: 9.5, color: 'var(--brass-hi)', overflow: 'hidden',
                  fontWeight: 600, fontFamily: 'var(--font-mono)',
                  transition: 'filter .15s, box-shadow var(--t-fast)',
                }}>
                #{c.index}
              </div>
            ))}
            {clips.length === 0 && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                justifyContent: 'center', fontSize: 12, color: 'var(--text-faint)' }}>
                视频轨（运行 SceneDetect 后显示片段）
              </div>
            )}
          </div>

          {/* 字幕轨 */}
          <div style={{ height: 28, position: 'relative', background: 'var(--ink-850)',
            border: '1px dashed var(--line-strong)', borderRadius: 4,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, color: 'var(--text-faint)' }}>
            字幕轨（M1 接入）
          </div>

          {/* 播放头 */}
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: px(playhead), width: 1,
            background: 'var(--brass)', zIndex: 3, pointerEvents: 'none' }}>
            <div style={{ position: 'absolute', top: -4, left: -4.5, width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: '5px solid var(--brass)' }} />
          </div>
        </div>
      </div>
    </div>
  )
}
