// 素材/输出中心（D-10 / t11）：输入素材 / 视频 / 音频 / 字幕 / 图片 / 草稿与数据 分组
// - 预览（媒体内联 / 结构化数据摘要）、定位 reveal、复制路径、重命名、删除（409 引用保护）、打包项目
// - 数据源：GET /api/assets（t11 新增列表端点）+ GET /api/assets/{aid}/file 预览 + POST /api/util/reveal
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  FolderOpen, Video, AudioLines, Captions, Image as ImageIcon, FileJson, X,
  RefreshCw, Copy, PencilLine, Trash2, Package, ExternalLink, Play, FileText, Boxes,
} from 'lucide-react'
import { panelsApi, assetFileUrl, revealAsset, type AssetListEntry, type AssetDeleteRef } from '../panelsApi'
import { useAppStore } from '../store'

function fmtSize(n?: number): string {
  if (!n) return '—'
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(0) + ' KB'
  return n + ' B'
}

function fmtTime(ts?: number): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function baseName(p?: string): string {
  if (!p) return '(无文件)'
  const parts = p.split(/[\\/]/)
  return parts[parts.length - 1]
}

// 分组定义：kind → 展示组（任务要求：输入素材/缓存/视频/音频/字幕/草稿）
const GROUP_DEFS: { key: string; label: string; icon: typeof Video; kinds: string[] }[] = [
  { key: 'video', label: '视频', icon: Video, kinds: ['VIDEO'] },
  { key: 'audio', label: '音频', icon: AudioLines, kinds: ['AUDIO'] },
  { key: 'subtitle', label: '字幕', icon: Captions, kinds: ['SUBTITLE'] },
  { key: 'image', label: '图片', icon: ImageIcon, kinds: ['IMAGE'] },
  { key: 'draft', label: '草稿 / 数据', icon: FileJson, kinds: ['SEGMENTS', 'TIMELINE', 'SCRIPT', 'JSON', 'STRUCT', 'META'] },
  { key: 'other', label: '其他', icon: Boxes, kinds: [] },
]

function groupOf(kind: string): string {
  for (const g of GROUP_DEFS) if (g.kinds.includes((kind || '').toUpperCase())) return g.key
  return 'other'
}

const AssetRow = memo(function AssetRow({ a, onPreview, onReveal, onCopy, onRename, onDelete, busy }: {
  a: AssetListEntry
  onPreview: (a: AssetListEntry) => void
  onReveal: (a: AssetListEntry) => void
  onCopy: (a: AssetListEntry) => void
  onRename: (a: AssetListEntry) => void
  onDelete: (a: AssetListEntry) => void
  busy: boolean
}) {
  const media = ['VIDEO', 'AUDIO', 'IMAGE'].includes((a.kind || '').toUpperCase())
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 9px', borderRadius: 9,
      border: '1px solid var(--border)', background: 'var(--bg-panel-2)', marginBottom: 5, fontSize: 11.5,
    }}>
      {media && (
        <button onClick={() => onPreview(a)} title="预览"
          style={{ border: 'none', background: 'var(--accent-soft)', color: 'var(--accent-strong)', borderRadius: 7, padding: 4, lineHeight: 0, cursor: 'pointer', flexShrink: 0 }}>
          <Play size={11} />
        </button>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={a.path_abs || a.path}>{baseName(a.path_abs || a.path)}</span>
          <span className="fw-badge" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)', flexShrink: 0, fontSize: 9 }}>{a.kind}</span>
          {a.size ? <span style={{ color: 'var(--text-faint)', fontSize: 10, flexShrink: 0 }}>{fmtSize(a.size)}</span> : null}
        </div>
        <div style={{ color: 'var(--text-faint)', fontSize: 9.5, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {a.id.slice(0, 8)} · {a.node_id ? '节点 ' + a.node_id.slice(0, 8) : '独立资产'} · {a.created_at ? fmtTime(a.created_at) : ''}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
        <button onClick={() => onReveal(a)} disabled={busy || !a.path_abs} title="在资源管理器中定位"
          className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          <FolderOpen size={12} />
        </button>
        <button onClick={() => onCopy(a)} title="复制绝对路径"
          className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          <Copy size={12} />
        </button>
        <button onClick={() => onRename(a)} disabled={busy || !a.path_abs} title="重命名（仅改文件名，引用旧路径的参数需手动更新）"
          className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}>
          <PencilLine size={12} />
        </button>
        <button onClick={() => onDelete(a)} disabled={busy} title="删除资产"
          className="fw-btn fw-btn-ghost" style={{ padding: 4, lineHeight: 0, color: 'var(--danger)' }}>
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  )
})

