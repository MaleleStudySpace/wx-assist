import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowDown, Trash, MagnifyingGlass, CaretRight, CaretDown } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

const LEVEL_STYLES = {
  DEBUG:   { color: '#888888', bg: 'rgba(136, 136, 136, 0.12)' },
  INFO:    { color: 'var(--status-info)', bg: 'var(--status-info-soft)' },
  WARNING: { color: 'var(--status-warn)', bg: 'var(--status-warn-soft)' },
  ERROR:   { color: 'var(--status-error)', bg: 'var(--status-error-soft)' },
}
const LEVEL_LABELS = { ALL: '全部', LLM: 'LLM', OP: '操作', INFO: 'INFO', WARNING: '警告', ERROR: '错误', DEBUG: 'DEBUG' }
const FILTER_OPTIONS = ['ALL', 'OP', 'LLM', 'INFO', 'WARNING', 'ERROR', 'DEBUG']

// Operation tags that appear in [TAG] format — used for filtering and highlighting
const OP_TAGS = [
  'BOOT', 'KEY-EXTRACT', 'DB', 'HOOK',
  'MSG-POLL', 'MSG-RECV', 'MSG-DEDUP', 'SEND', 'SEND-FAIL',
  'LLM', 'AI-CHAT',
  'API', 'EXPORT',
  'ALERT', 'DIGEST',
  'WND',
]
const OP_TAG_REGEX = new RegExp(`^\\[(${OP_TAGS.join('|')})\\]`)

// Tag-specific styling
const TAG_STYLES = {
  'BOOT':        { color: 'var(--brand-green)', bg: 'rgba(24, 226, 153, 0.12)' },
  'KEY-EXTRACT': { color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.12)' },
  'HOOK':        { color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.12)' },
  'DB':          { color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.12)' },
  'MSG-POLL':    { color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.12)' },
  'MSG-RECV':    { color: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.12)' },
  'SEND':        { color: 'var(--brand-green)', bg: 'rgba(24, 226, 153, 0.12)' },
  'SEND-FAIL':   { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.12)' },
  'API':         { color: '#6366f1', bg: 'rgba(99, 102, 241, 0.12)' },
  'EXPORT':      { color: '#6366f1', bg: 'rgba(99, 102, 241, 0.12)' },
  'ALERT':       { color: '#f97316', bg: 'rgba(249, 115, 22, 0.12)' },
  'DIGEST':      { color: '#14b8a6', bg: 'rgba(20, 184, 166, 0.12)' },
  'WND':         { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.12)' },
}

