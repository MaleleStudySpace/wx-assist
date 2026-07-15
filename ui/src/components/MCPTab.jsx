/* MCP 工具管理页 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  PuzzlePiece, Plus, X, ArrowsClockwise, Trash, Play, Pause,
  DotsThree, Terminal, Globe, CaretDown, CaretRight,
  MagnifyingGlass, WarningCircle, CheckCircle, Spinner,
} from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

const easeOut = [0.16, 1, 0.3, 1]

const STATUS_LABELS = {
  running:  { label: '运行中', cls: 'text-brand-green', dot: 'bg-brand-green' },
  degraded: { label: '降级',   cls: 'text-status-warn',  dot: 'bg-status-warn' },
  error:    { label: '错误',   cls: 'text-status-error',  dot: 'bg-status-error' },
  stopped:  { label: '已停止', cls: 'text-text-muted',    dot: 'bg-text-muted/50' },
}

/* ── 安全提示横幅 ─── */
function SafetyBanner({ onDismiss }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      className="flex items-start gap-3 px-5 py-4 rounded-2xl bg-status-warn/[0.06] dark:bg-status-warn/[0.07] border border-status-warn/[0.12]"
    >
      <WarningCircle size={18} weight="fill" className="text-status-warn shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0 text-sm text-text-secondary leading-relaxed">
        <strong className="text-text-main">安全须知</strong>
        <span className="ml-1">— MCP 服务器是独立运行的程序，拥有你本机的同等权限。仅添加</span>
        <strong className="text-text-main">可信来源</strong>。
        <span className="ml-1">你可以随时在列表中禁用或删除已添加的服务器。</span>
      </div>
      <button onClick={onDismiss} className="shrink-0 p-1 rounded-md text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer">
        <X size={16} />
      </button>
    </motion.div>
  )
}

/* ── 状态圆点 ─── */
function StatusDot({ status }) {
  const s = STATUS_LABELS[status]
  return (
    <span className={`block w-[10px] h-[10px] rounded-full shrink-0 mt-1 ${s?.dot || 'bg-text-muted/50'}`}
      style={{ boxShadow: status === 'running' ? '0 0 8px rgba(45,212,160,0.35)' : undefined }}
    />
  )
}

