// 个人中心弹窗（t3 功能开发）：
// ① 账号信息展示（邮箱 / 套餐徽章 / 订阅到期 / 积分 / 绑定设备）
// ② 卡密激活（/api/auth/activate → 刷新会话状态）
// ③ 退出登录（清会话回登录页）
// 视觉与流光织机一致：深空极光 + 玻璃拟态 + 紫蓝渐变强调（复用 --accent/--glass/--border token）。
import { useEffect, useRef, useState } from 'react'
import {
  UserCircle, Crown, Sparkles, Monitor, LogOut,
  KeyRound, ShieldCheck, RefreshCw, Mail, CircleCheck as CheckCircle2,
  CircleAlert as AlertCircle, X, BadgeCheck, CalendarClock, Smartphone, Wallet, Timer,
} from 'lucide-react'
import { api } from './api'
import { useAppStore, expiryMs } from './store'

interface ProfileModalProps {
  open: boolean
  onClose: () => void
}

function fmtDate(ms: number | undefined): string {
  if (!ms) return '未设置'
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return '未设置'
  const now = Date.now()
  const left = d.getTime() - now
  const dd = Math.ceil(left / 86400000)
  const ymd = d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  if (dd >= 0) return `${ymd}（剩余 ${dd} 天）`
  return `${ymd}（已到期）`
}

// 卡密激活错误码 → 用户可读文案（D-08/D-21：不再笼统「卡密无效或签名校验失败」）
const ACTIVATE_ERROR_TEXT: Record<string, string> = {
  card_invalid: '卡密无效或签名校验失败，请检查输入（格式应为 FW- 开头）',
  card_already_used: '该卡密已被使用，请更换一张新卡密',
  card_revoked: '该卡密已作废，请联系管理员',
  not_registered: '账号不存在，请先注册再激活',
  account_disabled: '账号已被禁用，请联系管理员',
  cloud_unavailable: '无法连接云端授权服务器，请联网后重试',
  cloud_not_configured: '云端授权未配置，请检查网络后重试',
  network: '无法连接本地服务，请稍后重试',
  timeout: '请求超时，请检查网络后重试',
}

