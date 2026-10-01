// 设置面板（v1.3）：拖线连接开关 / 亮暗主题 / 即时生效 + 本机持久
import { X } from 'lucide-react'
import { useAppStore } from './store'

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const settings = useAppStore((s) => s.settings)
  const setSettings = useAppStore((s) => s.setSettings)
  const prev = typeof document !== 'undefined' ? getComputedStyle(document.documentElement).getPropertyValue('--panel').trim() : '#16181f'
  const themes = [
    { id: 'dark' as const, name: '暗色', desc: '霜幕 · 专业深色', bg: '#0e0f14', panel: '#16181f', text: '#e8eaf1' },
    { id: 'light' as const, name: '亮色', desc: '明亮 · 清爽浅色', bg: '#f4f5f8', panel: '#ffffff', text: '#1c1e26' },
  ]
  void prev
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 150, background: 'rgba(0,0,0,.55)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 470, maxWidth: '92vw', background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 16, boxShadow: 'var(--shadow-lg)', padding: '18px 20px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <span style={{ fontSize: 15, fontWeight: 700, flex: 1 }}>设置</span>
          <button onClick={onClose} aria-label="关闭设置" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 4, display: 'inline-flex' }}>
            <X size={16} />
          </button>
        </div>

        {/* 编辑器 */}
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, color: 'var(--text-faint)', marginBottom: 8 }}>编辑器</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '10px 12px' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>从端口牵线连接</div>
              <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>拖线与拖到空白处自动弹出的可链接列表共享此开关</div>
            </div>
            <button role="switch" aria-checked={settings.dragEnabled} onClick={() => setSettings({ dragEnabled: !settings.dragEnabled })}
              title={settings.dragEnabled ? '点击关闭拖线' : '点击开启拖线'}
              style={{ width: 40, height: 22, borderRadius: 999, border: 'none', cursor: 'pointer', flexShrink: 0, background: settings.dragEnabled ? 'var(--accent)' : 'var(--border-strong)', position: 'relative', transition: 'background .18s' }}>
              <span style={{ position: 'absolute', top: 2, left: settings.dragEnabled ? 20 : 2, width: 18, height: 18, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,.3)', transition: 'left .18s' }} />
            </button>
          </div>
        </div>

        {/* 外观 */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1, color: 'var(--text-faint)', marginBottom: 8 }}>外观</div>
          <div style={{ display: 'flex', gap: 10 }}>
            {themes.map((t) => (
              <button key={t.id} onClick={() => setSettings({ theme: t.id })}
                style={{ flex: 1, textAlign: 'left', cursor: 'pointer', background: 'var(--panel)', border: '1px solid ' + (settings.theme === t.id ? 'var(--accent)' : 'var(--border)'), borderRadius: 12, padding: 12, outline: 'none' }}>
                <div style={{ display: 'flex', gap: 5, marginBottom: 8 }}>
                  <div style={{ width: 30, height: 24, borderRadius: 6, background: t.bg, border: '1px solid rgba(0,0,0,.15)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <span style={{ width: 16, height: 4, borderRadius: 3, background: t.panel, border: '1px solid rgba(0,0,0,.12)' }} />
                  </div>
                </div>
                <div style={{ fontSize: 13, fontWeight: 650 }}>{t.name}</div>
                <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 2 }}>{t.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
          <span>⚡ 修改即时生效无需重启，自动保存在本机</span>
        </div>
      </div>
    </div>
  )
}
