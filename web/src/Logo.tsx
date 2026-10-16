// FrameWeave 流光取景框：取景框 + 播放核心 + 帧线 + 极光渐变
export function Logo({ size = 40, color = 'currentColor' }: { size?: number; color?: string }) {
  const id = `fw-logo-${String(size).replace(/[^a-z0-9]/gi, '')}`
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-label="拾帧 FrameWeave" role="img">
      <defs>
        <linearGradient id={id} x1="6" y1="5" x2="43" y2="43" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={color === 'currentColor' ? '#A5B4FF' : color} />
          <stop offset="0.55" stopColor={color === 'currentColor' ? '#7C8CFF' : color} />
          <stop offset="1" stopColor={color === 'currentColor' ? '#22D3EE' : color} />
        </linearGradient>
        <filter id={`${id}-glow`} x="-35%" y="-35%" width="170%" height="170%">
          <feGaussianBlur stdDeviation="1.15" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      <path d="M13 5.5h18.7L42.5 16v19A7.5 7.5 0 0 1 35 42.5H13A7.5 7.5 0 0 1 5.5 35V13A7.5 7.5 0 0 1 13 5.5Z" fill={`url(#${id})`} opacity=".14" />
      <path d="M13 5.5h18.7L42.5 16v19A7.5 7.5 0 0 1 35 42.5H13A7.5 7.5 0 0 1 5.5 35V13A7.5 7.5 0 0 1 13 5.5Z" stroke={`url(#${id})`} strokeWidth="2.8" strokeLinejoin="round" filter={`url(#${id}-glow)`} />
      <path d="m19 17 13 7-13 7V17Z" fill={`url(#${id})`} />
      <path d="M11.5 36.5h10M25.5 36.5h9" stroke={`url(#${id})`} strokeWidth="2.4" strokeLinecap="round" />
      <path d="M32 9.5v6.5h6.5" stroke="#22D3EE" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity=".95" />
      <circle cx="40.5" cy="7.5" r="1.4" fill="#22D3EE" />
    </svg>
  )
}
