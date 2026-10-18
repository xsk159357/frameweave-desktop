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
    color: 'var(--brass)',
    body: '导入视频、语音识别、配音、字幕、渲染……每种能力都是一个「节点」。从左侧节点库点一下，节点就会放到画布上。',
    tip: '双击画布空白处可快速搜索节点',
  },
  {
    icon: GitBranch,
    title: '工作流 = 把节点连成流水线',
    color: 'var(--info)',
    body: '把节点用连线串起来，数据就从上一个节点流到下一个：视频 → 识别 → 配音 → 渲染。点「一键出片」整条链一次跑完，失败节点可单独重试。',
    tip: '长任务支持断点续跑：中断后点「继续运行」',
  },
  {
    icon: Store,
    title: '商城 = 安装更多节点与模板',
    color: 'var(--brass)',
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
      background: 'rgba(4,5,6,.7)',
      animation: 'fw-auth-fade .2s ease-out both',
    }}>
      <div style={{
        width: 'min(480px, 92vw)', border: '1px solid var(--line-strong)', borderRadius: 8,
        background: 'var(--ink-800)',
        boxShadow: 'var(--shadow-2)', padding: 24,
        animation: 'fw-pop-in .24s var(--t-ease) both',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <Icon size={20} color={s.color} />
          <b style={{ fontSize: 14.5, fontWeight: 650 }}>{s.title}</b>
          <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-faint)', fontFamily: 'var(--font-mono)' }}>{step + 1} / {STEPS.length}</span>
          <button onClick={() => finish(false)} title="跳过引导" style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', padding: 3 }}>
            <X size={15} />
          </button>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.85, minHeight: 96 }}>{s.body}</div>
        <div style={{
          marginTop: 6, padding: '8px 11px', borderRadius: 4, fontSize: 10.5,
          background: 'var(--ink-700)', border: '1px solid var(--line)', color: 'var(--paper-dim)', display: 'flex', gap: 7, alignItems: 'baseline',
        }}><span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '.12em', color: 'var(--brass)' }}>TIP</span>{s.tip}</div>

        {/* 进度点 */}
        <div style={{ display: 'flex', justifyContent: 'center', gap: 7, margin: '18px 0 14px' }}>
          {STEPS.map((_, i) => (
            <span key={i} style={{
              width: i === step ? 18 : 5, height: 2,
              background: i === step ? 'var(--brass)' : 'var(--line-strong)',
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