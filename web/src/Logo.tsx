// FrameWeave 品牌标志：取景框、编织路径与播放核心
export function Logo({ size = 40, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-label="拾帧 FrameWeave">
      <path d="M13 5.5h18.7L42.5 16v19A7.5 7.5 0 0 1 35 42.5H13A7.5 7.5 0 0 1 5.5 35V13A7.5 7.5 0 0 1 13 5.5Z" fill={color} opacity={0.14}/>
      <path d="M13 5.5h18.7L42.5 16v19A7.5 7.5 0 0 1 35 42.5H13A7.5 7.5 0 0 1 5.5 35V13A7.5 7.5 0 0 1 13 5.5Z" stroke={color} strokeWidth={2.8} strokeLinejoin="round"/>
      <path d="m19 17 13 7-13 7V17Z" fill={color}/>
      <path d="M11.5 36.5h10M25.5 36.5h9" stroke={color} strokeWidth={2.4} strokeLinecap="round"/>
      <path d="M32 9.5v6.5h6.5" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" opacity={0.8}/>
    </svg>
  )
}
