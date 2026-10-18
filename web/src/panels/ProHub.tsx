// 专业面板宿主（t11 / frontend-pro）：队列 / 素材 / 模型 三面板 + 断点续跑条 + 双击搜索 + 首登引导
//
// D-20 小视口响应式：
//  - matchMedia(max-width:900px) → body.fw-pro-sm：面板抽屉变全屏模态、FAB 更紧凑
//  - 面板打开时 body.fw-pro-focus → 画布专注（背景压暗、工作流窄栏收窄；参数/节点库互斥由 CSS 处理）
// 挂载方式：App.tsx 一行 <ProHub/>（最小 diff，不侵入顶栏/画布内部）。
import { useEffect, useRef, useState } from 'react'
import { Activity, Boxes, Brain, X, Minus } from 'lucide-react'
import { useAppStore } from '../store'
import { panelsApi, type QueueSnapshot } from '../panelsApi'
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
  const [queue, setQueue] = useState<QueueSnapshot | null>(null)
  const [fabOpen, setFabOpen] = useState(true)

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

  // FAB 队列徽标：面板未打开时 5s 轮询（打开时由 QueuePanel 自行 2s 轮询）
  useEffect(() => {
    if (!session) return
    let alive = true
    const tick = async () => {
      try { const q = await panelsApi.queueSnapshot(); if (alive) setQueue(q) } catch { /* 服务未就绪忽略 */ }
    }
    if (!tab) {
      tick()
      const it = setInterval(tick, 5000)
      return () => { alive = false; clearInterval(it) }
    }
    return () => { alive = false }
  }, [session, tab])

  if (!session) return null

  const activeCount = (queue?.running?.length || 0) + (queue?.pending?.length || 0)

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

      {/* 右下 FAB 簇（队列/素材/模型） */}
      <div className="fw-pro-fab" style={{
        position: 'fixed', right: 14, bottom: 34, zIndex: 200, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6,
      }}>
        {fabOpen && !tab && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, animation: 'fw-pop-in .16s var(--t-ease) both' }}>
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => switchTab(t.id)}
                title={t.id === 'queue' ? '任务队列（运行/等待/历史）' : t.id === 'assets' ? '素材 / 输出中心' : '模型中心'}
                style={{
                  display: 'flex', alignItems: 'center', gap: 7, padding: '8px 12px', borderRadius: 999, cursor: 'pointer',
                  border: '1px solid var(--border-strong)', background: 'var(--glass-strong)',
                  backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
                  color: 'var(--text)', fontSize: 12, fontWeight: 600, boxShadow: 'var(--elev-2)',
                  position: 'relative', transition: 'transform .15s var(--t-ease)',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateX(-3px)' }}
                onMouseLeave={(e) => { e.currentTarget.style.transform = 'translateX(0)' }}
              >
                <t.icon size={14} color="var(--accent-strong)" />
                {t.label}
                {t.id === 'queue' && activeCount > 0 && (
                  <span style={{
                    position: 'absolute', top: -5, right: -5, minWidth: 16, height: 16, borderRadius: 999, fontSize: 9,
                    background: 'var(--running)', color: '#0b1120', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    padding: '0 4px', fontWeight: 800, boxShadow: '0 0 10px rgba(245,165,36,.6)',
                  }}>{activeCount}</span>
                )}
              </button>
            ))}
          </div>
        )}
        <button
          onClick={() => setFabOpen((v) => !v)}
          title={fabOpen ? '收起面板入口' : '展开面板入口'}
          style={{
            width: 38, height: 38, borderRadius: 999, cursor: 'pointer',
            border: '1px solid var(--border-strong)', background: 'var(--accent-grad)', color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: 'var(--elev-3)', lineHeight: 0,
          }}
        >{fabOpen && !tab ? <Minus size={16} /> : <Activity size={16} />}</button>
      </div>

      {/* 右侧抽屉（小视口自动全屏） */}
      {tab && (
        <div className="fw-pro-drawer" style={{
          position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(430px, 100vw)', zIndex: 500,
          borderLeft: '1px solid var(--border-strong)', background: 'var(--glass-strong)',
          backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
          boxShadow: 'var(--elev-3), -12px 0 32px rgba(0,0,0,.28)', display: 'flex', flexDirection: 'column',
          animation: 'fw-drawer-in .22s var(--t-ease) both',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
            {TABS.map((t) => (
              <button key={t.id} onClick={() => switchTab(t.id)} title={'切换到' + t.label}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 11px', borderRadius: 999, cursor: 'pointer',
                  border: '1px solid ' + (tab === t.id ? 'var(--accent-strong)' : 'var(--border)'),
                  background: tab === t.id ? 'var(--accent-soft)' : 'transparent',
                  color: tab === t.id ? 'var(--accent-strong)' : 'var(--text-faint)', fontSize: 11.5, fontWeight: 600,
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