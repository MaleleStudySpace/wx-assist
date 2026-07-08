import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle, Warning, Spinner, MagnifyingGlass, Bell, Clock, ChatCircle, CaretDown, CaretRight, EnvelopeOpen, Archive, Lightning, Trash, X, Plus, Play } from '@phosphor-icons/react'
import { Toggle, SectionHeader, TagInput, API_BASE } from './SharedComponents'

const pageTransition = {
  initial: { opacity: 0, x: 12 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -12 },
}

const PRESET_TIMES = ['09:00', '12:00', '14:00', '18:00', '21:00', '23:00']

const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六']

// ── Cron helpers ─────────────────────────────────────────────────────

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

function parseCronExpr(cronExpr) {
  if (!cronExpr) return { freqMode: 'daily', times: ['09:00'], weekdays: [1,2,3,4,5] }

  // Support multi-line cron: parse each line and merge results
  const lines = cronExpr.trim().split(/\n/).map(l => l.trim()).filter(Boolean)
  let allTimes = []
  let allWeekdays = []
  // Start as null; set from first line, downgrade if lines conflict
  let detectedFreq = null

  for (const line of lines) {
    const fields = line.split(/\s+/)
    if (fields.length !== 5) continue

    const [min, hour, , , dow] = fields
    const hours = hour === '*' ? [9] : hour.split(',').map(Number).filter(n => !isNaN(n))
    const mins = min === '*' ? [0] : min.split(',').map(Number).filter(n => !isNaN(n))

    // For single-line cron with comma-separated hours/minutes,
    // generate all combinations (this is how cron works)
    for (const h of hours) {
      for (const m of mins) {
        allTimes.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
      }
    }

    // Detect frequency from dow field
    let lineFreq
    if (dow === '*') {
      lineFreq = 'daily'
    } else if (dow === '1-5' || _isWeekdayList(dow)) {
      lineFreq = 'weekday'
      allWeekdays = [1, 2, 3, 4, 5]
    } else {
      lineFreq = 'custom'
      allWeekdays = dow.split(',').map(Number).filter(n => !isNaN(n))
    }

    // Merge: if all lines agree, keep that freq; any conflict → custom
    if (detectedFreq === null) {
      detectedFreq = lineFreq
    } else if (detectedFreq !== lineFreq) {
      detectedFreq = 'custom'
    }
  }

  // Default if nothing parsed
  if (detectedFreq === null) detectedFreq = 'daily'

  // Deduplicate and sort times
  allTimes = [...new Set(allTimes)].sort()

  if (!allTimes.length) allTimes = ['09:00']
  if (!allWeekdays.length && detectedFreq === 'custom') allWeekdays = [1, 2, 3, 4, 5]

  return { freqMode: detectedFreq, times: allTimes, weekdays: allWeekdays }
}

