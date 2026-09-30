// 主应用：登录门禁 -> 工作区
import { useEffect, useState } from 'react'
import { FileText, Sun, Moon, RefreshCw, Store, Crown, Sparkles } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import { LoginPage } from './LoginPage'
import { Sidebar } from './Sidebar'
import { Canvas } from './Canvas'
import { Timeline } from './Timeline'
import { MarketPage } from './MarketPage'

export default function App() {
  const session = useAppStore((s) => s.session)
  const dark = useAppStore((s) => s.dark)
  const toggleDark = useAppStore((s) => s.toggleDark)
  const specs = useAppStore((s) => s.specs)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const workflowId = useAppStore((s) => s.workflowId)
  const setWorkflow = useAppStore((s) => s.setWorkflow)
  const selectedNodeId = useAppStore((s) => s.selectedNodeId)
  const [showMarket, setShowMarket] = useState(false)

  useEffect(() => {
    if (dark) document.documentElement.classList.add('dark')
    else document.documentElement.classList.remove('dark')
  }, [dark])

  // 加载节点规格 + 初始化工作流
  useEffect(() => {
    if (!session) return
    let alive = true
    ;(async () => {
      try {
        const s = await api.specs()
        if (alive) setSpecs(s)
        // 打开/创建工作流
        const list = await api.listWorkflows()
        let wid = ''
        if (list.length > 0) {
          wid = list[0].id
        } else {
          // 无工作流时：优先创建「一条龙」模板（15 分钟出片）
          try {
            const tpls = await api.listTemplates()
            if (tpls.length > 0) {
              const created = await api.createFromTemplate(tpls[0].id)
              wid = (created as any).id
            } else {
              const created = await api.createWorkflow('我的第一个工作流')
              wid = (created as any).id
            }
          } catch (e) {
            const created = await api.createWorkflow('我的第一个工作流')
            wid = (created as any).id
          }
        }
        if (alive) setWorkflow(wid, '')
      } catch (e: any) {
        console.error('初始化失败', e)
        window.alert('本地服务初始化失败：' + (e.message || '请重启应用'))
      }
    })()
    return () => { alive = false }
  }, [session, setSpecs, setWorkflow])

  // M17：启动在线校验（无离线宽限；订阅过期/设备被踢 → 回登录页）
  useEffect(() => {
    if (!session || !session.deviceId) return
    let alive = true
    ;(async () => {
      try {
        const v = await api.verify(session.token, session.deviceId)
        if (!alive) return
        if (v.ok) {
          if (v.plan && v.plan !== session.plan) {
            useAppStore.getState().setSession({
              ...session, plan: v.plan, credits: v.credits ?? session.credits,
              expiresAt: v.expires_at ?? session.expiresAt,
            })
          }
        } else {
          useAppStore.getState().setSession(null)
          window.alert(v.reason || '授权校验失败，请重新登录')
        }
      } catch (e:any) { console.error('在线校验失败', e) }
    })()
    return () => { alive = false }
  }, [session?.token])

  if (!session) return <LoginPage />

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column',
      background: 'var(--bg-grad)', backgroundAttachment: 'fixed', color: 'var(--text)' }}>
      {/* 顶栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px', height: 54,
        borderBottom: 'var(--glass-border)', background: 'var(--glass-strong)',
        backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        flexShrink: 0, boxShadow: 'var(--glass-inner), 0 1px 8px rgba(80,90,140,.06)',
        position: 'relative', zIndex: 10,
      }}>
        {/* 品牌区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginRight: 6 }}>
          <div style={{
            width: 30, height: 30, borderRadius: 9, background: 'var(--accent-grad)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 15, fontWeight: 800, color: '#fff', letterSpacing: 0,
            boxShadow: '0 6px 18px rgba(91,108,247,.45), inset 0 1px 0 rgba(255,255,255,.3)',
          }}>帧</div>
          <span style={{ fontWeight: 750, fontSize: 15.5, letterSpacing: .2 }}>拾帧 FrameWeave</span>
          <span className="fw-pill" style={{
            border: '1px solid ' + (dark ? '#333a50' : '#e2e6ee'),
            color: dark ? '#8b93a9' : '#7a8499', background: 'transparent', fontWeight: 500,
          }}>v0.2.8</span>
        </div>
        {/* 工作流 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 11px', borderRadius: 999,
          border: 'var(--glass-border)', background: 'var(--glass)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
          boxShadow: 'var(--glass-inner)', maxWidth: 240, overflow: 'hidden' }}>
          <FileText size={13} style={{ color: dark ? '#8b93a9' : '#7a8499', flexShrink: 0 }} />
          <span style={{ fontSize: 12, color: dark ? '#c6cbd8' : '#4a5264', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {workflowId}
          </span>
        </div>
        <span style={{ flex: 1 }} />
        {/* 右侧操作 */}
        <button className="fw-btn fw-btn-ghost" onClick={toggleDark} title="切换主题">
          {dark ? <><Sun size={14} /> 亮色</> : <><Moon size={14} /> 暗色</>}
        </button>
        <button className="fw-btn fw-btn-ghost"
          onClick={() => { try { (window as any).frameweave?.checkForUpdate() } catch { /* 浏览器模式 */ } }}>
          <><RefreshCw size={13} /> 更新</>
        </button>
        <button className="fw-btn fw-btn-primary" onClick={() => setShowMarket(true)}>
          <><Store size={14} /> 商城</>
        </button>
        {session.plan === 'member' ? (
          <span className="fw-pill" style={{
            color: dark ? '#ffd47e' : '#a06a00',
            border: '1px solid ' + (dark ? '#5a4a1a' : '#e8d48b'),
            background: dark ? '#2a2410' : '#fff8e1',
          }}><Crown size={12} style={{ verticalAlign: '-2px' }} /> 会员</span>
        ) : (
          <span className="fw-pill" style={{
            color: dark ? '#9fb4ff' : '#3a5bd9',
            border: '1px solid ' + (dark ? '#2c3a5c' : '#c9d6f9'),
            background: dark ? '#1a2138' : '#edf2ff',
          }}><Sparkles size={12} style={{ verticalAlign: '-2px' }} /> 试用 · 剩 {Math.max(1, Math.ceil((session.expiresAt - Date.now() / 1000) / 86400))} 天</span>
        )}
        <span style={{ fontSize: 12, color: dark ? '#8b93a9' : '#7a8499', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{session.email}</span>
        <button className="fw-btn fw-btn-ghost" onClick={() => useAppStore.getState().setSession(null)}>退出</button>
      </div>

      {showMarket && <MarketPage onClose={() => setShowMarket(false)} />}

      {/* 主体：侧栏 + 画布 */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <Sidebar onAddNode={(t) => { ;(window as any).__fwAddNode?.(t) }} />
        <Canvas workflowId={workflowId} />
      </div>

      {/* 底部时间线（双击节点展开） */}
      {selectedNodeId && <Timeline nodeId={selectedNodeId} />}
    </div>
  )
}
