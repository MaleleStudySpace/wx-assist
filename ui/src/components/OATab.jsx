import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Newspaper, MagnifyingGlass, Clock, Plus, Trash, Pencil, FileText, Play, Folder, X, Export, Globe, ArrowsClockwise, Sparkle, Info, CaretDown, CaretUp, NotePencil, CodeBlock, FilmStrip, ChartBar, NewspaperClipping, CaretDown as ChevronDown, CaretUp as ChevronUp, Bell, ToggleLeft } from '@phosphor-icons/react'
import { Toggle, Input, API_BASE, getWsUrl } from './SharedComponents'

// ── Preset cron schedules for easy selection ──
// Fixed rule: one trigger per line, minute/hour single int, day/month *, dow range/list/star
const CRON_PRESETS = [
  { label: '每天 9:00', cron: '0 9 * * *' },
  { label: '每天 12:00', cron: '0 12 * * *' },
  { label: '每天 20:30', cron: '30 20 * * *' },
  { label: '工作日 9:00', cron: '0 9 * * 1-5' },
  { label: '每天 9+20点', cron: '0 9 * * *\n0 20 * * *' },
  { label: '手动触发', cron: '' },
]

/**
 * Validate a cron expression against our fixed rule.
 * Returns error message string if invalid, empty string if valid.
 * (Same logic as AssistantPanel.jsx)
 */
function validateCronExpr(cronExpr) {
  if (!cronExpr || !cronExpr.trim()) return ''
  const lines = cronExpr.trim().split(/\n/).map(l => l.trim()).filter(Boolean)
  for (let i = 0; i < lines.length; i++) {
    const fields = lines[i].split(/\s+/)
    if (fields.length !== 5) return `第${i+1}行：必须有5个字段（分 时 日 月 周），当前: ${lines[i]}`
    const [min, hour, day, month, dow] = fields
    const m = Number(min)
    if (!Number.isInteger(m) || m < 0 || m > 59) return `第${i+1}行：分钟=${min} 必须是0-59的整数`
    const h = Number(hour)
    if (!Number.isInteger(h) || h < 0 || h > 23) return `第${i+1}行：小时=${hour} 必须是0-23的整数`
    if (day !== '*') return `第${i+1}行：日=${day} 必须是 *`
    if (month !== '*') return `第${i+1}行：月=${month} 必须是 *`
    if (dow !== '*' && !/^(\d+(-\d+)?)(,\d+(-\d+)?)*$/.test(dow)) {
      return `第${i+1}行：周=${dow} 格式错误，支持 * | 1-5 | 1,2,3,4,5`
    }
  }
  return ''
}

/** 从 cron_expr 估算智能回溯小时数 */
function estimateAutoLookback(cronExpr) {
  if (!cronExpr) return 24
  const parts = cronExpr.trim().split(/\s+/)
  if (parts.length < 2) return 24
  const hourPart = parts[1]
  const hours = new Set()
  for (const segment of hourPart.split(',')) {
    const h = segment.trim()
    if (h.startsWith('*/')) {
      const step = parseInt(h.slice(2), 10)
      if (step > 0) { for (let hh = 0; hh < 24; hh += step) hours.add(hh) }
    } else if (h.includes('-') && !h.startsWith('-')) {
      const [lo, hi] = h.split('-', 2).map(Number)
      if (!isNaN(lo) && !isNaN(hi)) { for (let hh = lo; hh <= hi; hh++) hours.add(hh) }
    } else {
      const n = parseInt(h, 10)
      if (!isNaN(n)) hours.add(n)
    }
  }
  if (hours.size >= 2) {
    const sorted = [...hours].sort((a, b) => a - b)
    const gaps = []
    for (let i = 1; i < sorted.length; i++) gaps.push(sorted[i] - sorted[i - 1])
    gaps.push(24 - sorted[sorted.length - 1] + sorted[0])
    return Math.min(...gaps) + 1
  } else if (hours.size === 1) {
    return 25
  }
  return 24
}

// ── Digest templates with clear descriptions ──
const TEMPLATES = [
  { value: 'default', label: '默认摘要', PhosphorIcon: FileText,
    preview: '公众号xxx： yyyy-mm-dd hh:mm 文章标题为：《标题》 摘要：核心要点\n\n原文链接\n总结：...' },
  { value: 'tech', label: '技术详尽', PhosphorIcon: CodeBlock,
    preview: '公众号xxx： 技术摘要：架构/方案/创新点\n\n原文链接\n技术总结：...' },
  { value: 'entertainment', label: '娱乐简报', PhosphorIcon: FilmStrip,
    preview: '公众号xxx： 一句话：核心事件+关键人物\n\n原文链接\n总结：...' },
  { value: 'business', label: '商业要点', PhosphorIcon: ChartBar,
    preview: '公众号xxx： 商业摘要：数据/趋势/投资信号\n\n原文链接\n商业总结：...' },
  { value: 'news', label: '新闻摘要', PhosphorIcon: NewspaperClipping,
    preview: '公众号xxx： 新闻摘要：谁+什么事+关键数据\n\n原文链接\n总结：...' },
  { value: 'custom', label: '自定义', PhosphorIcon: NotePencil,
    preview: '使用你自定义的提示词生成摘要' },
]

// 仅用于「自定义」模板的默认 system prompt（作为参考和 textarea 默认值）
const DEFAULT_CUSTOM_PROMPT = `你是一个专业的公众号信息摘要助手。请严格按照以下格式输出摘要：

对每篇文章，按此格式输出一行：
公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 摘要：{核心要点，≤50字}

所有文章输出完毕后：
1. 列出原文链接
2. 写一段2-3句的总结，概括这批文章的核心主题和价值

要求：
1. 每篇文章一行，格式统一
2. 摘要提炼核心要点，不重复标题
3. 总结要有洞察，不要简单罗列`

