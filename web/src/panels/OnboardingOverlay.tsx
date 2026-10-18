// 首登引导（D-21 / t11）：一次性三步说明「节点 / 工作流 / 商城」关系
// 仅首次进入工作台显示（localStorage fw_onboarding_done），可跳过；步进器 + 关系示意图
import { useEffect, useState } from 'react'
import { Puzzle, GitBranch, Store, X, ArrowRight, ArrowLeft, Check } from 'lucide-react'
import { useAppStore } from '../store'

const ONBOARDING_KEY = 'fw_onboarding_done'

const STEPS = [
  {
    icon: Puzzle,
    title: '节点 = 能力积木',
    color: '#7c8cf8',
    body: '导入视频、语音识别、配音、字幕、渲染……每种能力都是一个「节点」。从左侧节点库点一下，节点就会放到画布上。',
    tip: '双击画布空白处可快速搜索节点',
  },
  {
    icon: GitBranch,
    title: '工作流 = 把节点连成流水线',
    color: '#34d399',
    body: '把节点用连线串起来，数据就从上一个节点流到下一个：视频 → 识别 → 配音 → 渲染。点「一键出片」整条链一次跑完，失败节点可单独重试。',
    tip: '长任务支持断点续跑：中断后点「继续运行」',
  },
  {
    icon: Store,
    title: '商城 = 安装更多节点与模板',
    color: '#fbbf24',
    body: '官方与社区节点、一条龙模板都从商城下载安装。装好后自动刷新节点库，还能在工作流面板直接创建模板。',
    tip: '缺哪个节点，工作流会自动提示去商城安装',
  },
]

export function OnboardingOverlay() {
  const session = useAppStore((s) => s.session)
  const [step, setStep] = useState(0)
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (!session) return
    let done = true
    try { done = localStorage.getItem(ONBOARDING_KEY) === '1' } catch { /* 隐私模式忽略 */ }
    if (!done) {
      const t = setTimeout(() => setShow(true), 400)
      return () => clearTimeout(t)
    }
  }, [session])

  if (!show || !session) return null

  const finish = (mark = true) => {
    setShow(false)
    if (mark) { try { localStorage.setItem(ONBOARDING_KEY, '1') } catch { /* ignore */ } }
  }

  const s = STEPS[step]
  const Icon = s.icon

  return (
    <div className="fw-onboarding" style={{
      position: 'fixed', inset: 0, zIndex: 4000, display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'rgba(6,9,18,.72)', backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
      animation: 'fw-auth-fade .2s ease-out both',
    }}>
      <div style={{
        width: 'min(480px, 92vw)', border: '1px solid var(--border-strong)', borderRadius: 20,
        background: 'var(--glass-strong)', backdropFilter: 'var(--glass-blur)', WebkitBackdropFilter: 'var(--glass-blur)',
        boxShadow: 'var(--elev-3), 0 0 40px rgba(124,140,255,.16)', padding: 24,
        animation: 'fw-pop-in .24s var(--t-ease) both',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Icon size={20} color={s.color} />
          <b style={{ fontSize: 16 }}>{s.title}</b>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>{step + 1} / {STEPS.length}</span>
          <button onClick={() => finish(false)} title="跳过引导" style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', padding: 3 }}>
            <X size={15} />
          </button>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.85, minHeight: 96 }}>{s.body}</div>
        <div style={{
          marginTop: 6, padding: '8px 12px', borderRadius: 10, fontSize: 11,
          background: 'var(--bg-panel-2)', border: '1px solid var(--border)', color: 'var(--accent-strong)',
        }}>💡 {s.tip}</div>

        {/* 进度点 */}
        <div style={{ display: 'flex', justifyContent: 'center', gap: 7, margin: '18px 0 14px' }}>
          {STEPS.map((_, i) => (
            <span key={i} style={{
              width: i === step ? 22 : 7, height: 7, borderRadius: 999,
              background: i === step ? 'var(--accent)' : 'var(--border-strong)',
              transition: 'width .2s var(--t-ease)',
            }} />
          ))}
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <button onClick={() => setStep((p) => Math.max(0, p - 1))} disabled={step === 0}
            className="fw-btn fw-btn-ghost" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
            <ArrowLeft size={12} /> 上一步
          </button>
          {step < STEPS.length - 1 ? (
            <button onClick={() => setStep((p) => p + 1)} className="fw-btn fw-btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
              下一步 <ArrowRight size={12} />
            </button>
          ) : (
            <button onClick={() => finish(true)} className="fw-btn fw-btn-primary" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
              <Check size={12} /> 开始使用
            </button>
          )}
        </div>
      </div>
    </div>
  )
}