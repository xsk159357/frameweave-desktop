// 节点插件管理（v1.2）：已装列表 / 导入 zip / 打开目录 / 规范说明
import { useEffect, useRef, useState } from 'react'
import { X, Upload, FolderOpen, Puzzle, RefreshCw, FileCode2, Download } from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'

export function PluginPanel({ onClose }: { onClose: () => void }) {
  const [nodes, setNodes] = useState<string[]>([])
  const [dir, setDir] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const setSpecs = useAppStore((s) => s.setSpecs)
  const pushToast = useAppStore((s) => s.pushToast)

  const refresh = async () => {
    try { const u = await api.userNodes(); setNodes(u.nodes || []); setDir(u.dir || '') } catch (e: any) { setMsg('读取失败: ' + (e.message || e)) }
  }
  useEffect(() => { refresh() }, [])

  const doImport = async (f: File) => {
    setBusy(true); setMsg('')
    try {
      const r = await api.installUserNode(f)
      await api.reloadUserNodes()
      try { setSpecs(await api.specs()) } catch { /* ignore */ }
      pushToast(r.message || '插件导入成功，节点库已刷新', 'ok')
      await refresh()
    } catch (e: any) { setMsg('导入失败: ' + (e.message || e)); pushToast('导入失败: ' + (e.message || e), 'err') }
    setBusy(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(8,10,16,.66)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 540, maxWidth: '92vw', maxHeight: '82vh', display: 'flex', flexDirection: 'column',
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
          {/* 导入 */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="fw-btn fw-btn-primary" disabled={busy} onClick={() => fileRef.current?.click()}>
              <><Upload size={13} /> 导入插件包 (.zip)</>
            </button>
            <button className="fw-btn fw-btn-ghost" onClick={async () => {
              try { await api.reloadUserNodes(); setSpecs(await api.specs()); await refresh(); pushToast('节点库已重新扫描', 'ok') } catch (e: any) { pushToast('扫描失败: ' + (e.message || e), 'err') }
            }}>
              <><RefreshCw size={12} /> 重新扫描</>
            </button>
            <input ref={fileRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) doImport(f); e.target.value = '' }} />
            {busy && <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>导入中…</span>}
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
            {nodes.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-dim)', padding: '10px 0' }}>暂无第三方插件，导入 manifest 声明的 zip 包即可加载。</div>}
            {nodes.map((n) => (
              <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 9, background: 'var(--panel-2)', border: '1px solid var(--border)', marginBottom: 4, fontSize: 12.5 }}>
                <FileCode2 size={12} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{n}</span>
                <span style={{ flex: 1 }} />
                <button title="导出插件包 zip（可分享/上传商城）" onClick={() => {
                  api.exportNodeZip(n)
                    .then(() => pushToast('已导出 ' + n, 'ok'))
                    .catch((e: any) => pushToast('导出失败: ' + (e.message || e), 'err'))
                }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 3, lineHeight: 0 }}><Download size={12} /></button>
                <span className="fw-pill" style={{ fontSize: 10, color: 'var(--text-faint)' }}>声明式</span>
              </div>
            ))}
          </div>
          {/* 规范说明 */}
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.7, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            插件规范：插件包 = 一个 zip，内含 <span style={{ fontFamily: 'var(--font-mono)' }}>manifest.json</span>（声明 type_id / title / category / kind / inputs / outputs / params / transform）。识别：启动或「重新扫描」时遍历插件目录并注册为节点，随后可在画布右键菜单中添加。安全：仅白名单数据变换，不执行任意代码。
          </div>
        </div>
      </div>
    </div>
  )
}