function renderHighlightedMsg(msg) {
  if (!msg) return ''
  const regex = /(\[[^\]]+\]|收到消息|发送消息|\b\d+(?:\.\d+)?(?:ms|s|毫秒|秒)\b|\b(?:OK|SUCCESS|ERROR|FAIL)\b|成功|失败)/g
  const parts = msg.split(regex)
  if (parts.length === 1) return <span>{msg}</span>

  return (
    <span>
      {parts.map((part, i) => {
        if (!part) return null
        if (part.startsWith('[') && part.endsWith(']')) {
          // Check if this is an operation tag (e.g. [SEND], [MSG-POLL])
          const tagContent = part.slice(1, -1)
          if (OP_TAGS.includes(tagContent)) {
            const style = TAG_STYLES[tagContent] || { color: '#6366f1', bg: 'rgba(99, 102, 241, 0.12)' }
            return (
              <span key={i} className="px-1.5 py-0.5 rounded-full border text-xs font-semibold font-mono mx-0.5"
                style={{ color: style.color, backgroundColor: style.bg, borderColor: `${style.color}25` }}>
                {part}
              </span>
            )
          }
          // Special styling for LLM/MEMORY/OA-DIGEST tags
          const isLLM = part === '[LLM]' || part === '[LLM-ROUTE]'
          const isMemory = part === '[MEMORY]'
          const isOADigest = part === '[OA-DIGEST]'
          const tagStyle = isLLM
            ? 'bg-purple-500/10 text-purple-400 border-purple-500/20'
            : isMemory
              ? 'bg-teal-500/10 text-teal-400 border-teal-500/20'
              : isOADigest
                ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                : 'bg-status-info-soft text-status-info border-status-info/20'
          return (
            <span key={i} className={`px-1.5 py-0.5 rounded-full border text-xs font-semibold font-mono mx-0.5 ${tagStyle}`}>
              {part}
            </span>
          )
        }
        if (part === '收到消息' || part === '发送消息') {
          const isRecv = part === '收到消息'
          return (
            <span key={i} className={`px-1.5 py-0.5 rounded-full text-xs font-semibold font-mono mx-0.5 ${isRecv ? 'bg-[var(--brand-green)]/10 text-[var(--brand-green)] border border-[var(--brand-green)]/20' : 'bg-status-warn-soft text-status-warn border border-status-warn/20'}`}>
              {part}
            </span>
          )
        }
        if (/^\d+(?:\.\d+)?(?:ms|s|毫秒|秒)$/.test(part)) {
          return (
            <span key={i} className="px-1.5 py-0.5 rounded-full bg-bg-inset border border-border-main text-text-main text-xs font-semibold font-mono mx-0.5">
              {part}
            </span>
          )
        }
        if (part === '成功' || part === 'OK' || part === 'SUCCESS') {
          return (
            <span key={i} className="text-[var(--brand-green)] font-semibold font-mono mx-0.5">
              {part}
            </span>
          )
        }
        if (part === '失败' || part === 'ERROR' || part === 'FAIL') {
          return (
            <span key={i} className="text-status-error font-semibold font-mono mx-0.5">
              {part}
            </span>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </span>
  )
}

// ── Collapsible LLM Detail Section ──────────────────────────────────

function LLMSection({ label, content, defaultCollapsed = true }) {
  const [open, setOpen] = useState(!defaultCollapsed)
  if (!content) return null
  const isLong = content.length > 200
  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs font-semibold text-text-muted hover:text-text-main transition-colors cursor-pointer"
      >
        {open ? <CaretDown size={10} weight="fill" /> : <CaretRight size={10} weight="fill" />}
        <span className="uppercase tracking-wider">{label}</span>
        <span className="text-text-muted font-normal">({content.length} chars)</span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <pre className="mt-1 p-2.5 bg-bg-main/60 dark:bg-black/30 border border-border-main rounded-lg text-xs leading-relaxed text-text-main whitespace-pre-wrap break-all max-h-64 overflow-y-auto font-mono">
              {content}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ── LLM Log Entry (collapsible) ─────────────────────────────────────

function LLMLogEntry({ log, expanded, onToggle }) {
  const detail = log.llmDetail
  const isRoute = log.msg?.startsWith('[LLM-ROUTE]')

  return (
    <div className="flex flex-col">
      <div
        className="flex items-center gap-4 px-4 py-2 hover:bg-bg-main/50 dark:hover:bg-white/5 transition-colors border-0 cursor-pointer"
        onClick={detail ? onToggle : undefined}
      >
        <span className="text-text-muted shrink-0 text-xs font-mono" style={{ width: 70 }}>{log.ts}</span>
        <span
          className="shrink-0 font-bold rounded-full text-center text-xs tracking-wider inline-flex items-center justify-center border font-mono uppercase"
          style={{
            width: 68,
            height: 20,
            color: LEVEL_STYLES[log.level]?.color || '#888888',
            backgroundColor: LEVEL_STYLES[log.level]?.bg || 'var(--bg-main)',
            borderColor: LEVEL_STYLES[log.level]?.color ? `${LEVEL_STYLES[log.level].color}25` : 'var(--border-main)',
          }}
        >
          {log.level}
        </span>
        <span className="text-text-main break-all select-all font-mono flex-1">
          {renderHighlightedMsg(log.msg)}
        </span>
        {detail && (
          <span className="shrink-0 text-text-muted hover:text-purple-400 transition-colors">
            {expanded ? <CaretDown size={14} weight="fill" /> : <CaretRight size={14} weight="fill" />}
          </span>
        )}
      </div>

      {/* Expanded detail panel */}
      <AnimatePresence>
        {expanded && detail && !isRoute && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="ml-[70px] mr-4 mb-2 p-3 bg-purple-500/5 dark:bg-purple-500/10 border border-purple-500/15 rounded-xl space-y-1">
              {/* Metadata bar */}
              <div className="flex flex-wrap items-center gap-2 text-xs font-mono text-text-muted">
                <span className="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
                  {detail.backend}/{detail.model}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                  {detail.call_type}
                </span>
                {detail.latency_ms != null && (
                  <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                    {detail.latency_ms >= 1000
                      ? `${(detail.latency_ms / 1000).toFixed(1)}s`
                      : `${Math.round(detail.latency_ms)}ms`}
                  </span>
                )}
                {(detail.token_in > 0 || detail.token_out > 0) && (
                  <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                    {detail.token_in}→{detail.token_out} tokens
                  </span>
                )}
                {detail.extra?.requester && (
                  <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                    requester: {detail.extra.requester}
                  </span>
                )}
                {detail.extra?.group && (
                  <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                    group: {detail.extra.group}
                  </span>
                )}
                {detail.extra?.mode && (
                  <span className="px-1.5 py-0.5 rounded bg-bg-main/60 border border-border-main">
                    mode: {detail.extra.mode}
                  </span>
                )}
              </div>

              {/* Prompt & Response sections */}
              <LLMSection label="System Prompt" content={detail.system_prompt} defaultCollapsed={true} />
              <LLMSection label="User Prompt" content={detail.user_prompt} defaultCollapsed={true} />
              <LLMSection label="Response" content={detail.response} defaultCollapsed={false} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ── Main LogViewer Component ────────────────────────────────────────

export default function LogViewer() {
  const [filter, setFilter] = useState('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [logs, setLogs] = useState([])
  const [clearedManually, setClearedManually] = useState(false)
  const [expandedLLM, setExpandedLLM] = useState({})
  const scrollRef = useRef(null)
  const seenRef = useRef(new Set())
  const [autoScroll, setAutoScroll] = useState(true)
  const SCROLL_THRESHOLD = 50  // px from bottom considered "at bottom"

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/logs`)
      const data = await res.json()
      if (data.ok && data.logs?.length) {
        setLogs(prev => {
          const merged = [...prev]
          let added = false
          for (const entry of data.logs) {
            const key = entry.raw
            if (!seenRef.current.has(key)) {
              seenRef.current.add(key)
              // Parse LLM markers
              if (entry.msg?.startsWith('[LLM-DETAIL] ')) {
                try {
                  entry.llmDetail = JSON.parse(entry.msg.slice('[LLM-DETAIL] '.length))
                  entry.isLLMDetail = true
                } catch {}
              }
              if (entry.msg?.startsWith('[LLM] ') || entry.msg?.startsWith('[LLM-ROUTE]')) {
                entry.isLLMSummary = true
              }
              merged.push(entry)
              added = true
            }
          }
          if (added) {
            setClearedManually(false)
          }
          return merged.length > 2000 ? merged.slice(-2000) : merged
        })
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetchLogs()
    const timer = setInterval(fetchLogs, 10000)
    return () => clearInterval(timer)
  }, [fetchLogs])

  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setAutoScroll(distFromBottom < SCROLL_THRESHOLD)
  }

  function scrollToBottom() {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setAutoScroll(true)
  }

  useEffect(() => {
    if (autoScroll) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    }
  }, [logs, autoScroll])

  // Filter + merge LLM-DETAIL into LLM summary entries
  const processed = (() => {
    // First apply level + search filter
    const baseFiltered = logs.filter(l => {
      const matchesLevel = filter === 'ALL'
        ? true
        : filter === 'LLM'
          ? (l.isLLMSummary || l.isLLMDetail)
          : filter === 'OP'
            ? OP_TAG_REGEX.test(l.msg || '')
            : l.level === filter
      const matchesSearch = !searchQuery ||
        l.msg?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        l.ts?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        l.level?.toLowerCase().includes(searchQuery.toLowerCase())
      return matchesLevel && matchesSearch
    })

    // Merge [LLM-DETAIL] into preceding [LLM] summary entry
    const result = []
    for (const log of baseFiltered) {
      if (log.isLLMDetail && result.length > 0) {
        const prev = result[result.length - 1]
        if (prev.isLLMSummary) {
          prev.llmDetail = log.llmDetail
          continue  // skip standalone detail line
        }
      }
      result.push(log)
    }
    return result
  })()

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex items-center gap-1 bg-bg-card border border-border-main rounded-full p-1 shrink-0">
          {FILTER_OPTIONS.map(level => (
            <button
              key={level}
              onClick={() => setFilter(level)}
              className={`px-3 py-1.25 rounded-full text-xs font-semibold transition-all cursor-pointer ${
                filter === level
                  ? level === 'LLM'
                    ? 'bg-purple-500/15 text-purple-400 font-semibold shadow-sm'
                    : 'bg-bg-raised text-text-main font-semibold shadow-sm'
                  : 'text-text-muted hover:text-text-main'
              }`}
            >
              {LEVEL_LABELS[level]}
            </button>
          ))}
        </div>

        {/* Log Search Input */}
        <div className="relative flex-1 max-w-sm">
          <span className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-text-muted">
            <MagnifyingGlass size={14} />
          </span>
          <input
            type="text"
            placeholder="搜索日志关键字、事件、耗时..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-8 py-1.5 bg-bg-card border border-border-main rounded-full text-xs placeholder:text-text-muted focus:outline-none focus:border-brand-green/60 text-text-main transition-all font-mono"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-text-muted hover:text-status-error text-xs font-sans font-semibold cursor-pointer"
            >
              清除
            </button>
          )}
        </div>

        <div className="flex-1 hidden sm:block" />
        <div className="flex items-center gap-2.5 ml-auto sm:ml-0 shrink-0">
          <span className="text-xs text-text-muted font-mono bg-bg-card border border-border-main px-3 py-1 rounded-full">{processed.length} 条</span>
          <button onClick={scrollToBottom} title="滚动到底部" className="p-2 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer border border-transparent hover:border-border-main">
            <ArrowDown size={16} />
          </button>
          <button onClick={() => { setLogs([]); seenRef.current.clear(); setClearedManually(true); setExpandedLLM({}) }} title="仅清空屏幕展示日志（不删除后台日志文件）" className="p-2 rounded-full text-text-muted hover:text-status-error hover:bg-status-error-soft border border-transparent hover:border-status-error/20 transition-colors cursor-pointer">
            <Trash size={16} />
          </button>
        </div>
      </div>

      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }}
        ref={scrollRef}
        onScroll={handleScroll}
        className="bg-bg-inset border border-border-main rounded-2xl overflow-hidden max-h-[600px] overflow-y-auto font-mono text-[13px] leading-relaxed p-4 divide-y divide-border-main shadow-sm text-text-main"
      >
        {processed.length === 0 ? (
          clearedManually ? (
            <div className="p-16 text-center text-text-muted">
              <p className="text-base font-semibold text-text-main font-mono">Terminal Cleared</p>
              <p className="text-xs mt-1.5 font-medium text-text-muted">等待新事件写入日志文件...</p>
            </div>
          ) : (
            <div className="p-16 text-center text-text-muted">
              <p className="text-base font-semibold text-text-main font-mono">Console Offline</p>
              <p className="text-xs mt-1.5 font-medium text-text-muted">启动机器人后，日志数据流将在此输出</p>
              <p className="text-xs mt-1 font-mono text-text-muted">位置: data/bot.log</p>
            </div>
          )
        ) : (
          processed.map((log, i) => {
            // LLM entries with detail — render as collapsible
            if (log.isLLMSummary) {
              return (
                <LLMLogEntry
                  key={i}
                  log={log}
                  expanded={!!expandedLLM[i]}
                  onToggle={() => setExpandedLLM(prev => ({ ...prev, [i]: !prev[i] }))}
                />
              )
            }
            // Normal log entries
            return (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(i * 0.003, 0.2) }}
                className="flex items-center gap-4 px-4 py-2 hover:bg-bg-main/50 dark:hover:bg-white/5 transition-colors border-0"
              >
                <span className="text-text-muted shrink-0 text-xs font-mono" style={{ width: 70 }}>{log.ts}</span>
                <span
                  className="shrink-0 font-bold rounded-full text-center text-xs tracking-wider inline-flex items-center justify-center border font-mono uppercase"
                  style={{
                    width: 68,
                    height: 20,
                    color: LEVEL_STYLES[log.level]?.color || '#888888',
                    backgroundColor: LEVEL_STYLES[log.level]?.bg || 'var(--bg-main)',
                    borderColor: LEVEL_STYLES[log.level]?.color ? `${LEVEL_STYLES[log.level].color}25` : 'var(--border-main)',
                  }}
                >
                  {log.level}
                </span>
                <span className="text-text-main break-all select-all font-mono">{renderHighlightedMsg(log.msg)}</span>
              </motion.div>
            )
          })
        )}
      </motion.div>
    </div>
  )
}