export function ProfileModal({ open, onClose }: ProfileModalProps) {
  const session = useAppStore((s) => s.session)
  const setSession = useAppStore((s) => s.setSession)
  const pushToast = useAppStore((s) => s.pushToast)
  const [card, setCard] = useState('')
  const [busy, setBusy] = useState(false)
  const [revealDevice, setRevealDevice] = useState(false)
  const [copied, setCopied] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // 打开时聚焦卡密输入框（无障碍/键盘可达）
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 60)
  }, [open])

  if (!open || !session) return null

  const plan = session.plan === 'member' ? 'member' : 'trial'
  const expiresAt = expiryMs(session.expiresAt)

  const copyEmail = async () => {
    try {
      await navigator.clipboard.writeText(session.email)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      pushToast('复制失败，请手动复制', 'err')
    }
  }

  const doActivate = async () => {
    const c = card.trim()
    if (!c) { pushToast('请输入卡密', 'err'); return }
    // 格式预检（D-08）：本地先拦截非 FW- 前缀，避免把格式错误混入签名失败
    if (!/^FW-/i.test(c)) {
      pushToast('卡密格式应为 FW- 开头（如 FW-XXXX-XXXX）', 'err')
      return
    }
    setBusy(true)
    try {
      const r = await api.activate(session.email, c)
      if (!r.ok) throw Object.assign(new Error(r.message || '激活失败'), { code: (r as any).code })
      // 刷新会话：plan/expires_at 以云端为准（秒 → 毫秒统一由 expiryMs 归一）
      setSession({
        ...session,
        plan: r.plan || 'member',
        expiresAt: expiryMs(r.expires_at) ?? session.expiresAt,
      })
      setCard('')
      pushToast('卡密激活成功，会员权益已生效', 'ok')
    } catch (e: any) {
      const code = (e as any)?.code || ''
      const msg = (code && ACTIVATE_ERROR_TEXT[code]) || e?.message || '激活失败，请检查卡密'
      pushToast(msg, 'err')
    } finally {
      setBusy(false)
    }
  }

  const doLogout = () => {
    try { ;(window as any).frameweave?.log?.('info', 'profile logout: ' + session.email) } catch { /* 浏览器模式 */ }
    setSession(null)   // 清会话 → App 自动回登录页
    onClose()
  }

  return (
    <div className="fw-profile-modal" role="dialog" aria-modal="true" aria-labelledby="fw-profile-title" onClick={onClose}>
      <div className="fw-profile-card" onClick={(e) => e.stopPropagation()}>
        {/* 头部 */}
        <div className="fw-profile-head">
          <div className="fw-profile-head-ic"><UserCircle size={22} /></div>
          <div>
            <div id="fw-profile-title" className="fw-profile-title">个人中心</div>
            <div className="fw-profile-sub">账号与订阅管理</div>
          </div>
          <button className="fw-profile-close" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>

        {/* 账号邮箱 */}
        <div className="fw-profile-email">
          <Mail size={15} className="fw-profile-email-ic" />
          <span className="fw-profile-email-txt">{session.email}</span>
          <button className="fw-profile-copy" onClick={copyEmail} title="复制邮箱">{copied ? <CheckCircle2 size={13} /> : <KeyRound size={13} />}{copied ? '已复制' : '复制'}</button>
        </div>

        {/* 套餐徽章 */}
        <div className={'fw-plan-badge ' + plan}>
          {plan === 'member' ? <BadgeCheck size={14} /> : <Sparkles size={14} />}
          <span>{plan === 'member' ? '会员账号' : '试用账号'}</span>
          {plan === 'member'
            ? <em className="fw-plan-badge-tip">卡密已激活 · 完整权益</em>
            : <em className="fw-plan-badge-tip">激活卡密升级会员</em>}
        </div>

        {/* 数据四格 */}
        <div className="fw-profile-grid">
          <div className="fw-profile-stat">
            <Crown size={15} />
            <span>当前套餐</span>
            <b>{plan === 'member' ? '会员' : '试用'}</b>
          </div>
          <div className="fw-profile-stat">
            <CalendarClock size={15} />
            <span>订阅到期</span>
            <b>{fmtDate(expiresAt)}</b>
          </div>
          <div className="fw-profile-stat">
            <Wallet size={15} />
            <span>积分余额</span>
            <b>{session.credits ?? 0}</b>
          </div>
          <div className="fw-profile-stat">
            <Smartphone size={15} />
            <span>绑定设备</span>
            <b>{session.deviceId ? (revealDevice ? session.deviceId : session.deviceId.slice(0, 18)) : '本机'}</b>
          </div>
        </div>
        {session.deviceId && (
          <div className="fw-profile-device">
            <Monitor size={12} />
            <span>本账号 1 账号 1 设备绑定，如在新设备登录，本机将被自动下线。</span>
            <button onClick={() => setRevealDevice((v) => !v)}>{revealDevice ? '隐藏' : '显示'}</button>
          </div>
        )}

        {/* 卡密激活 */}
        <div className="fw-profile-section">
          <div className="fw-profile-label"><ShieldCheck size={12} />卡密激活</div>
          <div className="fw-profile-activate">
            <input
              ref={inputRef}
              value={card}
              onChange={(e) => setCard(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') doActivate() }}
              placeholder="输入 FW- 开头的卡密"
              aria-label="卡密"
              disabled={busy}
            />
            <button className="fw-btn fw-btn-primary" onClick={doActivate} disabled={busy}>
              {busy ? <><RefreshCw size={13} className="fw-spin" />激活中…</> : <><KeyRound size={13} />激活</>}
            </button>
          </div>
          <div className="fw-profile-hint">试用账号激活卡密后自动升级为会员，到期时间在云端续加。</div>
        </div>

        {/* 操作 */}
        <div className="fw-profile-actions">
          <button className="fw-btn fw-btn-ghost" onClick={() => pushToast('续费请联系管理员获取卡密', 'info')}><Timer size={13} />续费说明</button>
          <button className="fw-btn fw-btn-danger" onClick={doLogout}><LogOut size={14} />退出登录</button>
        </div>
      </div>
    </div>
  )
}
