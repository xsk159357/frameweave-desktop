// 模型中心（D-11 / t11）：ASR / TTS / 视觉 / LLM 分组的状态面板
// - 数据源：GET /api/models/status（models_registry 只读快照，与 core/check_models 同源逻辑）
//           + GET/PUT /api/settings/llm + POST /api/llm/probe（LLM 分组真实配置/探测）
// - 动作：一键把 core/check_models 加入画布运行自检（下载/校验/缺失指引）、复制路径、定位目录、
//         LLM 配置保存与连通性探测。删除模型出于安全不提供盲删（给出路径由 check_models/资源管理器处置）。
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Brain, Cpu, AudioLines, Captions, Eye, CheckCircle2, XCircle, Loader2,
  RefreshCw, Copy, FolderOpen, HardDriveDownload, Wifi, WifiOff, Bot,
} from 'lucide-react'
import { modelsApi, llmApi, type ModelStatusEntry, type ModelCatalogEntry } from '../panelsApi'
import { revealAsset } from '../panelsApi'
import { useAppStore } from '../store'

function fmtSize(n?: number | null): string {
  if (n == null) return '—'
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

function groupOf(id: string, name: string): 'asr' | 'tts' | 'vision' | 'llm' | 'other' {
  const h = (id + '|' + name).toLowerCase()
  if (/asr|whisper|语音识别|转写|faster-whisper|parakeet/.test(h)) return 'asr'
  if (/tts|cosyvoice|edge-tts|语音合成|配音|gpt-sovits|indextts/.test(h)) return 'tts'
  if (/vision|visual|clip|yolo|图像|视觉|检测|识别模型|scene/.test(h)) return 'vision'
  if (/llm|gpt|qwen|deepseek|kimi|glm|chat/.test(h)) return 'llm'
  return 'other'
}

const GROUPS = [
  { key: 'asr', label: 'ASR 语音识别', icon: AudioLines },
  { key: 'tts', label: 'TTS 语音合成', icon: Captions },
  { key: 'vision', label: '视觉 / 解析', icon: Eye },
  { key: 'llm', label: 'LLM 大语言模型', icon: Bot },
  { key: 'other', label: '其他模型', icon: Cpu },
] as const

const ModelRow = memo(function ModelRow({ m, onCopy, onReveal, onDownload, onDelete, busy }: {
  m: ModelCatalogEntry
  onCopy: (m: ModelCatalogEntry) => void
  onReveal: (m: ModelCatalogEntry) => void
  onDownload: (m: ModelCatalogEntry) => void
  onDelete: (m: ModelCatalogEntry) => void
  busy: string
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 9, padding: '8px 10px', borderRadius: 10,
      border: '1px solid var(--border)', background: 'var(--bg-panel-2)', marginBottom: 6, fontSize: 11.5,
    }}>
      {m.ready
        ? <CheckCircle2 size={14} color="var(--success)" style={{ flexShrink: 0 }} />
        : <XCircle size={14} color="var(--danger)" style={{ flexShrink: 0 }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 650 }}>{m.name}</span>
          <span className="fw-badge" style={{ background: 'var(--bg-panel)', color: m.ready ? 'var(--success)' : 'var(--danger)', flexShrink: 0, fontSize: 9 }}>
            {m.ready ? '就绪' : '缺失'}
          </span>
          {m.source && <span className="fw-badge" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)', flexShrink: 0, fontSize: 9 }}>{m.source}</span>}
          {m.weight_file && <span style={{ color: 'var(--text-faint)', fontSize: 9.5, flexShrink: 0 }}>{m.weight_file}</span>}
          <span style={{ color: 'var(--text-faint)', fontSize: 10, flexShrink: 0 }}>{m.size_bytes != null ? fmtSize(m.size_bytes) : '—'}</span>
        </div>
        <div style={{ color: 'var(--text-dim)', fontSize: 10, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          title={m.path}>
          <span style={{ fontFamily: 'var(--font-mono)' }}>{m.path || '（路径未解析）'}</span>
          {m.warnings && m.warnings.length > 0 && <span style={{ color: 'var(--running)', marginLeft: 6 }}>{m.warnings[0]}</span>}
        </div>
        {!m.ready && m.download.length > 0 && (
          <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 3, lineHeight: 1.6 }}>
            {m.download.slice(0, 2).map((d, i) => <div key={i} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>↓ {d}</div>)}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
        <button onClick={() => onCopy(m)} title="复制路径" className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          <Copy size={12} />
        </button>
        <button onClick={() => onReveal(m)} disabled={!m.path} title="在资源管理器中查看"
          className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          <FolderOpen size={12} />
        </button>
        {!m.ready && !m.installed && (
          <button onClick={() => onDownload(m)} disabled={busy === m.id} title="下载模型"
            className="fw-btn fw-btn-ghost" style={{ padding: '3px 7px', fontSize: 10, color: 'var(--accent-strong)' }}>
            {busy === m.id ? <Loader2 size={11} className="fw-spin" /> : <><HardDriveDownload size={11} /> 下载</>}
          </button>
        )}
        {(m.ready || m.installed) && (
          <button onClick={() => onDelete(m)} disabled={busy === m.id} title="删除模型（后端引用保护）"
            className="fw-btn fw-btn-ghost" style={{ padding: '3px 7px', fontSize: 10, color: 'var(--danger)' }}>
            删除
          </button>
        )}
      </div>
    </div>
  )
})

export function ModelCenter() {
  const pushToast = useAppStore((s) => s.pushToast)
  const specs = useAppStore((s) => s.specs)
  const [models, setModels] = useState<ModelCatalogEntry[]>([])
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [busyModel, setBusyModel] = useState('')
  const [progress, setProgress] = useState<Record<string, number>>({})
  const seq = useRef(0)

  // ---- LLM 配置（真实读写 /api/settings/llm + probe） ----
  const [llm, setLlm] = useState({ base_url: '', api_key: '', model: '' })
  const [llmLoaded, setLlmLoaded] = useState(false)
  const [llmSaving, setLlmSaving] = useState(false)
  const [probe, setProbe] = useState<null | { ok: boolean; message?: string; latency_ms?: number }>(null)
  const [probing, setProbing] = useState(false)

  const load = useCallback(async (silent = false) => {
    const s = ++seq.current
    setLoading(true)
    try {
      const [catalog, status] = await Promise.all([
        modelsApi.list().catch(() => ({ ok: false, models: [] as ModelCatalogEntry[], items: [] as ModelCatalogEntry[] })),
        modelsApi.status(),
      ])
      if (s !== seq.current) return
      const statusById = new Map((status.models || []).map((m) => [m.id, m]))
      const listed = (catalog.models || catalog.items || []) as ModelCatalogEntry[]
      const merged = (listed.length > 0 ? listed : (status.models || []).map((m) => ({ ...m, installed: m.ready }))).map((m: ModelCatalogEntry) => ({ ...statusById.get(m.id), ...m, installed: m.installed ?? statusById.get(m.id)?.ready ?? m.ready }))
      setModels(merged)
      setErr('')
    } catch (e: any) {
      if (!silent) setErr((e.message || '模型状态读取失败') + '（后端需支持 GET /api/models/status）')
    } finally {
      if (s === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    llmApi.get().then((s) => {
      setLlm({ base_url: s.base_url || '', api_key: s.api_key || '', model: s.model || '' })
      setLlmLoaded(true)
    }).catch(() => setLlmLoaded(true))
  }, [])

  const groups = useMemo(() => {
    const by = new Map<string, ModelStatusEntry[]>()
    for (const m of models) {
      const g = groupOf(m.id, m.name)
      if (!by.has(g)) by.set(g, [])
      by.get(g)!.push(m)
    }
    return GROUPS.map((g) => ({ ...g, items: by.get(g.key) || [] })).filter((g) => g.items.length > 0)
  }, [models])

  const readyCount = models.filter((m) => m.ready).length
  const missingCount = models.length - readyCount

  const copy = async (m: ModelStatusEntry) => {
    try { await navigator.clipboard.writeText(m.path || ''); pushToast('路径已复制', 'ok') }
    catch { pushToast('复制失败', 'err') }
  }
  const reveal = async (m: ModelCatalogEntry) => {
    const ok = await revealAsset(m.path)
    pushToast(ok ? '已打开所在目录' : '路径不存在', ok ? 'ok' : 'err')
  }
  const copyModel = async (m: ModelCatalogEntry) => {
    try { await navigator.clipboard.writeText(m.path || ''); pushToast('路径已复制', 'ok') } catch { pushToast('复制失败', 'err') }
  }
  const downloadModel = async (m: ModelCatalogEntry) => {
    setBusyModel(m.id); setProgress((p) => ({ ...p, [m.id]: 1 }))
    try {
      const r = await modelsApi.download(m.id)
      setProgress((p) => ({ ...p, [m.id]: 100 }))
      pushToast(r.message || '模型下载已完成/进入队列', 'ok')
      await load(false)
    } catch (e: any) { pushToast('模型下载失败：' + (e.message || e), 'err') }
    finally { setBusyModel('') }
  }
  const deleteModel = async (m: ModelCatalogEntry) => {
    if (!window.confirm('确认删除模型「' + m.name + '」？若被节点引用，后端会阻止删除。')) return
    setBusyModel(m.id)
    try { await modelsApi.remove(m.id); pushToast('模型已删除', 'ok'); await load(false) }
    catch (e: any) { pushToast('删除失败（可能仍被节点引用）：' + (e.message || e), 'err') }
    finally { setBusyModel('') }
  }

  const saveLlm = async () => {
    setLlmSaving(true)
    try {
      await llmApi.put({ base_url: llm.base_url, api_key: llm.api_key, model: llm.model })
      pushToast('LLM 设置已保存', 'ok')
    } catch (e: any) { pushToast('保存失败：' + (e.message || e), 'err') }
    finally { setLlmSaving(false) }
  }
  const runProbe = async () => {
    setProbing(true); setProbe(null)
    try {
      const r = await llmApi.probe()
      setProbe(r)
      pushToast(r.ok ? 'LLM 连通正常' : 'LLM 探测失败', r.ok ? 'ok' : 'err')
    } catch (e: any) { setProbe({ ok: false, message: e.message || '探测失败' }) }
    finally { setProbing(false) }
  }

  // 一键自检：把 core/check_models 加到画布并运行（下载/校验/缺失指引的官方路径）
  const checkNode = specs.find((s) => /check_models|模型自检|模型检查/i.test(s.type_id + s.title))
  const runCheck = () => {
    if (!checkNode) { pushToast('未安装 core/check_models 节点，请先到商城安装', 'err'); return }
    ;(window as any).__fwAddNode?.(checkNode.type_id)
    pushToast('已添加「模型自检」节点到画布，右键节点选择运行即可', 'info')
  }

  const input: any = {
    width: '100%', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 9px',
    fontSize: 11.5, outline: 'none', background: 'var(--input-bg)', color: 'inherit',
    boxSizing: 'border-box',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Brain size={15} color="var(--accent)" />
        <b style={{ fontSize: 13.5 }}>模型中心</b>
        <span className="fw-badge" style={{ background: 'var(--success-soft)', color: 'var(--success)' }}>{readyCount} 就绪</span>
        {missingCount > 0 && <span className="fw-badge" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>{missingCount} 缺失</span>}
        <button className="fw-btn fw-btn-ghost" title="刷新"
          onClick={() => void load(false)} style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          {loading ? <Loader2 size={12} className="fw-spin" /> : <RefreshCw size={12} />}
        </button>
        <div style={{ flex: 1 }} />
        <button onClick={runCheck} title="把 core/check_models 加入画布运行自检（下载/校验/缺失指引）"
          className="fw-btn fw-btn-ghost" style={{ color: 'var(--accent-strong)', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
          <HardDriveDownload size={12} /> 运行模型自检
        </button>
      </div>

      {err && <div style={{ padding: '6px 16px', fontSize: 11, color: 'var(--danger)', background: 'var(--danger-soft)' }}>{err}</div>}
      <div style={{ padding: '6px 16px 2px', fontSize: 10.5, color: 'var(--text-faint)' }}>
        数据源：models_registry 只读快照（与 core/check_models 同源）；下载/校验请运行自检节点。
      </div>

      <div className="fw-scroll" style={{ flex: 1, overflow: 'auto', padding: 10, minHeight: 0 }}>
        {groups.map((g) => (
          <div key={g.key} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px', fontSize: 11, fontWeight: 700, color: 'var(--text-dim)', letterSpacing: .6 }}>
              <g.icon size={11} /> {g.label}（{g.items.length}）
            </div>
            {g.items.map((m) => (
              <div key={m.id}>
                <ModelRow key={m.id} m={m} onCopy={copyModel} onReveal={reveal} onDownload={downloadModel} onDelete={deleteModel} busy={busyModel} />
                {busyModel === m.id && progress[m.id] != null && (
                  <div style={{ height: 3, margin: '-4px 10px 6px', background: 'var(--bg-panel-2)', borderRadius: 999, overflow: 'hidden' }}>
                    <div style={{ width: (progress[m.id] || 1) + '%', height: '100%', background: 'var(--accent)', transition: 'width .2s' }} />
                  </div>
                )}
              </div>
            ))}
            {g.items.length === 0 && <div style={{ color: 'var(--text-faint)', fontSize: 10.5, padding: '2px 2px 6px' }}>未登记模型</div>}
          </div>
        ))}
        {models.length === 0 && !err && (
          <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-faint)', fontSize: 12, lineHeight: 2 }}>
            {loading ? <Loader2 size={16} className="fw-spin" style={{ marginBottom: 4 }} /> : <Cpu size={16} style={{ opacity: .5, marginBottom: 4 }} />}
            <div>{loading ? '读取模型清单…' : '暂无模型登记'}</div>
          </div>
        )}

        {/* LLM 配置 */}
        <div style={{ marginTop: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px', fontSize: 11, fontWeight: 700, color: 'var(--text-dim)', letterSpacing: .6 }}>
            <Wifi size={11} /> LLM 配置（用于文案/润色类节点）
          </div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 10, background: 'var(--bg-panel-2)', marginBottom: 6 }}>
            {!llmLoaded ? (
              <div style={{ color: 'var(--text-faint)', fontSize: 11 }}>读取配置…</div>
            ) : (
              <>
                <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                  <input style={{ ...input, flex: 1.4 }} placeholder="API 地址（OpenAI 兼容 base_url）"
                    value={llm.base_url} onChange={(e) => setLlm((p) => ({ ...p, base_url: e.target.value }))} />
                  <input style={{ ...input, flex: 1 }} placeholder="模型名（如 qwen-plus）"
                    value={llm.model} onChange={(e) => setLlm((p) => ({ ...p, model: e.target.value }))} />
                </div>
                <input style={{ ...input, marginBottom: 6 }} type="password" placeholder="API Key（保存在本机密钥库）"
                  value={llm.api_key} onChange={(e) => setLlm((p) => ({ ...p, api_key: e.target.value }))} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button onClick={saveLlm} disabled={llmSaving} className="fw-btn fw-btn-primary" style={{ fontSize: 11, padding: '6px 12px' }}>
                    {llmSaving ? <Loader2 size={11} className="fw-spin" /> : '保存设置'}
                  </button>
                  <button onClick={runProbe} disabled={probing} className="fw-btn fw-btn-ghost" style={{ fontSize: 11, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    {probing ? <Loader2 size={11} className="fw-spin" /> : <Wifi size={11} />} 测试连通
                  </button>
                  {probe && (
                    <span style={{ fontSize: 10.5, display: 'inline-flex', alignItems: 'center', gap: 4,
                      color: probe.ok ? 'var(--success)' : 'var(--danger)' }}>
                      {probe.ok ? <Wifi size={10} /> : <WifiOff size={10} />}
                      {probe.ok ? (probe.latency_ms != null ? `连通（${probe.latency_ms}ms）` : '连通') : (probe.message || '探测失败')}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}