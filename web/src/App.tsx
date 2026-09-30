// 主应用：登录门禁 -> 工作区
import { useEffect } from 'react'
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
      background: dark ? '#12141c' : '#f8f9fb', color: dark ? '#e8eaf2' : '#1f2430' }}>
      {/* 顶栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
        borderBottom: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
        background: dark ? '#1a1d29' : '#fff', flexShrink: 0,
      }}>
        <span style={{ fontWeight: 700, fontSize: 16 }}>拾帧 FrameWeave</span>
        <span style={{ fontSize: 12, color: '#8b93a9' }}>工作流 · {workflowId}</span>
        <span style={{ flex: 1 }} />
        <button onClick={toggleDark} style={{
          padding: '5px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
          border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
          background: dark ? '#22263a' : '#f4f6fb', color: dark ? '#e8eaf2' : '#1f2430',
        }}>{dark ? '☀ 亮色' : '🌙 暗色'}</button>
        <button
          onClick={() => setShowMarket(true)}
          style={{
            padding: '5px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
            border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
            background: dark ? '#22263a' : '#f4f6fb', color: dark ? '#e8eaf2' : '#1f2430',
          }}>🛒 商城</button>
        <button
          onClick={() => {
            try { (window as any).frameweave?.checkForUpdate() } catch { /* 浏览器模式忽略 */ }
          }}
          style={{
            padding: '5px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
            border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
            background: dark ? '#22263a' : '#f4f6fb', color: dark ? '#e8eaf2' : '#1f2430',
          }}>🔄 检查更新</button>
        {session.plan === 'member' ? (
          <span style={{ fontSize: 12, fontWeight: 600, color: '#b8860b', border: '1px solid ' + (dark ? '#5a4a1a' : '#e8d48b'), background: dark ? '#2a2410' : '#fff8e1', borderRadius: 999, padding: '2px 9px' }}>⭐ 会员</span>
        ) : (
          <span style={{ fontSize: 12, fontWeight: 600, color: dark ? '#9fb4ff' : '#3a5bd9', border: '1px solid ' + (dark ? '#2c3a5c' : '#c9d6f9'), background: dark ? '#1a2138' : '#edf2ff', borderRadius: 999, padding: '2px 9px' }}>
            ✨ 试用中 · 剩 {Math.max(1, Math.ceil((session.expiresAt - Date.now() / 1000) / 86400))} 天
          </span>
        )}
        <span style={{ fontSize: 12, color: '#8b93a9' }}>{session.email}</span>
        <button onClick={() => useAppStore.getState().setSession(null)} style={{
          padding: '5px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
          border: '1px solid ' + (dark ? '#2c3142' : '#e2e6ee'),
          background: dark ? '#22263a' : '#f4f6fb', color: dark ? '#e8eaf2' : '#1f2430',
        }}>退出</button>
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
