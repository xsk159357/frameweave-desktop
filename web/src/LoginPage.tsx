// 登录门禁页（墨织 v2）：左侧品牌帧序列 + 右侧极简表单
import { useState } from 'react'
import { Logo } from './Logo'
import { Mail, Lock, CheckCircle2, AlertCircle, Sun, Moon } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

export function LoginPage() {
  const setSession = useAppStore((s) => s.setSession)
  const settings = useAppStore((s) => s.settings)
  const setSettings = useAppStore((s) => s.setSettings)
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

  return (
    <div className="lp-root">
      <div className="lp-grid-bg" />
      <button
        className="lp-theme-toggle"
        onClick={() => setSettings({ theme: settings.theme === 'light' ? 'dark' : 'light' })}
        title={settings.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'}
        aria-label={settings.theme === 'light' ? '切换到暗色主题' : '切换到亮色主题'}
      >
        {settings.theme === 'light' ? <Moon size={13} /> : <Sun size={13} />}
        <span>{settings.theme === 'light' ? '暗色' : '亮色'}</span>
      </button>

      <section className="lp-brand" aria-label="FrameWeave 拾帧">
        <div className="lp-brand-head">
          <div className="lp-brand-logo"><Logo size={40} color="#EDE8DE" /></div>
          <div className="lp-brand-name">拾帧<em>FRAMEWEAVE</em></div>
          <span className="lp-brand-kicker">INKWEAVE / v{__APP_VERSION__}</span>
        </div>

        <div className="lp-display">
          <span className="en">FRAME<b>WEAVE</b></span>
          <div className="cn">把帧编织成故事<span>WEAVE YOUR STORY</span></div>
        </div>
        <p className="lp-slogan">
          素材、脚本、配音、字幕与成片，在同一条可复用的工作流里完成。
          节点是能力，连线是节奏。
        </p>

        <div className="lp-sequence" aria-hidden="true">
          <div className="lp-seq-frame">
            <span className="no">SEQ 01</span>
            <div className="art" />
            <span className="tc">00:00:04:12</span>
          </div>
          <div className="lp-seq-frame">
            <span className="no">SEQ 02</span>
            <div className="art" />
            <span className="tc">00:00:18:03</span>
          </div>
          <div className="lp-seq-frame">
            <span className="no">SEQ 03</span>
            <div className="art" />
            <span className="tc">00:01:02:20</span>
          </div>
          <div className="lp-seq-note">
            取景框 / 时间码 / 轨道<br />
            —— 剪辑师熟悉的三件事。
          </div>
        </div>

        <div className="lp-brand-foot">
          <i className="dot" /> LOCAL ENGINE READY <span>·</span> frameweave.top <span>·</span> 2026
        </div>
      </section>

      <div className="lp-card-wrap">
        <div className="lp-card">
          <div className="lp-card-head">
            <div className="lp-card-title">{authView === 'login' ? '进入工作台' : authView === 'register' ? '创建账号' : '重设密码'}</div>
            <div className="lp-card-sub">{authView === 'login' ? '使用 QQ 邮箱登录你的创作空间' : authView === 'register' ? '验证 QQ 邮箱后完成注册' : '通过邮箱验证码重置密码'}</div>
          </div>

          {authView !== 'forgot' && (
            <div className={'lp-seg' + (authView === 'register' ? ' reg' : '')}>
              <button className={'lp-seg-btn' + (authView === 'login' ? ' on' : '')} onClick={() => setAuthView('login')}>登录</button>
              <button className={'lp-seg-btn' + (authView === 'register' ? ' on' : '')} onClick={() => setAuthView('register')}>注册</button>
              <div className="lp-seg-pill" />
            </div>
          )}

          <div {...field('email')}><span className="lp-field-ic"><Mail size={14} /></span><input className="lp-input" placeholder="QQ 邮箱" value={email} onChange={e => setEmail(e.target.value)} onFocus={() => setFocus('email')} onBlur={() => setFocus('')} /></div>
          {(authView === 'register' || authView === 'forgot') && <div className="lp-code-row"><div {...field('code')}><span className="lp-field-ic"><CheckCircle2 size={14} /></span><input className="lp-input" placeholder="邮箱验证码" value={code} onChange={e => setCode(e.target.value)} onFocus={() => setFocus('code')} onBlur={() => setFocus('')} /></div><button className="lp-code-btn" onClick={() => sendCode(authView === 'register' ? 'register' : 'reset_password')} disabled={busy || countdown > 0}>{countdown ? countdown + 's' : '获取验证码'}</button></div>}
          <div {...field('password')}><span className="lp-field-ic"><Lock size={14} /></span><input className="lp-input" placeholder={authView === 'forgot' ? '新密码（至少 8 位）' : '密码'} type="password" value={password} onChange={e => setPassword(e.target.value)} onFocus={() => setFocus('password')} onBlur={() => setFocus('')} /></div>
          {(authView === 'register' || authView === 'forgot') && <div {...field('confirm')}><span className="lp-field-ic"><Lock size={14} /></span><input className="lp-input" placeholder="确认密码" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} onFocus={() => setFocus('confirm')} onBlur={() => setFocus('')} /></div>}
          {err && <div className="lp-msg err"><AlertCircle size={13} /> {err}</div>}
          {msg && <div className="lp-msg ok"><CheckCircle2 size={13} /> {msg}</div>}
          {authView === 'login' && <button className="lp-forgot" onClick={() => setAuthView('forgot')}>忘记密码？</button>}
          <button className="lp-btn-main" onClick={authView === 'login' ? doLogin : authView === 'register' ? doRegister : doReset} disabled={busy}>
            {busy ? <><span className="lp-spin" /> 处理中</> : authView === 'login' ? '进入工作台' : authView === 'register' ? '创建账号' : '重设密码'}
          </button>
          {authView === 'forgot' && <button className="lp-btn-ghost" onClick={() => setAuthView('login')}>返回登录</button>}
          <div className="lp-foot">登录即表示同意账号合规使用条款<br />问题反馈：support@frameweave.top</div>
        </div>
      </div>
    </div>
  )
}
