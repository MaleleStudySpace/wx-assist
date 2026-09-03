import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle, Warning, Spinner, MagnifyingGlass, Bell, Clock, ChatCircle, CaretDown, CaretRight, EnvelopeOpen, Archive, Lightning, Trash, X, Plus, Play } from '@phosphor-icons/react'
import { Toggle, SectionHeader, TagInput, Input, API_BASE, getWsUrl } from './SharedComponents'
import { WEEKDAY_LABELS, parseCronExpr, cronToLabel } from '../utils/cron'

const pageTransition = {
  initial: { opacity: 0, x: 12 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -12 },
}

function parsePushTargets(value) {
  if (!value) return []
  if (Array.isArray(value)) return value
  try { const parsed = JSON.parse(value); return Array.isArray(parsed) ? parsed : [value] } catch { return [value] }
}

const PRESET_TIMES = ['09:00', '12:00', '14:00', '18:00', '21:00', '23:00']

function PushTargetSelect({ value, onChange, enabledPlatforms = ['ilink'] }) {
  const selected = parsePushTargets(value)
  function toggle(platform) {
    const next = selected.includes(platform) ? selected.filter(item => item !== platform) : [...selected, platform]
    onChange(next.length ? JSON.stringify(next) : '')
  }
  return <div className="flex flex-wrap gap-2">
    {[['ilink', '微信'], ['qqbot', 'QQ'], ['feishu', '飞书']].map(([id, label]) => enabledPlatforms.includes(id) && (
      <button key={id} type="button" onClick={() => toggle(id)} className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${selected.includes(id) ? 'border-brand-green bg-brand-green/10 text-brand-green-hover' : 'border-border-main text-text-muted hover:bg-bg-raised'}`}>
        {selected.includes(id) ? '✓ ' : ''}{label}
      </button>
    ))}
  </div>
}


// ── Cron helpers ─────────────────────────────────────────────────────
// WEEKDAY_LABELS / parseCronExpr / cronToLabel 已移到 utils/cron.js ——
// OATab、Dashboard、SchedulerPanel 必须用同一套标签口径。
// 下面两个刻意留在本文件：validateCronExpr 是**受限版**（要求日/月必须是 *），
// 与 utils/cron.js 里那个接受完整语法的同名函数语义不同，不能混用。

function buildCronExpr(times, freqMode, weekdays) {
  const parsed = times.map(t => {
    const [h, m] = t.split(':').map(Number)
    return { hour: h, minute: m || 0 }
  }).sort((a, b) => a.hour - b.hour || a.minute - b.minute)
  if (!parsed.length) parsed.push({ hour: 9, minute: 0 })

  // Build dow field. Use range for weekday so round-trip parsing is stable.
  let dowField = '*'
  if (freqMode === 'weekday') {
    dowField = '1-5'
  } else if (freqMode === 'custom') {
    dowField = [...weekdays].sort((a, b) => a - b).join(',') || '*'
  }

  // Store one cron line per selected time. This avoids ambiguous compact forms
  // and prevents accidental concatenation when users edit schedule repeatedly.
  return parsed.map(p => `${p.minute} ${p.hour} * * ${dowField}`).join('\n')
}

/**
 * Validate a cron expression against our fixed rule.
 * Returns error message string if invalid, empty string if valid.
 *
 * Rule: multi-line; each line = 5 fields; minute/hour = single int; day/month = *; dow = * or list/range
 */
function validateCronExpr(cronExpr) {
  if (!cronExpr || !cronExpr.trim()) return ''  // Empty is allowed (fallback to schedule)
  const lines = cronExpr.trim().split(/\n/).map(l => l.trim()).filter(Boolean)
  for (let i = 0; i < lines.length; i++) {
    const fields = lines[i].split(/\s+/)
    if (fields.length !== 5) return `第${i+1}行：必须有5个字段（分 时 日 月 周），当前: ${lines[i]}`
    const [min, hour, day, month, dow] = fields
    // minute: integer 0-59
    const m = Number(min)
    if (!Number.isInteger(m) || m < 0 || m > 59) return `第${i+1}行：分钟=${min} 必须是0-59的整数`
    // hour: integer 0-23
    const h = Number(hour)
    if (!Number.isInteger(h) || h < 0 || h > 23) return `第${i+1}行：小时=${hour} 必须是0-23的整数`
    // day/month must be *
    if (day !== '*') return `第${i+1}行：日=${day} 必须是 *`
    if (month !== '*') return `第${i+1}行：月=${month} 必须是 *`
    // dow: * or valid range/list of 0-6
    if (dow !== '*' && !/^(\d+(-\d+)?)(,\d+(-\d+)?)*$/.test(dow)) {
      return `第${i+1}行：周=${dow} 格式错误，支持 * | 1-5 | 1,2,3,4,5`
    }
  }
  return ''
}

/** 从 schedule 或 cron_expr 推导智能 lookback 值（前端计算，无上限） */
function estimateGroupLookback(schedule, cronExpr) {
  // 优先 cron 表达式
  if (cronExpr && cronExpr.trim()) {
    const lines = cronExpr.trim().split('\n').map(l => l.trim()).filter(Boolean)
    if (!lines.length) return 24

    // 收集所有行的小时和星期
    const hours = new Set()
    const days = new Set()

    for (const line of lines) {
      const parts = line.split(/\s+/)
      if (parts.length < 5) continue

      // 解析小时
      for (const seg of parts[1].split(',')) {
        const s = seg.trim()
        if (s === '*') { for (let h = 0; h < 24; h++) hours.add(h); break }
        if (s.startsWith('*/')) { const step = parseInt(s.slice(2)); if (step) for (let h = 0; h < 24; h += step) hours.add(h); continue }
        if (s.includes('-') && !s.startsWith('-')) { const [lo, hi] = s.split('-').map(Number); if (!isNaN(lo) && !isNaN(hi)) for (let h = lo; h <= hi; h++) hours.add(h); continue }
        const n = parseInt(s); if (!isNaN(n)) hours.add(n)
      }

      // 解析星期
      const dow = parts[4] || '*'
      if (dow === '*') {
        for (let d = 0; d < 7; d++) days.add(d)
      } else {
        for (const seg of dow.split(',')) {
          const s = seg.trim()
          if (s.includes('-') && !s.startsWith('-')) {
            const [lo, hi] = s.split('-').map(Number)
            if (!isNaN(lo) && !isNaN(hi)) for (let d = lo; d <= hi; d++) days.add(d % 7)
          } else { const n = parseInt(s); if (!isNaN(n)) days.add(n % 7) }
        }
      }
    }

    if (!hours.size || !days.size) return 24

    // 构建所有 (天×24+小时) 时间槽
    const slots = []
    for (const d of days) for (const h of hours) slots.push(d * 24 + h)
    if (slots.length <= 1) return 25
    slots.sort((a, b) => a - b)
    let maxGap = 0
    for (let i = 1; i < slots.length; i++) maxGap = Math.max(maxGap, slots[i] - slots[i - 1])
    // 跨周间隔（上限 48h，避免跳过周末导致 96h+）
    maxGap = Math.max(maxGap, 7 * 24 - slots[slots.length - 1] + slots[0])
    return Math.min(maxGap + 1, 48)
  }

  // schedule 格式 ["09:00", "18:00"]
  if (schedule && schedule.length) {
    const times = schedule.map(t => {
      const [h, m] = t.split(':').map(Number)
      return h + m / 60
    }).sort((a, b) => a - b)
    if (times.length >= 2) {
      const gaps = []
      for (let i = 1; i < times.length; i++) gaps.push(times[i] - times[i - 1])
      gaps.push(24 - times[times.length - 1] + times[0])
      return Math.ceil(Math.min(...gaps) + 1)
    }
    return 25
  }
  return 24
}

/** 带卡点的滑杆组件：0-72h，可点击卡点 6/12/24/48/72，松开吸附 */
function LookbackSlider({ value, onChange, min = 0, max = 72 }) {
  const DETENTS = [0, 6, 12, 24, 48, 72]

  function fmt(h) {
    if (h === 0) return '0小时'
    if (h >= 24) return `${Math.floor(h / 24)}天${h % 24 > 0 ? h % 24 + '小时' : ''}`
    return `${h}小时`
  }

  function snap(val) {
    let nearest = DETENTS[0], minDist = Infinity
    for (const d of DETENTS) { const dist = Math.abs(val - d); if (dist < minDist) { minDist = dist; nearest = d } }
    return minDist <= 2 ? nearest : val
  }

  // 最近卡点（用于高亮）
  const bestIdx = DETENTS.reduce((b, d, i) => Math.abs(d - value) < Math.abs(DETENTS[b] - value) ? i : b, 0)
  const nearDetent = Math.abs(DETENTS[bestIdx] - value) <= 0.5

  return (
    <div className="space-y-2">
      <div className="relative">
        <input
          type="range" min={min} max={max} step="1"
          value={value}
          onChange={e => onChange(parseInt(e.target.value))}
          onMouseUp={e => { const s = snap(parseInt(e.target.value)); if (s !== parseInt(e.target.value)) onChange(s) }}
          onTouchEnd={e => { const s = snap(parseInt(e.target.value)); if (s !== parseInt(e.target.value)) onChange(s) }}
          className="w-full accent-brand-green-hover cursor-pointer relative z-10"
        />
        {/* 刻度容器：左偏移 thumb 半宽(11px)使其对齐滑杆起点 */}
        <div
          className="relative pointer-events-none"
          style={{ marginTop: '-6px', marginLeft: '11px', width: 'calc(100% - 22px)' }}
        >
          {DETENTS.map(v => {
            const active = nearDetent && DETENTS[bestIdx] === v
            const pct = (v / max) * 100
            return (
              <div key={v} className="absolute" style={{ left: `${pct}%`, transform: 'translateX(-50%)' }}>
                <div className={`w-0.5 h-2 rounded-full mx-auto transition-colors ${active ? 'bg-brand-green' : 'bg-border-main'}`} />
                <span
                  className={`block text-[11px] mt-1.5 px-1.5 py-0.5 rounded transition-all cursor-pointer pointer-events-auto select-none
                    ${active ? 'text-brand-green font-semibold' : 'text-text-muted hover:text-brand-green hover:bg-brand-green/5'}`}
                  onClick={() => onChange(v)}
                >
                  {v === 0 ? '0' : v}
                </span>
              </div>
            )
          })}
        </div>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-text-main">{fmt(value)}</span>
        <span className="text-xs text-text-muted/60">
          {value === 0 ? '不回溯消息' : `往前推 ${value} 小时`}
        </span>
      </div>
    </div>
  )
}

const notificationTypes = {
  keyword_alert: '关键词提醒',
  group_digest: '定时摘要',
  oa_digest: '公众号摘要',
  oa_article_alert: '公众号即时',
}

const notificationStatuses = {
  pending: '待投递',
  delivered: '已投递',
  ignored: '已忽略',
  failed: '失败',
}

const statusColors = {
  pending: 'var(--status-warn)',
  delivered: 'var(--brand-green)',
  ignored: 'var(--text-muted)',
  failed: 'var(--status-error)',
}

// ── Main component ──────────────────────────────────────────────────

export default function AssistantPanel() {
  const [config, setConfig] = useState(null)
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [dirty, setDirty] = useState(false)
  const saveTimerRef = useRef(null)
  const alertEditorRef = useRef(null)
  const digestEditorRef = useRef(null)
  const [saveFlash, setSaveFlash] = useState(null)  // 'saving' | 'saved' | 'error' | null
  const [digestRunning, setDigestRunning] = useState('')  // group id of currently running digest
  const [notifications, setNotifications] = useState([])
  const [notificationLoading, setNotificationLoading] = useState(false)
  const [notificationError, setNotificationError] = useState('')
  const [filters, setFilters] = useState({ chat_id: '', type: '', status: '' })
  // Track which alert/digest items are expanded
  const [expandedAlerts, setExpandedAlerts] = useState({})
  const [expandedDigests, setExpandedDigests] = useState({})
  const [expandedProfiles, setExpandedProfiles] = useState({})
  const [notificationExpanded, setNotificationExpanded] = useState(false)
  // Inline editors
  const [showAlertEditor, setShowAlertEditor] = useState(false)
  const [showDigestEditor, setShowDigestEditor] = useState(false)
  const [alertDraft, setAlertDraft] = useState({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: 'ilink' })
  const [digestDraft, setDigestDraft] = useState({
    id: '', name: '', chats: [], schedule: [], cron_expr: '', lookback_hours: 6, lookback_mode: 'manual', enabled: true,
    unread_only: false, push_target: 'ilink', memory_enabled: true, memory: '', memory_rev: 0,
    profile: { style: '', custom_prompt: '' },
  })
  const [editorError, setEditorError] = useState('')
  // Push result toast (auto-disappears after 3s)
  const [pushToast, setPushToast] = useState(null)  // { group_name, success, error }
  // Draft state for editing existing cards (separate from saved config)
  const [alertDrafts, setAlertDrafts] = useState({})  // { index: { ...values } }
  const [digestDrafts, setDigestDrafts] = useState({})  // { dgId: { ...values } }

  // WebSocket for digest push results
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'digest_push_result' || data.type === 'oa_digest_push_result') {
          setPushToast(data)
          // Session expired: show longer so user can read the fix
          const duration = data.session_expired ? 10000 : 3000
          setTimeout(() => setPushToast(null), duration)
        }
      } catch {}
    }
    let ws = window.__assistant_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__assistant_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  useEffect(() => {
    const loadStart = performance.now()
    async function load() {
      try {
        const [configRes, groupsRes] = await Promise.all([
          fetch(`${API_BASE}/api/assistant/config`),
          fetch(`${API_BASE}/api/nicknames/groups`),
        ])
        const configData = await configRes.json()
        const groupsData = await groupsRes.json()
        const loadMs = Math.round(performance.now() - loadStart)
        console.log(`[PERF] AssistantPanel load: ${loadMs}ms (config=${Math.round(configData?.response_time||0)} groups=${Math.round(groupsData?.response_time||0)})`)
        setConfig(normalizeConfig(configData.config || defaultConfig()))
        if (groupsData.ok) {
          setGroups(groupsData.groups || [])
          // === 性能优化：禁用 WCDB 实时成员数查询 ===
          // 原因：wcdb_api.dll 不是线程安全的，每次 DLL 调用需串行排队。
          // 对每个群调用 get_group_member_count() 会造成 N×100ms 延迟，
          // 群多时首次加载需数秒甚至卡顿。member_count 非关键信息，
          // 当前使用 messages.db 的统计（基于历史消息的粗略值）已足够。
          // 后续如需启用真实成员数，可考虑：
          // 1. 后台定时预计算并持久化到 data/member_counts.json
          // 2. 用户点击群详情时再异步加载
          // ---
          // try {
          //   const countsRes = await fetch(`${API_BASE}/api/groups/member-counts`)
          //   const countsData = await countsRes.json()
          //   if (countsData.ok && countsData.counts) {
          //     setGroups(prev => prev.map(g => ({
          //       ...g,
          //       member_count: countsData.counts[g.chat_id] ?? g.member_count,
          //     })))
          //   }
          // } catch {
          //   // Fallback: keep original counts from messages.db
          // }
        }
      } catch {
        setConfig(defaultConfig())
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  useEffect(() => {
    loadNotifications()
  }, [filters.chat_id, filters.type, filters.status])

  // Scroll to editor when adding new alert/digest
  // Delay 200ms to let AnimatePresence animation complete before scroll
  useEffect(() => {
    if (showAlertEditor && alertEditorRef.current) {
      setTimeout(() => {
        alertEditorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 200)
    }
  }, [showAlertEditor])
  useEffect(() => {
    if (showDigestEditor && digestEditorRef.current) {
      setTimeout(() => {
        digestEditorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 200)
    }
  }, [showDigestEditor])

  function defaultConfig() {
    return {
      version: 1,
      assistant_enabled: false,
      alert_groups: [],
      digest_groups: [],
      notification_queue: { enabled: true, retention_hours: 24 },
      outbox_retention_hours: 24,
      default_system_prompt: '',
      style_presets: {},
    }
  }

  // Cron storage rule is strict: one line per time. Do not auto-repair invalid cron here;
  // backend validation returns an error and frontend displays it to the user.

  function normalizeConfig(raw) {
    const queue = raw.notification_queue || {
      enabled: (raw.notify_channels || []).some(ch => ch.enabled !== false) || true,
      retention_hours: raw.outbox_retention_hours || 24,
    }
    return {
      ...defaultConfig(),
      ...raw,
      notification_queue: queue,
      alert_groups: (raw.alert_groups || []).map(item => ({ chat_id: '', ...item })),
      digest_groups: (raw.digest_groups || []).map((item, idx) => ({
        ...item,
        id: item.id || `local_${idx}`,
        name: item.name ?? item.group_name ?? '',
        chats: (item.chats || []).map(c => ({ enabled: true, ...c })),
        memory_rev: item.memory_rev ?? 0,
      })),
    }
  }

  function update(field, value) {
    setDirty(true)
    setConfig(prev => {
      const next = { ...prev, [field]: value }
      // Auto-save after state update
      scheduleAutoSave(next)
      return next
    })
  }

  function updateQueue(patch) {
    setDirty(true)
    setConfig(prev => {
      const next = {
        ...prev,
        notification_queue: { ...(prev.notification_queue || {}), ...patch },
        outbox_retention_hours: patch.retention_hours ?? prev.outbox_retention_hours,
      }
      scheduleAutoSave(next)
      return next
    })
  }

  // Auto-save with debounce: switches/toggles save immediately,
  // text/tag inputs debounce 800ms
  function scheduleAutoSave(configToSave, immediate = false) {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    if (immediate) {
      doSave(configToSave)
    } else {
      saveTimerRef.current = setTimeout(() => doSave(configToSave), 800)
    }
  }

  // 错误 toast 要同时清 saveFlash 和 saveError：只清前者红色提示会永久停在
  // 屏幕上，只清后者用户看到的永远是笼统的「保存失败」而不知道原因。
  function flashSaveError(msg, ms = 4000) {
    setSaveError(msg || '保存失败')
    setSaveFlash('error')
    setTimeout(() => { setSaveError(''); setSaveFlash(null) }, ms)
  }

  async function doSave(configToSave) {
    try {
      setSaveFlash('saving')
      const res = await fetch(`${API_BASE}/api/assistant/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configToSave || config),
      })
      const d = await res.json()
      if (d.ok) {
        setSaved(true)
        setSaveError('')
        setDirty(false)
        setSaveFlash('saved')
        setTimeout(() => { setSaved(false); setSaveFlash(null) }, 1500)
      } else {
        flashSaveError(d.error, 2500)
      }
    } catch (e) {
      flashSaveError(e.message, 2500)
    }
  }

  // For immediate saves on toggle switches
  function updateAndSaveNow(field, value) {
    setDirty(true)
    setConfig(prev => {
      const next = { ...prev, [field]: value }
      scheduleAutoSave(next, true)
      return next
    })
  }

  function updateQueueAndSave(patch) {
    setDirty(true)
    setConfig(prev => {
      const next = {
        ...prev,
        notification_queue: { ...(prev.notification_queue || {}), ...patch },
        outbox_retention_hours: patch.retention_hours ?? prev.outbox_retention_hours,
      }
      scheduleAutoSave(next, true)
      return next
    })
  }

  function findGroup(chatId) {
    return groups.find(g => g.chat_id === chatId)
  }

  // 一个 chat_id 只能归属一个摘要分组，picker 用它禁用已被别的分组占用的会话
  function buildOccupied(excludeId) {
    const map = {}
    for (const g of (config.digest_groups || [])) {
      if (g.id === excludeId) continue
      for (const c of (g.chats || [])) {
        map[c.chat_id] = g.name || g.id
      }
    }
    return map
  }

  function applyGroupToAlert(index, chatId) {
    const selected = findGroup(chatId)
    const next = [...(config.alert_groups || [])]
    next[index] = {
      ...next[index],
      chat_id: chatId,
      group_name: selected?.group_name || next[index].group_name || '',
    }
    update('alert_groups', next)
  }

  function applyGroupToDigest(index, chatId) {
    const selected = findGroup(chatId)
    const next = [...(config.digest_groups || [])]
    next[index] = {
      ...next[index],
      chat_id: chatId,
      group_name: selected?.group_name || next[index].group_name || '',
    }
    update('digest_groups', next)
  }

  async function save() {
    await doSave(config)
  }

  // For inline editors that build config first then save

  async function loadNotifications() {
    setNotificationLoading(true)
    setNotificationError('')
    try {
      const params = new URLSearchParams()
      if (filters.chat_id) params.set('chat_id', filters.chat_id)
      if (filters.type) params.set('type', filters.type)
      if (filters.status) params.set('status', filters.status)
      params.set('limit', '50')
      const res = await fetch(`${API_BASE}/api/assistant/notifications?${params.toString()}`)
      const data = await res.json()
      if (data.ok) setNotifications(data.notifications || [])
      else setNotificationError(data.error || '通知记录加载失败')
    } catch {
      setNotificationError('通知记录加载失败')
    } finally {
      setNotificationLoading(false)
    }
  }

  async function createTestNotification() {
    await fetch(`${API_BASE}/api/assistant/notifications/test`, { method: 'POST' })
    loadNotifications()
  }

  async function updateNotificationStatus(id, action) {
    await fetch(`${API_BASE}/api/assistant/notifications/${id}/${action}`, { method: 'POST' })
    loadNotifications()
  }

  async function handleRunDigest(groupId, groupName) {
    if (!groupId) return
    setDigestRunning(groupId)
    try {
      const res = await fetch(`${API_BASE}/api/assistant/digest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_id: groupId }),
      })
      const data = await res.json()
      if (data.ok) {
        setPushToast({ group_name: groupName, success: true, is_task: true })
        setTimeout(() => setPushToast(null), 4000)
      } else {
        setPushToast({ group_name: groupName, success: false, error: data.error || '触发失败' })
        setTimeout(() => setPushToast(null), 4000)
        setDigestRunning('')
      }
      // Clear running state after timeout
      setTimeout(() => setDigestRunning(''), 60000)
    } catch {
      setDigestRunning('')
    }
  }

  if (loading) {
    return (
      <motion.div {...pageTransition} className="p-8 flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <Spinner size={24} weight="bold" className="animate-spin text-brand-green mx-auto mb-3" />
          <p className="text-sm text-text-muted font-mono">加载微信助手配置...</p>
        </div>
      </motion.div>
    )
  }

  if (!config) return null

  const assistantOn = config.assistant_enabled
  const alertCount = (config.alert_groups || []).filter(g => g.enabled).length
  const digestCount = (config.digest_groups || []).filter(g => g.enabled).length

  return (
    <motion.div {...pageTransition} className="p-8 space-y-10 max-w-5xl">
      {/* Push result toast */}
      {pushToast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all max-w-sm ${
          pushToast.success
            ? 'bg-brand-green/90 text-white'
            : 'bg-status-error/90 text-white'
        }`}>
          {pushToast.is_task
            ? pushToast.success
              ? `✓ 任务已提交: ${pushToast.group_name}，右上角任务中心查看进度`
              : `⚠ 提交失败: ${pushToast.group_name} — ${pushToast.error || '未知错误'}`
            : pushToast.success
              ? `✓ 推送成功: ${pushToast.group_name}`
              : pushToast.session_expired
                ? <div>
                    <div>⚠ 推送失败: ${pushToast.group_name}</div>
                    <div className="text-xs mt-1 opacity-90">微信链接可能已断开，请在微信中主动回复一条消息即可恢复，或扫码重新绑定</div>
                  </div>
                : `⚠ 推送失败: ${pushToast.group_name}`}
        </div>
      )}

      {/* ── Auto-save flash indicator ───────────────────────────── */}
      <AnimatePresence>
        {saveFlash && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            transition={{ duration: 0.2 }}
            className="fixed bottom-6 right-6 z-50 max-w-[26rem]"
          >
            <div className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium shadow-lg transition-all ${
              saveFlash === 'saving' ? 'bg-bg-raised text-text-muted' :
              saveFlash === 'saved' ? 'bg-brand-green/90 text-white' :
              'bg-status-error/90 text-white'
            }`}>
              {saveFlash === 'saving' && <Spinner size={14} className="animate-spin shrink-0" />}
              {saveFlash === 'saved' && <CheckCircle size={14} weight="fill" className="shrink-0" />}
              {saveFlash === 'error' && <Warning size={14} weight="fill" className="shrink-0" />}
              {saveFlash === 'saving' ? '保存中...' : saveFlash === 'saved' ? '已保存' : (saveError || '保存失败')}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Status bar (with main toggle) ───────────────────── */}
      <div className={`flex items-center gap-3 px-5 py-3 rounded-xl border text-sm transition-all duration-300 ${
        assistantOn
          ? 'bg-brand-green/5 border-brand-green/20'
          : 'bg-bg-raised/60 border-border-main'
      }`}>
        <div className={`w-2.5 h-2.5 rounded-full ${assistantOn ? 'bg-brand-green animate-pulse' : 'bg-text-muted'}`} />
        <span className={`font-semibold ${assistantOn ? 'text-brand-green-hover dark:text-brand-green' : 'text-text-muted'}`}>
          {assistantOn ? '微信助手已开启' : '微信助手已关闭'}
        </span>
        <span className="text-text-muted">·</span>
        <span className="text-xs text-text-muted">
          {alertCount > 0 && `${alertCount} 个提醒群 · `}
          {digestCount > 0 && `${digestCount} 个摘要分组 · `}
          {config.notification_queue?.enabled !== false ? '通知队列开启' : '通知队列关闭'}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <span className={`text-xs font-semibold uppercase tracking-wider ${assistantOn ? 'text-brand-green' : 'text-text-muted'}`}>
            {assistantOn ? 'ON' : 'OFF'}
          </span>
          <Toggle enabled={config.assistant_enabled} onChange={v => updateAndSaveNow('assistant_enabled', v)} />
        </div>
      </div>

      {/* ── Keyword Alerts ─────────────────────────────────────── */}
      <section className="relative">
        {!assistantOn && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-bg-main/60 backdrop-blur-[2px] rounded-2xl">
            <p className="text-sm text-text-muted font-medium mb-3">请先开启微信助手</p>
            <button
              onClick={() => updateAndSaveNow('assistant_enabled', true)}
              className="px-5 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
            >开启微信助手</button>
          </div>
        )}
        <SectionHeader
          title="关键词即时提醒"
          accent="#f59e0b"
          icon={Lightning}
          subtitle="检测到关键词时即时提醒"
        />
        <div className={`bg-bg-card rounded-2xl border border-border-main shadow-sm overflow-hidden transition-opacity duration-300 ${!assistantOn ? 'opacity-40' : ''}`}>
          <div className="p-6 space-y-3">
            {/* 已有群列表 */}
            <AnimatePresence>
              {(config.alert_groups || []).map((ag, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <AlertGroupCard
                    ag={ag}
                    index={i}
                    groups={groups}
                    expanded={!!expandedAlerts[i]}
                    draft={alertDrafts[i] || null}
                    onToggleExpand={() => {
                      const nextExpanded = !expandedAlerts[i]
                      setExpandedAlerts(prev => ({ ...prev, [i]: nextExpanded }))
                      if (nextExpanded) {
                        // Initialize draft from current config
                        setAlertDrafts(prev => ({ ...prev, [i]: { ...ag } }))
                      } else {
                        // Clear draft on collapse
                        setAlertDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      }
                    }}
                    onToggleEnabled={v => {
                      // Toggle saves immediately — directly patch config, skip draft
                      const next = [...config.alert_groups]
                      next[i] = { ...next[i], enabled: v }
                      setConfig(prev => ({ ...prev, alert_groups: next }))
                      scheduleAutoSave({ ...config, alert_groups: next }, true)
                    }}
                    onDelete={() => {
                      const next = config.alert_groups.filter((_, idx) => idx !== i)
                      updateAndSaveNow('alert_groups', next)
                    }}
                    onSelectGroup={chatId => {
                      const selected = findGroup(chatId)
                      setAlertDrafts(prev => ({ ...prev, [i]: { ...prev[i], chat_id: chatId, group_name: selected?.group_name || prev[i]?.group_name || '' } }))
                    }}
                    onKeywordsChange={keywords => {
                      setAlertDrafts(prev => ({ ...prev, [i]: { ...prev[i], keywords } }))
                    }}
                    onPushTargetChange={v => {
                      // Toggle updates draft only — save button persists
                      setAlertDrafts(prev => ({ ...prev, [i]: { ...(prev[i] || ag), push_target: v } }))
                    }}
                    onSave={() => {
                      const draft = alertDrafts[i]
                      if (!draft) return
                      const next = [...config.alert_groups]
                      // If chat_id changed, check for conflict with another group → merge
                      if (draft.chat_id && draft.chat_id !== config.alert_groups[i].chat_id) {
                        const conflictIdx = next.findIndex((g, idx) => idx !== i && g.chat_id === draft.chat_id)
                        if (conflictIdx >= 0) {
                          const merged = [...new Set([...next[conflictIdx].keywords, ...(draft.keywords || [])])]
                          next[conflictIdx] = { ...next[conflictIdx], keywords: merged }
                          next.splice(i, 1)
                          setConfig(prev => ({ ...prev, alert_groups: next }))
                          scheduleAutoSave({ ...config, alert_groups: next }, true)
                          setAlertDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                          setExpandedAlerts(prev => ({ ...prev, [i]: false }))
                          return
                        }
                      }
                      // Strip enabled from draft — toggle is handled independently
                      // by onToggleEnabled which saves immediately.
                      // Merging draft's stale enabled would undo the user's toggle.
                      const { enabled: _enabled, ...safeDraft } = draft || {}
                      next[i] = { ...next[i], ...safeDraft }
                      setConfig(prev => ({ ...prev, alert_groups: next }))
                      scheduleAutoSave({ ...config, alert_groups: next }, true)
                      setAlertDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      setExpandedAlerts(prev => ({ ...prev, [i]: false }))
                    }}
                    onCancel={() => {
                      setAlertDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      setExpandedAlerts(prev => ({ ...prev, [i]: false }))
                    }}
                  />
                </motion.div>
              ))}
            </AnimatePresence>

            {/* 空状态 */}
            {!config.alert_groups?.length && !showAlertEditor && (
              <div className="py-10 text-center">
                <Lightning size={32} className="text-text-muted/30 mx-auto mb-3" />
                <p className="text-sm text-text-muted">添加联系人以配置关键词提醒</p>
                <button
                  onClick={() => { setShowAlertEditor(true); setAlertDraft({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: 'ilink' }); setEditorError('') }}
                  className="mt-4 text-sm text-brand-green-hover hover:underline cursor-pointer font-medium"
                >+ 添加提醒群</button>
              </div>
            )}

            {/* Inline 编辑器 */}
            <AnimatePresence>
              {showAlertEditor && (
                <motion.div
                  ref={alertEditorRef}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <AlertGroupEditor
                    draft={alertDraft}
                    groups={groups}
                    error={editorError}
                    onDraftChange={setAlertDraft}
                    onSave={() => {
                      if (!alertDraft.chat_id) { setEditorError('请先选择联系人'); return }
                      const selected = findGroup(alertDraft.chat_id)
                      const groups = config.alert_groups || []

                      // Same chat_id → merge keywords (dedup), don't create new row
                      const existing = groups.find(g => g.chat_id === alertDraft.chat_id)
                      if (existing) {
                        const merged = [...new Set([...existing.keywords, ...alertDraft.keywords])]
                        const next = groups.map(g =>
                          g.chat_id === alertDraft.chat_id ? { ...g, keywords: merged } : g
                        )
                        updateAndSaveNow('alert_groups', next)
                        setShowAlertEditor(false)
                        setEditorError('')
                        return
                      }

                      const next = [...groups, {
                        ...alertDraft,
                        group_name: selected?.group_name || alertDraft.group_name || '',
                      }]
                      updateAndSaveNow('alert_groups', next)
                      setShowAlertEditor(false)
                      setEditorError('')
                    }}
                    onCancel={() => { setShowAlertEditor(false); setEditorError('') }}
                  />
                </motion.div>
              )}
            </AnimatePresence>

            {/* 有群时的添加按钮 */}
            {(config.alert_groups?.length > 0 || showAlertEditor) && !showAlertEditor && (
              <button
                onClick={() => { setShowAlertEditor(true); setAlertDraft({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: 'ilink' }); setEditorError('') }}
                className="w-full py-3.5 text-sm text-text-muted hover:text-brand-green border border-dashed border-border-main hover:border-brand-green/40 rounded-xl transition-all duration-200 cursor-pointer bg-bg-raised/30 hover:bg-brand-green/5"
              >
                + 添加提醒群
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── Timed Digests ──────────────────────────────────────── */}
      <section className="relative">
        {!assistantOn && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-bg-main/60 backdrop-blur-[2px] rounded-2xl">
            <p className="text-sm text-text-muted font-medium mb-3">请先开启微信助手</p>
            <button
              onClick={() => updateAndSaveNow('assistant_enabled', true)}
              className="px-5 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
            >开启微信助手</button>
          </div>
        )}
        <SectionHeader
          title="定时分组摘要"
          accent="var(--status-warn)"
          icon={Clock}
          subtitle="一个分组打包多个会话，到点一次性生成按会话分段的摘要"
        />
        <div className={`bg-bg-card rounded-2xl border border-border-main shadow-sm overflow-hidden transition-opacity duration-300 ${!assistantOn ? 'opacity-40' : ''}`}>
          <div className="p-6 space-y-3">
            {/* 已有群列表 */}
            <AnimatePresence>
              {(config.digest_groups || []).map((dg, i) => {
                const cardKey = dg.id || `fallback_${i}`
                return (
                <motion.div
                  key={cardKey}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <DigestGroupCard
                    dg={dg}
                    index={i}
                    groups={groups}
                    expanded={!!expandedDigests[cardKey]}
                    profileExpanded={!!expandedProfiles[cardKey]}
                    draft={digestDrafts[cardKey] || null}
                    occupied={buildOccupied(dg.id)}
                    defaultSystemPrompt={config.default_system_prompt}
                    stylePresets={config.style_presets || {}}
                    onToggleExpand={() => {
                      const nextExpanded = !expandedDigests[cardKey]
                      setExpandedDigests(prev => ({ ...prev, [cardKey]: nextExpanded }))
                      if (nextExpanded) {
                        // Initialize draft from current config
                        setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...dg } }))
                      } else {
                        // Clear draft on collapse
                        setDigestDrafts(prev => { const n = { ...prev }; delete n[cardKey]; return n })
                      }
                    }}
                    onToggleProfile={() => setExpandedProfiles(prev => ({ ...prev, [cardKey]: !prev[cardKey] }))}
                    onToggleEnabled={v => {
                      // Toggle saves immediately — directly patch config, skip draft
                      const next = [...config.digest_groups]
                      next[i] = { ...next[i], enabled: v }
                      setConfig(prev => ({ ...prev, digest_groups: next }))
                      scheduleAutoSave({ ...config, digest_groups: next }, true)
                    }}
                    onDelete={() => {
                      const next = config.digest_groups.filter((_, idx) => idx !== i)
                      // id 会被后端复用（dg_NNN 按数量分配），残留条目会让新分组
                      // 继承已删分组的草稿与展开态，所以三个 map 都要清
                      for (const setter of [setDigestDrafts, setExpandedDigests, setExpandedProfiles]) {
                        setter(prev => { const n = { ...prev }; delete n[cardKey]; return n })
                      }
                      updateAndSaveNow('digest_groups', next)
                    }}
                    onNameChange={name => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), name } }))
                    }}
                    onSelectChats={chats => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), chats } }))
                    }}
                    onScheduleChange={schedule => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...prev[cardKey], schedule } }))
                    }}
                    onCronExprChange={cron_expr => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...prev[cardKey], cron_expr } }))
                    }}
                    onLookbackChange={lookback_hours => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...prev[cardKey], lookback_hours } }))
                    }}
                    onLookbackModeChange={mode => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...prev[cardKey], lookback_mode: mode } }))
                    }}
                    onProfileChange={patch => {
                      setDigestDrafts(prev => {
                        const profile = prev[cardKey]?.profile || {}
                        return { ...prev, [cardKey]: { ...prev[cardKey], profile: { ...profile, ...patch } } }
                      })
                    }}
                    onMemoryChange={memory => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), memory } }))
                    }}
                    onMemoryEnabledChange={memory_enabled => {
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), memory_enabled } }))
                    }}
                    onUnreadOnlyChange={v => {
                      // Toggle updates draft only — save button persists
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), unread_only: v } }))
                    }}
                    onPushTargetChange={v => {
                      // Toggle updates draft only — save button persists
                      setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || dg), push_target: v } }))
                    }}
                    onSave={async () => {
                      const draft = digestDrafts[cardKey]
                      if (!draft) return
                      const name = (draft.name || '').trim()
                      if (!name) {
                        flashSaveError('请填写分组名称')
                        return
                      }
                      // Validate cron before save
                      const cronErr = validateCronExpr(draft.cron_expr || '')
                      if (cronErr) {
                        flashSaveError(cronErr)
                        return
                      }
                      // picker 里已 disable 被占用的会话，这里是双保险
                      const occupiedMap = buildOccupied(dg.id)
                      const conflict = (draft.chats || []).find(c => occupiedMap[c.chat_id])
                      if (conflict) {
                        flashSaveError(`"${conflict.name || conflict.chat_id}" 已在分组「${occupiedMap[conflict.chat_id]}」中`)
                        return
                      }
                      // 记忆走独立端点：批量 PUT 的 merge 一律以磁盘为准，带上 memory 会被忽略
                      let newMemoryRev = null
                      if ((draft.memory || '') !== (dg.memory || '')) {
                        try {
                          const res = await fetch(`${API_BASE}/api/assistant/digest-group-memory`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ id: dg.id, memory: draft.memory || '', memory_rev: dg.memory_rev ?? 0 }),
                          })
                          const d = await res.json()
                          if (!d.ok) {
                            if (d.memory !== undefined) {
                              // 版本冲突：期间后台摘要写过记忆，回填最新版本并保持展开，让用户重新确认
                              setConfig(prev => ({
                                ...prev,
                                digest_groups: (prev.digest_groups || []).map(g => g.id === dg.id ? { ...g, memory: d.memory, memory_rev: d.memory_rev } : g),
                              }))
                              setDigestDrafts(prev => ({ ...prev, [cardKey]: { ...(prev[cardKey] || draft), memory: d.memory, memory_rev: d.memory_rev } }))
                            }
                            flashSaveError(d.error || '记忆保存失败', 5000)
                            return
                          }
                          newMemoryRev = d.memory_rev
                        } catch {
                          flashSaveError('记忆保存失败')
                          return
                        }
                      }
                      const next = [...config.digest_groups]
                      // enabled 由 onToggleEnabled 独立即时保存；memory/memory_rev 已单独走上面的端点
                      const { enabled: _enabled, memory: _memory, memory_rev: _memoryRev, ...safeDraft } = draft || {}
                      next[i] = { ...next[i], ...safeDraft, name }
                      if (newMemoryRev !== null) {
                        next[i].memory = draft.memory || ''
                        next[i].memory_rev = newMemoryRev
                      }
                      setConfig(prev => ({ ...prev, digest_groups: next }))
                      scheduleAutoSave({ ...config, digest_groups: next }, true)
                      setDigestDrafts(prev => { const n = { ...prev }; delete n[cardKey]; return n })
                      setExpandedDigests(prev => ({ ...prev, [cardKey]: false }))
                      setSaveFlash('saved')
                      setTimeout(() => setSaveFlash(null), 1500)
                    }}
                    onCancel={() => {
                      setDigestDrafts(prev => { const n = { ...prev }; delete n[cardKey]; return n })
                      setExpandedDigests(prev => ({ ...prev, [cardKey]: false }))
                    }}
                    digestRunning={digestRunning}
                    onRunDigest={handleRunDigest}
                  />
                </motion.div>
                )
              })}
            </AnimatePresence>

            {/* 空状态 */}
            {!config.digest_groups?.length && !showDigestEditor && (
              <div className="py-10 text-center">
                <Clock size={32} className="text-text-muted/30 mx-auto mb-3" />
                <p className="text-sm text-text-muted">新建一个摘要分组，把要一起摘要的会话挑进去</p>
                <button
                  onClick={() => { setShowDigestEditor(true); setDigestDraft({ id: '', name: '', chats: [], schedule: [], cron_expr: '', lookback_hours: 6, lookback_mode: 'manual', enabled: true, unread_only: false, push_target: 'ilink', memory_enabled: true, memory: '', memory_rev: 0, profile: { style: '', custom_prompt: '' } }); setEditorError('') }}
                  className="mt-4 text-sm text-brand-green-hover hover:underline cursor-pointer font-medium"
                >+ 添加摘要分组</button>
              </div>
            )}

            {/* Inline 编辑器 */}
            <AnimatePresence>
              {showDigestEditor && (
                <motion.div
                  ref={digestEditorRef}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <DigestGroupEditor
                    draft={digestDraft}
                    groups={groups}
                    error={editorError}
                    occupied={buildOccupied('')}
                    defaultSystemPrompt={config.default_system_prompt}
                    stylePresets={config.style_presets || {}}
                    onDraftChange={setDigestDraft}
                    onSave={() => {
                      const name = (digestDraft.name || '').trim()
                      if (!name) { setEditorError('请填写分组名称'); return }
                      if (!(digestDraft.chats || []).length) { setEditorError('请至少选择一个会话'); return }
                      const cron_expr = digestDraft.cron_expr || '0 9 * * *'
                      const cronErr = validateCronExpr(cron_expr)
                      if (cronErr) { setEditorError(cronErr); return }
                      // picker 里已 disable 被占用的会话，这里是双保险
                      const occupiedMap = buildOccupied('')
                      const conflict = (digestDraft.chats || []).find(c => occupiedMap[c.chat_id])
                      if (conflict) {
                        setEditorError(`"${conflict.name || conflict.chat_id}" 已在分组「${occupiedMap[conflict.chat_id]}」中`)
                        return
                      }
                      const schedule = digestDraft.schedule?.length ? digestDraft.schedule : ['09:00']
                      const next = [...(config.digest_groups || []), {
                        ...digestDraft,
                        name,
                        schedule,
                        cron_expr,
                      }]
                      updateAndSaveNow('digest_groups', next)
                      setShowDigestEditor(false)
                      setEditorError('')
                    }}
                    onCancel={() => { setShowDigestEditor(false); setEditorError('') }}
                  />
                </motion.div>
              )}
            </AnimatePresence>

            {/* 有群时的添加按钮 */}
            {(config.digest_groups?.length > 0 || showDigestEditor) && !showDigestEditor && (
              <button
                onClick={() => { setShowDigestEditor(true); setDigestDraft({ id: '', name: '', chats: [], schedule: [], cron_expr: '', lookback_hours: 6, lookback_mode: 'manual', enabled: true, unread_only: false, push_target: 'ilink', memory_enabled: true, memory: '', memory_rev: 0, profile: { style: '', custom_prompt: '' } }); setEditorError('') }}
                className="w-full py-3.5 text-sm text-text-muted hover:text-brand-green border border-dashed border-border-main hover:border-brand-green/40 rounded-xl transition-all duration-200 cursor-pointer bg-bg-raised/30 hover:bg-brand-green/5"
              >
                + 添加摘要分组
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── Notification Center ────────────────────────────────── */}
      <section>
        <SectionHeader
          title="通知中心"
          accent="var(--brand-green)"
          icon={Bell}
          subtitle="查看提醒和摘要的通知记录"
          action={
            <button
              onClick={() => setNotificationExpanded(!notificationExpanded)}
              className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
            >
              {notificationExpanded ? '收起' : '展开'}
              <CaretDown size={12} className={`transition-transform duration-200 ${notificationExpanded ? 'rotate-180' : ''}`} />
            </button>
          }
        />
        <AnimatePresence>
          {notificationExpanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="overflow-hidden"
            >
              <div className="space-y-5">
                {/* Queue status card */}
                <div className="bg-bg-card rounded-2xl border border-border-main shadow-sm overflow-hidden">
                  <div className="p-6 space-y-4">
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3">
                        <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${
                          config.notification_queue?.enabled !== false
                            ? 'bg-brand-green/10 text-brand-green'
                            : 'bg-bg-raised text-text-muted'
                        }`}>
                          <EnvelopeOpen size={18} />
                        </div>
                        <div>
                          <p className="text-sm text-text-main font-medium">通知投递队列</p>
                          <p className="text-xs text-text-muted mt-0.5">
                            {config.notification_queue?.enabled !== false ? '队列运行中' : '队列已暂停'}
                          </p>
                        </div>
                      </div>
                      <Toggle enabled={config.notification_queue?.enabled !== false} onChange={v => updateQueueAndSave({ enabled: v })} />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pl-12">
                      <div>
                        <label className="text-xs text-text-muted block mb-1.5">通知保留时间</label>
                        <div className="flex items-center gap-2">
                          <input type="number" min={1} max={168} value={config.notification_queue?.retention_hours || 24}
                            onChange={e => updateQueue({ retention_hours: parseInt(e.target.value) || 24 })}
                            className="w-20 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                          />
                          <span className="text-xs text-text-muted">小时</span>
                        </div>
                      </div>
                      <div>
                        <label className="text-xs text-text-muted block mb-1.5">API 拉取地址</label>
                        <code className="text-xs text-text-muted bg-bg-raised border border-border-main rounded-lg px-3 py-2 block truncate font-mono">
                          GET /api/assistant/notifications/pending
                        </code>
                      </div>
                    </div>
                    <div className="pl-12">
                      <button
                        onClick={createTestNotification}
                        className="text-sm text-brand-green-hover hover:underline cursor-pointer font-medium"
                      >+ 写入一条测试通知</button>
                    </div>
                  </div>
                </div>

                {/* Notification history */}
                <div className="bg-bg-card rounded-2xl border border-border-main shadow-sm overflow-hidden">
                  <div className="p-6 space-y-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg flex items-center justify-center bg-bg-raised text-text-muted">
                          <Archive size={18} />
                        </div>
                        <p className="text-sm text-text-main font-medium">通知记录</p>
                      </div>
                      <button onClick={loadNotifications} className="text-sm text-brand-green-hover hover:underline cursor-pointer font-medium">刷新</button>
                    </div>
                    {/* Filters */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      <SearchableGroupSelect
                        groups={[...groups, ...(config.digest_groups || []).map(dg => ({ chat_id: dg.id, group_name: dg.name, type: 'digest_group' }))]}
                        value={filters.chat_id}
                        onChange={chatId => setFilters(prev => ({ ...prev, chat_id: chatId }))}
                        placeholder="全部联系人"
                        allowClear
                      />
                      <select value={filters.type} onChange={e => setFilters(prev => ({ ...prev, type: e.target.value }))} className="bg-bg-raised border border-border-main rounded-lg px-3 py-2.5 text-sm text-text-main focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all">
                        <option value="">全部类型</option>
                        <option value="keyword_alert">关键词提醒</option>
                        <option value="group_digest">定时摘要</option>
                        <option value="oa_digest">公众号摘要</option>
                        <option value="oa_article_alert">公众号即时</option>
                      </select>
                      <select value={filters.status} onChange={e => setFilters(prev => ({ ...prev, status: e.target.value }))} className="bg-bg-raised border border-border-main rounded-lg px-3 py-2.5 text-sm text-text-main focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all">
                        <option value="">全部状态</option>
                        <option value="pending">待投递</option>
                        <option value="delivered">已投递</option>
                        <option value="ignored">已忽略</option>
                        <option value="failed">失败</option>
                      </select>
                    </div>
                    {notificationError && <p className="text-xs text-status-error">{notificationError}</p>}
                    {notificationLoading ? (
                      <div className="flex items-center gap-2 text-xs text-text-muted py-8 justify-center"><Spinner size={14} className="animate-spin" />加载中...</div>
                    ) : (
                      <div className="space-y-2 max-h-[480px] overflow-y-auto">
                        {notifications.map(n => (
                          <NotificationCard
                            key={n.id}
                            notification={n}
                            onAck={() => updateNotificationStatus(n.id, 'ack')}
                            onIgnore={() => updateNotificationStatus(n.id, 'ignore')}
                          />
                        ))}
                        {!notifications.length && (
                          <div className="py-10 text-center">
                            <Archive size={28} className="text-text-muted/40 mx-auto mb-2" />
                            <p className="text-xs text-text-muted">暂无通知记录</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </section>


    </motion.div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────

function AlertGroupCard({ ag, index, groups, expanded, draft, onToggleExpand, onToggleEnabled, onDelete, onSelectGroup, onKeywordsChange, onPushTargetChange, onSave, onCancel }) {
  const bodyRef = useRef(null)
  // Use draft if available (editing), otherwise use saved values
  const values = draft || ag

  return (
    <div className="border border-border-main rounded-xl overflow-hidden transition-all duration-200 hover:border-border-main/80">
      {/* Header */}
      <div
        className="flex items-center gap-3 p-4 cursor-pointer hover:bg-bg-raised/30 transition-colors"
        onClick={onToggleExpand}
      >
        <Toggle enabled={ag.enabled} onChange={onToggleEnabled} />
        <div className="flex-1 min-w-0">
          <span className="text-sm text-text-main font-medium truncate block">
            {values.group_name || `提醒群 #${index + 1}`}
          </span>
          <div className="flex gap-1 mt-1 flex-wrap items-center">
            {(values.keywords || []).map((kw, ki) => (
              <span key={ki} className="text-xs px-2 py-0.5 rounded bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-medium">{kw}</span>
            ))}
            <span className="text-xs px-1.5 py-0.5 rounded bg-status-info-soft text-status-info font-medium">推送</span>
          </div>
        </div>
        <DeleteButton onDelete={onDelete} />
        <div className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}>
          <CaretDown size={16} className="text-text-muted" />
        </div>
      </div>
      {/* Body */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
            onAnimationComplete={() => {
              if (bodyRef.current) {
                bodyRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
              }
            }}
          >
            <div ref={bodyRef} className="px-4 pb-4 space-y-3 border-t border-border-main/50 pt-4 mx-4">
              <div>
                <label className="text-xs text-text-muted block mb-1.5">选择联系人</label>
                <SearchableGroupSelect
                  groups={groups}
                  value={values.chat_id || ''}
                  onChange={onSelectGroup}
                  placeholder="搜索联系人..."
                />
                {!values.chat_id && values.group_name && (
                  <p className="text-xs text-status-warn mt-1">历史群名：{values.group_name}，请从下拉重新绑定</p>
                )}
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1.5">关键词</label>
                <TagInput
                  tags={values.keywords || []}
                  onChange={onKeywordsChange}
                  placeholder="输入关键词后按回车添加"
                />
              </div>
              <div className="rounded-lg bg-bg-raised border border-border-main px-3 py-2">
                <p className="text-xs text-text-muted">自动推送到已扫码绑定的平台（在「系统配置 → 消息推送」完成绑定后生效）</p>
              </div>
              {/* Save / Cancel buttons */}
              {draft && (
                <div className="flex items-center gap-2 pt-3 border-t border-border-main/30">
                  <button
                    onClick={onSave}
                    className="text-sm px-5 py-2 rounded-lg bg-brand-green-hover text-white font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                  >保存</button>
                  <button
                    onClick={onCancel}
                    className="text-sm px-5 py-2 rounded-lg bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
                  >取消</button>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ── ScheduleConfig — 摘要时间配置（频率+时间+星期+高阶cron）──

function ScheduleConfig({ schedule = [], cronExpr = '', onScheduleChange, onCronExprChange }) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [customTimeInput, setCustomTimeInput] = useState('')
  // Override parsed freqMode when user explicitly clicks a mode button.
  // Without this, clicking "自定义" with weekdays=[1-5] generates cron "1,2,3,4,5"
  // which parseCronExpr immediately re-interprets as "工作日", hiding the weekday selector.
  const [freqModeOverride, setFreqModeOverride] = useState(null)

  // 从 cron_expr 解析基础模式；无 cron 时从 schedule 推断
  const parsed = cronExpr
    ? parseCronExpr(cronExpr)
    : { freqMode: 'daily', times: schedule.length ? schedule : ['09:00'], weekdays: [1,2,3,4,5] }

  // Use explicit override if set; otherwise fall back to parsed result
  const freqMode = freqModeOverride ?? parsed.freqMode
  const times = parsed.times
  const weekdays = parsed.weekdays
  const cronError = validateCronExpr(cronExpr)

  function syncCron(newTimes, newFreq, newWeekdays) {
    const cron = buildCronExpr(newTimes, newFreq, newWeekdays)
    onScheduleChange(newTimes)
    onCronExprChange(cron)
    // After syncing cron, clear override so parseCronExpr takes over for display.
    // Exception: if newFreq is 'custom' and the weekdays happen to be 1-5,
    // keep the override so the weekday selector stays visible.
    const reparsed = parseCronExpr(cron)
    if (newFreq === 'custom' && (reparsed.freqMode === 'weekday' || reparsed.freqMode === 'daily')) {
      setFreqModeOverride('custom')
    } else {
      setFreqModeOverride(null)
    }
  }

  function handleFreqChange(mode) {
    setFreqModeOverride(mode)
    const wds = mode === 'weekday' ? [1,2,3,4,5] : mode === 'daily' ? [] : weekdays
    syncCron(times, mode, wds)
  }

  function handleTimeToggle(time) {
    const next = times.includes(time) ? times.filter(t => t !== time) : [...times, time].sort()
    if (!next.length) next.push('09:00')
    syncCron(next, freqMode, weekdays)
  }

  function addCustomTime() {
    if (!customTimeInput) return
    // <input type="time"> returns "HH:MM" format, validated by browser
    const [h, m] = customTimeInput.split(':').map(Number)
    if (isNaN(h) || isNaN(m) || h < 0 || h > 23 || m < 0 || m > 59) return
    const timeStr = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
    const next = [...new Set([...times, timeStr])].sort()
    syncCron(next, freqMode, weekdays)
    setCustomTimeInput('')
  }

  function removeCustomTime(time) {
    const next = times.filter(t => t !== time)
    if (!next.length) next.push('09:00')
    syncCron(next, freqMode, weekdays)
  }

  function handleWeekdayToggle(day) {
    const next = weekdays.includes(day) ? weekdays.filter(d => d !== day) : [...weekdays, day].sort((a,b)=>a-b)
    if (!next.length) return // 至少选一天
    syncCron(times, 'custom', next)
  }

  return (
    <div className="space-y-3">
      <label className="text-xs text-text-muted block">摘要时间</label>

      {/* 频率选择 */}
      <div className="flex gap-1.5">
        {[
          { key: 'daily', label: '每天' },
          { key: 'weekday', label: '工作日' },
          { key: 'custom', label: '自定义' },
        ].map(f => (
          <button
            key={f.key}
            onClick={() => handleFreqChange(f.key)}
            className={`text-sm px-3.5 py-2 rounded-lg font-medium transition-all duration-150 cursor-pointer ${
              freqMode === f.key
                ? 'bg-brand-green-hover text-white shadow-sm'
                : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40 hover:text-text-main'
            }`}
          >{f.label}</button>
        ))}
      </div>

      {/* 时间 chips（预设 + 用户自定义的都显示为可点选 chip） */}
      <div className="flex flex-wrap gap-1.5">
        {PRESET_TIMES.map(t => {
          const active = times.includes(t)
          return (
            <button
              key={t}
              onClick={() => handleTimeToggle(t)}
              className={`text-xs px-3 py-2 rounded-lg font-mono font-medium transition-all duration-150 cursor-pointer ${
                active
                  ? 'bg-brand-green-hover text-white shadow-sm'
                  : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40 hover:text-text-main'
              }`}
            >{t}</button>
          )
        })}
        {/* 用户添加的自定义时间也显示为 chip，可点击删除 */}
        {times.filter(t => !PRESET_TIMES.includes(t)).map(t => (
          <span
            key={t}
            className="inline-flex items-center gap-1 text-xs px-3 py-2 rounded-lg font-mono font-medium bg-brand-green-hover text-white shadow-sm"
          >
            {t}
            <button
              onClick={() => removeCustomTime(t)}
              className="text-bg-main/60 hover:text-bg-main transition-colors cursor-pointer"
            >
              <X size={10} weight="bold" />
            </button>
          </span>
        ))}
      </div>

      {/* 添加自定义时间 — 时间选择器 + 添加按钮 */}
      <div className="flex items-center gap-1.5">
        <input
          type="time"
          value={customTimeInput}
          onChange={e => setCustomTimeInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') addCustomTime() }}
          onBlur={() => { if (customTimeInput) addCustomTime() }}
          className="w-36 bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
        />
        <button
          onClick={addCustomTime}
          disabled={!customTimeInput}
          className="flex items-center justify-center w-9 h-9 rounded-lg bg-brand-green-hover text-white font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Plus size={14} weight="bold" />
        </button>
        <span className="text-xs text-text-muted ml-1">回车或失焦自动添加</span>
      </div>

      {/* 星期勾选 — 仅自定义模式 */}
      {freqMode === 'custom' && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-text-muted shrink-0">星期</span>
          {WEEKDAY_LABELS.map((label, i) => {
            const dayNum = i // 0=日, 1=一, ..., 6=六
            const active = weekdays.includes(dayNum)
            return (
              <button
                key={i}
                onClick={() => handleWeekdayToggle(dayNum)}
                className={`text-xs w-9 h-9 rounded-lg font-medium transition-all duration-150 cursor-pointer ${
                  active
                    ? 'bg-brand-green-hover text-white shadow-sm'
                    : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40'
                }`}
              >{label}</button>
            )
          })}
        </div>
      )}

      {/* 高阶 Cron 设置 */}
      <div>
        <button
          onClick={() => setAdvancedOpen(!advancedOpen)}
          className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
        >
          {advancedOpen ? <CaretDown size={10} /> : <CaretRight size={10} />}
          高阶 Cron 设置
        </button>
        <AnimatePresence>
          {advancedOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="overflow-hidden"
            >
              <div className="mt-2 space-y-2 pl-2">
                <textarea
                  value={cronExpr}
                  onChange={e => onCronExprChange(e.target.value)}
                  placeholder={`0 9 * * 1-5\n30 12 * * 1-5\n0 18 * * 1-5`}
                  rows={3}
                  className={`w-full bg-bg-raised border rounded-lg px-3.5 py-2 text-sm text-text-main font-mono placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all resize-none ${
                    cronError ? 'border-status-error' : 'border-border-main'
                  }`}
                />
                {cronError && (
                  <p className="text-xs text-status-error font-medium">{cronError}</p>
                )}
                <p className="text-xs text-text-muted">
                  多行格式，每行一个时间点：<code className="text-text-muted">分 时 日 月 周</code>。例：
                  <code className="text-text-muted">0 9 * * 1-5</code> = 工作日9点
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

function DigestGroupCard({ dg, index, groups, expanded, profileExpanded, draft, onToggleExpand, onToggleProfile, onToggleEnabled, onDelete, onNameChange, onSelectChats, onScheduleChange, onCronExprChange, onLookbackChange, onLookbackModeChange, onProfileChange, onUnreadOnlyChange, onPushTargetChange, onMemoryChange, onMemoryEnabledChange, onSave, onCancel, defaultSystemPrompt, stylePresets, digestRunning, onRunDigest, occupied }) {
  const bodyRef = useRef(null)
  // Use draft if available (editing), otherwise use saved values
  const values = draft || dg

  // 解析 cron/schedule 为 header 展示用
  const headerSchedule = dg.cron_expr
    ? cronToLabel(dg.cron_expr)
    : (dg.schedule || []).length > 0
      ? dg.schedule.join(' · ')
      : ''

  // 智能回溯计算
  const autoEstimate = estimateGroupLookback(values.schedule, values.cron_expr)
  const lookbackMode = values.lookback_mode ?? 'manual'

  return (
    <div className="border border-border-main rounded-xl overflow-hidden transition-all duration-200 hover:border-border-main/80">
      {/* Header */}
      <div
        className="flex items-center gap-3 p-4 cursor-pointer hover:bg-bg-raised/30 transition-colors"
        onClick={onToggleExpand}
      >
        <Toggle enabled={dg.enabled} onChange={onToggleEnabled} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm text-text-main font-medium truncate">
              {values.name || '未命名分组'}
            </span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-bg-raised text-text-muted font-medium shrink-0">
              {values.chats?.length || 0} 个会话
            </span>
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {headerSchedule ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-mono">{headerSchedule}</span>
            ) : (
              <span className="text-xs text-status-warn">未设置时间</span>
            )}
            {lookbackMode === 'auto' ? (
              <span className="text-xs text-text-muted">智能 · ~{autoEstimate}h</span>
            ) : values.lookback_hours && values.lookback_hours !== 6 ? (
              <span className="text-xs text-text-muted">{values.lookback_hours}h</span>
            ) : null}
            {values.unread_only && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-status-warn-soft text-status-warn font-medium">未读</span>
            )}
            <span className="text-xs px-1.5 py-0.5 rounded bg-status-info-soft text-status-info font-medium">推送</span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={e => { e.stopPropagation(); onRunDigest(values.id, values.name) }}
            disabled={!values.chats?.length || digestRunning === values.id}
            className={`flex items-center gap-1 text-xs font-medium transition-colors cursor-pointer px-2 py-1 rounded-lg
              ${digestRunning === values.id
                ? 'text-brand-green/50 cursor-wait'
                : 'text-brand-green hover:text-brand-green-hover hover:bg-brand-green/[0.06]'
              }`}
            title="手动生成摘要"
          >
            <Play size={13} weight="fill" />
            {digestRunning === values.id ? '生成中...' : '生成摘要'}
          </button>
          <DeleteButton onDelete={onDelete} />
        </div>        <div className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}>
          <CaretDown size={16} className="text-text-muted" />
        </div>
      </div>
      {/* Body */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
            onAnimationComplete={() => {
              if (bodyRef.current) {
                bodyRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
              }
            }}
          >
            <div ref={bodyRef} className="px-4 pb-4 space-y-4 border-t border-border-main/50 pt-4 mx-4">
              {/* Group name */}
              <div>
                <label className="text-xs text-text-muted block mb-1.5">分组名称 <span className="text-status-error">*</span></label>
                <Input
                  value={values.name || ''}
                  onChange={onNameChange}
                  placeholder="给这个摘要分组起个名字"
                />
              </div>
              {/* Group select */}
              <div>
                <label className="text-xs text-text-muted block mb-1.5">选择联系人</label>
                <MultiChatPicker
                  groups={groups}
                  value={values.chats || []}
                  onChange={onSelectChats}
                  occupied={occupied || {}}
                />
                {!(values.chats || []).length && (
                  <p className="text-xs text-status-warn mt-1">该分组没有可用会话，请重新绑定</p>
                )}
              </div>
              {/* Schedule config */}
              <ScheduleConfig
                schedule={values.schedule || []}
                cronExpr={values.cron_expr || ''}
                onScheduleChange={onScheduleChange}
                onCronExprChange={onCronExprChange}
              />
              {/* 时间范围：智能/手动 切换 */}
              <div>
                <label className="text-xs text-text-muted block mb-1.5">摘要时间范围</label>
                <p className="text-xs text-text-muted mb-2">从当前时间往前取多少小时的消息进行摘要</p>
                <div className="flex gap-2 mb-3">
                  <button
                    onClick={() => {
                      onLookbackModeChange('auto')
                      onLookbackChange(autoEstimate)
                    }}
                    className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer ${
                      lookbackMode === 'auto'
                        ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                        : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                    }`}
                  >
                    <p className="text-xs font-medium">智能回溯</p>
                    <p className="text-xs opacity-60 mt-0.5">约 {autoEstimate} 小时</p>
                  </button>
                  <button
                    onClick={() => onLookbackModeChange('manual')}
                    className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer ${
                      lookbackMode === 'manual'
                        ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                        : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
                    }`}
                  >
                    <p className="text-xs font-medium">手动指定</p>
                    <p className="text-xs opacity-60 mt-0.5">自定义小时数</p>
                  </button>
                </div>
                {lookbackMode === 'manual' && (
                  <LookbackSlider value={values.lookback_hours ?? 6} onChange={onLookbackChange} />
                )}
                {lookbackMode === 'auto' && (
                  <p className="text-xs text-text-muted/70">根据定时计划间隔 + 1h 缓冲自动计算</p>
                )}
                {values.unread_only && (
                  <p className="text-xs text-status-warn/80 mt-1">仅摘要该时间窗口内的未读消息</p>
                )}
              </div>
              {/* Unread only toggle — 加深标签 */}
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm text-text-main/80 font-medium">仅摘要未读</p>
                  <p className="text-xs text-text-muted mt-0.5">开启后只在时间窗口内摘要未读消息，无未读则跳过</p>
                </div>
                <Toggle
                  enabled={values.unread_only || false}
                  onChange={onUnreadOnlyChange}
                />
              </div>
              {/* Push — auto to bound platforms */}
              <div className="rounded-lg bg-bg-raised border border-border-main px-3 py-2">
                <p className="text-xs text-text-muted">开启后摘要结果自动推送到已绑定的平台（在「系统配置 → 消息推送」完成绑定后生效）</p>
              </div>
              {/* Group profile */}
              <div>
                <button
                  onClick={onToggleProfile}
                  className="flex items-center gap-2 text-sm text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  {profileExpanded ? <CaretDown size={12} /> : <CaretRight size={12} />}
                  群档案 Profile
                  {values.profile && (values.profile.custom_prompt || values.profile.style) ? (
                    <span className="text-xs text-brand-green">· 已填写</span>
                  ) : (
                    <span className="text-xs text-text-muted">· 可选</span>
                  )}
                </button>
                <AnimatePresence>
                  {profileExpanded && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="mt-3 space-y-2.5 pl-4">
                        {/* 群记忆开关 + 可编辑记忆 */}
                        <div>
                          <div className="flex items-center justify-between gap-3 mb-1">
                            <label className="text-xs text-text-muted">群记忆</label>
                            <Toggle
                              enabled={!!values.memory_enabled}
                              onChange={onMemoryEnabledChange}
                            />
                          </div>
                          <p className="text-xs text-text-muted mb-1.5">每次摘要后自动浓缩更新；关闭后不再更新。可直接编辑，删除文本保存即清空。</p>
                          <textarea
                            value={values.memory || ''}
                            onChange={e => onMemoryChange(e.target.value)}
                            placeholder="（暂无记忆，第一次摘要后自动生成）"
                            rows={4}
                            disabled={!values.memory_enabled}
                            className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm text-text-main placeholder:text-text-muted/65 resize-none focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all disabled:opacity-45 disabled:cursor-not-allowed"
                          />
                          <div className="text-right text-[11px] text-text-muted mt-1">{values.memory?.length || 0} / 2000</div>
                        </div>
                        {/* 摘要风格 — preset chips + 自定义 */}
                        <div>
                          <label className="text-xs text-text-muted block mb-1.5">摘要风格</label>
                          <div className="flex flex-wrap gap-1.5">
                            {[
                              { key: '', label: '默认' },
                              { key: '行动项优先', label: '行动项优先' },
                              { key: '完整复盘', label: '完整复盘' },
                              { key: '极简速览', label: '极简速览' },
                              { key: 'custom', label: '自定义' },
                            ].map(s => (
                              <button
                                key={s.key}
                                onClick={() => {
                                  if (s.key === 'custom') {
                                    onProfileChange({ style: 'custom' })
                                  } else {
                                    onProfileChange({ style: s.key, custom_prompt: '' })
                                  }
                                }}
                                className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-all duration-150 cursor-pointer ${
                                  (values.profile?.style || '') === s.key
                                    ? 'bg-brand-green-hover text-white shadow-sm'
                                    : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40 hover:text-text-main'
                                }`}
                              >{s.label}</button>
                            ))}
                          </div>
                          {/** 非自定义风格 → 显示对应提示词预览（只读）；默认风格也显示默认 prompt */}
                          {values.profile?.style !== 'custom' && (
                            <div className="mt-1.5 p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-16 overflow-y-auto whitespace-pre-wrap">
                              {stylePresets?.[values.profile?.style] || defaultSystemPrompt || '（暂无说明）'}
                            </div>
                          )}
                        </div>
                        {/* 自定义摘要指令 — 仅选中自定义时显示 */}
                        {(values.profile?.style || '') === 'custom' && (
                          <div className="space-y-2">
                            {/* 当前默认 Prompt 预览（只读参考） */}
                            <div>
                              <label className="text-xs text-text-muted mb-1 block">当前默认 Prompt（只读参考）</label>
                              <div className="p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-20 overflow-y-auto whitespace-pre-wrap">
                                {defaultSystemPrompt || '（暂无默认提示词）'}
                              </div>
                            </div>
                            {/* 自定义 Prompt 输入框（可编辑，保存后完全替代默认） */}
                            <div>
                              <label className="text-xs text-text-muted font-medium mb-1 block">自定义 Prompt（完全替代默认，仅影响此群）</label>
                              <textarea
                                value={values.profile?.custom_prompt || ''}
                                onChange={e => onProfileChange({ custom_prompt: e.target.value })}
                                placeholder={values.profile?.custom_prompt ? '' : (defaultSystemPrompt || '修改后将完全替代默认 System Prompt...')}
                                rows={3}
                                className="w-full bg-bg-main border border-border-main rounded-xl px-4 py-2.5 text-sm text-text-main
                                  placeholder:text-text-muted/65 resize-none
                                  focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15"
                              />
                              <p className="text-xs text-status-warn mt-1">⚠ 填写后完全替代默认摘要指令，仅影响此群，不影响其他群</p>
                            </div>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              {/* Save / Cancel buttons */}
              {draft && (
                <div className="flex items-center gap-2 pt-3 border-t border-border-main/30">
                  <button
                    onClick={onSave}
                    className="text-sm px-5 py-2 rounded-lg bg-brand-green-hover text-white font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                  >保存</button>
                  <button
                    onClick={onCancel}
                    className="text-sm px-5 py-2 rounded-lg bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
                  >取消</button>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function AlertGroupEditor({ draft, groups, error, onDraftChange, onSave, onCancel }) {
  return (
    <div className="border border-brand-green/30 rounded-xl p-4 space-y-3 bg-brand-green/[0.02]">
      <p className="text-sm text-brand-green font-semibold mb-1">新增提醒群</p>
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div>
        <label className="text-xs text-text-muted block mb-1.5">选择联系人 <span className="text-status-error">*</span></label>
        <SearchableGroupSelect
          groups={groups}
          value={draft.chat_id || ''}
          onChange={chatId => {
            const selected = groups.find(g => g.chat_id === chatId)
            onDraftChange({ ...draft, chat_id: chatId, group_name: selected?.group_name || '' })
          }}
          placeholder="搜索联系人..."
        />
      </div>
      <div>
        <label className="text-xs text-text-muted block mb-1.5">关键词</label>
        <TagInput
          tags={draft.keywords || []}
          onChange={keywords => onDraftChange({ ...draft, keywords })}
          placeholder="输入关键词后按回车添加"
        />
      </div>
      <div className="rounded-lg bg-bg-raised border border-border-main px-3 py-2">
        <p className="text-xs text-text-muted">保存后自动推送到已绑定的平台（在「系统配置 → 消息推送」完成绑定后生效）</p>
      </div>
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onSave}
          className="text-sm px-5 py-2 rounded-lg bg-brand-green-hover text-white font-semibold hover:bg-brand-green-hover transition-colors cursor-pointer"
        >保存</button>
        <button
          onClick={onCancel}
          className="text-sm px-5 py-2 rounded-lg bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
        >取消</button>
      </div>
    </div>
  )
}

function DigestGroupEditor({ draft, groups, error, onDraftChange, onSave, onCancel, defaultSystemPrompt, stylePresets, occupied }) {
  const [profileOpen, setProfileOpen] = useState(false)
  return (
    <div className="border border-brand-green/30 rounded-xl p-4 space-y-3 bg-brand-green/[0.02]">
      <p className="text-sm text-brand-green font-semibold mb-1">新增摘要分组</p>
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div>
        <label className="text-xs text-text-muted block mb-1.5">分组名称 <span className="text-status-error">*</span></label>
        <Input
          value={draft.name || ''}
          onChange={name => onDraftChange({ ...draft, name })}
          placeholder="给这个摘要分组起个名字"
        />
      </div>
      <div>
        <label className="text-xs text-text-muted block mb-1.5">选择联系人 <span className="text-status-error">*</span></label>
        <MultiChatPicker
          groups={groups}
          value={draft.chats || []}
          onChange={chats => onDraftChange({ ...draft, chats })}
          occupied={occupied || {}}
        />
      </div>
      {/* Schedule config */}
      <ScheduleConfig
        schedule={draft.schedule || []}
        cronExpr={draft.cron_expr || ''}
        onScheduleChange={schedule => onDraftChange({ ...draft, schedule })}
        onCronExprChange={cron_expr => onDraftChange({ ...draft, cron_expr })}
      />
      {/* 回溯时长 — 智能/手动 */}
      <div>
        <label className="text-xs text-text-muted block mb-1.5">摘要时间范围</label>
        <p className="text-xs text-text-muted mb-2">从当前时间往前取多少小时的消息进行摘要</p>
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => {
              const est = estimateGroupLookback(draft.schedule, draft.cron_expr)
              onDraftChange({ ...draft, lookback_mode: 'auto', lookback_hours: est })
            }}
            className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer ${
              draft.lookback_mode === 'auto'
                ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
            }`}
          >
            <p className="text-xs font-medium">智能回溯</p>
            <p className="text-xs opacity-60 mt-0.5">约 {estimateGroupLookback(draft.schedule, draft.cron_expr)} 小时</p>
          </button>
          <button
            onClick={() => onDraftChange({ ...draft, lookback_mode: 'manual' })}
            className={`flex-1 text-left px-3 py-2 rounded-lg border transition-all cursor-pointer ${
              draft.lookback_mode === 'manual'
                ? 'border-brand-green/40 bg-brand-green-light/15 text-brand-green'
                : 'border-border-main bg-bg-raised text-text-muted hover:border-text-muted/30 hover:text-text-main'
            }`}
          >
            <p className="text-xs font-medium">手动指定</p>
            <p className="text-xs opacity-60 mt-0.5">自定义小时数</p>
          </button>
        </div>
        {draft.lookback_mode === 'manual' && (
          <LookbackSlider
            value={draft.lookback_hours ?? 6}
            onChange={v => onDraftChange({ ...draft, lookback_hours: v })}
          />
        )}
        {draft.lookback_mode === 'auto' && (
          <p className="text-xs text-text-muted/70">根据定时计划间隔 + 1h 缓冲自动计算</p>
        )}
      </div>
      {/* 仅摘要未读 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-text-main/80 font-medium">仅摘要未读</p>
          <p className="text-xs text-text-muted mt-0.5">开启后只在时间窗口内摘要未读消息，无未读则跳过</p>
        </div>
        <Toggle enabled={draft.unread_only || false} onChange={v => onDraftChange({ ...draft, unread_only: v })} />
      </div>
      <div className="rounded-lg bg-bg-raised border border-border-main px-3 py-2">
        <p className="text-xs text-text-muted">自动推送到已绑定的平台（在「系统配置 → 消息推送」完成绑定后生效）</p>
      </div>
      {/* Profile */}
      <div>
        <button
          onClick={() => setProfileOpen(!profileOpen)}
          className="flex items-center gap-2 text-sm text-text-muted hover:text-text-main transition-colors cursor-pointer"
        >
          {profileOpen ? <CaretDown size={12} /> : <CaretRight size={12} />}
          群档案 Profile
          <span className="text-xs text-text-muted">· 可选</span>
        </button>
        <AnimatePresence>
          {profileOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="mt-3 space-y-2.5 pl-4">
                {/* 群记忆开关 + 可编辑记忆 */}
                <div>
                  <div className="flex items-center justify-between gap-3 mb-1">
                    <label className="text-xs text-text-muted">群记忆</label>
                    <Toggle
                      enabled={!!draft.memory_enabled}
                      onChange={v => onDraftChange({ ...draft, memory_enabled: v })}
                    />
                  </div>
                  <p className="text-xs text-text-muted mb-1.5">每次摘要后自动浓缩更新；关闭后不再更新。可直接编辑，删除文本保存即清空。</p>
                  <textarea
                    value={draft.memory || ''}
                    onChange={e => onDraftChange({ ...draft, memory: e.target.value })}
                    placeholder="（暂无记忆，第一次摘要后自动生成）"
                    rows={4}
                    disabled={!draft.memory_enabled}
                    className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm text-text-main placeholder:text-text-muted/65 resize-none focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all disabled:opacity-45 disabled:cursor-not-allowed"
                  />
                  <div className="text-right text-[11px] text-text-muted mt-1">{draft.memory?.length || 0} / 2000</div>
                </div>
                {/* 摘要风格 — preset chips + 自定义 */}
                <div>
                  <label className="text-xs text-text-muted block mb-1.5">摘要风格</label>
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      { key: '', label: '默认' },
                      { key: '行动项优先', label: '行动项优先' },
                      { key: '完整复盘', label: '完整复盘' },
                      { key: '极简速览', label: '极简速览' },
                      { key: 'custom', label: '自定义' },
                    ].map(s => (
                      <button
                        key={s.key}
                        onClick={() => onDraftChange({ ...draft, profile: { ...draft.profile, style: s.key } })}
                        className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-all duration-150 cursor-pointer ${
                          (draft.profile?.style || '') === s.key
                            ? 'bg-brand-green-hover text-white shadow-sm'
                            : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40 hover:text-text-main'
                        }`}
                      >{s.label}</button>
                    ))}
                  </div>
                  {/** 非自定义风格 → 显示对应提示词预览（只读）；默认风格也显示默认 prompt */}
                  {draft.profile?.style !== 'custom' && (
                    <div className="mt-1.5 p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-16 overflow-y-auto whitespace-pre-wrap">
                      {stylePresets?.[draft.profile?.style] || defaultSystemPrompt || '（暂无说明）'}
                    </div>
                  )}
                </div>
                {/* 自定义摘要指令 — 仅选中自定义时显示 */}
                {(draft.profile?.style || '') === 'custom' && (
                  <div>
                    <label className="text-xs text-text-muted font-medium mb-1.5">自定义摘要指令</label>
                    <textarea
                      value={draft.profile?.custom_prompt || ''}
                      onChange={e => onDraftChange({ ...draft, profile: { ...draft.profile, custom_prompt: e.target.value } })}
                      placeholder={draft.profile?.custom_prompt ? '' : (defaultSystemPrompt || '输入自定义摘要指令...')}
                      rows={3}
                      className="w-full bg-bg-main border border-border-main rounded-xl px-4 py-2.5 text-sm text-text-main
                        placeholder:text-text-muted/65 resize-none
                        focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15"
                    />
                    <p className="text-xs text-status-warn mt-1">⚠ 填写后完全替代默认摘要指令，请确保指令完整</p>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onSave}
          className="text-sm px-5 py-2 rounded-lg bg-brand-green-hover text-white font-semibold hover:bg-brand-green-hover transition-colors cursor-pointer"
        >保存</button>
        <button
          onClick={onCancel}
          className="text-sm px-5 py-2 rounded-lg bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
        >取消</button>
      </div>
    </div>
  )
}

function DeleteButton({ onDelete }) {
  const [confirming, setConfirming] = useState(false)

  if (confirming) {
    return (
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-xs text-status-error font-medium">确认?</span>
        <button
          onClick={e => { e.stopPropagation(); onDelete(); setConfirming(false) }}
          className="text-xs px-2.5 py-1 rounded bg-status-error text-bg-main font-medium cursor-pointer"
        >是</button>
        <button
          onClick={e => { e.stopPropagation(); setConfirming(false) }}
          className="text-xs px-2.5 py-1 rounded bg-bg-raised border border-border-main text-text-muted font-medium cursor-pointer"
        >否</button>
      </div>
    )
  }

  return (
    <button
      onClick={e => { e.stopPropagation(); setConfirming(true) }}
      className="text-sm text-text-muted hover:text-status-error shrink-0 px-2 py-1.5 transition-colors cursor-pointer"
    >
      <Trash size={16} />
    </button>
  )
}

function NotificationCard({ notification, onAck, onIgnore }) {
  const statusColor = statusColors[notification.status] || '#a0aec0'
  const [expanded, setExpanded] = useState(false)
  const rawContent = notification.content || ''
  const isLong = rawContent.length > 200
  const displayContent = expanded ? rawContent : (isLong ? rawContent.slice(0, 200) + '...' : rawContent)
  return (
    <div className="bg-bg-raised/40 border border-border-main rounded-xl p-4 transition-all hover:border-border-main/80">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap mb-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-medium">
              {notificationTypes[notification.type] || notification.type}
            </span>
            <span className="inline-flex items-center gap-1 text-xs" style={{ color: statusColor }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: statusColor }} />
              {notificationStatuses[notification.status] || notification.status}
            </span>
            <span className="text-xs text-text-muted">{notification.created_at}</span>
          </div>
          <p className="text-sm text-text-main font-medium truncate">{notification.title || '无标题'}</p>
          <p className="text-xs text-text-muted mt-0.5">{notification.group_name || notification.chat_id || '未知联系人'}</p>
          <pre className="whitespace-pre-wrap text-sm text-text-main/75 mt-3 font-sans leading-relaxed">{displayContent}</pre>
          {isLong && (
            <button onClick={() => setExpanded(!expanded)} className="mt-1 text-xs text-brand-green hover:underline cursor-pointer font-medium">
              {expanded ? '收起' : '展开全部'}
            </button>
          )}
        </div>
        {notification.status === 'pending' && (
          <div className="flex gap-1.5 shrink-0">
            <button onClick={onAck} className="text-xs px-3.5 py-1.5 rounded-full bg-brand-green/10 text-brand-green-hover hover:bg-brand-green/20 transition-colors cursor-pointer font-medium">标记投递</button>
            <button onClick={onIgnore} className="text-xs px-3.5 py-1.5 rounded-full bg-bg-raised text-text-muted hover:text-status-error hover:bg-status-error-soft transition-colors cursor-pointer">忽略</button>
          </div>
        )}
      </div>
    </div>
  )
}

function SearchableGroupSelect({ groups, value, onChange, placeholder, allowClear }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const selected = groups.find(g => g.chat_id === value)
  const filtered = query
    ? groups.filter(g => g.group_name.toLowerCase().includes(query.toLowerCase()) || g.chat_id.toLowerCase().includes(query.toLowerCase()))
    : groups

  // 按类型分组：摘要分组在前，群聊次之，个人好友在后
  const digestGroups = filtered.filter(g => g.type === 'digest_group')
  const chatrooms = filtered.filter(g => g.type !== 'digest_group' && (g.type === 'chatroom' || g.chat_id.endsWith('@chatroom')))
  const contacts = filtered.filter(g => g.type !== 'digest_group' && g.type !== 'chatroom' && !g.chat_id.endsWith('@chatroom'))

  const displayText = open ? query : (selected ? selected.group_name : '')

  return (
    <div ref={ref} className="relative">
      <div className="relative">
        <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={displayText}
          placeholder={placeholder || '搜索联系人...'}
          onFocus={() => { setOpen(true); setQuery('') }}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          className="w-full bg-bg-raised border border-border-main rounded-lg pl-9 pr-4 py-2 text-[14px] text-text-main placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
        />
      </div>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-bg-card border border-border-main rounded-lg shadow-lg max-h-52 overflow-y-auto">
          {allowClear && value && (
            <button
              type="button"
              className="w-full text-left px-4 py-2.5 text-sm text-text-muted hover:bg-bg-raised transition-colors border-b border-border-main/50"
              onMouseDown={e => e.preventDefault()}
              onClick={() => { onChange(''); setQuery(''); setOpen(false) }}
            >全部联系人</button>
          )}
          {filtered.length === 0 ? (
            <p className="px-4 py-3 text-xs text-text-muted text-center">无匹配联系人</p>
          ) : (
            <>
              {digestGroups.length > 0 && (
                <>
                  <div className="px-4 py-1.5 text-[11px] text-text-muted/60 font-semibold uppercase tracking-wider sticky top-0 bg-bg-card border-b border-border-main/30">📋 摘要分组</div>
                  {digestGroups.map(g => (
                    <button
                      key={g.chat_id}
                      type="button"
                      className={`w-full text-left px-4 py-2.5 text-sm hover:bg-bg-raised transition-colors flex items-center gap-2 ${
                        g.chat_id === value ? 'bg-brand-green/10 text-brand-green-hover' : 'text-text-main'
                      }`}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => { onChange(g.chat_id); setQuery(''); setOpen(false) }}
                    >
                      <span className="truncate">{g.group_name}</span>
                    </button>
                  ))}
                </>
              )}
              {chatrooms.length > 0 && (
                <>
                  <div className="px-4 py-1.5 text-[11px] text-text-muted/60 font-semibold uppercase tracking-wider sticky top-0 bg-bg-card border-b border-border-main/30">👥 群聊</div>
                  {chatrooms.map(g => (
                    <button
                      key={g.chat_id}
                      type="button"
                      className={`w-full text-left px-4 py-2.5 text-sm hover:bg-bg-raised transition-colors flex items-center gap-2 ${
                        g.chat_id === value ? 'bg-brand-green/10 text-brand-green-hover' : 'text-text-main'
                      }`}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => { onChange(g.chat_id); setQuery(''); setOpen(false) }}
                    >
                      <span className="truncate">{g.group_name}</span>
                    </button>
                  ))}
                </>
              )}
              {contacts.length > 0 && (
                <>
                  <div className="px-4 py-1.5 text-[11px] text-text-muted/60 font-semibold uppercase tracking-wider sticky top-0 bg-bg-card border-b border-border-main/30">👤 好友</div>
                  {contacts.map(g => (
                    <button
                      key={g.chat_id}
                      type="button"
                      className={`w-full text-left px-4 py-2.5 text-sm hover:bg-bg-raised transition-colors flex items-center gap-2 ${
                        g.chat_id === value ? 'bg-brand-green/10 text-brand-green-hover' : 'text-text-main'
                      }`}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => { onChange(g.chat_id); setQuery(''); setOpen(false) }}
                    >
                      <span className="truncate">{g.group_name}</span>
                    </button>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function MultiChatPicker({ groups, value = [], onChange, occupied = {} }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const selectedIds = new Set(value.map(c => c.chat_id))

  const filtered = query
    ? groups.filter(g => g.group_name.toLowerCase().includes(query.toLowerCase()) || g.chat_id.toLowerCase().includes(query.toLowerCase()))
    : groups

  const chatrooms = filtered.filter(g => g.type === 'chatroom' || g.chat_id.endsWith('@chatroom'))
  const contacts = filtered.filter(g => g.type !== 'chatroom' && !g.chat_id.endsWith('@chatroom'))

  function toggleChat(g) {
    if (occupied[g.chat_id]) return
    if (selectedIds.has(g.chat_id)) {
      onChange(value.filter(c => c.chat_id !== g.chat_id))
    } else {
      onChange([...value, { chat_id: g.chat_id, name: g.group_name, enabled: true }])
    }
  }

  function renderRow(g) {
    const checked = selectedIds.has(g.chat_id)
    const occupiedBy = occupied[g.chat_id]
    return (
      <label
        key={g.chat_id}
        className={`w-full text-left px-4 py-2.5 text-sm transition-colors flex items-center gap-2 ${
          occupiedBy ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:bg-bg-raised'
        } ${checked ? 'bg-brand-green/10' : ''}`}
        onMouseDown={e => e.preventDefault()}
      >
        <input
          type="checkbox"
          checked={checked}
          disabled={!!occupiedBy}
          onChange={() => toggleChat(g)}
          className="accent-brand-green shrink-0"
        />
        <span className={`truncate flex-1 ${checked ? 'text-brand-green-hover font-medium' : 'text-text-main'}`}>{g.group_name}</span>
        {occupiedBy && <span className="text-xs text-text-muted shrink-0">已在「{occupiedBy}」</span>}
      </label>
    )
  }

  return (
    <div>
      {value.length > 0 && (
        <div className="mb-2">
          <div className="flex flex-wrap gap-1.5">
            {value.map(c => (
              <span
                key={c.chat_id}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-brand-green/10 text-brand-green-hover dark:text-brand-green text-xs font-medium"
              >
                {c.name || c.chat_id}
                <button
                  type="button"
                  onClick={() => onChange(value.filter(v => v.chat_id !== c.chat_id))}
                  className="text-brand-green/50 hover:text-status-error transition-colors cursor-pointer"
                >
                  <X size={10} weight="bold" />
                </button>
              </span>
            ))}
          </div>
          <p className="text-xs text-text-muted mt-1.5">已选 {value.length} 个会话</p>
        </div>
      )}
      {/* ref 只包搜索框和下拉。包到外层的话，chips 那一条也算「框内」，
          点「选择联系人」标签下方就永远收不回下拉 */}
      <div ref={ref} className="relative">
        <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
        <input
          type="text"
          value={open ? query : ''}
          placeholder={value.length ? '继续搜索添加会话...' : '搜索联系人...'}
          onFocus={() => { setOpen(true); setQuery('') }}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          className="w-full bg-bg-raised border border-border-main rounded-lg pl-9 pr-4 py-2 text-[14px] text-text-main placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
        />
        {open && (
          <div className="absolute top-full left-0 z-50 mt-1 w-full bg-bg-card border border-border-main rounded-lg shadow-lg max-h-52 overflow-y-auto">
            {filtered.length === 0 ? (
              <p className="px-4 py-3 text-xs text-text-muted text-center">无匹配联系人</p>
            ) : (
              <>
                {chatrooms.length > 0 && (
                  <>
                    <div className="px-4 py-1.5 text-[11px] text-text-muted/60 font-semibold uppercase tracking-wider sticky top-0 bg-bg-card border-b border-border-main/30">👥 群聊</div>
                    {chatrooms.map(renderRow)}
                  </>
                )}
                {contacts.length > 0 && (
                  <>
                    <div className="px-4 py-1.5 text-[11px] text-text-muted/60 font-semibold uppercase tracking-wider sticky top-0 bg-bg-card border-b border-border-main/30">👤 好友</div>
                    {contacts.map(renderRow)}
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
