// 节点插件管理：已装列表 / 生命周期（禁用/启用/升级/回滚/卸载/健康检查）/ 重新扫描 / 打开目录 / 导出
import { useEffect, useState } from 'react'
import { X, FolderOpen, Puzzle, RefreshCw, FileCode2, Download, Power, PowerOff, RotateCcw, Trash2, Upload, Stethoscope } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

interface PluginInfo {
  pkg: string
  type_id: string
  version?: string
  state?: string
  state_reason?: string
  last_error?: string
  backup_count?: number
  backup_versions?: string[]
}

const STATE_LABEL: Record<string, string> = {
  active: '已启用', disabled: '已禁用', quarantined: '已隔离', updating: '升级中', absent: '缺失',
}

export function PluginPanel({ onClose }: { onClose: () => void }) {
  const [nodes, setNodes] = useState<string[]>([])
  const [dir, setDir] = useState('')
  const [infos, setInfos] = useState<Record<string, PluginInfo>>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const setSpecs = useAppStore((s) => s.setSpecs)
  const pushToast = useAppStore((s) => s.pushToast)

  const refresh = async () => {
    try {
      const u = await api.userNodes(); setNodes(u.nodes || []); setDir(u.dir || '')
      try {
        const m = await api.pluginMetrics()
        const by: Record<string, PluginInfo> = {}
        for (const p of (m.plugins || [])) by[p.pkg] = p
        setInfos(by)
      } catch { /* metrics 可选 */ }
    } catch (e: any) { setMsg('读取失败: ' + (e.message || e)) }
  }
  useEffect(() => { refresh() }, [])

  const act = async (pkg: string, fn: () => Promise<any>, okMsg: string) => {
    setBusy(pkg)
    try {
      const r = await fn()
      if (r && r.ok === false) { pushToast((r.message || '操作失败'), 'err'); return }
      setSpecs(await api.specs())
      await refresh()
      pushToast(okMsg, 'ok')
    } catch (e: any) {
      pushToast((e.message || '操作失败'), 'err')
      await refresh()
    } finally { setBusy('') }
  }

  const onUpdate = (pkg: string) => {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = '.zip'
    input.onchange = async () => {
      const f = input.files && input.files[0]
      if (!f) return
      setBusy(pkg)
      try {
        const r = await api.pluginUpdate(pkg, f)
        if (r && r.ok === false) { pushToast((r.message || '升级失败'), 'err'); return }
        setSpecs(await api.specs()); await refresh()
        pushToast('已升级 ' + pkg + ' → v' + (r.version || ''), 'ok')
      } catch (e: any) {
        pushToast((e.message || '升级失败'), 'err'); await refresh()
      } finally { setBusy('') }
    }
    input.click()
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(8,10,16,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 620, maxWidth: '94vw', maxHeight: '84vh', display: 'flex', flexDirection: 'column',
        background: 'var(--overlay)', border: '1px solid var(--border-strong)', borderRadius: 16, boxShadow: 'var(--shadow-lg)', overflow: 'hidden',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '13px 16px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ width: 26, height: 26, borderRadius: 8, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Puzzle size={14} />
          </div>
          <span style={{ fontSize: 14, fontWeight: 700, flex: 1 }}>节点插件</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 4, lineHeight: 0 }}><X size={15} /></button>
        </div>
        <div className="fw-scroll" style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>插件请从商城下载并安装</span>
            <button className="fw-btn fw-btn-ghost" onClick={async () => {
              try { await api.reloadUserNodes(); setSpecs(await api.specs()); await refresh(); pushToast('节点库已重新扫描', 'ok') } catch (e: any) { pushToast('扫描失败: ' + (e.message || e), 'err') }
            }}>
              <><RefreshCw size={12} /> 重新扫描</>
            </button>
          </div>
          {msg && <div style={{ fontSize: 12, color: 'var(--danger)', lineHeight: 1.5 }}>{msg}</div>}
          {/* 目录 */}
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', background: 'var(--panel-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '8px 10px', wordBreak: 'break-all' }}>
            插件目录：{dir || '…'}
            {dir && <button className="fw-btn fw-btn-ghost" onClick={async () => { try { await api.revealPath(dir) } catch (e: any) { pushToast('无法打开目录: ' + (e.message || e), 'err') } }}
              style={{ marginLeft: 8, padding: '2px 8px', fontSize: 11 }}><><FolderOpen size={11} /> 打开</></button>}
          </div>
          {/* 已装列表 */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: .8, color: 'var(--text-faint)', marginBottom: 6 }}>已安装（{nodes.length}）</div>
            {nodes.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-dim)', padding: '10px 0' }}>暂无第三方插件，请前往商城下载并安装。</div>}
            {nodes.map((n) => {
              const info = infos[n]
              const state = info?.state || 'active'
              const disabled = state === 'disabled'
              const quarantined = state === 'quarantined'
              return (
                <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 9, background: 'var(--panel-2)', border: '1px solid var(--border)', marginBottom: 4, fontSize: 12.5, opacity: disabled ? .72 : 1 }}>
                  <FileCode2 size={12} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{n}</span>
                  {info?.version && <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>v{info.version}</span>}
                  <span className="fw-pill" style={{ fontSize: 10, color: quarantined ? 'var(--danger)' : disabled ? 'var(--text-dim)' : 'var(--accent)' }}>
                    {STATE_LABEL[state] || state}
                  </span>
                  {info && info.backup_count ? <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>备份×{info.backup_count}</span> : null}
                  <span style={{ flex: 1 }} />
                  {/* 生命周期操作 */}
                  {!disabled && !quarantined && (
                    <button title="禁用（节点从画布卸载，可恢复）" disabled={!!busy} onClick={() => act(n, () => api.pluginDisable(n), '已禁用 ' + n)}
                      style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><PowerOff size={12} /></button>
                  )}
                  {(disabled || quarantined) && (
                    <button title="启用（重新校验并加载）" disabled={!!busy} onClick={() => act(n, () => api.pluginEnable(n), '已启用 ' + n)}
                      style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer', color: 'var(--accent)', padding: 3, lineHeight: 0 }}><Power size={12} /></button>
                  )}
                  <button title="升级（上传新 zip，失败自动回滚）" disabled={!!busy || disabled} onClick={() => onUpdate(n)}
                    style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : disabled ? 'default' : 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><Upload size={12} /></button>
                  {info && info.backup_count ? (
                    <button title="回滚到上一版本" disabled={!!busy} onClick={() => act(n, () => api.pluginRollback(n), '已回滚 ' + n)}
                      style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><RotateCcw size={12} /></button>
                  ) : null}
                  <button title="健康检查" disabled={!!busy} onClick={() => act(n, async () => {
                    const h = await api.pluginHealthcheck(n)
                    pushToast(`${n}：${h.healthy ? '健康' : '异常'}${(h.issues || []).length ? ' — ' + h.issues.join('；') : ''}`, h.healthy ? 'ok' : 'err')
                    return { ok: true }
                  }, '')}
                    style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><Stethoscope size={12} /></button>
                  <button title="卸载（删除插件与备份）" disabled={!!busy} onClick={() => {
                    if (!window.confirm(`确定卸载插件 ${n}？将删除插件目录与全部备份。`)) return
                    act(n, () => api.pluginUninstall(n), '已卸载 ' + n)
                  }}
                    style={{ background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer', color: 'var(--danger)', padding: 3, lineHeight: 0 }}><Trash2 size={12} /></button>
                  <button title="导出插件包 zip（可分享/上传商城）" onClick={() => {
                    api.exportNodeZip(n)
                      .then(() => pushToast('已导出 ' + n, 'ok'))
                      .catch((e: any) => pushToast('导出失败: ' + (e.message || e), 'err'))
                  }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><Download size={12} /></button>
                </div>
              )
            })}
          </div>
          {/* 规范说明 */}
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.7, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            插件规范：插件包 = 一个 zip，内含 <span style={{ fontFamily: 'var(--font-mono)' }}>manifest.json</span>（声明 type_id / title / category / kind / inputs / outputs / params / transform）。生命周期：禁用把目录移入 .disabled/（节点从画布卸载，可启用恢复）；升级先备份 3 份再原子替换，失败自动回滚旧版本；回滚恢复最新备份。安全：仅白名单数据变换，不执行任意代码。
          </div>
        </div>
      </div>
    </div>
  )
}
