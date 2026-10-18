// 专业面板宿主（t11 / frontend-pro）：队列 / 素材 / 模型 三面板 + 断点续跑条 + 双击搜索 + 首登引导
//
// D-20 小视口响应式：
//  - matchMedia(max-width:900px) → body.fw-pro-sm：面板抽屉变全屏模态、FAB 更紧凑
//  - 面板打开时 body.fw-pro-focus → 画布专注（背景压暗、工作流窄栏收窄；参数/节点库互斥由 CSS 处理）
// 挂载方式：App.tsx 一行 <ProHub/>（最小 diff，不侵入顶栏/画布内部）。
import { useEffect, useState } from 'react'
import { Activity, Boxes, Brain, X } from 'lucide-react'
import { useAppStore } from '../store'
import { QueuePanel } from './QueuePanel'
import { AssetsPanel } from './AssetsPanel'
import { ModelCenter } from './ModelCenter'
import { NodeSearchMenu } from './NodeSearchMenu'
import { ResumeBar } from './ResumeBar'
import { OnboardingOverlay } from './OnboardingOverlay'

type TabId = 'queue' | 'assets' | 'models'

const TABS: { id: TabId; label: string; icon: typeof Activity }[] = [
  { id: 'queue', label: '队列', icon: Activity },
  { id: 'assets', label: '素材', icon: Boxes },
  { id: 'models', label: '模型', icon: Brain },
]

export function ProHub({ onOpenWf }: { onOpenWf?: (wid: string) => void }) {
  const session = useAppStore((s) => s.session)
  const setWorkflow = useAppStore((s) => s.setWorkflow)
  const pushToast = useAppStore((s) => s.pushToast)
  const [tab, setTab] = useState<TabId | null>(null)
  const [sm, setSm] = useState(false)

  // 左侧图标栏入口（App 派发 fw-open-pro）
  useEffect(() => {
    const h = (ev: Event) => {
      const d = (ev as CustomEvent).detail || {}
      if (d.tab === 'queue' || d.tab === 'assets' || d.tab === 'models') setTab(d.tab)
    }
    window.addEventListener('fw-open-pro', h)
    return () => window.removeEventListener('fw-open-pro', h)
  }, [])

  // D-20：小视口检测（900px 为节点库+画布可用宽度阈值）
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 900px)')
    const upd = () => setSm(mq.matches)
    upd()
    mq.addEventListener('change', upd)
    return () => mq.removeEventListener('change', upd)
  }, [])

  // 面板打开/关闭时切换 body 类（画布专注 + 小视口抽屉全屏样式）
  useEffect(() => {
    const body = document.body
    body.classList.toggle('fw-pro-focus', !!tab)
    body.classList.toggle('fw-pro-sm', sm)
    return () => { body.classList.remove('fw-pro-focus', 'fw-pro-sm') }
  }, [tab, sm])

  if (!session) return null

  const openWf = (wid: string) => {
    setWorkflow(wid, '')
    setTab(null)
    if (onOpenWf) onOpenWf(wid)
  }

  const switchTab = (id: TabId) => setTab((t) => (t === id ? null : id))

  return (
    <>
      <NodeSearchMenu />
      <ResumeBar />
      <OnboardingOverlay />

      {/* 右侧抽屉（小视口自动全屏） */}
      {tab && (
        <div className="fw-pro-drawer" style={{
          position: 'fixed', top: 46, right: 0, bottom: 26, width: 'min(432px, 100vw)', zIndex: 500,
          borderLeft: '1px solid var(--line-strong)', background: 'var(--ink-850)',
          backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
          boxShadow: 'var(--shadow-2)', display: 'flex', flexDirection: 'column',
          animation: 'fw-drawer-in .22s var(--t-ease) both',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '0 10px', height: 40, borderBottom: '1px solid var(--line)' }}>
            {TABS.map((t) => (
              <button key={t.id} onClick={() => switchTab(t.id)} title={'切换到' + t.label}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 10px', height: 40, cursor: 'pointer',
                  border: 0, borderBottom: '1px solid ' + (tab === t.id ? 'var(--brass)' : 'transparent'),
                  background: 'transparent',
                  color: tab === t.id ? 'var(--paper)' : 'var(--paper-faint)', fontSize: 11, fontWeight: 600, letterSpacing: '.04em',
                }}>
                <t.icon size={12} /> {t.label}
              </button>
            ))}
            <span style={{ flex: 1 }} />
            <button onClick={() => setTab(null)} title="关闭"
              style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', padding: 5 }}>
              <X size={16} />
            </button>
          </div>
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            {tab === 'queue' && <QueuePanel onOpenWf={openWf} />}
            {tab === 'assets' && <AssetsPanel />}
            {tab === 'models' && <ModelCenter />}
          </div>
        </div>
      )}
    </>
  )
}

export type ProHubTab = TabId