// Logo v2「取景切角」：取景框 + 播放三角 + 字幕线（帧/播放/剪辑三层语义，单色）
export function Logo({ size = 40, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-label="拾帧 FrameWeave">
      <path
        d="M12 6h20.6L44 17.4V36a6 6 0 0 1-6 6H12a6 6 0 0 1-6-6V12a6 6 0 0 1 6-6Z"
        fill={color}
        opacity={0.16}
      />
      <path
        d="M12 6h20.6L44 17.4V36a6 6 0 0 1-6 6H12a6 6 0 0 1-6-6V12a6 6 0 0 1 6-6Z"
        stroke={color}
        strokeWidth={3.2}
        strokeLinejoin="round"
      />
      <path d="M20 17.5 33 24l-13 6.5Z" fill={color} />
      <rect x="10" y="37.2" width="28" height="2.6" rx="1.3" fill={color} />
    </svg>
  )
}