/* ── 工具标签列表 ─── */
function ToolTags({ tools, degraded }) {
  if (!tools?.length) return null
  return (
    <div className="flex flex-wrap gap-1.5 mt-3">
      {tools.map(t => (
        <span key={t}
          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] leading-none font-mono
            ${degraded
              ? 'bg-bg-raised text-text-muted/50 line-through'
              : 'bg-brand-green-light/60 dark:bg-brand-green-light/30 text-brand-green dark:text-brand-green'}`}
        >
          {t}
        </span>
      ))}
    </div>
  )
}

/* ── 单张 Server 卡片 ─── */
function ServerCard({ server, status, onRestart, onToggle, onDelete, onEdit }) {
  const [expanded, setExpanded] = useState(false)
  const st = status || 'stopped'
  const meta = STATUS_LABELS[st]
  const transportIcon = server.transport === 'http' ? Globe : Terminal
  const transportLabel = server.transport === 'http' ? '远程 HTTP' : '本地 stdio'
  const nTools = status?.tools_count ?? 0
  const errorMsg = status?.error

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      className="bg-bg-card border border-border-main rounded-xl overflow-hidden transition-colors hover:border-border-strong"
    >
      <div className="p-5">
        {/* 顶行：状态 + 名称 + 操作按钮 */}
        <div className="flex items-start gap-3.5">
          <StatusDot status={st} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2.5 flex-wrap">
              <span className="text-sm font-semibold text-text-main">{server.name}</span>
              <span className={`text-[12px] font-mono font-semibold ${meta.cls}`}>{meta.label}</span>
              {server.description && (
                <span className="text-[12px] text-text-muted/70 hidden sm:inline">— {server.description}</span>
              )}
            </div>

            {/* Meta 行 */}
            <div className="flex items-center gap-4 mt-1.5 text-[12px] text-text-muted/80 flex-wrap">
              <span className="inline-flex items-center gap-1">
                <transportIcon size={13} weight="regular" />
                {transportLabel}
              </span>
              <span>{nTools} 个工具</span>
              <span>{server.timeout || 30}s 超时</span>
              {server.auto_restart !== false && <span>自动重启</span>}
            </div>

            {/* 错误信息 */}
            {errorMsg && st !== 'running' && (
              <p className="mt-1.5 text-[12px] text-status-error/90 font-mono leading-snug truncate">{errorMsg}</p>
            )}
          </div>

          {/* 操作按钮组 */}
          <div className="flex items-center gap-1 shrink-0">
            {/* 重启 */}
            <button
              onClick={() => onRestart?.(server.name)}
              className="p-2 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
              title="重启"
            >
              <ArrowsClockwise size={15} />
            </button>
            {/* 启用/禁用 */}
            {st === 'degraded' || st === 'stopped' ? (
              <button onClick={() => onToggle?.(server.name)}
                className="p-2 rounded-lg text-text-muted hover:text-brand-green hover:bg-brand-green-light/20 transition-colors cursor-pointer"
                title="启用"
              >
                <Play size={15} />
              </button>
            ) : (
              <button onClick={() => onToggle?.(server.name)}
                className="p-2 rounded-lg text-text-muted hover:text-status-warn hover:bg-status-warn/10 transition-colors cursor-pointer"
                title="禁用"
              >
                <Pause size={15} />
              </button>
            )}
            {/* 展开工具详情 */}
            {nTools > 0 && (
              <button onClick={() => setExpanded(!expanded)}
                className="p-2 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
              >
                {expanded ? <CaretDown size={15} weight="fill" /> : <CaretRight size={15} weight="fill" />}
              </button>
            )}
            {/* 删除 */}
            <DelButton onConfirm={() => onDelete?.(server.name)} />
          </div>
        </div>
      </div>

      {/* 工具详情展开 */}
      <AnimatePresence>
        {expanded && server.tools?.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: easeOut }}
            className="overflow-hidden"
          >
            <div className="border-t border-border-main/50 mx-5 pt-3 pb-4 space-y-2">
              {server.tools.map(t => (
                <div key={t.name} className="flex items-start gap-3 text-sm">
                  <span className="font-mono text-sm text-brand-green shrink-0 mt-0.5">{t.name}</span>
                  {t.description && (
                    <span className="text-text-muted/80 leading-snug">{t.description}</span>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/* ── 带确认的删除按钮 ─── */
function DelButton({ onConfirm }) {
  const [confirming, setConfirming] = useState(false)
  return (
    <div className="relative">
      {!confirming ? (
        <button onClick={() => setConfirming(true)}
          className="p-2 rounded-lg text-text-muted hover:text-status-error hover:bg-status-error/10 transition-colors cursor-pointer"
          title="删除">
          <Trash size={15} />
        </button>
      ) : (
        <div className="flex items-center gap-1 bg-status-error/10 rounded-lg px-2 py-1">
          <span className="text-[11px] text-status-error font-medium whitespace-nowrap">确认?</span>
          <button onClick={() => { onConfirm?.(); setConfirming(false) }}
            className="px-2 py-1 rounded-md text-[11px] font-semibold bg-status-error text-white cursor-pointer">是</button>
          <button onClick={() => setConfirming(false)}
            className="px-2 py-1 rounded-md text-[11px] font-semibold bg-bg-raised text-text-muted cursor-pointer">否</button>
        </div>
      )}
    </div>
  )
}

/* ── 弹窗：添加/编辑服务器 ─── */
function ServerModal({ mode, initial, onSave, onClose }) {
  const isEdit = mode === 'edit'
  const [transport, setTransport] = useState(isEdit ? (initial?.transport || 'stdio') : 'stdio')
  const [name, setName] = useState(isEdit ? (initial?.name || '') : '')
  const [desc, setDesc] = useState(isEdit ? (initial?.description || '') : '')
  const [cmd, setCmd] = useState(isEdit ? (initial?.command || '') : '')
  const [args, setArgs] = useState(isEdit ? ((initial?.args || []).join(', ')) : '')
  const [cwd, setCwd] = useState(isEdit ? (initial?.cwd || '') : '')
  const [url, setUrl] = useState(isEdit ? (initial?.url || '') : '')
  const [headers, setHeaders] = useState(isEdit ? (JSON.stringify(initial?.headers || {}, null, 1)) : '')
  const [timeout, setTimeout_] = useState(isEdit ? (initial?.timeout || 30) : 30)
  const [env, setEnv] = useState(isEdit ? (JSON.stringify(initial?.env || {}, null, 1)) : '')
  const [autoRestart, setAutoRestart] = useState(isEdit ? (initial?.auto_restart !== false) : true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSave = async () => {
    if (!name.trim()) { setError('名称不能为空'); return }
    setError('')
    setSaving(true)

    const config = {
      name: name.trim(),
      description: desc.trim() || undefined,
      transport,
      timeout: Number(timeout) || 30,
      auto_restart: autoRestart,
    }

    if (transport === 'stdio') {
      if (!cmd.trim()) { setError('命令不能为空'); setSaving(false); return }
      config.command = cmd.trim()
      config.args = args.split(',').map(s => s.trim()).filter(Boolean)
      if (cwd.trim()) config.cwd = cwd.trim()
    } else {
      if (!url.trim()) { setError('URL 不能为空'); setSaving(false); return }
      config.url = url.trim()
      try { config.headers = headers.trim() ? JSON.parse(headers) : {} } catch { setError('Headers JSON 格式错误'); setSaving(false); return }
    }

    try { config.env = env.trim() ? JSON.parse(env) : {} } catch { setError('环境变量 JSON 格式错误'); setSaving(false); return }

    if (onSave) await onSave(config)
    setSaving(false)
    onClose()
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={{ duration: 0.2, ease: easeOut }}
        className="relative bg-bg-card border border-border-main rounded-2xl shadow-xl w-full max-w-lg max-h-[85vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-main">
          <h2 className="text-base font-semibold text-text-main">{isEdit ? '编辑服务器' : '添加服务器'}</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Transport 切换 */}
          <div className="flex rounded-xl bg-bg-raised border border-border-main p-0.5">
            {['stdio', 'http'].map(t => (
              <button key={t} onClick={() => setTransport(t)}
                className={`flex-1 px-4 py-2 rounded-[10px] text-sm font-medium transition-all cursor-pointer
                  ${transport === t
                    ? 'bg-bg-main text-text-main shadow-sm border border-border-main'
                    : 'text-text-muted hover:text-text-main'}`}
              >
                {t === 'stdio' ? '📡 本地 (stdio)' : '☁️ 远程 (HTTP)'}
              </button>
            ))}
          </div>

          {/* 通用字段 */}
          <Field label="名称" required>
            <input type="text" value={name} onChange={e => setName(e.target.value)}
              className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm text-text-main placeholder:text-text-muted/60
                focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
              placeholder="weather-mcp" />
          </Field>
          <Field label="描述">
            <input type="text" value={desc} onChange={e => setDesc(e.target.value)}
              className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm text-text-main placeholder:text-text-muted/60
                focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
              placeholder="可选说明" />
          </Field>

          {/* Stdio 字段 */}
          {transport === 'stdio' && (
            <div className="space-y-4">
              <Field label="命令" required>
                <input type="text" value={cmd} onChange={e => setCmd(e.target.value)}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm font-mono text-text-main placeholder:text-text-muted/60
                    focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                  placeholder="npx" />
              </Field>
              <Field label="参数">
                <input type="text" value={args} onChange={e => setArgs(e.target.value)}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm font-mono text-text-muted placeholder:text-text-muted/60
                    focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                  placeholder="-y, @xxx/server-weather, --api-key=xxx" />
              </Field>
              <Field label="工作目录">
                <input type="text" value={cwd} onChange={e => setCwd(e.target.value)}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm font-mono text-text-muted placeholder:text-text-muted/60
                    focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                  placeholder="可选" />
              </Field>
            </div>
          )}

          {/* HTTP 字段 */}
          {transport === 'http' && (
            <div className="space-y-4">
              <Field label="URL" required>
                <input type="text" value={url} onChange={e => setUrl(e.target.value)}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm font-mono text-text-main placeholder:text-text-muted/60
                    focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                  placeholder="https://mcp.example.com/mcp" />
              </Field>
              <Field label="Headers (JSON)">
                <textarea value={headers} onChange={e => setHeaders(e.target.value)} rows={2}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm font-mono text-text-muted placeholder:text-text-muted/60
                    focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all resize-none" />
              </Field>
            </div>
          )}

          {/* 可选字段行 */}
          <div className="flex gap-4">
            <Field label="超时 (s)" className="flex-1">
              <input type="number" value={timeout} onChange={e => setTimeout_(Number(e.target.value))} min={1} max={300}
                className="w-full bg-bg-raised border border-border-main rounded-lg px-3 py-2.5 text-sm font-mono text-text-main
                  focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all" />
            </Field>
            <Field label="环境变量 (JSON)" className="flex-1">
              <input type="text" value={env} onChange={e => setEnv(e.target.value)}
                className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2.5 text-sm font-mono text-text-muted placeholder:text-text-muted/60
                  focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                placeholder='{"KEY":"val"}' />
            </Field>
          </div>

          <label className="flex items-center gap-2.5 cursor-pointer">
            <input type="checkbox" checked={autoRestart} onChange={e => setAutoRestart(e.target.checked)}
              className="accent-brand-green w-4 h-4 rounded border-border-main" />
            <span className="text-sm text-text-secondary">崩溃后自动重启</span>
          </label>

          {/* 错误提示 */}
          {error && (
            <p className="text-sm text-status-error flex items-center gap-1.5">
              <WarningCircle size={15} weight="fill" /> {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border-main">
          <button onClick={onClose}
            className="px-5 py-2 rounded-lg text-sm font-medium bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer">
            取消
          </button>
          <button onClick={handleSave} disabled={saving}
            className="px-5 py-2 rounded-lg text-sm font-semibold bg-brand-green-hover dark:bg-brand-green text-white hover:brightness-110 disabled:opacity-40 transition-all cursor-pointer inline-flex items-center gap-2">
            {saving && <Spinner size={14} className="animate-spin" />}
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

function Field({ label, required, children, className }) {
  return (
    <div className={className || ''}>
      <label className="block text-readable-label text-text-secondary mb-1.5">
        {label}{required && <span className="text-status-error ml-0.5">*</span>}
      </label>
      {children}
    </div>
  )
}

/* ── 空状态 ─── */
function EmptyState({ onAdd }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <PuzzlePiece size={48} weight="light" className="text-text-muted/30 mb-4" />
      <h3 className="text-base font-medium text-text-secondary mb-1.5">还没有配置 MCP 服务器</h3>
      <p className="text-sm text-text-muted/70 mb-6">添加后，摘星就能通过 MCP 工具扩展能力</p>
      <button onClick={onAdd}
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold bg-brand-green-hover dark:bg-brand-green text-white hover:brightness-110 transition-all cursor-pointer">
        <Plus size={16} weight="bold" />
        添加服务器
      </button>
    </div>
  )
}

/* ── 主组件 ─── */
export default function MCPTab() {
  const [servers, setServers] = useState([])
  const [statuses, setStatuses] = useState({})
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editTarget, setEditTarget] = useState(null)
  const [modalMode, setModalMode] = useState('add')
  const [safetyDismissed, setSafetyDismissed] = useState(
    () => localStorage.getItem('mcp_safety_dismissed') === '1'
  )

  const fetchServers = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/mcp/servers`)
      const d = await r.json()
      if (d.ok) {
        setServers(d.servers || [])
        setStatuses(d.status || {})
      }
    } catch {
      // 静默
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchServers() }, [fetchServers])

  const dismissSafety = () => {
    setSafetyDismissed(true)
    localStorage.setItem('mcp_safety_dismissed', '1')
  }

  const handleAdd = () => {
    setModalMode('add')
    setEditTarget(null)
    setShowModal(true)
  }

  const handleEdit = name => {
    const server = servers.find(s => s.name === name)
    if (server) {
      setModalMode('edit')
      setEditTarget(server)
      setShowModal(true)
    }
  }

  const handleSave = async config => {
    const r = await fetch(`${API_BASE}/api/mcp/servers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    await r.json()
    await fetchServers()
  }

  const handleDelete = async name => {
    await fetch(`${API_BASE}/api/mcp/servers/${encodeURIComponent(name)}`, { method: 'DELETE' })
    await fetchServers()
  }

  const handleRestart = async name => {
    await fetch(`${API_BASE}/api/mcp/servers/${encodeURIComponent(name)}/restart`, { method: 'POST' })
    await fetchServers()
  }

  const handleToggle = async name => {
    await fetch(`${API_BASE}/api/mcp/servers/${encodeURIComponent(name)}/toggle`, { method: 'POST' })
    await fetchServers()
  }

  const nOnline = Object.values(statuses).filter(s => s?.status === 'running').length

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3, ease: easeOut }}
      className="p-8 space-y-6 max-w-4xl "
    >
      {/* 顶部栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <PuzzlePiece size={22} weight="fill" className="text-brand-green" />
          <h1 className="text-lg font-semibold text-text-main">MCP 工具</h1>
          {servers.length > 0 && (
            <span className="text-[12px] font-mono text-text-muted/70 bg-bg-raised px-2.5 py-1 rounded-full border border-border-main">
              {nOnline}/{servers.length} 在线
            </span>
          )}
        </div>
        <button onClick={fetchServers}
          className="p-2 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
          title="刷新">
          <ArrowsClockwise size={16} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* 安全提示 */}
      <AnimatePresence>
        {!safetyDismissed && <SafetyBanner onDismiss={dismissSafety} />}
      </AnimatePresence>

      {/* 操作栏 */}
      <div className="flex items-center gap-3">
        <button onClick={handleAdd}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-brand-green-hover dark:bg-brand-green text-white hover:brightness-110 transition-all cursor-pointer">
          <Plus size={16} weight="bold" />
          添加服务器
        </button>
      </div>

      {/* Server 列表 / 空状态 */}
      {loading ? (
        <div className="flex items-center justify-center py-24">
          <Spinner size={24} className="animate-spin text-text-muted" />
        </div>
      ) : servers.length === 0 ? (
        <EmptyState onAdd={handleAdd} />
      ) : (
        <div className="space-y-3">
          {servers.map(s => {
            // 合并配置与状态；statuses 的 key 是 server name
            const st = statuses[s.name]
            const displayTools = s.name ? [] : []  // 未来可从 status 取
            return (
              <ServerCard
                key={s.name}
                server={s}
                status={st}
                onRestart={handleRestart}
                onToggle={handleToggle}
                onDelete={handleDelete}
                onEdit={handleEdit}
              />
            )
          })}
        </div>
      )}

      {/* 添加/编辑弹窗 */}
      <AnimatePresence>
        {showModal && (
          <ServerModal
            mode={modalMode}
            initial={editTarget}
            onSave={handleSave}
            onClose={() => setShowModal(false)}
          />
        )}
      </AnimatePresence>
    </motion.div>
  )
}
