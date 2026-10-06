// 主应用（v4）：登录门禁 → 工作区；顶栏工作流下拉 + 底部状态栏 + toast
import { useEffect, useState } from 'react'
import { FileText, RefreshCw, Store, Crown, Sparkles, ChevronDown, Plus, LayoutTemplate, CheckCircle2, AlertCircle, Info, Trash2, Puzzle, Settings as SettingsIcon, UserCircle } from 'lucide-react'
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
  const workflowName = useAppStore((s) => s.workflowName)
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
  const [wfMenu, setWfMenu] = useState(false)
  const [wfList, setWfList] = useState<{ id: string; name: string; updated_at: number }[]>([])
  const [tplList, setTplList] = useState<{ id: string; title: string; description: string; nodes: number }[]>([])
  const [showTpl, setShowTpl] = useState(false)
  const [backend, setBackend] = useState<null | boolean>(null)
  const [authNotice, setAuthNotice] = useState('')

  // 主题：亮/暗即时生效（CSS 变量覆盖集，无需重启）
  const settings = useAppStore((s) => s.settings)
  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme
    // 无边框联动：原生窗口控件 Overlay 配色随主题切换（Electron 打包版）
    try { ;(window as any).frameweave?.window?.setTitlebarTheme?.(settings.theme) } catch { /* 浏览器模式忽略 */ }
  }, [settings.theme])

  // 自动更新状态反馈：按钮点击、最新版本、错误、开发模式均显示 toast
  useEffect(() => {
    const onStatus = (s: any) => {
      const kind = s?.type === 'error' ? 'err' : s?.type === 'ok' ? 'ok' : 'info'
      useAppStore.getState().pushToast(s?.message || '更新状态未知', kind)
    }
    try { const w = (window as any).frameweave; w?.onUpdateStatus?.(onStatus) } catch { /* 浏览器模式 */ }
  }, [])

  // 画布右键菜单「导入节点插件」事件
  useEffect(() => {
    const h = () => setShowPlugin(true)
    window.addEventListener('fw-open-plugins', h)
    return () => window.removeEventListener('fw-open-plugins', h)
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
          try {
            const tpls = await api.listTemplates()
            if (tpls.length > 0) {
              const created = await api.createFromTemplate(tpls[0].id)
              wid = (created as any).id; wname = (created as any).name || ''
            } else {
              const created = await api.createWorkflow('我的第一个工作流')
              wid = (created as any).id; wname = '我的第一个工作流'
            }
          } catch {
            const created = await api.createWorkflow('我的第一个工作流')
            wid = (created as any).id; wname = '我的第一个工作流'
          }
        }
        if (alive) setWorkflow(wid, wname)
      } catch (e: any) {
        console.error('初始化失败', e)
        window.alert('本地服务初始化失败：' + (e.message || '请重启应用'))
      }
    })()
    return () => { alive = false }
  }, [session, setSpecs, setWorkflow])

  // 打开工作流下拉时拉取列表
  const openWfMenu = async () => {
    setWfMenu(true)
    try {
      const list = await api.listWorkflows()
      setWfList(list || [])
      try { const t = await api.listTemplates(); setTplList(t || []) } catch { /* ignore */ }
    } catch { /* ignore */ }
  }

  const switchWf = (id: string, name: string) => {
    setWorkflow(id, name)
    setWfMenu(false)
    useAppStore.getState().setTimelineNodeId(null)
  }

  const newWf = async () => {
    setWfMenu(false)
    try {
      const created = await api.createWorkflow('新工作流')
      setWorkflow((created as any).id, '新工作流')
      useAppStore.getState().setTimelineNodeId(null)
    } catch (e: any) { useAppStore.getState().pushToast('新建失败: ' + (e.message || e), 'err') }
  }

  const newFromTpl = async (tplId: string) => {
    setShowTpl(false); setWfMenu(false)
    try {
      const created = await api.createFromTemplate(tplId)
      setWorkflow((created as any).id, (created as any).name || '模板工作流')
      useAppStore.getState().setTimelineNodeId(null)
    } catch (e: any) { useAppStore.getState().pushToast('创建失败: ' + (e.message || e), 'err') }
  }

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
        } else {
          useAppStore.getState().setSession(null)
          setAuthNotice(v.reason || '授权校验失败，请重新登录')
        }
      } catch (e: any) { console.error('在线校验失败', e) }
    }
    check()
    const timer = window.setInterval(check, 30000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [session?.token])

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
        {/* 工作流下拉（A3） */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => wfMenu ? setWfMenu(false) : openWfMenu()}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 999,
              border: 'var(--glass-border)', background: 'var(--glass)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
              boxShadow: 'var(--glass-inner)', maxWidth: 220, cursor: 'pointer', color: 'var(--text-dim)',
            }}
          >
            <FileText size={13} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
            <span style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{workflowName || workflowId}</span>
            <ChevronDown size={12} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
          </button>
          {wfMenu && (
            <div style={{
              position: 'absolute', left: 0, top: 40, zIndex: 500, width: 280,
              background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
              border: 'var(--glass-border)', borderRadius: 14, boxShadow: 'var(--glass-inner), var(--shadow-lg)',
              padding: 6,
            }}>
              <div style={{ padding: '6px 10px 4px', fontSize: 10.5, fontWeight: 700, letterSpacing: 1, color: 'var(--text-faint)' }}>工作流（{wfList.length}）</div>
              <div style={{ maxHeight: 220, overflow: 'auto' }}>
                {wfList.map((w) => (
                  <div key={w.id} onClick={() => switchWf(w.id, w.name)}
                    style={{
                      padding: '7px 10px', borderRadius: 9, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7,
                      fontSize: 12.5, color: w.id === workflowId ? 'var(--text)' : 'var(--text-dim)',
                      background: w.id === workflowId ? 'var(--hover-bg)' : 'transparent',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = w.id === workflowId ? 'var(--hover-bg)' : 'var(--hover-bg)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = w.id === workflowId ? 'var(--hover-bg)' : 'transparent' }}
                  >
                    <FileText size={12} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.name || w.id}</span>
                    {w.id === workflowId && <span style={{ color: 'var(--accent)' }}>●</span>}
                  </div>
                ))}
              </div>
              <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
              <div style={{ padding: '6px 10px', borderRadius: 9, cursor: 'pointer', fontSize: 12.5, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
                onClick={newWf} onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover-bg)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                <Plus size={13} /> 新建工作流
              </div>
              <div style={{ padding: '6px 10px', borderRadius: 9, cursor: 'pointer', fontSize: 12.5, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 7 }}
                onClick={() => setShowTpl(true)} onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover-bg)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                <LayoutTemplate size={13} /> 从模板新建…
              </div>
              {workflowId && (
                <div style={{ padding: '6px 10px', borderRadius: 9, cursor: 'pointer', fontSize: 12.5, color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: 7 }}
                  onClick={async () => {
                    if (!window.confirm('删除当前工作流？此操作不可恢复。')) return
                    setWfMenu(false)
                    try {
                      await api.deleteWorkflow(workflowId)
                      useAppStore.getState().pushToast('工作流已删除', 'info')
                      const list = await api.listWorkflows()
                      if (list.length > 0) setWorkflow(list[0].id, (list[0] as any).name || '')
                      else { const c = await api.createWorkflow('新工作流'); setWorkflow((c as any).id, '新工作流') }
                    } catch (e: any) { useAppStore.getState().pushToast('删除失败: ' + (e.message || e), 'err') }
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(248,113,113,.12)' }} onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
                  <Trash2 size={13} /> 删除当前工作流
                </div>
              )}
            </div>
          )}
        </div>
        <span style={{ flex: 1 }} />
        {/* 右侧操作（分组：更新｜插件，预留原生控件区 150px） */}
        <div className="fw-topbar-actions" style={{ display: 'flex', alignItems: 'center', gap: 7, position: 'relative' }}>
          {showAccount && <div className="fw-account-popover" style={{ top: 38, right: 0, left: 'auto', bottom: 'auto' }}>
            <div className="fw-account-name">{session.email}</div>
            <div className="fw-account-plan">{session.plan === 'member' ? '会员账户' : '试用账户'}</div>
            <button onClick={() => { setShowAccount(false); useAppStore.getState().pushToast('个人中心即将开放', 'info') }}>个人中心</button>
            <button className="danger" onClick={() => useAppStore.getState().setSession(null)}>退出登录</button>
          </div>}
          <button className="fw-btn fw-btn-ghost" onClick={() => { try { (window as any).frameweave?.checkForUpdate() } catch { /* 浏览器模式 */ } }}>
            <><RefreshCw size={13} /> 更新</>
          </button>
          <span style={{ width: 1, height: 18, background: 'var(--border)' }} />
          <button className="fw-btn fw-btn-ghost" title="节点插件导入与管理" onClick={() => setShowPlugin(true)}>
            <><Puzzle size={13} /> 插件</>
          </button>
          <button className="fw-btn fw-btn-ghost" title="打开商城" onClick={() => setShowMarket(true)}>
            <><Store size={13} /> 商城</>
          </button>
          <button className={'fw-btn fw-btn-ghost' + (showAccount ? ' active' : '')} title="账户" onClick={() => setShowAccount(v => !v)}>
            <><UserCircle size={13} /> 账户</>
          </button>
          <button className="fw-btn fw-btn-ghost" title="设置" onClick={() => setShowSettings(true)}>
            <><SettingsIcon size={13} /> 设置</>
          </button>
        </div>
      </div>

      {showMarket && <MarketPage onClose={() => setShowMarket(false)} />}
      {showPlugin && <PluginPanel onClose={() => setShowPlugin(false)} />}
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}

      {/* 模板选择弹层 */}
      {showTpl && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(6,8,16,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowTpl(false)}>
          <div onClick={(e) => e.stopPropagation()} style={{
            width: 560, maxWidth: '90vw', borderRadius: 18, padding: 18,
            background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
            border: '1px solid var(--border-strong)', boxShadow: 'var(--glass-inner), var(--shadow-lg)',
          }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}><LayoutTemplate size={15} color="var(--accent)" /> 从模板新建工作流</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {tplList.map((t) => (
                <div key={t.id} onClick={() => newFromTpl(t.id)} style={{
                  padding: '10px 12px', borderRadius: 12, cursor: 'pointer', border: '1px solid var(--border)',
                  background: 'var(--bg-panel)', transition: 'border-color .15s',
                }} onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent)' }} onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}>
                  <div style={{ fontSize: 13, fontWeight: 650 }}>{t.title}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>{t.description} · {t.nodes} 节点</div>
                </div>
              ))}
              {tplList.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>暂无模板</div>}
            </div>
          </div>
        </div>
      )}

      {/* 主体：侧栏 + 画布 */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <Canvas workflowId={workflowId} />
      </div>

      {/* 底部时间线（双击有 segments 资产的节点 / 右键查看时间线） */}
      {timelineNodeId && nodeAssets[timelineNodeId]?.segments && <Timeline nodeId={timelineNodeId} />}

      {/* 右侧工作流抽屉 */}
      <div className={'fw-workflow-drawer' + (showWorkflowDrawer ? ' open' : '')}>
        <div className="fw-workflow-drawer-inner"><WorkflowPanel onOpenPlugin={() => setShowPlugin(true)} /></div>
      </div>
      <button className="fw-workflow-toggle" onClick={() => setShowWorkflowDrawer(v => !v)} title="工作流列表">
        <FileText size={14} /> <span>工作流</span>
      </button>

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
            {t.kind === 'ok' ? <CheckCircle2 size={14} color="var(--success)" style={{ flexShrink: 0, marginTop: 1 }} />
              : t.kind === 'err' ? <AlertCircle size={14} color="var(--danger)" style={{ flexShrink: 0, marginTop: 1 }} />
              : <Info size={14} color="var(--accent)" style={{ flexShrink: 0, marginTop: 1 }} />}
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
