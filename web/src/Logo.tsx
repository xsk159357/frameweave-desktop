// 拾帧 FrameWeave · 墨织品牌标记
// 取景框 + 破框的 F：Frame（框）+ F（FrameWeave），黄铜是唯一强调。
import { useId } from 'react'

export function Logo({
  size = 40,
  color = 'currentColor',
  accent = '#D2A45E',
  tile = true,
}: {
  size?: number
  color?: string
  accent?: string
  tile?: boolean
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const gid = `fw-brass-${uid}`
  const glowId = `fw-glow-${uid}`
  const bg = '#0B0C0E'

  const frame = (
    <rect x="11.5" y="11.5" width="41" height="41" rx="3"
      fill="none" stroke={color} strokeOpacity={0.9} strokeWidth={2.6} />
  )
  const fPath = 'M20 16h31v9H31v7h13.5v8H31v13H20V16Z'

  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none"
      aria-label="拾帧 FrameWeave" role="img"
      style={{ display: 'block', flexShrink: 0 }}>
      <defs>
        <linearGradient id={gid} x1="18" y1="14" x2="52" y2="54" gradientUnits="userSpaceOnUse">
          <stop stopColor={accent} stopOpacity=".92" />
          <stop offset=".52" stopColor={accent} />
          <stop offset="1" stopColor={accent} stopOpacity=".72" />
        </linearGradient>
        <radialGradient id={glowId} cx="20%" cy="10%" r="95%">
          <stop stopColor={accent} stopOpacity=".14" />
          <stop offset=".55" stopColor={accent} stopOpacity="0" />
        </radialGradient>
      </defs>
      {tile && <rect width="64" height="64" rx="14" fill={bg} />}
      {tile && <rect width="64" height="64" rx="14" fill={`url(#${glowId})`} />}
      {tile && (
        <rect x="1.25" y="1.25" width="61.5" height="61.5" rx="12.9"
          fill="none" stroke={color} strokeOpacity=".10" strokeWidth="1" />
      )}
      {frame}
      {tile && <path d={fPath} fill={bg} stroke={bg} strokeWidth={5} strokeLinejoin="miter" />}
      <path d={fPath} fill={`url(#${gid})`} />
    </svg>
  )
}