/** Check if a dow field represents weekdays (1-5), regardless of format. */
function _isWeekdayList(dow) {
  if (!dow || dow === '*') return false
  const nums = dow.split(',').map(Number).filter(n => !isNaN(n))
  return nums.length === 5 && nums.every(n => n >= 1 && n <= 5) && new Set(nums).size === 5
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

function cronToLabel(cronExpr) {
  if (!cronExpr) return ''
  const p = parseCronExpr(cronExpr)
  const timeLabel = p.times.join(' · ') || '9:00'
  if (p.freqMode === 'daily') return `每天 ${timeLabel}`
  if (p.freqMode === 'weekday') return `工作日 ${timeLabel}`
  if (p.freqMode === 'custom' && p.weekdays.length) {
    const days = p.weekdays.map(d => '周' + WEEKDAY_LABELS[d]).join(' ')
    return `${days} ${timeLabel}`
  }
  return cronExpr
}

const LOOKBACK_DETENTS = [0, 6, 12, 24, 48, 72]

function formatLookback(h) {
  if (h === 0) return '不限'
  if (h >= 24) return `${Math.floor(h/24)}天${h%24>0?h%24+'小时':''}`
  return `${h}小时`
}

function snapLookback(raw) {
  for (const d of LOOKBACK_DETENTS) {
    if (Math.abs(raw - d) <= 2) return d
  }
  return raw
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
  const [digestRunning, setDigestRunning] = useState('')  // chat_id of currently running digest
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
  const [alertDraft, setAlertDraft] = useState({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: '' })
  const [digestDraft, setDigestDraft] = useState({
    chat_id: '', group_name: '', schedule: [], cron_expr: '', lookback_hours: 6, enabled: true,
    unread_only: false, push_target: '', profile: { summary: '', focus: [], ignore: [], style: '', custom_prompt: '' },
  })
  const [editorError, setEditorError] = useState('')
  // Push result toast (auto-disappears after 3s)
  const [pushToast, setPushToast] = useState(null)  // { group_name, success, error }
  // Draft state for editing existing cards (separate from saved config)
  const [alertDrafts, setAlertDrafts] = useState({})  // { index: { ...values } }
  const [digestDrafts, setDigestDrafts] = useState({})  // { index: { ...values } }

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
      ws = new WebSocket(`ws://${API_BASE.replace(/^https?:\/\//, '')}/ws`)
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
      digest_groups: (raw.digest_groups || []).map(item => ({ chat_id: '', ...item })),
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
        setSaveError(d.error || '保存失败')
        setSaveFlash('error')
        setTimeout(() => setSaveFlash(null), 2500)
      }
    } catch (e) {
      setSaveError(e.message || '保存失败')
      setSaveFlash('error')
      setTimeout(() => setSaveFlash(null), 2500)
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

  async function handleRunDigest(chatId, groupName) {
    if (!chatId) return
    setDigestRunning(chatId)
    try {
      const res = await fetch(`${API_BASE}/api/assistant/digest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, group_name: groupName }),
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
            className="fixed bottom-6 right-6 z-50"
          >
            <div className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium shadow-lg transition-all ${
              saveFlash === 'saving' ? 'bg-bg-raised text-text-muted' :
              saveFlash === 'saved' ? 'bg-brand-green/90 text-white' :
              'bg-status-error/90 text-white'
            }`}>
              {saveFlash === 'saving' && <Spinner size={14} className="animate-spin" />}
              {saveFlash === 'saved' && <CheckCircle size={14} weight="fill" />}
              {saveFlash === 'error' && <Warning size={14} weight="fill" />}
              {saveFlash === 'saving' ? '保存中...' : saveFlash === 'saved' ? '已保存' : '保存失败'}
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
          {digestCount > 0 && `${digestCount} 个摘要群 · `}
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
          subtitle="检测到关键词时即时提醒，可推送到微信"
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
                <p className="text-sm text-text-muted">添加群聊以配置关键词提醒</p>
                <button
                  onClick={() => { setShowAlertEditor(true); setAlertDraft({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: '' }); setEditorError('') }}
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
                      if (!alertDraft.chat_id) { setEditorError('请先选择群聊'); return }
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
                onClick={() => { setShowAlertEditor(true); setAlertDraft({ chat_id: '', group_name: '', keywords: [], enabled: true, push_target: '' }); setEditorError('') }}
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
          title="定时群摘要"
          accent="var(--status-warn)"
          icon={Clock}
          subtitle="定时生成群聊摘要，可推送到微信"
        />
        <div className={`bg-bg-card rounded-2xl border border-border-main shadow-sm overflow-hidden transition-opacity duration-300 ${!assistantOn ? 'opacity-40' : ''}`}>
          <div className="p-6 space-y-3">
            {/* 已有群列表 */}
            <AnimatePresence>
              {(config.digest_groups || []).map((dg, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <DigestGroupCard
                    dg={dg}
                    index={i}
                    groups={groups}
                    expanded={!!expandedDigests[i]}
                    profileExpanded={!!expandedProfiles[i]}
                    draft={digestDrafts[i] || null}
                    defaultSystemPrompt={config.default_system_prompt}
                    stylePresets={config.style_presets || {}}
                    onToggleExpand={() => {
                      const nextExpanded = !expandedDigests[i]
                      setExpandedDigests(prev => ({ ...prev, [i]: nextExpanded }))
                      if (nextExpanded) {
                        // Initialize draft from current config
                        setDigestDrafts(prev => ({ ...prev, [i]: { ...dg } }))
                      } else {
                        // Clear draft on collapse
                        setDigestDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      }
                    }}
                    onToggleProfile={() => setExpandedProfiles(prev => ({ ...prev, [i]: !prev[i] }))}
                    onToggleEnabled={v => {
                      // Toggle saves immediately — directly patch config, skip draft
                      const next = [...config.digest_groups]
                      next[i] = { ...next[i], enabled: v }
                      setConfig(prev => ({ ...prev, digest_groups: next }))
                      scheduleAutoSave({ ...config, digest_groups: next }, true)
                    }}
                    onDelete={() => {
                      const next = config.digest_groups.filter((_, idx) => idx !== i)
                      updateAndSaveNow('digest_groups', next)
                    }}
                    onSelectGroup={chatId => {
                      const selected = findGroup(chatId)
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...prev[i], chat_id: chatId, group_name: selected?.group_name || prev[i]?.group_name || '' } }))
                    }}
                    onScheduleChange={schedule => {
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...prev[i], schedule } }))
                    }}
                    onCronExprChange={cron_expr => {
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...prev[i], cron_expr } }))
                    }}
                    onLookbackChange={lookback_hours => {
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...prev[i], lookback_hours } }))
                    }}
                    onProfileChange={patch => {
                      setDigestDrafts(prev => {
                        const profile = prev[i]?.profile || {}
                        return { ...prev, [i]: { ...prev[i], profile: { ...profile, ...patch } } }
                      })
                    }}
                    onUnreadOnlyChange={v => {
                      // Toggle updates draft only — save button persists
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...(prev[i] || dg), unread_only: v } }))
                    }}
                    onPushTargetChange={v => {
                      // Toggle updates draft only — save button persists
                      setDigestDrafts(prev => ({ ...prev, [i]: { ...(prev[i] || dg), push_target: v } }))
                    }}
                    onSave={() => {
                      const draft = digestDrafts[i]
                      if (!draft) return
                      // Validate cron before save
                      const cronErr = validateCronExpr(draft.cron_expr || '')
                      if (cronErr) {
                        setSaveError(cronErr)
                        setSaveFlash('error')
                        setTimeout(() => setSaveError(''), 3000)
                        return
                      }
                      // If chat_id changed, check conflict with another digest group
                      if (draft.chat_id && draft.chat_id !== config.digest_groups[i].chat_id) {
                        const conflict = (config.digest_groups || []).find((g, idx) => idx !== i && g.chat_id === draft.chat_id)
                        if (conflict) {
                          setSaveError(`"${conflict.group_name || conflict.chat_id}" 已存在定时摘要配置`)
                          setSaveFlash('error')
                          setTimeout(() => setSaveError(''), 3000)
                          return
                        }
                      }
                      const next = [...config.digest_groups]
                      // Same as alert groups: enabled is handled independently
                      const { enabled: _enabled, ...safeDraft } = draft || {}
                      next[i] = { ...next[i], ...safeDraft }
                      setConfig(prev => ({ ...prev, digest_groups: next }))
                      scheduleAutoSave({ ...config, digest_groups: next }, true)
                      setDigestDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      setExpandedDigests(prev => ({ ...prev, [i]: false }))
                      setSaveFlash('saved')
                      setTimeout(() => setSaveFlash(null), 1500)
                    }}
                    onCancel={() => {
                      setDigestDrafts(prev => { const n = { ...prev }; delete n[i]; return n })
                      setExpandedDigests(prev => ({ ...prev, [i]: false }))
                    }}
                    digestRunning={digestRunning}
                    onRunDigest={handleRunDigest}
                  />
                </motion.div>
              ))}
            </AnimatePresence>

            {/* 空状态 */}
            {!config.digest_groups?.length && !showDigestEditor && (
              <div className="py-10 text-center">
                <Clock size={32} className="text-text-muted/30 mx-auto mb-3" />
                <p className="text-sm text-text-muted">添加群聊以配置定时摘要</p>
                <button
                  onClick={() => { setShowDigestEditor(true); setDigestDraft({ chat_id: '', group_name: '', schedule: [], cron_expr: '', lookback_hours: 6, enabled: true, unread_only: false, push_target: '', profile: { summary: '', focus: [], ignore: [], style: '' } }); setEditorError('') }}
                  className="mt-4 text-sm text-brand-green-hover hover:underline cursor-pointer font-medium"
                >+ 添加摘要群</button>
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
                    defaultSystemPrompt={config.default_system_prompt}
                    stylePresets={config.style_presets || {}}
                    onDraftChange={setDigestDraft}
                    onSave={() => {
                      if (!digestDraft.chat_id) { setEditorError('请先选择群聊'); return }
                      const cron_expr = digestDraft.cron_expr || '0 9 * * *'
                      const cronErr = validateCronExpr(cron_expr)
                      if (cronErr) { setEditorError(cronErr); return }
                      // Check duplicate chat_id in digest_groups
                      if ((config.digest_groups || []).some(g => g.chat_id === digestDraft.chat_id)) {
                        const name = findGroup(digestDraft.chat_id)?.group_name || digestDraft.chat_id
                        setEditorError(`"${name}" 已存在定时摘要配置`)
                        return
                      }
                      const selected = findGroup(digestDraft.chat_id)
                      const schedule = digestDraft.schedule?.length ? digestDraft.schedule : ['09:00']
                      const next = [...(config.digest_groups || []), {
                        ...digestDraft,
                        schedule,
                        cron_expr,
                        group_name: selected?.group_name || digestDraft.group_name || '',
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
                onClick={() => { setShowDigestEditor(true); setDigestDraft({ chat_id: '', group_name: '', schedule: [], cron_expr: '', lookback_hours: 6, enabled: true, unread_only: false, push_target: '', profile: { purpose: '', description: '', focus: [], ignore: [], style: '' } }); setEditorError('') }}
                className="w-full py-3.5 text-sm text-text-muted hover:text-brand-green border border-dashed border-border-main hover:border-brand-green/40 rounded-xl transition-all duration-200 cursor-pointer bg-bg-raised/30 hover:bg-brand-green/5"
              >
                + 添加摘要群
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
                        groups={groups}
                        value={filters.chat_id}
                        onChange={chatId => setFilters(prev => ({ ...prev, chat_id: chatId }))}
                        placeholder="全部群聊"
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
            {values.push_target === 'ilink' && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-status-warn-soft text-status-warn font-medium">推送</span>
            )}
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
                <label className="text-xs text-text-muted block mb-1.5">选择群聊</label>
                <SearchableGroupSelect
                  groups={groups}
                  value={values.chat_id || ''}
                  onChange={onSelectGroup}
                  placeholder="搜索群聊..."
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
              {/* Push to WeChat toggle */}
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm text-text-main/80 font-medium">推送到微信</p>
                  <p className="text-xs text-text-muted mt-0.5">开启后关键词命中时自动推送到微信私聊（需先绑定 iLink Bot）</p>
                </div>
                <Toggle
                  enabled={values.push_target === 'ilink'}
                  onChange={v => onPushTargetChange?.(v ? 'ilink' : '')}
                />
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

function DigestGroupCard({ dg, index, groups, expanded, profileExpanded, draft, onToggleExpand, onToggleProfile, onToggleEnabled, onDelete, onSelectGroup, onScheduleChange, onCronExprChange, onLookbackChange, onProfileChange, onUnreadOnlyChange, onPushTargetChange, onSave, onCancel, defaultSystemPrompt, stylePresets, digestRunning, onRunDigest }) {
  const bodyRef = useRef(null)
  // Use draft if available (editing), otherwise use saved values
  const values = draft || dg

  // 解析 cron/schedule 为 header 展示用
  const headerSchedule = dg.cron_expr
    ? cronToLabel(dg.cron_expr)
    : (dg.schedule || []).length > 0
      ? dg.schedule.join(' · ')
      : ''

  return (
    <div className="border border-border-main rounded-xl overflow-hidden transition-all duration-200 hover:border-border-main/80">
      {/* Header */}
      <div
        className="flex items-center gap-3 p-4 cursor-pointer hover:bg-bg-raised/30 transition-colors"
        onClick={onToggleExpand}
      >
        <Toggle enabled={dg.enabled} onChange={onToggleEnabled} />
        <div className="flex-1 min-w-0">
          <span className="text-sm text-text-main font-medium truncate block">
            {values.group_name || `摘要群 #${index + 1}`}
          </span>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {headerSchedule ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-brand-green/10 text-brand-green-hover dark:text-brand-green font-mono">{headerSchedule}</span>
            ) : (
              <span className="text-xs text-status-warn">未设置时间</span>
            )}
            {values.lookback_hours && values.lookback_hours !== 6 && (
              <span className="text-xs text-text-muted">{values.lookback_hours}h</span>
            )}
            {values.unread_only && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-status-warn-soft text-status-warn font-medium">未读</span>
            )}
            {values.push_target === 'ilink' && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-status-info-soft text-status-info font-medium">推送</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={e => { e.stopPropagation(); onRunDigest(values.chat_id, values.group_name) }}
            disabled={!values.chat_id || digestRunning === values.chat_id}
            className={`flex items-center gap-1 text-xs font-medium transition-colors cursor-pointer px-2 py-1 rounded-lg
              ${digestRunning === values.chat_id
                ? 'text-brand-green/50 cursor-wait'
                : 'text-brand-green hover:text-brand-green-hover hover:bg-brand-green/[0.06]'
              }`}
            title="手动生成摘要"
          >
            <Play size={13} weight="fill" />
            {digestRunning === values.chat_id ? '生成中...' : '生成摘要'}
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
              {/* Group select */}
              <div>
                <label className="text-xs text-text-muted block mb-1.5">选择群聊</label>
                <SearchableGroupSelect
                  groups={groups}
                  value={values.chat_id || ''}
                  onChange={onSelectGroup}
                  placeholder="搜索群聊..."
                />
                {!values.chat_id && values.group_name && (
                  <p className="text-xs text-status-warn mt-1">历史群名：{values.group_name}，请从下拉重新绑定</p>
                )}
              </div>
              {/* Schedule config */}
              <ScheduleConfig
                schedule={values.schedule || []}
                cronExpr={values.cron_expr || ''}
                onScheduleChange={onScheduleChange}
                onCronExprChange={onCronExprChange}
              />
              {/* Lookback — 滑杆 0-72h */}
              <div>
                <label className="text-xs text-text-muted block mb-1.5">摘要时间范围</label>
                <p className="text-xs text-text-muted mb-2">从当前时间往前取多少小时的消息进行摘要</p>
                <div className="space-y-2">
                  <input
                    type="range" min="0" max="72" step="1"
                    value={values.lookback_hours ?? 6}
                    onChange={e => onLookbackChange(parseInt(e.target.value))}
                    onMouseUp={e => onLookbackChange(snapLookback(parseInt(e.target.value)))}
                    onTouchEnd={e => onLookbackChange(snapLookback(parseInt(e.target.value)))}
                    className="w-full accent-brand-green-hover cursor-pointer"
                  />
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-text-main min-w-[5rem]">
                      {formatLookback(values.lookback_hours ?? 6)}
                    </span>
                    <div className="flex items-center gap-1">
                      {LOOKBACK_DETENTS.filter(d => d > 0).map(d => (
                        <span key={d} className="text-[10px] text-text-muted/60 w-6 text-center">{d}</span>
                      ))}
                    </div>
                  </div>
                </div>
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
              {/* Push to WeChat toggle — 加深标签 */}
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm text-text-main/80 font-medium">推送到微信</p>
                  <p className="text-xs text-text-muted mt-0.5">开启后摘要结果自动推送到微信私聊（需先绑定 iLink Bot）</p>
                </div>
                <Toggle
                  enabled={values.push_target === 'ilink'}
                  onChange={v => onPushTargetChange?.(v ? 'ilink' : '')}
                />
              </div>
              {/* Group profile */}
              <div>
                <button
                  onClick={onToggleProfile}
                  className="flex items-center gap-2 text-sm text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  {profileExpanded ? <CaretDown size={12} /> : <CaretRight size={12} />}
                  群档案 Profile
                  {values.profile && (values.profile.summary || values.profile.focus?.length || values.profile.custom_prompt) ? (
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
                        {/* 群简介 — merged from purpose + description */}
                        <div>
                          <label className="text-xs text-text-muted block mb-1">群简介</label>
                          <textarea
                            value={values.profile?.summary || ''}
                            onChange={e => onProfileChange({ summary: e.target.value })}
                            placeholder="这个群是做什么的？主要聊什么？"
                            rows={2}
                            className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm text-text-main placeholder:text-text-muted/65 resize-none focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                          />
                        </div>
                        {/* 关注点 — tag input */}
                        <ProfileInput label="关注点（逗号分隔）" value={(values.profile?.focus || []).join(', ')} placeholder="新需求, 报价, 截止时间" onChange={v => onProfileChange({ focus: v.split(',').map(s => s.trim()).filter(Boolean) })} />
                        {/* 忽略内容 — tag input */}
                        <ProfileInput label="忽略内容（逗号分隔）" value={(values.profile?.ignore || []).join(', ')} placeholder="闲聊, 表情, 广告" onChange={v => onProfileChange({ ignore: v.split(',').map(s => s.trim()).filter(Boolean) })} />
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
                          {/** 非自定义风格 → 显示对应提示词预览（只读） */}
                          {values.profile?.style && values.profile.style !== 'custom' && (
                            <div className="mt-1.5 p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-16 overflow-y-auto whitespace-pre-wrap">
                              {stylePresets?.[values.profile.style] || defaultSystemPrompt || '（暂无说明）'}
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
        <label className="text-xs text-text-muted block mb-1.5">选择群聊 <span className="text-status-error">*</span></label>
        <SearchableGroupSelect
          groups={groups}
          value={draft.chat_id || ''}
          onChange={chatId => {
            const selected = groups.find(g => g.chat_id === chatId)
            onDraftChange({ ...draft, chat_id: chatId, group_name: selected?.group_name || '' })
          }}
          placeholder="搜索群聊..."
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
      {/* Push to WeChat toggle */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-text-main/80 font-medium">推送到微信</p>
          <p className="text-xs text-text-muted mt-0.5">开启后关键词命中时自动推送到微信私聊（需先绑定 iLink Bot）</p>
        </div>
        <Toggle enabled={draft.push_target === 'ilink'} onChange={v => onDraftChange({ ...draft, push_target: v ? 'ilink' : '' })} />
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

function DigestGroupEditor({ draft, groups, error, onDraftChange, onSave, onCancel, defaultSystemPrompt, stylePresets }) {
  const [profileOpen, setProfileOpen] = useState(false)
  return (
    <div className="border border-brand-green/30 rounded-xl p-4 space-y-3 bg-brand-green/[0.02]">
      <p className="text-sm text-brand-green font-semibold mb-1">新增摘要群</p>
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div>
        <label className="text-xs text-text-muted block mb-1.5">选择群聊 <span className="text-status-error">*</span></label>
        <SearchableGroupSelect
          groups={groups}
          value={draft.chat_id || ''}
          onChange={chatId => {
            const selected = groups.find(g => g.chat_id === chatId)
            onDraftChange({ ...draft, chat_id: chatId, group_name: selected?.group_name || '' })
          }}
          placeholder="搜索群聊..."
        />
      </div>
      {/* Schedule config */}
      <ScheduleConfig
        schedule={draft.schedule || []}
        cronExpr={draft.cron_expr || ''}
        onScheduleChange={schedule => onDraftChange({ ...draft, schedule })}
        onCronExprChange={cron_expr => onDraftChange({ ...draft, cron_expr })}
      />
      {/* 回溯时长 — 滑杆 */}
      <div>
        <label className="text-xs text-text-muted block mb-1.5">摘要时间范围</label>
        <div className="space-y-2">
          <input
            type="range" min="0" max="72" step="1"
            value={draft.lookback_hours ?? 6}
            onChange={e => onDraftChange({ ...draft, lookback_hours: parseInt(e.target.value) })}
            onMouseUp={e => onDraftChange({ ...draft, lookback_hours: snapLookback(parseInt(e.target.value)) })}
            onTouchEnd={e => onDraftChange({ ...draft, lookback_hours: snapLookback(parseInt(e.target.value)) })}
            className="w-full accent-brand-green-hover cursor-pointer"
          />
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-text-main min-w-[5rem]">
              {formatLookback(draft.lookback_hours ?? 6)}
            </span>
            <div className="flex items-center gap-1">
              {LOOKBACK_DETENTS.filter(d => d > 0).map(d => (
                <span key={d} className="text-[10px] text-text-muted/60 w-6 text-center">{d}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
      {/* 仅摘要未读 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-text-main/80 font-medium">仅摘要未读</p>
          <p className="text-xs text-text-muted mt-0.5">开启后只在时间窗口内摘要未读消息，无未读则跳过</p>
        </div>
        <Toggle enabled={draft.unread_only || false} onChange={v => onDraftChange({ ...draft, unread_only: v })} />
      </div>
      {/* 推送到微信 */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-text-main/80 font-medium">推送到微信</p>
          <p className="text-xs text-text-muted mt-0.5">开启后摘要结果自动推送到微信私聊（需先绑定 iLink Bot）</p>
        </div>
        <Toggle enabled={draft.push_target === 'ilink'} onChange={v => onDraftChange({ ...draft, push_target: v ? 'ilink' : '' })} />
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
                {/* 群简介 — merged from purpose + description */}
                <div>
                  <label className="text-xs text-text-muted block mb-1">群简介</label>
                  <textarea
                    value={draft.profile?.summary || ''}
                    onChange={e => onDraftChange({ ...draft, profile: { ...draft.profile, summary: e.target.value } })}
                    placeholder="这个群是做什么的？主要聊什么？"
                    rows={2}
                    className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm text-text-main placeholder:text-text-muted/65 resize-none focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
                  />
                </div>
                <ProfileInput label="关注点（逗号分隔）" value={(draft.profile?.focus || []).join(', ')} placeholder="新需求, 报价, 截止时间" onChange={v => onDraftChange({ ...draft, profile: { ...draft.profile, focus: v.split(',').map(s => s.trim()).filter(Boolean) } })} />
                <ProfileInput label="忽略内容（逗号分隔）" value={(draft.profile?.ignore || []).join(', ')} placeholder="闲聊, 表情, 广告" onChange={v => onDraftChange({ ...draft, profile: { ...draft.profile, ignore: v.split(',').map(s => s.trim()).filter(Boolean) } })} />
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
                  {/** 非自定义风格 → 显示对应提示词预览（只读） */}
                  {draft.profile?.style && draft.profile.style !== 'custom' && (
                    <div className="mt-1.5 p-2 rounded-lg bg-bg-inset border border-border-main text-xs text-text-muted max-h-16 overflow-y-auto whitespace-pre-wrap">
                      {stylePresets?.[draft.profile.style] || defaultSystemPrompt || '（暂无说明）'}
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

function ProfileInput({ label, value, placeholder, onChange }) {
  return (
    <div>
      <label className="text-xs text-text-muted block mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-bg-raised border border-border-main rounded-lg px-3.5 py-2 text-sm text-text-main placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all"
      />
    </div>
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
          <p className="text-xs text-text-muted mt-0.5">{notification.group_name || notification.chat_id || '未知群聊'}</p>
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

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const selected = groups.find(g => g.chat_id === value)
  const filtered = query
    ? groups.filter(g => g.group_name.toLowerCase().includes(query.toLowerCase()))
    : groups

  const displayText = open ? query : (selected ? selected.group_name : '')

  return (
    <div ref={ref} className="relative">
      <div className="relative">
        <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
        <input
          type="text"
          value={displayText}
          placeholder={placeholder || '搜索群聊...'}
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
              onClick={() => { onChange(''); setQuery(''); setOpen(false) }}
            >全部群聊</button>
          )}
          {filtered.length === 0 ? (
            <p className="px-4 py-3 text-xs text-text-muted text-center">无匹配群聊</p>
          ) : (
            filtered.map(g => (
              <button
                key={g.chat_id}
                type="button"
                className={`w-full text-left px-4 py-2.5 text-sm hover:bg-bg-raised transition-colors flex items-center justify-between gap-2 ${
                  g.chat_id === value ? 'bg-brand-green/10 text-brand-green-hover' : 'text-text-main'
                }`}
                onClick={() => { onChange(g.chat_id); setQuery(''); setOpen(false) }}
              >
                <span className="truncate">{g.group_name}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
