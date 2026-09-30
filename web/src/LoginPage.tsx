// 登录门禁页：独立登录窗口（未登录不可进入主界面）
import { useState } from 'react'
import {
  Mail, Lock, KeyRound, CheckCircle2, AlertCircle,
  Layers, Workflow, Sparkles, ShieldCheck, DownloadCloud,
} from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

function Logo({ size = 76 }: { size?: number }) {
  return (
    <svg width={size * 0.56} height={size * 0.56} viewBox="0 0 48 48" fill="none">
      <rect x="3" y="6" width="42" height="36" rx="7" stroke="#fff" strokeWidth="3.2" fill="rgba(255,255,255,.08)" />
      <path d="M3 13C3 9 6 6 10.5 6h2.5v4H10.5c-1.5 0-3.5 1.3-3.5 3v0z" fill="#fff" />
      <path d="M45 13c0-4-3-7-7.5-7h-2.5v4h2.5c1.5 0 3.5 1.3 3.5 3z" fill="#fff" />
      <rect x="3" y="34" width="4" height="6.5" rx="1.6" fill="#fff" />
      <rect x="41" y="34" width="4" height="6.5" rx="1.6" fill="#fff" />
      <path d="M21 19.5l9 4.5-9 4.5z" fill="#fff" />
      <rect x="16" y="37.5" width="3" height="2.6" rx="1.3" fill="#fff" opacity=".9" />
      <rect x="22.5" y="37.5" width="3" height="2.6" rx="1.3" fill="#fff" opacity=".9" />
      <rect x="29" y="37.5" width="3" height="2.6" rx="1.3" fill="#fff" opacity=".9" />
    </svg>
  )
}