function GroupCard({ group, onEdit, onDelete, onRunDigest, digestRunning, accounts, lastDigest, onViewAccount }) {
  const [expanded, setExpanded] = useState(false)
  const [showDigest, setShowDigest] = useState(false)
  const isRunning = digestRunning === group.id
  const digest = lastDigest?.groupId === group.id ? lastDigest : null
  const cardRef = useRef(null)

  // Auto-scroll when expanded so the card body is fully visible
  useEffect(() => {
    if (expanded && cardRef.current) {
      setTimeout(() => {
        cardRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 150)
    }
  }, [expanded])

  // Resolve account info, filter out accounts not in current list
  const accountEntries = (group.accounts || [])
    .filter(gh => accounts?.some(a => a.username === gh))
    .map(gh => {
      const acc = accounts?.find(a => a.username === gh)
      return { username: gh, nickname: acc ? acc.nickname : gh }
    })

  const scheduleLabel = (() => {
    const cron = group.cron_expr || ''
    if (!cron) return '手动触发'
    const preset = CRON_PRESETS.find(p => p.cron === cron)
    return preset ? preset.label : cron
  })()

  const templateInfo = TEMPLATES.find(t => t.value === (group.digest_template || 'default'))

  return (
    <div ref={cardRef} className={`border rounded-xl overflow-hidden bg-bg-card transition-colors
      ${isRunning ? 'border-brand-green/40 shadow-[0_0_12px_rgba(24,226,153,0.08)]' : 'border-border-main hover:border-text-muted/20'}`}>
      <div
        className="flex items-center gap-3 p-4 cursor-pointer hover:bg-bg-raised/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="w-9 h-9 rounded-lg bg-brand-green-light/30 flex items-center justify-center text-brand-green">
          {templateInfo?.PhosphorIcon ? <templateInfo.PhosphorIcon size={18} /> : <Folder size={18} />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-text-main">{group.name}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-xs text-text-muted">{accountEntries.length} 个公众号</span>
            <span className="text-xs text-text-muted">·</span>
            <span className="text-xs text-text-muted">{scheduleLabel}</span>
            {templateInfo && (
              <>
                <span className="text-xs text-text-muted">·</span>
                <span className="text-xs text-brand-green/70">{templateInfo.label}</span>
              </>
            )}
            {group.push_target === 'ilink' && (
              <>
                <span className="text-xs text-text-muted">·</span>
                <span className="text-xs text-status-success flex items-center gap-0.5">
                  <Bell size={10} weight="fill" />推送
                </span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isRunning && (
            <div className="flex items-center gap-1.5 text-brand-green text-xs">
              <div className="w-3.5 h-3.5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
              <span className="text-xs">生成中</span>
            </div>
          )}
          {expanded ? <CaretUp size={14} className="text-text-muted" /> : <CaretDown size={14} className="text-text-muted" />}
        </div>
      </div>

      {expanded && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          className="px-4 pb-4"
        >
          <div className="pt-3 border-t border-border-main">
            <div className="space-y-2.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">摘要模板</span>
                <span className="text-text-main">
                  {templateInfo?.PhosphorIcon ? <templateInfo.PhosphorIcon size={14} className="inline text-brand-green" /> : null} {templateInfo?.label || group.digest_template || 'default'}
                  {group.custom_prompt && group.digest_template === 'custom' && (
                    <span className="text-text-muted ml-1 truncate max-w-[120px] inline-block align-bottom">
                      {group.custom_prompt.slice(0, 30)}...
                    </span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">执行时间</span>
                <span className="text-text-main">{scheduleLabel}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">时间范围</span>
                <span className="text-text-main">
                  {group.lookback_mode === 'auto'
                    ? `智能 · ~${estimateAutoLookback(group.cron_expr)}h`
                    : `${group.lookback_hours || 24} 小时`}
                </span>
              </div>
              {group.push_target && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">推送目标</span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-medium">
                    消息推送
                  </span>
                </div>
              )}
            </div>

            {/* Digest preview */}
            {digest && digest.text && (
              <div className="mt-3 pt-3 border-t border-border-main">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs text-text-muted font-medium">最近摘要</p>
                  <button
                    onClick={() => setShowDigest(!showDigest)}
                    className="text-xs text-brand-green hover:underline cursor-pointer"
                  >
                    {showDigest ? '收起' : '展开查看'}
                  </button>
                </div>
                {showDigest && (
                  <div className="p-3 rounded-lg bg-bg-raised border border-border-main text-xs text-text-main leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
                    {digest.text}
                  </div>
                )}
                {!showDigest && (
                  <p className="text-xs text-text-muted line-clamp-2">{digest.text}</p>
                )}
              </div>
            )}

            {accountEntries.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border-main">
                <p className="text-xs text-text-muted mb-2">包含公众号</p>
                <div className="flex flex-wrap gap-1.5">
                  {accountEntries.map((entry, i) => (
                    <button
                      key={i}
                      onClick={() => onViewAccount?.(entry.username, entry.nickname)}
                      className="text-xs px-2.5 py-1 rounded-full bg-brand-green-light/20 text-brand-green/80 border border-brand-green/10 hover:bg-brand-green-light/40 hover:text-brand-green transition-colors cursor-pointer"
                      title="点击查看历史文章"
                    >
                      {entry.nickname}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Action bar */}
            <div className="mt-3 pt-3 border-t border-border-main flex items-center gap-4">
              <button
                onClick={(e) => { e.stopPropagation(); onRunDigest(group.id) }}
                disabled={isRunning}
                className={`flex items-center gap-1.5 text-xs font-medium transition-colors cursor-pointer
                  ${isRunning
                    ? 'text-brand-green/50 cursor-wait'
                    : 'text-brand-green hover:text-brand-green-hover'
                  }`}
              >
                <Play size={13} weight="fill" />
                {isRunning ? '生成中...' : '生成摘要'}
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onEdit(group) }}
                className="flex items-center gap-1.5 text-xs font-medium text-text-muted hover:text-text-main transition-colors cursor-pointer"
              >
                <Pencil size={13} />
                编辑
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onDelete(group.id) }}
                className="flex items-center gap-1.5 text-xs font-medium text-text-muted hover:text-status-error transition-colors cursor-pointer"
              >
                <Trash size={13} />
                删除
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </div>
  )
}

function GroupEditor({ group, accounts, onSave, onCancel, onViewAccount }) {
  const [name, setName] = useState(group?.name || '')
  const [cronExpr, setCronExpr] = useState(group?.cron_expr || '')
  const [template, setTemplate] = useState(group?.digest_template || 'default')
  const [customPrompt, setCustomPrompt] = useState(() => {
    if (group?.digest_template === 'custom' && !group?.custom_prompt) {
      return DEFAULT_CUSTOM_PROMPT
    }
    return group?.custom_prompt || ''
  })
  const [lookback, setLookback] = useState(group?.lookback_hours || 24)
  const [lookbackMode, setLookbackMode] = useState(group?.lookback_mode || 'auto')
  const [pushTarget, setPushTarget] = useState(group?.push_target === 'ilink')
  const [selectedAccounts, setSelectedAccounts] = useState(group?.accounts || [])
  const [accountSearch, setAccountSearch] = useState('')
  const [showAccountPicker, setShowAccountPicker] = useState(false)
  const [templateExpanded, setTemplateExpanded] = useState(false)

  // ── Account picker logic ──────────────────────────────────────────────
  const filteredAccounts = accounts.filter(acc => {
    if (!accountSearch) return true
    const q = accountSearch.toLowerCase()
    return (acc.nickname || '').toLowerCase().includes(q) || (acc.username || '').toLowerCase().includes(q)
  })

  // Sort: alphabetically by nickname — no "selected first" sort, which causes
  // the list to reflow when selecting multiple accounts, making it hard to
  // pick more than one in a session.
  const sortedAccounts = [...filteredAccounts].sort((a, b) =>
    (a.nickname || a.username).localeCompare(b.nickname || b.username),
  )

  function toggleAccount(username) {
    setSelectedAccounts(prev =>
      prev.includes(username) ? prev.filter(a => a !== username) : [...prev, username]
    )
  }

  function removeAccount(username) {
    setSelectedAccounts(prev => prev.filter(a => a !== username))
  }

  // ── Cron / lookback logic ─────────────────────────────────────────────
  const autoLookbackEstimate = estimateAutoLookback(cronExpr)

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="border border-border-main rounded-xl bg-bg-card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-text-main">{group ? '编辑分组' : '新建分组'}</h4>
        <button onClick={onCancel} className="text-text-muted hover:text-text-main cursor-pointer">
          <X size={16} />
        </button>
      </div>

      {/* Row 1: Name */}
      <div>
        <label className="block text-xs text-text-muted mb-1.5">
          分组名称 <span className="text-brand-green">*</span>
        </label>
        <Input value={name} onChange={setName} placeholder="例如：科技资讯、每日必读" />
      </div>

      {/* Row 2: Accounts — search box always visible, dropdown on focus */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-text-muted">
            公众号列表 <span className="text-brand-green">*</span>
            {selectedAccounts.length > 0 && (
              <span className="ml-1.5 text-brand-green">已选 {selectedAccounts.length} 个</span>
            )}
          </label>
        </div>

        {/* Selected accounts as removable tags — always visible */}
        {selectedAccounts.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {selectedAccounts.map(gh => {
              const acc = accounts?.find(a => a.username === gh)
              return (
                <span
                  key={gh}
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-brand-green-light/30 border border-brand-green/20 text-brand-green"
                >
                  <button
                    onClick={() => onViewAccount?.(gh, acc?.nickname)}
                    className="hover:text-brand-green-hover transition-colors cursor-pointer"
                    title="点击查看该公众号的历史文章"
                  >
                    {acc?.nickname || gh}
                  </button>
                  <button
                    onClick={() => removeAccount(gh)}
                    className="hover:text-brand-green-hover cursor-pointer"
                  >
                    <X size={10} />
                  </button>
                </span>
              )
            })}
          </div>
        )}

        {/* Search box — always visible, dropdown appears on focus */}
        <div className="border border-border-main rounded-lg overflow-hidden">
          <div className="relative bg-bg-raised">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={accountSearch}
              onChange={(e) => { setAccountSearch(e.target.value); setShowAccountPicker(true) }}
              onFocus={() => setShowAccountPicker(true)}
              onBlur={() => setTimeout(() => setShowAccountPicker(false), 200)}
              placeholder="搜索公众号..."
              className="w-full bg-transparent pl-9 pr-3 py-2 text-xs text-text-main
                placeholder:text-text-muted focus:outline-none"
            />
          </div>
          {showAccountPicker && (
            <div className="max-h-48 overflow-y-auto border-t border-border-main">
              {sortedAccounts.length === 0 ? (
                <p className="text-xs text-text-muted py-4 text-center">
                  {accountSearch ? '没有匹配的公众号' : '暂无公众号数据'}
                </p>
              ) : (
                sortedAccounts.map(acc => {
                  const isSelected = selectedAccounts.includes(acc.username)
                  return (
                    <button
                      key={acc.username}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => toggleAccount(acc.username)}
                      className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors cursor-pointer
                        ${isSelected ? 'bg-brand-green-light/10' : 'hover:bg-bg-raised/60'}`}
                    >
                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors
                        ${isSelected ? 'bg-brand-green border-brand-green' : 'border-border-main'}`}>
                        {isSelected && (
                          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-bg-main" fill="currentColor">
                            <path d="M10.28 2.28L4.5 8.06 1.72 5.28l-.72.72L4.5 9.5l6.5-6.5z"/>
                          </svg>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs text-text-main truncate">{acc.nickname || acc.username}</p>
                        <p className="text-xs text-text-muted font-mono truncate">{acc.username}</p>
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          )}
        </div>
      </div>

      {/* Row 3: Schedule + Template — two columns */}
      <div className="grid grid-cols-2 gap-4">
        {/* Schedule */}
        <div>
          <label className="block text-xs text-text-muted mb-2">执行时间</label>
          <div className="grid grid-cols-3 gap-1.5 mb-2">
            {CRON_PRESETS.map((preset, idx) => {
              const isActive = preset.cron && cronExpr === preset.cron
              const isManual = !preset.cron && !cronExpr
              return (
                <button
                  key={idx}
                  onClick={() => setCronExpr(preset.cron)}
                  className={`text-center px-2 py-1.5 rounded-lg border text-xs transition-all cursor-pointer
                    ${isActive || isManual
                      ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green font-medium'
                      : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                    }`}
                >
                  {preset.label}
                </button>
              )
            })}
          </div>
          <div>
            <p className="text-xs text-text-muted mb-1">自定义 Cron（分钟 小时 日 月 周）</p>
            <textarea
              value={cronExpr}
              onChange={e => setCronExpr(e.target.value)}
              placeholder={`0 9 * * 1-5\n30 12 * * 1-5`}
              rows={3}
              className={`w-full bg-bg-raised border rounded-lg px-3 py-2 text-sm text-text-main font-mono placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all resize-none ${
                validateCronExpr(cronExpr) ? 'border-status-error' : 'border-border-main'
              }`}
            />
            {validateCronExpr(cronExpr) && (
              <p className="text-xs text-status-error font-medium mt-1">{validateCronExpr(cronExpr)}</p>
            )}
            <p className="text-xs text-text-muted mt-1">多行格式，每行一个时间点。例：<code className="text-text-muted">0 9 * * 1-5</code> = 工作日9点</p>
          </div>
        </div>

        {/* Template */}
        <div>
          <label className="block text-xs text-text-muted mb-2">摘要模板</label>
          <div className="grid grid-cols-3 gap-1.5 mb-2">
            {TEMPLATES.map(t => (
              <button
                key={t.value}
                onClick={() => {
                  if (t.value === 'custom' && !customPrompt.trim()) {
                    setCustomPrompt(DEFAULT_CUSTOM_PROMPT)
                  }
                  setTemplate(t.value)
                  setTemplateExpanded(false)
                }}
                className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg border text-xs transition-all cursor-pointer
                  ${template === t.value
                    ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green font-medium'
                    : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                  }`}
              >
                {t.PhosphorIcon ? <t.PhosphorIcon size={12} /> : null}
                {t.label}
              </button>
            ))}
          </div>

          {/* Template preview — always visible for non-custom, truncated with expand */}
          {template !== 'custom' && (() => {
            const preview = TEMPLATES.find(t => t.value === template)?.preview || ''
            const needsTruncate = preview.length > 60
            return (
              <div className="mt-1 flex gap-2 items-start">
                <div className={`flex-1 min-w-0 text-xs text-text-muted p-2 rounded-lg bg-bg-raised border border-border-main leading-relaxed
                  ${!templateExpanded && needsTruncate ? 'line-clamp-2' : ''}
                  ${templateExpanded && needsTruncate ? 'max-h-32 overflow-y-auto' : ''}`}>
                  {!templateExpanded && needsTruncate ? preview.slice(0, 60) + '...' : preview}
                </div>
                {needsTruncate && (
                  <button
                    onClick={() => setTemplateExpanded(!templateExpanded)}
                    className="text-xs text-brand-green hover:text-brand-green-hover cursor-pointer whitespace-nowrap pt-2.5 shrink-0"
                  >
                    {templateExpanded ? '收起' : '展开'}
                  </button>
                )}
              </div>
            )
          })()}

          <AnimatePresence>
            {template === 'custom' && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden space-y-2"
              >
                {/* 默认 System Prompt 预览（只读） */}
                <div>
                  <label className="text-xs text-text-muted mb-1 block">当前默认 System Prompt（只读参考）</label>
                  <div className="p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-28 overflow-y-auto whitespace-pre-wrap">
                    {DEFAULT_CUSTOM_PROMPT}
                  </div>
                </div>
                {/* 自定义 Prompt 输入框（可编辑） */}
                <div>
                  <label className="text-xs text-text-muted font-medium mb-1 block">自定义 Prompt（保存后完全替代默认，仅影响当前分组）</label>
                  <textarea
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    placeholder="填写后将完全替代默认 System Prompt，自行控制AI角色和输出格式。例如：你是一个科技资讯速读助手，只输出3条关键信息..."
                    rows={5}
                    className="w-full bg-bg-raised border border-border-main rounded-xl px-4 py-2 text-sm text-text-main
                      placeholder:text-text-muted resize-none
                      focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15"
                  />
                  <p className="text-xs text-status-warn mt-1">⚠ 保存后将用这里的内容完全替代默认 System Prompt，只影响当前公众号分组</p>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Row 4: Lookback + Push — two columns */}
      <div className="grid grid-cols-2 gap-4">
        {/* Lookback */}
        <div>
          <label className="block text-xs text-text-muted mb-2">时间范围</label>
          <div className="flex gap-2 mb-2">
            <button
              onClick={() => setLookbackMode('auto')}
              className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer
                ${lookbackMode === 'auto'
                  ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                  : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                }`}
            >
              <p className="text-xs font-medium">智能回溯</p>
              <p className="text-xs opacity-60 mt-0.5">
                约 {autoLookbackEstimate} 小时
              </p>
            </button>
            <button
              onClick={() => setLookbackMode('manual')}
              className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer
                ${lookbackMode === 'manual'
                  ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                  : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                }`}
            >
              <p className="text-xs font-medium">手动指定</p>
              <p className="text-xs opacity-60 mt-0.5">自定义小时数</p>
            </button>
          </div>
          {lookbackMode === 'manual' && (
            <div className="flex items-center gap-3">
              <input
                type="range"
                min="1"
                max="72"
                value={lookback}
                onChange={(e) => setLookback(parseInt(e.target.value))}
                className="flex-1 accent-brand-green"
              />
              <span className="text-xs font-medium text-text-main w-16 text-right">
                {lookback} 小时
              </span>
            </div>
          )}
          <p className="text-xs text-text-muted mt-1">
            {lookbackMode === 'auto'
              ? '定时间隔 + 1h 缓冲'
              : '获取多长时间内的文章'}
          </p>
        </div>

        {/* Push to WeChat — prominent card-style toggle */}
        <div>
          <label className="block text-xs text-text-muted mb-2">推送设置</label>
          <div className={`flex items-center justify-between gap-3 p-3 rounded-lg border transition-colors
            ${pushTarget ? 'border-brand-green/30 bg-brand-green-light/10' : 'border-border-main bg-bg-raised'}`}>
            <div className="flex items-center gap-2.5">
              <Export size={18} className={pushTarget ? 'text-brand-green' : 'text-text-muted'} />
              <div>
                <p className="text-sm font-medium text-text-main">推送到微信</p>
                <p className="text-xs text-text-muted">摘要自动推送至私聊</p>
              </div>
            </div>
            <Toggle enabled={pushTarget} onChange={setPushTarget} />
          </div>
        </div>
      </div>

      {/* Save / Cancel */}
      <div className="flex gap-2 pt-1">
        <button
          onClick={() => onSave({
            name,
            cron_expr: cronExpr,
            digest_template: template,
            custom_prompt: template === 'custom' ? customPrompt : '',
            lookback_hours: lookback,
            lookback_mode: lookbackMode,
            push_target: pushTarget ? 'ilink' : '',
            accounts: selectedAccounts,
          })}
          disabled={!name.trim() || selectedAccounts.length === 0 || !!validateCronExpr(cronExpr) || (template === 'custom' && !customPrompt.trim())}
          className="flex-1 py-2.5 rounded-full bg-brand-green-hover text-white text-sm font-semibold
            hover:bg-brand-green-hover transition-colors cursor-pointer
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          保存分组
        </button>
        <button
          onClick={onCancel}
          className="flex-1 py-2.5 rounded-full bg-bg-raised text-text-muted text-sm font-semibold
            hover:bg-bg-raised/80 transition-colors cursor-pointer"
        >
          取消
        </button>
      </div>
    </div>
  )
}

// ── OA Monitor Group Card ────────────────────────────────────────────

function MonitorGroupCard({ group, accounts, onEdit, onDelete, onToggle }) {
  const [expanded, setExpanded] = useState(false)

  const accountNames = (group.accounts || [])
    .filter(gh => accounts?.some(a => a.username === gh))
    .map(gh => {
      const acc = accounts?.find(a => a.username === gh)
      return acc ? acc.nickname : gh
    })

  return (
    <div className={`border rounded-xl overflow-hidden bg-bg-card transition-colors
      ${group.enabled ? 'border-border-main hover:border-text-muted/20' : 'border-border-main/50 opacity-60'}`}>
      <div className="flex items-center gap-3 p-3.5">
        <Toggle enabled={group.enabled} onChange={() => onToggle(group)} />
        <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpanded(!expanded)}>
          <div className="flex items-center gap-2">
            <Bell size={14} className="text-amber-500/70 shrink-0" />
            <p className="text-sm font-medium text-text-main truncate">{group.name || '未命名关注'}</p>
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-xs text-text-muted">{accountNames.length} 个公众号</span>
            {group.push_target === 'ilink' && (
              <>
                <span className="text-xs text-text-muted">·</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-medium">通知</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onEdit(group)}
            className="p-1.5 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
          >
            <Pencil size={13} />
          </button>
          <button
            onClick={() => onDelete(group.id)}
            className="p-1.5 rounded-lg text-text-muted hover:text-status-error hover:bg-bg-raised transition-colors cursor-pointer"
          >
            <Trash size={13} />
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-1.5 rounded-lg text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
          >
            {expanded ? <CaretUp size={13} /> : <CaretDown size={13} />}
          </button>
        </div>
      </div>

      {expanded && accountNames.length > 0 && (
        <div className="px-3.5 pb-3.5 pt-1 border-t border-border-main">
          <div className="flex flex-wrap gap-1.5 mt-2">
            {accountNames.map((name, i) => (
              <span
                key={i}
                className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/15"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── OA Monitor Group Editor ──────────────────────────────────────────

function MonitorGroupEditor({ group, accounts, onSave, onCancel }) {
  const [name, setName] = useState(group?.name || '')
  const [selectedAccounts, setSelectedAccounts] = useState(group?.accounts || [])
  const [pushTarget, setPushTarget] = useState(group?.push_target === 'ilink')
  const [accountSearch, setAccountSearch] = useState('')
  const [showAccountPicker, setShowAccountPicker] = useState(false)

  const filteredAccounts = accounts.filter(acc => {
    if (!accountSearch) return true
    const q = accountSearch.toLowerCase()
    return (acc.nickname || '').toLowerCase().includes(q) || (acc.username || '').toLowerCase().includes(q)
  })

  const sortedAccounts = [...filteredAccounts].sort((a, b) => {
    const aSel = selectedAccounts.includes(a.username) ? 0 : 1
    const bSel = selectedAccounts.includes(b.username) ? 0 : 1
    if (aSel !== bSel) return aSel - bSel
    return (a.nickname || a.username).localeCompare(b.nickname || b.username)
  })

  function toggleAccount(username) {
    setSelectedAccounts(prev =>
      prev.includes(username) ? prev.filter(a => a !== username) : [...prev, username]
    )
  }

  function removeAccount(username) {
    setSelectedAccounts(prev => prev.filter(a => a !== username))
  }

  return (
    <div className="border border-amber-500/30 rounded-xl bg-bg-card p-4 space-y-3.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bell size={16} className="text-amber-500" />
          <h4 className="text-sm font-semibold text-text-main">{group ? '编辑关注' : '新建关注'}</h4>
        </div>
        <button onClick={onCancel} className="text-text-muted hover:text-text-main cursor-pointer">
          <X size={16} />
        </button>
      </div>

      {/* Name */}
      <div>
        <label className="block text-xs text-text-muted mb-1.5">关注名称 <span className="text-amber-500">*</span></label>
        <Input value={name} onChange={setName} placeholder="例如：科技动态、行业快讯" />
      </div>

      {/* Account picker */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-text-muted">
            关注公众号 <span className="text-amber-500">*</span>
            {selectedAccounts.length > 0 && (
              <span className="ml-1.5 text-amber-500">已选 {selectedAccounts.length} 个</span>
            )}
          </label>
        </div>

        {selectedAccounts.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2">
            {selectedAccounts.map(gh => {
              const acc = accounts?.find(a => a.username === gh)
              return (
                <span
                  key={gh}
                  className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400"
                >
                  {acc?.nickname || gh}
                  <button onClick={() => removeAccount(gh)} className="hover:text-amber-700 cursor-pointer">
                    <X size={10} />
                  </button>
                </span>
              )
            })}
          </div>
        )}

        {/* 搜索框始终可见，focus 时展开下拉列表 */}
        <div className="border border-border-main rounded-lg overflow-hidden">
          <div className="relative bg-bg-raised">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={accountSearch}
              onChange={(e) => { setAccountSearch(e.target.value); setShowAccountPicker(true) }}
              onFocus={() => setShowAccountPicker(true)}
              placeholder="搜索公众号..."
              className="w-full bg-transparent pl-9 pr-3 py-2 text-xs text-text-main
                placeholder:text-text-muted focus:outline-none"
              onBlur={() => setTimeout(() => setShowAccountPicker(false), 200)}
            />
          </div>
          {showAccountPicker && (
            <div className="max-h-48 overflow-y-auto border-t border-border-main">
              {sortedAccounts.length === 0 ? (
                <p className="text-xs text-text-muted py-4 text-center">
                  {accountSearch ? '没有匹配的公众号' : '暂无公众号数据'}
                </p>
              ) : (
                sortedAccounts.map(acc => {
                  const isSelected = selectedAccounts.includes(acc.username)
                  return (
                    <button
                      key={acc.username}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => toggleAccount(acc.username)}
                      className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors cursor-pointer
                        ${isSelected ? 'bg-brand-green-light/10' : 'hover:bg-bg-raised/60'}`}
                    >
                      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors
                        ${isSelected ? 'bg-brand-green border-brand-green' : 'border-border-main'}`}>
                        {isSelected && (
                          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-bg-main" fill="currentColor">
                            <path d="M10.28 2.28L4.5 8.06 1.72 5.28l-.72.72L4.5 9.5l6.5-6.5z"/>
                          </svg>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs text-text-main truncate">{acc.nickname || acc.username}</p>
                        <p className="text-xs text-text-muted font-mono truncate">{acc.username}</p>
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          )}
        </div>
      </div>

      {/* Push toggle */}
      <div className={`flex items-center justify-between gap-3 p-3 rounded-lg border transition-colors
        ${pushTarget ? 'border-brand-green/30 bg-brand-green-light/10' : 'border-border-main bg-bg-raised'}`}>
        <div className="flex items-center gap-2.5">
          <Export size={18} className={pushTarget ? 'text-brand-green' : 'text-text-muted'} />
          <div>
            <p className="text-sm font-medium text-text-main">推送到微信</p>
            <p className="text-xs text-text-muted">新文章即时推送至私聊</p>
          </div>
        </div>
        <Toggle enabled={pushTarget} onChange={setPushTarget} />
      </div>

      {/* Save / Cancel */}
      <div className="flex gap-2 pt-1">
        <button
          onClick={() => onSave({
            name,
            accounts: selectedAccounts,
            push_target: pushTarget ? 'ilink' : '',
          })}
          disabled={!name.trim() || selectedAccounts.length === 0}
          className="flex-1 py-2.5 rounded-full bg-amber-500 text-white text-sm font-semibold
            hover:bg-amber-600 transition-colors cursor-pointer
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          保存关注
        </button>
        <button
          onClick={onCancel}
          className="flex-1 py-2.5 rounded-full bg-bg-raised text-text-muted text-sm font-semibold
            hover:bg-bg-raised/80 transition-colors cursor-pointer"
        >
          取消
        </button>
      </div>
    </div>
  )
}

function ArticleCard({ article }) {
  const timeStr = article.create_time
    ? new Date(article.create_time * 1000).toLocaleDateString('zh-CN')
    : article.pub_time
      ? new Date(article.pub_time * 1000).toLocaleDateString('zh-CN')
      : ''

  return (
    <div className="border border-border-main rounded-xl overflow-hidden bg-bg-card hover:border-text-muted/20 transition-colors">
      <div className="p-4">
        <div className="flex items-start gap-3">
          {article.cover ? (
            <div className="w-14 h-14 rounded-lg overflow-hidden shrink-0 bg-bg-raised border border-border-main">
              <img src={article.cover} alt="" className="w-full h-full object-cover"
                onError={(e) => { e.target.style.display = 'none' }} />
            </div>
          ) : (
            <div className="w-14 h-14 rounded-lg bg-bg-raised flex items-center justify-center text-text-muted shrink-0 border border-border-main">
              <FileText size={20} />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-text-main hover:text-brand-green transition-colors line-clamp-2"
            >
              {article.title}
            </a>
            <p className="text-xs text-text-muted mt-1 line-clamp-2">
              {article.digest || ''}
            </p>
            <div className="flex items-center gap-2 mt-1.5">
              {article.source_name && (
                <span className="text-xs text-brand-green/80 flex items-center gap-0.5">
                  <Globe size={8} /> {article.source_name}
                </span>
              )}
              {timeStr && (
                <span className="text-xs text-text-muted font-mono">{timeStr}</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function OATab() {
  const [accounts, setAccounts] = useState([])
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showEditor, setShowEditor] = useState(false)
  const [editingGroup, setEditingGroup] = useState(null)
  const [search, setSearch] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [digestRunning, setDigestRunning] = useState('')
  const [digestProgress, setDigestProgress] = useState('')
  const [lastDigest, setLastDigest] = useState(null)
  const [selectedAccount, setSelectedAccount] = useState(null)
  const [accountArticles, setAccountArticles] = useState([])
  const [loadingArticles, setLoadingArticles] = useState(false)
  const [accountFilter, setAccountFilter] = useState('')

  // OA Monitor state
  const [monitorGroups, setMonitorGroups] = useState([])
  const [showMonitorEditor, setShowMonitorEditor] = useState(false)
  const [editingMonitor, setEditingMonitor] = useState(null)

  // "已关注公众号" collapse
  const [showAllAccounts, setShowAllAccounts] = useState(false)
  const ACCOUNTS_COLLAPSE_LIMIT = 5

  const editorRef = useRef(null)
  const articlesRef = useRef(null)

  useEffect(() => {
    loadData()
  }, [])

  // Auto-scroll to editor when it opens
  useEffect(() => {
    if (showEditor && editorRef.current) {
      setTimeout(() => {
        editorRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 350)
    }
  }, [showEditor])

  // Auto-scroll to monitor editor when it opens
  const monitorEditorRef = useRef(null)
  useEffect(() => {
    if (showMonitorEditor && monitorEditorRef.current) {
      setTimeout(() => {
        monitorEditorRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 350)
    }
  }, [showMonitorEditor])

  // WebSocket for digest + monitor push results
  const [monitorToast, setMonitorToast] = useState(null)

  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'oa_digest_progress') {
          if (data.status === 'completed') {
            setDigestRunning('')
            setDigestProgress(`✅ 摘要生成完成（${data.articles_count || 0} 篇文章）`)
            setTimeout(() => setDigestProgress(''), 3000)
            setLastDigest({ groupId: data.group_id, text: data.digest_text, articlesCount: data.articles_count || 0 })
          } else if (data.status === 'error') {
            setDigestRunning('')
            setDigestProgress(`⚠ ${data.error || '生成失败'}`)
            setTimeout(() => setDigestProgress(''), 5000)
          } else if (data.progress) {
            setDigestProgress(data.progress)
          }
        }
        if (data.type === 'oa_digest_push_result') {
          const pushMsg = data.success
            ? `✓ 推送成功: ${data.group_name}`
            : `⚠ 推送失败: ${data.group_name} — ${data.error || '未知错误'}`
          setDigestProgress(pushMsg)
          setTimeout(() => setDigestProgress(''), 3000)
        }
        // OA Monitor push result — show toast
        if (data.type === 'oa_monitor_push_result') {
          setMonitorToast({
            success: data.success,
            group_name: data.group_name,
            session_expired: data.session_expired,
            error: data.error,
          })
          setTimeout(() => setMonitorToast(null), data.session_expired ? 10000 : 3000)
        }
      } catch {}
    }
    let ws = window.__oa_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__oa_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  async function loadData() {
    setLoading(true)
    try {
      const [accRes, groupRes, configRes] = await Promise.all([
        fetch(`${API_BASE}/api/oa/accounts`),
        fetch(`${API_BASE}/api/oa/groups`),
        fetch(`${API_BASE}/api/assistant/config`),
      ])
      const accData = await accRes.json()
      const groupData = await groupRes.json()
      const configData = await configRes.json()
      if (accData.ok) setAccounts(accData.data || [])
      if (groupData.ok) setGroups(groupData.data || [])
      if (configData.ok && configData.config) {
        setMonitorGroups(configData.config.oa_monitor_groups || [])
      }
    } catch {
      setError('加载失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSaveGroup(data) {
    try {
      const method = editingGroup ? 'PUT' : 'POST'
      const url = editingGroup
        ? `${API_BASE}/api/oa/groups/${editingGroup.id}`
        : `${API_BASE}/api/oa/groups/create`

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      const result = await res.json()
      if (result.ok) {
        setShowEditor(false)
        setEditingGroup(null)
        loadData()
      }
    } catch {}
  }

  async function handleDeleteGroup(id) {
    if (!confirm('确定删除此分组？')) return
    try {
      const res = await fetch(`${API_BASE}/api/oa/groups/${id}`, { method: 'DELETE' })
      const result = await res.json()
      if (result.ok) loadData()
    } catch {}
  }

  async function handleRunDigest(groupId) {
    setDigestRunning(groupId)
    setDigestProgress('正在提交任务...')
    try {
      const res = await fetch(`${API_BASE}/api/oa/digest/run/${groupId}`, { method: 'POST' })
      const data = await res.json()
      if (data.ok) {
        setDigestProgress('⏳ 摘要生成中，右上角任务中心查看进度')
      } else {
        setDigestProgress(`⚠ ${data.error || '提交失败'}`)
        setTimeout(() => setDigestProgress(''), 4000)
        setDigestRunning('')
      }
    } catch {
      setDigestProgress('⚠ 提交失败，请重试')
      setTimeout(() => setDigestProgress(''), 4000)
      setDigestRunning('')
    }
  }

  async function handleSearch() {
    if (!search.trim()) return
    setSearching(true)
    try {
      const res = await fetch(`${API_BASE}/api/oa/search?q=${encodeURIComponent(search)}`)
      const data = await res.json()
      if (data.ok) setSearchResults(data.data || [])
    } catch {}
    setSearching(false)
  }

  async function handleViewAccount(ghId, nickname) {
    if (selectedAccount?.username === ghId) {
      setSelectedAccount(null)
      setAccountArticles([])
      return
    }
    setSelectedAccount({ username: ghId, nickname })
    setLoadingArticles(true)
    setAccountArticles([])
    // Scroll to the articles panel so the user sees results directly
    setTimeout(() => {
      articlesRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 100)
    try {
      const res = await fetch(`${API_BASE}/api/oa/articles?gh_id=${encodeURIComponent(ghId)}&limit=50`)
      const data = await res.json()
      if (data.ok) setAccountArticles(data.data || [])
    } catch {}
    setLoadingArticles(false)
  }

  function clearSearch() {
    setSearch('')
    setSearchResults([])
  }

  // ── OA Monitor CRUD ──────────────────────────────────────────────

  async function saveMonitorConfig(updatedGroups) {
    try {
      const res = await fetch(`${API_BASE}/api/assistant/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ oa_monitor_groups: updatedGroups }),
      })
      const data = await res.json()
      if (data.ok) {
        setMonitorGroups(updatedGroups)
        return true
      }
    } catch {}
    return false
  }

  async function handleSaveMonitor(data) {
    const updated = [...monitorGroups]
    if (editingMonitor) {
      const idx = updated.findIndex(g => g.id === editingMonitor.id)
      if (idx >= 0) {
        updated[idx] = { ...editingMonitor, ...data }
      }
    } else {
      const newId = `oam_${Date.now().toString(36)}`
      updated.push({ id: newId, enabled: true, ...data })
    }
    const ok = await saveMonitorConfig(updated)
    if (ok) {
      setShowMonitorEditor(false)
      setEditingMonitor(null)
    }
  }

  async function handleDeleteMonitor(id) {
    if (!confirm('确定删除此关注？')) return
    const updated = monitorGroups.filter(g => g.id !== id)
    await saveMonitorConfig(updated)
  }

  async function handleToggleMonitor(group) {
    const updated = monitorGroups.map(g =>
      g.id === group.id ? { ...g, enabled: !g.enabled } : g
    )
    await saveMonitorConfig(updated)
  }

  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }}>
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm" style={{ backgroundColor: '#F59E0B' }} />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">公众号</h3>
          <Newspaper size={16} className="text-text-muted" />
          <div className="ml-auto">
            <button
              onClick={() => { setEditingGroup(null); setShowEditor(true) }}
              className="flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold bg-brand-green-hover text-white hover:bg-brand-green-hover transition-colors cursor-pointer"
            >
              <Plus size={14} />
              新建分组
            </button>
          </div>
        </div>
        <p className="text-xs text-text-muted leading-relaxed pl-4">将公众号按主题分组，AI 定时生成摘要 · 数据来源于本地微信数据库</p>
      </div>

      {/* Refresh button */}
      <div className="mb-3 flex items-center justify-end">
        <button
          onClick={loadData}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
          title="刷新公众号列表（需先在微信中打开公众号历史消息）"
        >
          <ArrowsClockwise size={12} />
          刷新数据
        </button>
      </div>

      {/* Account overview */}
      {accounts.length > 0 && (
        <div className="mb-5 p-4 rounded-xl border border-border-main bg-bg-card">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs text-text-muted font-medium">已关注公众号 ({accounts.length})</p>
            <p className="text-xs text-text-muted">点击查看历史文章</p>
          </div>
          {accounts.length > 10 && (
            <div className="relative mb-2">
              <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                value={accountFilter}
                onChange={(e) => setAccountFilter(e.target.value)}
                placeholder="搜索公众号..."
                className="w-full bg-bg-raised border border-border-main rounded-full pl-9 pr-3 py-1.5 text-xs text-text-main
                  placeholder:text-text-muted focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15"
              />
              {accountFilter && (
                <button
                  onClick={() => setAccountFilter('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main cursor-pointer"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-1.5">
            {accounts
              .filter(acc => !accountFilter || (acc.nickname || acc.username).toLowerCase().includes(accountFilter.toLowerCase()))
              .slice(0, showAllAccounts || accountFilter ? undefined : ACCOUNTS_COLLAPSE_LIMIT)
              .map(acc => (
                <button
                  key={acc.username}
                  onClick={() => handleViewAccount(acc.username, acc.nickname)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors cursor-pointer
                    ${selectedAccount?.username === acc.username
                      ? 'bg-brand-green-light/30 border-brand-green/30 text-brand-green'
                      : 'bg-bg-raised text-text-main border-border-main hover:border-brand-green/30 hover:text-brand-green'
                    }`}
                >
                  {acc.nickname || acc.username}
                </button>
              ))}
            {!accountFilter && accounts.length > ACCOUNTS_COLLAPSE_LIMIT && (
              <button
                onClick={() => setShowAllAccounts(!showAllAccounts)}
                className="text-xs px-2.5 py-1 rounded-full border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-colors cursor-pointer"
              >
                {showAllAccounts ? `收起` : `+ ${accounts.length - ACCOUNTS_COLLAPSE_LIMIT} 个`}
              </button>
            )}
            {accountFilter && accounts.filter(acc => (acc.nickname || acc.username).toLowerCase().includes(accountFilter.toLowerCase())).length === 0 && (
              <p className="text-xs text-text-muted py-1">没有匹配的公众号</p>
            )}
          </div>
        </div>
      )}

      {/* Selected account articles panel */}
      <AnimatePresence>
        {selectedAccount && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-5 overflow-hidden"
          >
            <div ref={articlesRef} className="p-4 rounded-xl border border-brand-green/20 bg-bg-card">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Globe size={14} className="text-brand-green" />
                  <span className="text-sm font-medium text-text-main">{selectedAccount.nickname || selectedAccount.username}</span>
                  <span className="text-xs text-text-muted">的历史文章</span>
                </div>
                <button
                  onClick={() => { setSelectedAccount(null); setAccountArticles([]) }}
                  className="p-1.5 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
                >
                  <X size={14} />
                </button>
              </div>
              {loadingArticles ? (
                <div className="flex items-center justify-center py-8">
                  <div className="w-5 h-5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
                </div>
              ) : accountArticles.length === 0 ? (
                <div className="text-center py-8 text-text-muted text-xs">
                  <FileText size={24} className="mx-auto mb-2 opacity-30" />
                  <p>暂无文章</p>
                  <p className="text-xs mt-1">请在微信中打开该公众号的历史消息后刷新</p>
                </div>
              ) : (
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {accountArticles.map((article, i) => (
                    <ArticleCard key={i} article={article} />
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search */}
      <div className="mb-5">
        <div className="flex gap-2 mb-3">
          <div className="relative flex-1">
            <MagnifyingGlass size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="搜索公众号文章..."
              className="w-full bg-bg-raised border border-border-main rounded-full pl-10 pr-10 py-2.5 text-sm text-text-main
                placeholder:text-text-muted font-mono
                focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                transition-all"
            />
            {search && (
              <button
                onClick={clearSearch}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main cursor-pointer"
              >
                <X size={14} />
              </button>
            )}
          </div>
          <button
            onClick={handleSearch}
            disabled={searching || !search.trim()}
            className="px-4 py-2.5 rounded-full text-xs font-medium bg-bg-raised border border-border-main
              text-text-muted hover:text-text-main hover:border-text-muted/30 transition-all
              disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {searching ? '搜索中...' : '搜索'}
          </button>
        </div>

        {/* Search results */}
        <AnimatePresence>
          {searchResults.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="mb-4"
            >
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs text-text-muted">搜索结果 ({searchResults.length})</p>
                <button onClick={() => setSearchResults([])} className="text-xs text-text-muted hover:text-text-main cursor-pointer">
                  清除
                </button>
              </div>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {searchResults.map((article, i) => (
                  <ArticleCard key={i} article={article} />
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── OA Monitor: 公众号即时提醒 ──────────────────────────────── */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-2.5">
          <div className="flex items-center gap-2">
            <Bell size={14} className="text-amber-500/80" />
            <p className="text-xs text-text-muted font-medium">
              即时提醒 ({monitorGroups.length})
            </p>
          </div>
          {monitorGroups.length > 0 && (
            <button
              onClick={() => { setEditingMonitor(null); setShowMonitorEditor(true) }}
              className="text-xs text-amber-500 hover:underline cursor-pointer flex items-center gap-1"
            >
              <Plus size={10} /> 新建
            </button>
          )}
        </div>

        {/* Monitor editor */}
        <AnimatePresence>
          {showMonitorEditor && (
            <motion.div
              key={editingMonitor?.id || 'new'}
              ref={monitorEditorRef}
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="mb-3"
            >
              <MonitorGroupEditor
                group={editingMonitor}
                accounts={accounts}
                onSave={handleSaveMonitor}
                onCancel={() => { setShowMonitorEditor(false); setEditingMonitor(null) }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Monitor cards */}
        {monitorGroups.length === 0 ? (
          <div className="border border-dashed border-border-main rounded-xl bg-bg-raised/30 p-4 text-center">
            <Bell size={20} className="mx-auto mb-2 text-amber-500/30" />
            <p className="text-xs text-text-muted mb-1">公众号新文章即时提醒</p>
            <p className="text-xs text-text-muted/60 mb-2.5">关注公众号发新文章后，推送通知到微信</p>
            <button
              onClick={() => { setEditingMonitor(null); setShowMonitorEditor(true) }}
              className="px-4 py-1.5 rounded-full text-xs font-semibold bg-amber-500 text-white
                hover:bg-amber-600 transition-colors cursor-pointer"
            >
              <Plus size={11} className="inline mr-1 -mt-0.5" />
              创建关注
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {monitorGroups.map(group => (
              <MonitorGroupCard
                key={group.id}
                group={group}
                accounts={accounts}
                onEdit={(g) => { setEditingMonitor(g); setShowMonitorEditor(true) }}
                onDelete={handleDeleteMonitor}
                onToggle={handleToggleMonitor}
              />
            ))}
          </div>
        )}
      </div>

      {/* Group Editor */}
      <AnimatePresence>
        {showEditor && (
          <motion.div
            key={editingGroup?.id || 'new'}
            ref={editorRef}
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            className="mb-6"
          >
            <GroupEditor
              group={editingGroup}
              accounts={accounts}
              onSave={handleSaveGroup}
              onCancel={() => { setShowEditor(false); setEditingGroup(null) }}
              onViewAccount={handleViewAccount}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Groups */}
      <div className="mb-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-text-muted font-medium">
            AI 摘要 ({groups.length})
          </p>
          {groups.length > 0 && (
            <button
              onClick={() => { setEditingGroup(null); setShowEditor(true) }}
              className="text-xs text-brand-green hover:underline cursor-pointer flex items-center gap-1"
            >
              <Plus size={10} /> 新建
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-6 h-6 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
        </div>
      ) : groups.length === 0 ? (
        <div className="text-center py-12 border border-dashed border-border-main rounded-xl bg-bg-raised/30">
          <Sparkle size={32} className="mx-auto mb-3 text-brand-green/30" />
          <p className="text-sm text-text-muted">还没有摘要任务</p>
          <div className="mt-3 space-y-1.5 text-xs text-text-muted max-w-xs mx-auto">
            <p><span className="text-brand-green/80">1.</span> 新建分组，给关注的公众号分类（如"科技资讯"）</p>
            <p><span className="text-brand-green/80">2.</span> 选择摘要模板和执行时间</p>
            <p><span className="text-brand-green/80">3.</span> AI 按时生成该分组所有公众号的内容摘要</p>
          </div>
          <button
            onClick={() => { setEditingGroup(null); setShowEditor(true) }}
            className="mt-4 px-5 py-2 rounded-full text-xs font-semibold bg-brand-green-hover text-white
              hover:bg-brand-green-hover transition-colors cursor-pointer"
          >
            <Plus size={12} className="inline mr-1 -mt-0.5" />
            创建第一个分组
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map(group => (
            <GroupCard
              key={group.id}
              group={group}
              accounts={accounts}
              onEdit={(g) => { setEditingGroup(g); setShowEditor(true) }}
              onDelete={handleDeleteGroup}
              onRunDigest={handleRunDigest}
              digestRunning={digestRunning}
              lastDigest={lastDigest}
              onViewAccount={handleViewAccount}
            />
          ))}
        </div>
      )}

      {/* Running digest indicator */}
      {digestRunning && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="fixed bottom-6 right-6 px-4 py-3 rounded-xl bg-brand-green-light border border-brand-green/30 text-brand-green text-sm font-medium flex items-center gap-2 shadow-lg cursor-pointer"
          onClick={() => window.dispatchEvent(new CustomEvent('open-task-center'))}
        >
          <div className="w-4 h-4 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
          {digestProgress || '生成摘要中...'}
          <span className="text-xs opacity-70 ml-1">点击查看任务中心</span>
        </motion.div>
      )}

      {/* Monitor push toast */}
      {monitorToast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2.5 rounded-lg text-sm font-medium shadow-lg border transition-all
          ${monitorToast.success
            ? 'bg-brand-green-light border-brand-green/30 text-brand-green'
            : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800/40 text-red-600 dark:text-red-400'
          }`}>
          {monitorToast.success
            ? `✓ 推送成功: ${monitorToast.group_name}`
            : monitorToast.session_expired
              ? <div>
                  <div>⚠ 推送失败: {monitorToast.group_name}</div>
                  <div className="text-xs mt-1 opacity-90">微信链接可能已断开，请在系统配置中重新绑定</div>
                </div>
              : `⚠ 推送失败: ${monitorToast.group_name}${monitorToast.error ? ' — ' + monitorToast.error : ''}`
          }
        </div>
      )}
    </motion.div>
  )
}