export function AssetsPanel() {
  const pushToast = useAppStore((s) => s.pushToast)
  const specs = useAppStore((s) => s.specs)
  const [assets, setAssets] = useState<AssetListEntry[]>([])
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<AssetListEntry | null>(null)
  const [confirmDel, setConfirmDel] = useState<{ a: AssetListEntry; refs: AssetDeleteRef[] } | null>(null)
  const seq = useRef(0)

  const load = useCallback(async (silent = false) => {
    const s = ++seq.current
    try {
      const r = await panelsApi.listAssets()
      if (s !== seq.current) return
      setAssets(r.assets || [])
      setErr('')
    } catch (e: any) {
      if (!silent) setErr((e.message || '素材列表读取失败') + '（后端需支持 GET /api/assets）')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const h = () => void load(true)
    window.addEventListener('fw-nodes-installed', h)
    return () => window.removeEventListener('fw-nodes-installed', h)
  }, [load])

  const groups = useMemo(() => {
    const map = new Map<string, AssetListEntry[]>()
    const nq = q.trim().toLowerCase()
    for (const a of assets) {
      if (nq && !baseName(a.path_abs || a.path).toLowerCase().includes(nq) && !(a.id || '').includes(nq)) continue
      const g = groupOf(a.kind)
      if (!map.has(g)) map.set(g, [])
      map.get(g)!.push(a)
    }
    return GROUP_DEFS.map((g) => ({ ...g, items: map.get(g.key) || [] })).filter((g) => g.items.length > 0)
  }, [assets, q])

  const reveal = async (a: AssetListEntry) => {
    const ok = await revealAsset(a.path_abs || a.path)
    pushToast(ok ? '已在资源管理器中定位' : '无法定位：路径不存在', ok ? 'ok' : 'err')
  }
  const copyPath = async (a: AssetListEntry) => {
    try {
      await navigator.clipboard.writeText(a.path_abs || a.path || '')
      pushToast('路径已复制', 'ok')
    } catch { pushToast('复制失败', 'err') }
  }
  const rename = async (a: AssetListEntry) => {
    const cur = baseName(a.path_abs || a.path)
    const name = window.prompt('输入新文件名（可省略扩展名）', cur.replace(/\.[^.]+$/, ''))
    if (!name) return
    setBusy(true)
    try {
      await panelsApi.renameAsset(a.id, name)
      pushToast('重命名成功', 'ok')
      void load(true)
    } catch (e: any) { pushToast('重命名失败：' + (e.message || e), 'err') }
    finally { setBusy(false) }
  }
  const del = async (a: AssetListEntry) => {
    setBusy(true)
    try {
      const r = await panelsApi.deleteAsset(a.id)
      pushToast(r.ok ? '资产已删除' : '删除失败', r.ok ? 'ok' : 'err')
      if (r.ok) void load(true)
    } catch (e: any) {
      if (e.code === 'asset_in_use' && Array.isArray(e.references)) {
        setConfirmDel({ a, refs: e.references as AssetDeleteRef[] })
      } else {
        pushToast('删除失败：' + (e.message || e), 'err')
      }
    } finally { setBusy(false) }
  }
  const forceDelete = async () => {
    if (!confirmDel) return
    setBusy(true)
    try {
      const r = await panelsApi.deleteAsset(confirmDel.a.id, true)
      pushToast(r.ok ? '资产已删除' : '删除失败', r.ok ? 'ok' : 'err')
      if (r.ok) void load(true)
    } catch (e: any) { pushToast('删除失败：' + (e.message || e), 'err') }
    finally { setBusy(false); setConfirmDel(null) }
  }

  // 打包项目：优先找 specs 中的 project_package 类节点
  const pkgNode = specs.find((s) => /project.?package|工程包|打包/i.test(s.type_id + s.title))
  const runPackage = () => {
    if (!pkgNode) return
    ;(window as any).__fwAddNode?.(pkgNode.type_id)
    pushToast('已添加「' + pkgNode.title + '」节点到画布，连接上游后运行即可打包项目', 'info')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Boxes size={15} color="var(--accent)" />
        <b style={{ fontSize: 13.5 }}>素材 / 输出中心</b>
        <span className="fw-badge" style={{ background: 'var(--bg-panel-2)', color: 'var(--text-dim)' }}>{assets.length} 项</span>
        <button className="fw-btn fw-btn-ghost" title="刷新" onClick={() => void load(false)}
          style={{ padding: 4, lineHeight: 0, color: 'var(--text-dim)' }}><RefreshCw size={12} /></button>
        <div style={{ flex: 1 }} />
        <button onClick={runPackage} disabled={!pkgNode} title={pkgNode ? '把「' + pkgNode.title + '」节点加入画布后运行' : '需安装项目打包节点（商城搜索 project_package）'}
          className="fw-btn fw-btn-ghost" style={{ color: pkgNode ? 'var(--accent-strong)' : 'var(--text-faint)', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
          <Package size={12} /> 打包项目
        </button>
      </div>

      {err && <div style={{ padding: '6px 16px', fontSize: 11, color: 'var(--danger)', background: 'var(--danger-soft)' }}>{err}</div>}

      <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按文件名 / 资产 id 过滤…"
          className="fw-input" style={{ flex: 1, padding: '5px 9px', fontSize: 11.5, borderRadius: 8 }} />
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>删除被引用资产会二次确认</span>
      </div>

      <div className="fw-scroll" style={{ flex: 1, overflow: 'auto', padding: 10, minHeight: 0 }}>
        {groups.length === 0 && (
          <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-faint)', fontSize: 12, lineHeight: 2 }}>
            <FileText size={18} style={{ opacity: .5, marginBottom: 4 }} />
            <div>{assets.length === 0 ? '暂无资产' : '无匹配资产'}</div>
            <div style={{ fontSize: 11 }}>运行节点后输出的中间产物与导出文件会出现在这里</div>
          </div>
        )}
        {groups.map((g) => (
          <div key={g.key} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 2px 7px', fontSize: 11, fontWeight: 700, color: 'var(--text-dim)', letterSpacing: .6 }}>
              <g.icon size={11} /> {g.label}（{g.items.length}）
            </div>
            {g.items.map((a) => (
              <AssetRow key={a.id} a={a} onPreview={setPreview} onReveal={reveal} onCopy={copyPath} onRename={rename} onDelete={del} busy={busy} />
            ))}
          </div>
        ))}
      </div>

      {/* 预览弹层 */}
      {preview && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(8,10,18,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setPreview(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{
            width: 'min(720px, 92vw)', maxHeight: '80vh', border: '1px solid var(--line-strong)', borderRadius: 8,
            background: 'var(--bg-panel)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <b style={{ fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{baseName(preview.path_abs || preview.path)}</b>
              <button onClick={() => setPreview(null)} style={{ border: 'none', background: 'transparent', color: 'var(--text-faint)', cursor: 'pointer', padding: 4 }}><X size={16} /></button>
            </div>
            <div style={{ flex: 1, overflow: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#05070d', padding: 14 }}>
              {preview.kind === 'VIDEO' ? (
                <video src={assetFileUrl(preview.id)} controls autoPlay style={{ maxWidth: '100%', maxHeight: '56vh', borderRadius: 8 }} />
              ) : preview.kind === 'AUDIO' ? (
                <audio src={assetFileUrl(preview.id)} controls autoPlay style={{ width: '100%' }} />
              ) : preview.kind === 'IMAGE' ? (
                <img src={assetFileUrl(preview.id)} alt="" style={{ maxWidth: '100%', maxHeight: '60vh', borderRadius: 8, objectFit: 'contain' }} />
              ) : (
                <div style={{ color: 'var(--text-dim)', fontSize: 12, textAlign: 'center', padding: 20 }}>
                  结构化数据（{preview.kind}）· {preview.id}
                  <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-faint)' }}>JSON 内容可在节点参数面板的资产详情中查看</div>
                </div>
              )}
            </div>
            <div style={{ padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text-faint)', display: 'flex', gap: 12 }}>
              <span>{preview.kind}</span>
              <span>{fmtSize(preview.size)}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{preview.path_abs || preview.path}</span>
              <button onClick={() => { void reveal(preview) }} style={{ border: 'none', background: 'transparent', color: 'var(--accent-strong)', cursor: 'pointer', fontSize: 11, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                <ExternalLink size={10} /> 定位
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认（409 引用保护） */}
      {confirmDel && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 3001, background: 'rgba(8,10,18,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setConfirmDel(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{
            width: 'min(460px, 92vw)', border: '1px solid var(--line-strong)', borderRadius: 8, background: 'var(--bg-panel)', padding: 18,
          }}>
            <b style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>资产仍被引用</b>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.7, marginBottom: 10 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{baseName(confirmDel.a.path_abs || confirmDel.a.path)}</span> 正被
              <b style={{ color: 'var(--danger)' }}>{confirmDel.refs.length}</b> 处引用：
            </div>
            <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 6, marginBottom: 12, fontSize: 11 }}>
              {confirmDel.refs.map((r, i) => (
                <div key={i} style={{ padding: '3px 4px', color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  工作流 {r.workflow_name || r.workflow_id?.slice(0, 10)} → 节点 {r.node_id.slice(0, 10)} / {r.port}
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 14 }}>删除后引用该资产的节点在下次运行时缓存失效并自动重算。</div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="fw-btn fw-btn-ghost" onClick={() => setConfirmDel(null)}>取消</button>
              <button className="fw-btn fw-btn-primary" onClick={forceDelete} disabled={busy} style={{ background: 'var(--danger)', borderColor: 'var(--danger)' }}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}