export function LoginPage() {
  const setSession = useAppStore((s) => s.setSession)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [card, setCard] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [focus, setFocus] = useState('')

  const deviceId = (() => {
    let d = localStorage.getItem('fw_device_id')
    if (!d) { d = 'dev-' + Math.random().toString(36).slice(2, 10); localStorage.setItem('fw_device_id', d) }
    return d
  })()

  const doLogin = async () => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      if (!email.includes('@') || password.length < 4) {
        setErr('请输入有效邮箱和至少 4 位密码'); return
      }
      const r = await api.login(email, password, deviceId)
      if (r.ok && r.session) {
        setSession({
          token: r.session.token, email: r.session.email, expiresAt: r.session.expires_at,
          plan: r.session.plan || 'trial', credits: r.session.credits || 0, deviceId,
        })
        if (r.device_kick) setMsg(r.device_kick.message || '账号已在其他设备登录，旧设备已下线')
      } else {
        setErr(r.message || '登录失败')
      }
    } catch (e: any) { setErr(e.message || '登录失败') }
    finally { setBusy(false) }
  }

  const doActivate = async () => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      const r = await api.activate(email, card)
      if (r.ok) setMsg(r.message || '激活成功')
      else setErr(r.message || '激活失败')
    } catch (e: any) { setErr(e.message || '激活失败') }
    finally { setBusy(false) }
  }

  const field = (name: string) => ({ className: 'lp-field' + (focus === name ? ' focus' : '') })
  const featIc = (c: string) => ({ color: c })

  return (
    <div className="lp-root">
      <div className="lp-grid-bg" />
      <div className="lp-glow" style={{ width: 460, height: 460, left: '-70px', top: '-70px', background: 'rgba(108,140,255,.5)' }} />
      <div className="lp-glow" style={{ width: 400, height: 400, right: '15%', bottom: '-110px', background: 'rgba(167,139,255,.42)' }} />
      <div className="lp-glow" style={{ width: 300, height: 300, left: '38%', top: '-140px', background: 'rgba(77,208,225,.28)' }} />

      {/* 品牌区 */}
      <div className="lp-brand">
        <div className="lp-logo"><Logo size={76} /></div>
        <div className="lp-title">
          拾帧 <span className="lp-title-grad">FrameWeave</span>
        </div>
        <div className="lp-slogan">节点式 AI 视频创作工作台<br />把灵感编成画面，把画面剪成故事</div>
        <div className="lp-feats">
          <div className="lp-feat">
            <div className="lp-feat-ic"><Workflow size={17} {...featIc('var(--accent)')} /></div>
            无限画布 · 节点式工作流编排
          </div>
          <div className="lp-feat">
            <div className="lp-feat-ic"><Layers size={17} {...featIc('var(--accent2)')} /></div>
            节点与工作流商城，一键安装即用
          </div>
          <div className="lp-feat">
            <div className="lp-feat-ic"><Sparkles size={17} {...featIc('var(--cyan)')} /></div>
            AI 驱动的视频创作全流程
          </div>
          <div className="lp-feat">
            <div className="lp-feat-ic"><ShieldCheck size={17} {...featIc('var(--success)')} /></div>
            账号体系 · 安全订阅
          </div>
        </div>
        <div className="lp-ver">FrameWeave v{__APP_VERSION__}</div>
      </div>

      {/* 登录卡片 */}
      <div className="lp-card-wrap">
        <div className="lp-card">
          <div className="lp-card-head">
            <div className="lp-card-title">{mode === 'login' ? '欢迎回来' : '创建账号'}</div>
            <div className="lp-card-sub">
              {mode === 'login' ? '登录后继续你的创作' : '注册后立享 1 天试用'}
            </div>
          </div>

          <div className="lp-seg" style={{ height: 38 }}>
            <div className="lp-seg-pill" style={{ width: 'calc(50% - 4px)', left: mode === 'login' ? 4 : 'calc(50% + 0px)' }} />
            {(['login', 'register'] as const).map(m => (
              <button key={m} className={'lp-seg-btn' + (mode === m ? ' on' : '')} onClick={() => { setMode(m); setErr(''); setMsg('') }}>
                {m === 'login' ? '登 录' : '注 册'}
              </button>
            ))}
          </div>

          <div {...field('email')}>
            <span className="lp-field-ic"><Mail size={15} /></span>
            <input className="lp-input" placeholder="邮箱（QQ 邮箱亦可）" value={email}
              onFocus={() => setFocus('email')} onBlur={() => setFocus('')}
              onChange={e => setEmail(e.target.value)} />
          </div>
          <div {...field('password')}>
            <span className="lp-field-ic"><Lock size={15} /></span>
            <input className="lp-input" placeholder="密码" type="password" value={password}
              onFocus={() => setFocus('password')} onBlur={() => setFocus('')}
              onChange={e => setPassword(e.target.value)} />
          </div>
          <div {...field('card')} style={{ display: mode === 'register' ? 'block' : 'none' }}>
            <span className="lp-field-ic"><KeyRound size={15} /></span>
            <input className="lp-input" placeholder="卡密（FW-XXXX-XXXX-XXXX）" value={card}
              onFocus={() => setFocus('card')} onBlur={() => setFocus('')}
              onChange={e => setCard(e.target.value)} />
          </div>

          {err && (
            <div className="lp-msg err"><AlertCircle size={14} /> {err}</div>
          )}
          {msg && (
            <div className="lp-msg ok"><CheckCircle2 size={14} /> {msg}</div>
          )}

          {mode === 'register' && (
            <button className="lp-btn-ghost" onClick={doActivate} disabled={busy}>
              {busy ? '处理中…' : '卡密激活'}
            </button>
          )}

          <button className="lp-btn-main" onClick={doLogin} disabled={busy}>
            {busy ? <><span className="lp-spin" /> {mode === 'login' ? '登录中…' : '注册中…'}</> : (mode === 'login' ? '登 录' : '注册并登录')}
          </button>

          <div className="lp-foot">
            <DownloadCloud size={12} /> 注册同意以合规方式使用账号 · 问题反馈见官方社区
          </div>
        </div>
      </div>
    </div>
  )
}
