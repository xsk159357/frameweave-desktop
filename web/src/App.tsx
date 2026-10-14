// 主应用（v4）：登录门禁 → 工作区；顶栏工作流下拉 + 底部状态栏 + toast
import { useEffect, useState } from 'react'
import { RefreshCw, Store, Crown, Sparkles, CheckCircle2, AlertCircle, Info, Puzzle, Download, Settings as SettingsIcon, UserCircle, CreditCard, Monitor, LogOut } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import { LoginPage } from './LoginPage'
import { WorkflowPanel } from './WorkflowPanel'
import { PluginPanel } from './PluginPanel'
import { SettingsPanel } from './SettingsPanel'
import { Logo } from './Logo'
import { Canvas } from './Canvas'
import { Timeline } from './Timeline'
import { MarketPage } from './MarketPage'

export default function App() {
  const session = useAppStore((s) => s.session)
  const specs = useAppStore((s) => s.specs)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const workflowId = useAppStore((s) => s.workflowId)
  const setWorkflow = useAppStore((s) => s.setWorkflow)
  const timelineNodeId = useAppStore((s) => s.timelineNodeId)
  const nodeAssets = useAppStore((s) => s.nodeAssets)
  const dirty = useAppStore((s) => s.dirty)
  const toasts = useAppStore((s) => s.toasts)
  const removeToast = useAppStore((s) => s.removeToast)
  const [showMarket, setShowMarket] = useState(false)
  const [showPlugin, setShowPlugin] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showAccount, setShowAccount] = useState(false)
  const [showWorkflowDrawer, setShowWorkflowDrawer] = useState(false)
  const [engineReady, setEngineReady] = useState(false)
  const [backend, setBackend] = useState<null | boolean>(null)
  const [authNotice, setAuthNotice] = useState('')
  const [updateState, setUpdateState] = useState<{ mode: 'checking' | 'available' | 'downloading' | 'downloaded' | 'error' | 'latest'; version?: string; message?: string; progress?: number } | null>(null)
  const [showProfile, setShowProfile] = useState(false)
  const [profileCard, setProfileCard] = useState('')
  const [subscriptionExpired, setSubscriptionExpired] = useState(() => !!session?.expiresAt && session.expiresAt <= Date.now())

  // 主题：亮/暗即时生效（CSS 变量覆盖集，无需重启）
  const settings = useAppStore((s) => s.settings)
  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme
    // 无边框联动：原生窗口控件 Overlay 配色随主题切换（Electron 打包版）
    try { ;(window as any).frameweave?.window?.setTitlebarTheme?.(settings.theme) } catch { /* 浏览器模式忽略 */ }
  }, [settings.theme])

  // 自动更新状态统一进入更新面板，不再使用原生丑陋弹窗。
  useEffect(() => {
    const onStatus = (s: any) => {
      const type = s?.type === 'error' ? 'error' : s?.type === 'ok' ? 'latest' : s?.type === 'downloaded' ? 'downloaded' : 'checking'
      setUpdateState((prev) => ({ mode: type, version: s?.version || prev?.version, message: s?.message }))
    }
    const onAvailable = (v: string) => setUpdateState({ mode: 'available', version: v, message: '新版本已准备好下载' })
    const onProgress = (p: number) => setUpdateState((prev) => ({ mode: 'downloading', version: prev?.version, progress: p, message: '正在下载更新' }))
    try {
      const w = (window as any).frameweave
      w?.onUpdateStatus?.(onStatus)
      w?.onUpdateAvailable?.(onAvailable)
      w?.onUpdateProgress?.(onProgress)
    } catch { /* 浏览器模式 */ }
  }, [])

  const triggerUpdateCheck = () => {
    setUpdateState({ mode: 'checking', message: '正在检查更新…' })
    try { (window as any).frameweave?.checkForUpdate() } catch { setUpdateState({ mode: 'error', message: '当前环境不支持自动更新' }) }
  }
  const closeUpdate = () => setUpdateState(null)


  // 画布右键菜单「导入节点插件」事件
  useEffect(() => {
    const h = () => setShowPlugin(true)
    window.addEventListener('fw-open-plugins', h)
    return () => window.removeEventListener('fw-open-plugins', h)
  }, [])

  // 画布空态「去商城安装节点」事件 → 打开商城面板
  useEffect(() => {
    const h = () => setShowMarket(true)
    window.addEventListener('fw-open-market', h)
    return () => window.removeEventListener('fw-open-market', h)
  }, [])

  // 引擎就绪轮询（启动加速：Electron 窗口先行，界面先渲染加载层，本地引擎 health 200 后进入应用）
  useEffect(() => {
    let alive = true
    let tries = 0
    const tick = async () => {
      try { const h = await api.health(); if (alive && h.ok) { setEngineReady(true); setBackend(true); return } } catch { /* 引擎未就绪 */ }
      tries++
      if (alive) { if (tries > 40) setEngineReady(true); else setTimeout(tick, 500) }
    }
    tick()
    return () => { alive = false }
  }, [])

  // 后端健康轮询（A4，就绪后常驻）+ 断连/恢复提示（WS 已 2s 自动重连，这里补 HTTP 层提示）
  useEffect(() => {
    if (!session || !engineReady) return
    let alive = true
    let lastOk: boolean | null = null
    const tick = async () => {
      let ok = false
      try { const h = await api.health(); ok = !!h.ok } catch { ok = false }
      if (!alive) return
      setBackend(ok)
      if (lastOk !== null && lastOk !== ok) {
        if (ok) useAppStore.getState().pushToast('本地服务已恢复', 'ok')
        else useAppStore.getState().pushToast('本地服务离线，正在重连…', 'err')
      }
      lastOk = ok
    }
    tick()
    const it = setInterval(tick, 10000)
    return () => { alive = false; clearInterval(it) }
  }, [session, engineReady])

  // 加载节点规格 + 初始化工作流
  useEffect(() => {
    if (!session) return
    let alive = true
    ;(async () => {
      try {
        // 启动加速：specs 与工作流列表并行拉取
        const [s, list] = await Promise.all([api.specs(), api.listWorkflows()])
        if (alive) setSpecs(s)
        let wid = ''
        let wname = ''
        if (list.length > 0) {
          wid = list[0].id; wname = (list[0] as any).name || ''
        } else {
          // 节点/模板来自商城：无工作流时直接建空工作流（画布空态引导去商城装节点）
          const created = await api.createWorkflow('我的第一个工作流')
          wid = (created as any).id; wname = '我的第一个工作流'
        }
        if (alive) setWorkflow(wid, wname)
      } catch (e: any) {
        console.error('初始化失败', e)
        window.alert('本地服务初始化失败：' + (e.message || '请重启应用'))
      }
    })()
    return () => { alive = false }
  }, [session, setSpecs, setWorkflow])

  // 模板入口从左侧工作流管理区打开；顶部保持简洁，当前工作流由左侧列表体现。

  // M17：在线校验（启动即验 + 每 30s 周期轮询）
  // 产品策略：账号登录制，单端在线——同一账号在新设备登录后，旧设备立即被踢下线。
  // 云端 verify 检测到 device_id 不匹配即返回失败（device_kick），此处在周期轮询中发现即清会话回登录页。
  useEffect(() => {
    if (!session || !session.deviceId) return
    let alive = true
    const check = async () => {
      try {
        const v = await api.verify(session.token, session.deviceId)
        if (!alive) return
        if (v.ok) {
          if (v.plan && v.plan !== session.plan) {
            useAppStore.getState().setSession({ ...session, plan: v.plan, credits: v.credits ?? session.credits, expiresAt: v.expires_at ?? session.expiresAt })
          }
          const expired = !!v.expires_at && v.expires_at * 1000 <= Date.now()
          setSubscriptionExpired(expired)
        } else {
          const reason = v.reason || '授权校验失败，请重新登录'
          if (/过期|到期|续费/.test(reason)) {
            setSubscriptionExpired(true)
            setAuthNotice(reason)
          } else {
            useAppStore.getState().setSession(null)
            setAuthNotice(reason)
          }
        }
      } catch (e: any) { console.error('在线校验失败', e) }
    }
    check()
    const timer = window.setInterval(check, 30000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [session?.token])

  const activateCard = async () => {
    const card = profileCard.trim()
    if (!card) { useAppStore.getState().pushToast('请输入卡密', 'err'); return }
    try {
      const r = await api.activate(session?.email || '', card)
      if (!r.ok) throw new Error(r.message || '激活失败')
      useAppStore.getState().setSession({ ...(session as any), plan: r.plan || 'member', expiresAt: (r.expires_at || 0) * 1000 })
      setSubscriptionExpired(false)
      setProfileCard('')
      useAppStore.getState().pushToast('卡密激活成功', 'ok')
    } catch (e: any) { useAppStore.getState().pushToast(e.message || '激活失败', 'err') }
  }

  if (!engineReady) {
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-grad)', color: 'var(--text)', gap: 14, WebkitAppRegion: 'drag' as any }}>
        <div style={{ animation: 'fw-splash-in .6s cubic-bezier(.25,.1,.25,1)' }}>
          <Logo size={76} color="var(--accent)" />
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: .5 }}>拾帧 <span style={{ color: 'var(--accent)', fontWeight: 800 }}>FrameWeave</span></div>
        <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>节点式 AI 视频创作工作台</div>
        <div style={{ width: 220, height: 3, borderRadius: 2, background: 'var(--panel-2)', overflow: 'hidden', marginTop: 6, position: 'relative' }}>
          <div style={{ position: 'absolute', inset: 0, background: 'var(--accent-grad)', animation: 'fw-splash-bar 1.6s ease-in-out infinite' }} />
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text-faint)', display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', border: '1.5px solid var(--text-faint)', borderTopColor: 'var(--accent)', animation: 'fw-spin .8s linear infinite', display: 'inline-block' }} />
          正在启动本地引擎…
        </div>
      </div>
    )
  }
  if (!session) return (
    <>
      <LoginPage />
      {showProfile && (
        <div className="fw-profile-modal" onClick={() => setShowProfile(false)}>
          <div className="fw-profile-card" onClick={(e) => e.stopPropagation()}>
            <div className="fw-profile-head"><div><div className="fw-profile-title"><UserCircle size={20} />个人中心</div><div className="fw-profile-sub">账号与订阅管理</div></div><button className="fw-profile-close" onClick={() => setShowProfile(false)}>×</button></div>
            <div className="fw-profile-email">{session?.email}</div>
            <div className="fw-profile-grid">
              <div className="fw-profile-stat"><Crown size={16} /><span>套餐</span><b>{session?.plan === 'member' ? '会员' : '试用'}</b></div>
              <div className="fw-profile-stat"><CreditCard size={16} /><span>到期时间</span><b>{session?.expiresAt ? new Date(session.expiresAt).toLocaleDateString('zh-CN') : '未设置'}</b></div>
              <div className="fw-profile-stat"><Sparkles size={16} /><span>积分</span><b>{session?.credits ?? 0}</b></div>
              <div className="fw-profile-stat"><Monitor size={16} /><span>绑定设备</span><b>{session?.deviceId ? session.deviceId.slice(0, 18) : '本机'}</b></div>
            </div>
            <div className="fw-profile-section"><div className="fw-profile-label">卡密激活</div><div className="fw-profile-activate"><input value={profileCard} onChange={(e) => setProfileCard(e.target.value)} placeholder="输入 FW- 开头的卡密" /><button className="fw-btn fw-btn-primary" onClick={activateCard}>激活</button></div></div>
            <div className="fw-profile-actions"><button className="fw-btn fw-btn-ghost" onClick={() => useAppStore.getState().pushToast('续费请联系管理员获取卡密', 'info')}>续费说明</button><button className="fw-btn fw-btn-danger" onClick={() => useAppStore.getState().setSession(null)}><LogOut size={14} />退出登录</button></div>
          </div>
        </div>
      )}
      {authNotice && (
        <div className="fw-auth-modal" role="dialog" aria-modal="true" aria-labelledby="fw-auth-title">
          <div className="fw-auth-modal-card">
            <div className="fw-auth-modal-icon"><Crown size={22} /></div>
            <div className="fw-auth-modal-content">
              <div id="fw-auth-title" className="fw-auth-modal-title">订阅需要续费</div>
              <div className="fw-auth-modal-text">{authNotice}</div>
              <div className="fw-auth-modal-actions">
                <button className="fw-btn fw-btn-ghost" onClick={() => setAuthNotice('')}>知道了</button>
                <button className="fw-btn fw-btn-primary" onClick={() => setAuthNotice('')}>前往续费</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )

  return (
    <div className="fw-app-shell" style={{ height: '100%', display: 'flex', flexDirection: 'column',
      background: 'var(--bg-grad)', backgroundAttachment: 'fixed', color: 'var(--text)' }}>
      {/* 顶栏（无边框方案A：整体拖拽区 + 右上预留原生控件区 150px） */}
      <div className="fw-topbar" style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', paddingRight: 150, height: 52,
        borderBottom: 'var(--glass-border)', background: 'var(--glass-strong)',
        backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        flexShrink: 0, boxShadow: 'var(--glass-inner), 0 1px 12px rgba(0,0,0,.28)',
        position: 'relative', zIndex: 10,
      }}>
        {/* 品牌区（无边框方案A：保留为拖拽窗口的主力区域） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginRight: 8, WebkitAppRegion: 'drag' as any }}>
          <div style={{ width: 30, height: 30, borderRadius: 9, background: 'var(--accent-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Logo size={20} color="var(--accent)" />
          </div>
          <span style={{ fontWeight: 750, fontSize: 15, letterSpacing: .2 }}>拾帧 FrameWeave</span>
          <span className="fw-pill" style={{ border: '1px solid var(--border)', color: 'var(--text-faint)', background: 'transparent', fontWeight: 500 }}>v{__APP_VERSION__}</span>
        </div>
        <span style={{ flex: 1 }} />
        {/* 右侧操作（分组：更新｜插件/商城｜设置/账户；预留原生控件区 150px） */}
        <div className="fw-topbar-actions fw-topbar-groups" style={{ position: 'relative' }}>
          {showAccount && <div className="fw-account-popover" style={{ top: 38, right: 0, left: 'auto', bottom: 'auto' }}>
            <div className="fw-account-name">{session.email}</div>
            <div className="fw-account-plan">{session.plan === 'member' ? '会员账户' : '试用账户'}</div>
            <button onClick={() => { setShowAccount(false); setProfileCard(''); setShowProfile(true) }}>个人中心</button>
            <button className="danger" onClick={() => useAppStore.getState().setSession(null)}>退出登录</button>
          </div>}
          <div className="fw-topbar-group">
            <button className="fw-btn fw-btn-ghost" onClick={triggerUpdateCheck}>
              <><RefreshCw size={13} /> 更新</>
            </button>
          </div>
          <div className="fw-topbar-group">
            <button className="fw-btn fw-btn-ghost" title="节点插件导入与管理" onClick={() => setShowPlugin(true)}>
              <><Puzzle size={13} /> 插件</>
            </button>
            <button className="fw-btn fw-btn-ghost" title="打开商城" onClick={() => setShowMarket(true)}>
              <><Store size={13} /> 商城</>
            </button>
          </div>
          <div className="fw-topbar-group">
            <button className={'fw-btn fw-btn-ghost' + (showAccount ? ' active' : '')} title="账户" onClick={() => setShowAccount(v => !v)}>
              <><UserCircle size={13} /> 账户</>
            </button>
            <button className="fw-btn fw-btn-ghost" title="设置" onClick={() => setShowSettings(true)}>
              <><SettingsIcon size={13} /> 设置</>
            </button>
          </div>
        </div>
      </div>

      {showMarket && <MarketPage onClose={() => setShowMarket(false)} />}
      {showPlugin && <PluginPanel onClose={() => setShowPlugin(false)} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}

      {updateState && (
        <div className="fw-update-overlay" role="dialog" aria-modal="true" aria-labelledby="fw-update-title">
          <div className="fw-update-card">
            <div className="fw-update-mark"><Download size={22} /></div>
            <div className="fw-update-kicker">FRAMEWEAVE UPDATE</div>
            <h2 id="fw-update-title">{updateState.mode === 'available' ? '发现新版本' : updateState.mode === 'downloading' ? '正在下载更新' : updateState.mode === 'downloaded' ? '更新已准备好' : updateState.mode === 'latest' ? '已是最新版本' : updateState.mode === 'error' ? '更新遇到问题' : '检查更新'}</h2>
            <p className="fw-update-message">{updateState.message || '正在获取更新状态…'}</p>
            {updateState.version && <div className="fw-update-version">v{updateState.version}</div>}
            {updateState.mode === 'downloading' && <div className="fw-update-progress"><div className="fw-update-progress-track"><div className="fw-update-progress-fill" style={{ width: `${Math.max(0, Math.min(100, updateState.progress || 0))}%` }} /></div><div className="fw-update-progress-meta"><span>{Math.round(updateState.progress || 0)}%</span><span>请保持应用开启</span></div></div>}
            <div className="fw-update-actions">
              {updateState.mode === 'available' && <button className="fw-btn fw-btn-primary" onClick={() => { setUpdateState((p) => p ? ({ ...p, mode: 'downloading', progress: 0, message: '正在下载更新' }) : p); try { (window as any).frameweave?.downloadUpdate() } catch {} }}><Download size={14} /> 下载更新</button>}
              {updateState.mode === 'downloaded' && <button className="fw-btn fw-btn-primary" onClick={() => { try { (window as any).frameweave?.installUpdate() } catch {} }}>立即重启安装</button>}
              {(updateState.mode === 'checking' || updateState.mode === 'downloading') && <button className="fw-btn fw-btn-ghost" disabled>处理中…</button>}
              {updateState.mode === 'latest' && <button className="fw-btn fw-btn-primary" onClick={closeUpdate}>知道了</button>}
              {updateState.mode === 'error' && <button className="fw-btn fw-btn-primary" onClick={triggerUpdateCheck}>重试</button>}
              {(updateState.mode === 'available' || updateState.mode === 'error') && <button className="fw-btn fw-btn-ghost" onClick={closeUpdate}>稍后</button>}
            </div>
          </div>
        </div>
      )}

      {/* 模板选择弹层 */}
      {/* 主体：工作流导航 + 中央画布（方案 B：画布为焦点，导航固定可见） */}
      <div className="fw-main-layout">
        <aside className="fw-workspace-sidebar" aria-label="工作流导航">
          <WorkflowPanel readOnly={subscriptionExpired} onOpenPlugin={() => { if (!subscriptionExpired) setShowPlugin(true) }} />
        </aside>
        <main className="fw-canvas-stage">
          <Canvas workflowId={workflowId} readOnly={subscriptionExpired} />
        </main>
      </div>

      {/* 底部时间线（双击有 segments 资产的节点 / 右键查看时间线） */}
      {timelineNodeId && nodeAssets[timelineNodeId]?.segments && <div className="fw-timeline-bar"><Timeline nodeId={timelineNodeId} /></div>}

      {/* 底部状态栏（A4） */}
      <div className="fw-statusbar">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span className="dot" style={{ background: backend === null ? 'var(--text-faint)' : backend ? 'var(--success)' : 'var(--danger)', boxShadow: '0 0 6px ' + (backend === null ? 'transparent' : backend ? 'rgba(52,211,153,.7)' : 'rgba(248,113,113,.7)') }} />
          {backend === null ? '连接中…' : backend ? '服务正常' : '服务离线'}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {dirty ? <><span style={{ color: 'var(--running)' }}>●</span> 有未保存改动</> : <><CheckCircle2 size={11} color="var(--success)" /> 已保存</>}
        </span>
        <span style={{ flex: 1 }} />
        <span>Ctrl+S 保存 · Ctrl+Z 撤销 · ? 快捷键</span>
      </div>

      {/* toast 容器 */}
      <div className="fw-toast-wrap">
        {toasts.map((t) => (
          <div key={t.id} className={'fw-toast ' + t.kind} onClick={() => removeToast(t.id)}>
            <span className="fw-toast-ic">
              {t.kind === 'ok' ? <CheckCircle2 size={13} color="var(--success)" style={{ flexShrink: 0 }} />
                : t.kind === 'err' ? <AlertCircle size={13} color="var(--danger)" style={{ flexShrink: 0 }} />
                : <Info size={13} color="var(--accent)" style={{ flexShrink: 0 }} />}
            </span>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
