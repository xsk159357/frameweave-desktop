// 设置面板（v1.3）：拖线连接开关 / 亮暗主题 / 即时生效 + 本机持久
import { X } from 'lucide-react'
import { useAppStore } from './store'

export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const settings = useAppStore((s) => s.settings)
  const setSettings = useAppStore((s) => s.setSettings)
  const prev = typeof document !== 'undefined' ? getComputedStyle(document.documentElement).getPropertyValue('--panel').trim() : '#16181f'
  const themes = [
    { id: 'dark' as const, name: '墨色', desc: 'INK · 编辑台默认', bg: '#0B0C0E', panel: '#101113', text: '#EDE8DE' },
    { id: 'light' as const, name: '纸色', desc: 'PAPER · 白天剪辑', bg: '#F4F1EA', panel: '#FCFAF5', text: '#17181A' },
  ]
  void prev
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 150, background: 'var(--modal-backdrop)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 470, maxWidth: '92vw', background: 'var(--ink-800)', border: '1px solid var(--line-strong)', borderRadius: 10, boxShadow: 'var(--shadow-2)', padding: '18px 20px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <span style={{ fontSize: 14, fontWeight: 650, flex: 1 }}>设置 · PREFERENCES</span>
          <button onClick={onClose} aria-label="关闭设置" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 4, display: 'inline-flex' }}>
            <X size={16} />
          </button>
        </div>

        {/* 编辑器 */}
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.14em', color: 'var(--text-faint)', marginBottom: 8 }}>编辑器</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--ink-700)', border: '1px solid var(--line)', borderRadius: 6, padding: '10px 12px' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>从端口牵线连接</div>
              <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>拖线与拖到空白处自动弹出的可链接列表共享此开关</div>
            </div>
            <button role="switch" aria-checked={settings.dragEnabled} onClick={() => setSettings({ dragEnabled: !settings.dragEnabled })}
              title={settings.dragEnabled ? '点击关闭拖线' : '点击开启拖线'}
              style={{ width: 40, height: 22, borderRadius: 999, border: 'none', cursor: 'pointer', flexShrink: 0, background: settings.dragEnabled ? 'var(--brass)' : 'var(--ink-400)', position: 'relative', transition: 'background .18s' }}>
              <span style={{ position: 'absolute', top: 2, left: settings.dragEnabled ? 20 : 2, width: 18, height: 18, borderRadius: '50%', background: '#FFF8E9', boxShadow: '0 1px 3px rgba(0,0,0,.35)', transition: 'left .18s' }} />
            </button>
          </div>
        </div>

        {/* 外观 */}
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.14em', color: 'var(--text-faint)', marginBottom: 8 }}>外观</div>
          <div style={{ display: 'flex', gap: 10 }}>
            {themes.map((t) => (
              <button key={t.id} onClick={() => setSettings({ theme: t.id })}
                style={{ flex: 1, textAlign: 'left', cursor: 'pointer', border: '1px solid ' + (settings.theme === t.id ? 'var(--brass)' : 'var(--line)'), borderRadius: 6, padding: 12, background: settings.theme === t.id ? 'var(--brass-soft)' : 'var(--ink-700)', outline: 'none' }}>
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
          <span>修改即时生效，无需重启；偏好保存在本机。</span>
        </div>
      </div>
    </div>
  )
}
