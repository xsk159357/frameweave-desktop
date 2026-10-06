// 登录门禁页：独立登录窗口（未登录不可进入主界面）
import { useState } from 'react'
import { Logo } from './Logo'
import {
  Mail, Lock, CheckCircle2, AlertCircle, Sun, Moon,
  Layers, Workflow, Sparkles, ShieldCheck, DownloadCloud,
} from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

export function LoginPage() {
  const setSession = useAppStore((s) => s.setSession)
  const settings = useAppStore((s) => s.settings)
  const setSettings = useAppStore((s) => s.setSettings)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [authView, setAuthView] = useState<'login' | 'register' | 'forgot'>('login')
  const [countdown, setCountdown] = useState(0)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [focus, setFocus] = useState('')

  const deviceId = (() => {
    let d = localStorage.getItem('fw_device_id')
    if (!d) {
      // M2 修复：使用密码学随机 UUID（不可预测），替代可预测的 Math.random
      d = (typeof crypto !== 'undefined' && (crypto as any).randomUUID)
        ? 'dev-' + (crypto as any).randomUUID()
        : 'dev-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
      localStorage.setItem('fw_device_id', d)
    }
    return d
  })()

  const sendCode = async (scene: 'register' | 'reset_password') => {
    setErr(''); setMsg('')
    if (!email.toLowerCase().endsWith('@qq.com')) { setErr('请输入 QQ 邮箱'); return }
    setBusy(true)
    try {
      const r = await api.sendEmailCode(email, scene)
      if (r.ok) { setMsg(r.message || '验证码已发送'); setCountdown(r.retry_after || 60); const timer = window.setInterval(() => setCountdown(v => { if (v <= 1) { window.clearInterval(timer); return 0 } return v - 1 }), 1000) }
      else setErr(r.message || '验证码发送失败')
    } catch (e: any) { setErr(e.message || '验证码发送失败') } finally { setBusy(false) }
  }

  const doLogin = async () => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      if (!email.toLowerCase().endsWith('@qq.com') || password.length < 8) { setErr('请输入 QQ 邮箱和至少 8 位密码'); return }
      const r = await api.login(email, password, deviceId)
      if (r.ok && r.session) {
        if (r.device_kick?.message) setMsg(r.device_kick.message)
        setSession({ token: r.session.token, email: r.session.email, expiresAt: r.session.expires_at, plan: r.session.plan || 'trial', credits: r.session.credits || 0, deviceId })
      }
      else setErr(r.message || '登录失败')
    } catch (e: any) {
      const message = e?.message || '登录失败'
      setErr(message.includes('超时') || message.includes('无法连接') ? message : '登录失败：' + message)
    } finally { setBusy(false) }
  }

  const doRegister = async () => {
    setErr(''); setMsg('')
    if (password.length < 8) { setErr('密码至少 8 位'); return }
    if (password !== confirmPassword) { setErr('两次输入的密码不一致'); return }
    setBusy(true)
    try {
      const r = await api.register(email, code, password, deviceId)
      if (r.ok && r.session) setSession({ token: r.session.token, email: r.session.email, expiresAt: r.session.expires_at, plan: r.session.plan || 'trial', credits: r.session.credits || 0, deviceId })
      else setErr(r.message || '注册失败')
    } catch (e: any) { setErr(e.message || '注册失败') } finally { setBusy(false) }
  }

  const doReset = async () => {
    setErr(''); setMsg('')
    if (password.length < 8) { setErr('密码至少 8 位'); return }
    if (password !== confirmPassword) { setErr('两次输入的密码不一致'); return }
    setBusy(true)
    try {
      const r = await api.resetPassword(email, code, password)
      if (r.ok) { setMsg('密码重置成功，请返回登录'); setAuthView('login'); setCode(''); setConfirmPassword('') }
      else setErr(r.message || '密码重置失败')
    } catch (e: any) { setErr(e.message || '密码重置失败') } finally { setBusy(false) }
  }
  const field = (name: string) => ({ className: 'lp-field' + (focus === name ? ' focus' : '') })
  const featIc = (c: string) => ({ color: c })

  return (
    <div className="lp-root">
      <div className="lp-grid-bg" />
      <div className="lp-glow" style={{ width: 520, height: 520, left: '-80px', top: '-80px', background: 'rgba(139,147,255,.32)' }} />
      <button
        className="lp-theme-toggle"
        onClick={() => setSettings({ theme: settings.theme === 'light' ? 'dark' : 'light' })}
        title={settings.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'}
        aria-label={settings.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'}
      >
        {settings.theme === 'light' ? <Moon size={15} /> : <Sun size={15} />}
        <span>{settings.theme === 'light' ? '暗色' : '亮色'}</span>
      </button>

      {/* 全新品牌场景：创作工作台视觉，不复用旧功能列表结构 */}
      <section className="lp-hero" aria-label="FrameWeave AI 视频创作工作台">
        <div className="lp-hero-head">
          <div className="lp-hero-logo"><Logo size={42} color="var(--accent)" /></div>
          <div>
            <div className="lp-hero-name">拾帧 <span>FRAMEWEAVE</span></div>
            <div className="lp-hero-kicker">AI VIDEO WORKSPACE</div>
          </div>
        </div>

        <div className="lp-hero-copy">
          <div className="lp-hero-overline">FROM IDEA TO FRAME</div>
          <h1>把想法，编成<br /><em>可见的故事</em></h1>
          <p>用节点、画面与声音，构建属于你的创作流程。</p>
        </div>

        <div className="lp-hero-stage" aria-hidden="true">
          <div className="lp-stage-line lp-stage-line-a" />
          <div className="lp-stage-line lp-stage-line-b" />
          <div className="lp-stage-line lp-stage-line-c" />
          <div className="lp-stage-node lp-stage-node-a"><span className="lp-node-dot" />灵感</div>
          <div className="lp-stage-node lp-stage-node-b"><span className="lp-node-dot" />画面</div>
          <div className="lp-stage-node lp-stage-node-c"><span className="lp-node-dot" />成片</div>
          <div className="lp-stage-node lp-stage-node-d"><span className="lp-node-dot" />分镜</div>
          <div className="lp-stage-node lp-stage-node-e"><span className="lp-node-dot" />旁白</div>
          <div className="lp-stage-node lp-stage-node-f"><span className="lp-node-dot" />镜头</div>
          <div className="lp-stage-node lp-stage-node-g"><span className="lp-node-dot" />音轨</div>
          <div className="lp-stage-node lp-stage-node-h"><span className="lp-node-dot" />节奏</div>
          <div className="lp-stage-node lp-stage-node-i"><span className="lp-node-dot" />调色</div>
          <div className="lp-stage-node lp-stage-node-j"><span className="lp-node-dot" />字幕</div>
          <div className="lp-stage-frame lp-stage-frame-main">
            <div className="lp-frame-top"><span /> <span /> <span /></div>
            <div className="lp-frame-art"><i /><b /></div>
            <div className="lp-frame-caption">SEQUENCE / 01</div>
          </div>
          <div className="lp-stage-frame lp-stage-frame-small"><div /><div /><div /></div>
        </div>

        <div className="lp-hero-foot">
          <span className="lp-hero-status"><i /> WORKSPACE READY</span>
          <span>FrameWeave v{__APP_VERSION__}</span>
        </div>
      </section>

      {/* 登录卡片 */}
      <div className="lp-card-wrap">
        <div className="lp-card">
          <div className="lp-card-head">
            <div className="lp-card-title">{authView === 'login' ? '欢迎回来' : authView === 'register' ? '创建账号' : '找回密码'}</div>
            <div className="lp-card-sub">{authView === 'login' ? '登录后继续你的创作' : authView === 'register' ? '验证 QQ 邮箱后完成注册' : '通过 QQ 邮箱重置密码'}</div>
          </div>

          {authView !== 'forgot' && <div className="lp-seg" style={{ height: 38 }}><div className="lp-seg-pill" style={{ width: 'calc(50% - 4px)', left: authView === 'login' ? 4 : 'calc(50% + 0px)' }} /><button className={'lp-seg-btn' + (authView === 'login' ? ' on' : '')} onClick={() => setAuthView('login')}>登 录</button><button className={'lp-seg-btn' + (authView === 'register' ? ' on' : '')} onClick={() => setAuthView('register')}>注 册</button></div>}

          <div {...field('email')}><span className="lp-field-ic"><Mail size={15} /></span><input className="lp-input" placeholder="QQ 邮箱" value={email} onChange={e => setEmail(e.target.value)} /></div>
          {(authView === 'register' || authView === 'forgot') && <div className="lp-code-row"><div {...field('code')}><span className="lp-field-ic"><CheckCircle2 size={15} /></span><input className="lp-input" placeholder="邮箱验证码" value={code} onChange={e => setCode(e.target.value)} /></div><button className="lp-code-btn" onClick={() => sendCode(authView === 'register' ? 'register' : 'reset_password')} disabled={busy || countdown > 0}>{countdown ? countdown + 's' : '获取验证码'}</button></div>}
          <div {...field('password')}><span className="lp-field-ic"><Lock size={15} /></span><input className="lp-input" placeholder={authView === 'forgot' ? '新密码' : '密码'} type="password" value={password} onChange={e => setPassword(e.target.value)} /></div>
          {(authView === 'register' || authView === 'forgot') && <div {...field('confirm')}><span className="lp-field-ic"><Lock size={15} /></span><input className="lp-input" placeholder="确认密码" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} /></div>}
          {err && <div className="lp-msg err"><AlertCircle size={14} /> {err}</div>}
          {msg && <div className="lp-msg ok"><CheckCircle2 size={14} /> {msg}</div>}
          {authView === 'login' && <button className="lp-forgot" onClick={() => setAuthView('forgot')}>忘记密码？</button>}
          <button className="lp-btn-main" onClick={authView === 'login' ? doLogin : authView === 'register' ? doRegister : doReset} disabled={busy}>{busy ? '处理中…' : authView === 'login' ? '登 录' : authView === 'register' ? '注册并登录' : '重置密码'}</button>
          {authView === 'forgot' && <button className="lp-btn-ghost" onClick={() => setAuthView('login')}>返回登录</button>}
          <div className="lp-foot"><DownloadCloud size={12} /> 注册同意以合规方式使用账号 · 问题反馈见官方社区</div>
        </div>
      </div>
    </div>
  )
}
