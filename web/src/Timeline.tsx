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
    <div style={{ height: 200, borderTop: 'var(--glass-border)',
      background: 'var(--glass)',       display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
      {/* 工具栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 14px',
        borderBottom: '1px solid var(--border)', background: 'transparent' }}>
        <button onClick={() => useAppStore.getState().setTimelineNodeId(null)} title="关闭时间线" style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 3, lineHeight: 0 }}><X size={13} /></button>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-dim)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Film size={12} color="var(--accent)" /> 时间线
        </span>
        <button onClick={togglePlay} className="fw-btn" style={{ padding: '4px 13px', color: 'var(--text)' }}>
          {playingRef.current ? <><Pause size={12} /> 暂停</> : <><Play size={12} /> 播放</>}
        </button>
        <input type="range" min={10} max={80} value={scale}
          onChange={(e) => setScale(+e.target.value)}
          style={{ width: 120, accentColor: 'var(--accent)' }} />
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{scale}px/s</span>
        <span style={{ flex: 1 }} />
        <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-faint)' }}>
          {nodeId} · {clips.length} 片段 · {totalDur.toFixed(1)}s
        </span>
      </div>

      {/* 标尺 + 轨道 */}
      <div style={{ flex: 1, overflowX: 'auto', position: 'relative', paddingLeft: 60 }}>
        <div style={{ width: px(seconds) + 40, minWidth: '100%', position: 'relative' }}>
          {/* 标尺 */}
          <div style={{ height: 20, borderBottom: '1px solid var(--border)',
            display: 'flex', position: 'sticky', top: 0, background: 'var(--bg-panel-2)', zIndex: 2 }}>
            {Array.from({ length: seconds }).map((_, i) => (
              <div key={i} style={{ position: 'absolute', left: px(i), fontSize: 9, color: 'var(--text-faint)' }}>
                {i}s
              </div>
            ))}
          </div>

          {/* 视频轨 */}
          <div style={{ height: 54, margin: '4px 0', position: 'relative', background: 'var(--glass)',             border: 'var(--glass-border)', borderRadius: 12, boxShadow: 'var(--glass-inner), var(--shadow-sm)' }}>
            {clips.map((c) => (
              <div key={c.index} title={`#${c.index} ${c.start}s-${c.end}s`}
                style={{
                  position: 'absolute', left: px(c.start), width: Math.max(px(c.duration), 8),
                  top: 7, bottom: 7, borderRadius: 6, cursor: 'pointer',
                  background: 'rgba(139,147,255,.16)',
                  border: '1px solid rgba(139,147,255,.4)', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', fontSize: 10, color: 'var(--accent-strong)', overflow: 'hidden',
                  fontWeight: 600, boxShadow: '0 0 10px rgba(108,140,255,.22), inset 0 1px 0 rgba(255,255,255,.14)',
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
          <div style={{ height: 30, position: 'relative', background: 'var(--bg-panel-2)',
            border: '1px dashed var(--border-strong)', borderRadius: 6,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, color: 'var(--text-faint)' }}>
            字幕轨（M1 接入）
          </div>

          {/* 播放头 */}
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: px(playhead), width: 1.5,
            background: 'var(--accent)', boxShadow: '0 0 8px rgba(139,147,255,.45)', zIndex: 3, pointerEvents: 'none' }}>
            <div style={{ position: 'absolute', top: -4, left: -4.5, width: 0, height: 0, borderLeft: '5px solid transparent', borderRight: '5px solid transparent', borderTop: '6px solid var(--accent)' }} />
          </div>
        </div>
      </div>
    </div>
  )
}
