import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, ChatCircleDots, Newspaper, Clock, Spinner, ArrowClockwise, Question } from '@phosphor-icons/react'
import { API_BASE, getWsUrl } from './SharedComponents'

const TASK_TYPES = {
  group_digest: { label: '群聊摘要', icon: ChatCircleDots, color: 'text-brand-green' },
  oa_digest: { label: '公众号摘要', icon: Newspaper, color: 'text-blue-400' },
  oa_article_alert: { label: '公众号即时提醒', icon: Newspaper, color: 'text-orange-400' },
  cron: { label: '定时任务', icon: Clock, color: 'text-amber-400' },
}

// 任务中心不展示的 OA 缓存同步类任务（全文抓取/增量同步/账号同步）
// 后端仍会创建（cache_* 前缀），仅前端过滤，避免噪音
const HIDDEN_TASK_PREFIXES = ['cache_oa_', 'oa_crawl', 'oa_incremental']

const STATUS_STYLES = {
  pending:  { label: '待执行', color: 'text-text-muted', bg: 'bg-bg-raised', dot: 'bg-text-muted/40' },
  running:  { label: '执行中', color: 'text-brand-green', bg: 'bg-brand-green/[0.08]', dot: 'bg-brand-green animate-pulse' },
  completed:{ label: '已完成', color: 'text-brand-green/70', bg: 'bg-brand-green/[0.04]', dot: 'bg-brand-green/60' },
  failed:   { label: '失败', color: 'text-[#d45656]', bg: 'bg-[#d45656]/[0.06]', dot: 'bg-[#d45656]' },
}

const SOURCE_LABELS = { scheduler: '调度', manual: '手动', agent: 'Agent' }

function timeAgo(dateStr) {
  if (!dateStr) return ''
  try {
    const diff = (Date.now() - new Date(dateStr).getTime()) / 1000
    if (diff < 60) return '刚刚'
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
    return `${Math.floor(diff / 86400)} 天前`
  } catch { return '' }
}

function formatTime(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  } catch { return '' }
}

export default function TaskCenter({ open, onClose }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [retryingIds, setRetryingIds] = useState({})   // 单条重推中: { [taskId]: true }
  const [batchState, setBatchState] = useState(null)   // 批量重推进度: { total, done, success, fail, active }
  const [retryFloating, setRetryFloating] = useState(null) // { taskId, platforms: [{platform, label, status, error}], visible }
  const refreshTimer = useRef(null)
  const platformLabels = { ilink: '微信', qqbot: 'QQ', feishu: '飞书' }

  // Fetch tasks
  async function loadTasks() {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filter !== 'all') params.set('status', filter)
      if (typeFilter !== 'all') {
        const typeVal = typeFilter === 'oa' ? 'oa_digest,oa_article_alert' : typeFilter
        params.set('type', typeVal)
      }
      params.set('limit', '50')
      // 排除后台同步噪音任务（cache_oa_*/cache_fav_* 等）——
      // 后端 SQL 层过滤，limit 只作用于真实任务，避免真实任务被挤占显示不全
      params.set('exclude', 'cache_')
      const res = await fetch(`${API_BASE}/api/tasks?${params}`)
      const data = await res.json()
      if (data.ok) setTasks(data.tasks || [])
    } catch {}
    setLoading(false)
  }

  // 单条重推：同步等待结果，成功或部分成功后任务按钮自然消失
  async function retryTask(taskId) {
    setRetryingIds(prev => ({ ...prev, [taskId]: true }))
    const platformList = Object.entries(platformLabels).map(([platform, label]) => ({
      platform, label, status: 'pending', error: '', response: '',
    }))
    setRetryFloating({ taskId, status: 'pushing', platforms: platformList, error: '' })
    // Refresh the checklist without blocking the retry request. A slow
    // platform-status endpoint must never delay the actual retry.
    fetch(`${API_BASE}/api/platforms`)
      .then(res => res.json())
      .then(platformData => {
        const bound = (platformData.platforms || [])
          .filter(p => p.name === 'wechat' ? p.status?.ok : p.config?.bound)
          .map(p => p.name === 'wechat' ? 'ilink' : p.name)
        if (bound.length) {
          setRetryFloating(prev => prev?.taskId === taskId ? {
            ...prev, platforms: platformList.filter(p => bound.includes(p.platform)),
          } : prev)
        }
      })
      .catch(() => {})
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/retry`, { method: 'POST' })
      const data = await res.json()
      if (data.ok && Array.isArray(data.results)) {
        const platforms = data.results.map(r => ({
          platform: r.platform || '',
          label: r.label || platformLabels[r.platform] || r.platform || '未知',
          status: r.success ? 'success' : 'failed',
          error: r.error || '',
          response: r.response || r.error || (r.success ? '推送成功' : '推送失败'),
        }))
        setRetryFloating({ taskId, status: data.status || (data.success ? 'success' : 'failed'), platforms, error: data.error || '' })
      } else {
        setRetryFloating({ taskId, status: 'failed', platforms: [], error: data.error || '重推失败' })
      }
      setTimeout(() => setRetryFloating(null), 8000)
    } catch (e) {
      setRetryFloating({ taskId, status: 'failed', platforms: [], error: e.message || '重推失败' })
    }
    loadTasks()
    setTimeout(() => {
      setRetryingIds(prev => {
        const next = { ...prev }
        delete next[taskId]
        return next
      })
    }, 2500)
  }

  // 一键重推：后端排队后逐条推送（间隔 3s），结果经 WS 实时到达
  async function batchRetry() {
    try {
      const res = await fetch(`${API_BASE}/api/tasks/retry-batch?hours=24`, { method: 'POST' })
      const data = await res.json()
      if (data.ok) {
        if (data.total === 0) {
          setBatchState({ total: 0, done: 0, success: 0, fail: 0, active: false })
          setTimeout(() => setBatchState(null), 2500)
        } else {
          setBatchState({ total: data.total, done: 0, success: 0, fail: 0, active: true })
          // 兜底：WS 全断时 12 分钟后强制收起进度条（批量最长 ~9 分钟）
          setTimeout(() => setBatchState(null), 12 * 60 * 1000)
        }
      }
    } catch {}
  }

  // 批量重推收尾：全部完成 3.5s 后收起进度条（WS 事件驱动）
  useEffect(() => {
    if (batchState && batchState.total > 0 && batchState.done >= batchState.total) {
      const t = setTimeout(() => setBatchState(null), 3500)
      return () => clearTimeout(t)
    }
  }, [batchState])

  // Load on open + periodic refresh
  useEffect(() => {
    if (open) {
      loadTasks()
      refreshTimer.current = setInterval(loadTasks, 10000)
    } else {
      clearInterval(refreshTimer.current)
    }
    return () => clearInterval(refreshTimer.current)
  }, [open, filter, typeFilter])

  // WebSocket for task_update events
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'task_retry_platform') {
          setRetryFloating(prev => {
            if (!prev || prev.taskId !== data.task_id) return prev
            const current = prev.platforms || []
            const index = current.findIndex(p => p.platform === data.platform)
            const item = {
              platform: data.platform,
              label: data.label || platformLabels[data.platform] || data.platform || '未知',
              status: data.status || 'pending',
              error: data.error || '',
            }
            const platforms = index >= 0
              ? current.map((p, i) => i === index ? { ...p, ...item } : p)
              : [...current, item]
            return { ...prev, platforms }
          })
          return
        }
        if (data.type === 'task_retry_result') {
          // 批量重推进度：更新计数 + 刷新列表（对应任务 push_status 已变）
          setBatchState(prev => {
            if (!prev || !prev.active || prev.total === 0) return prev
            return {
              ...prev,
              done: Math.min(prev.done + 1, prev.total),
              success: prev.success + (data.success ? 1 : 0),
              fail: prev.fail + (data.success ? 0 : 1),
            }
          })
          loadTasks()
          return
        }
        if (data.type === 'task_retry_batch_done') {
          // 批量全部结束（收尾事件）：即使中间结果丢失也按此收起进度条
          setBatchState(prev => {
            if (!prev) return prev
            return { ...prev, done: prev.total, success: data.success, fail: data.fail }
          })
          loadTasks()
          return
        }
        if (data.type === 'task_update') {
          setTasks(prev => {
            const idx = prev.findIndex(t => t.id === data.task_id)
            if (idx >= 0) {
              const updated = [...prev]
              updated[idx] = {
                ...updated[idx],
                status: data.status,
                progress: data.progress,
                error: data.error || updated[idx].error,
              }
              return updated
            }
            // New task not in list — reload
            loadTasks()
            return prev
          })
        }
      } catch {}
    }
    let ws = window.__task_center_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__task_center_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => ws.removeEventListener('message', handleMessage)
  }, [])

  const filters = [
    { key: 'all', label: '全部' },
    { key: 'running', label: '执行中' },
    { key: 'completed', label: '已完成' },
    { key: 'failed', label: '失败' },
  ]

  return (
    <>
      {/* Backdrop */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={onClose}
          />
        )}
      </AnimatePresence>

      {/* Panel */}
      <motion.div
        initial={{ x: '100%' }}
        animate={{ x: open ? 0 : '100%' }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="fixed right-0 top-0 h-full w-[420px] max-w-[calc(100vw-1rem)] bg-bg-main border-l border-border-main z-50 flex flex-col"
        style={{ pointerEvents: open ? 'auto' : 'none' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-main shrink-0">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-text-main">任务中心</h3>
            {/* 一键重推：重新推送 24h 内推送失败的消息（每条间隔 3 秒） */}
            <div className="flex items-center gap-1.5">
              <button
                onClick={batchRetry}
                disabled={batchState?.active}
                className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium rounded-lg
                           text-sky-400 bg-sky-400/[0.06] border border-sky-400/[0.12]
                           hover:bg-sky-400/[0.12] hover:border-sky-400/[0.25] hover:text-sky-300
                           transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-wait"
              >
                <ArrowClockwise size={12} weight="bold" className={batchState?.active ? 'animate-spin' : ''} />
                <span>{batchState?.active ? '重推中' : '一键重推'}</span>
              </button>
              {/* 问号 tooltip — 向下弹出（header 在顶部，向上弹会被容器裁剪） */}
              <div className="relative group">
                <Question size={13} className="text-text-muted cursor-help hover:text-sky-300 transition-colors" />
                <div className="opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-opacity duration-150
                                absolute top-full left-1/2 -translate-x-1/2 mt-2 z-[60]
                                bg-bg-raised border border-border-main rounded-lg px-3 py-2
                                text-[11px] leading-relaxed text-text-muted w-52 shadow-xl pointer-events-none">
                  重新推送 24 小时内推送失败的消息<br />每条间隔 3 秒，避免触发 iLink 限速
                </div>
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-bg-raised transition-colors text-text-muted hover:text-text-main cursor-pointer">
            <X size={18} />
          </button>
        </div>

        {/* 批量重推进度条 */}
        {batchState && (
          <div className="px-5 py-3 border-b border-border-main bg-sky-400/[0.03] shrink-0">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-sky-300 font-medium">
                {batchState.total === 0
                  ? '没有可重推的推送失败任务'
                  : batchState.done < batchState.total
                    ? `正在重推 ${batchState.done + 1}/${batchState.total}...`
                    : `重推完成：${batchState.success} 成功，${batchState.fail} 失败`}
              </span>
              {batchState.active && batchState.total > 0 && (
                <span className="text-[10px] text-text-muted">间隔 3s</span>
              )}
            </div>
            {batchState.total > 0 && (
              <div className="h-1 bg-bg-raised rounded-full overflow-hidden">
                <div
                  className="h-full bg-sky-400/60 rounded-full transition-all duration-500"
                  style={{ width: `${Math.min(100, (batchState.done / batchState.total) * 100)}%` }}
                />
              </div>
            )}
          </div>
        )}

        {/* Status filter tabs */}
        <div className="flex gap-1 px-5 py-3 border-b border-border-main shrink-0">
          {filters.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1 text-xs font-medium rounded-full transition-colors cursor-pointer ${
                filter === f.key
                  ? 'bg-brand-green/[0.12] text-brand-green-hover dark:text-brand-green'
                  : 'text-text-muted hover:text-text-main hover:bg-bg-raised/60'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Type filter tabs */}
        <div className="flex gap-1 px-5 py-2 border-b border-border-main shrink-0">
          {[
            { key: 'all', label: '全部' },
            { key: 'cron', label: '⏰ 定时任务' },
            { key: 'oa', label: '📰 公众号' },
            { key: 'group_digest', label: '💬 群聊' },
          ].map(f => (
            <button
              key={f.key}
              onClick={() => setTypeFilter(f.key)}
              className={`px-3 py-1 text-xs font-medium rounded-full transition-colors cursor-pointer ${
                typeFilter === f.key
                  ? 'bg-brand-green/[0.12] text-brand-green-hover dark:text-brand-green'
                  : 'text-text-muted hover:text-text-main hover:bg-bg-raised/60'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Task list */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
          {loading && tasks.length === 0 && (
            <div className="flex items-center justify-center py-12 text-text-muted">
              <Spinner size={20} className="animate-spin mr-2" />
              <span className="text-sm">加载中...</span>
            </div>
          )}

          {!loading && tasks.length === 0 && (
            <div className="text-center py-12 text-text-muted">
              <Clock size={32} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">暂无任务记录</p>
              <p className="text-xs mt-1 opacity-60">定时摘要和手动触发的任务会在这里显示</p>
            </div>
          )}

          {tasks.filter(task =>
            !HIDDEN_TASK_PREFIXES.some(p => (task.task_type || '').startsWith(p))
          ).map(task => {
            const typeMeta = TASK_TYPES[task.task_type] || TASK_TYPES.group_digest
            const statusMeta = STATUS_STYLES[task.status] || STATUS_STYLES.pending
            const TypeIcon = typeMeta.icon
            // 可重推：推送失败的任务（digest/cron 类 push_status=failed；
            // 即时提醒 oa_article_alert 推送失败时 status=failed）
            const canRetry = task.push_status === 'failed' ||
              (task.task_type === 'oa_article_alert' && task.status === 'failed')

            return (
              <motion.div
                key={task.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.15 }}
                className="bg-bg-card rounded-xl border border-border-main p-3.5 space-y-2"
              >
                {/* Title row */}
                <div className="flex items-center gap-2">
                  <TypeIcon size={14} className={typeMeta.color} weight="fill" />
                  <span className="text-sm font-medium text-text-main truncate flex-1">{task.group_name}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${typeMeta.color} bg-brand-green/[0.06]`}>
                    {typeMeta.label}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium text-text-muted bg-bg-raised">
                    {SOURCE_LABELS[task.source] || task.source}
                  </span>
                </div>

                {/* Status / Progress row */}
                <div className="flex items-center gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${statusMeta.dot}`} />
                  {task.status === 'running' ? (
                    <span className="text-xs text-brand-green font-medium">{task.progress || '执行中...'}</span>
                  ) : task.status === 'failed' ? (
                    <span className="text-xs text-[#d45656] truncate">{task.error || '执行失败'}</span>
                  ) : task.status === 'completed' ? (
                    <span className="text-xs text-text-muted truncate">{task.result || '完成'}</span>
                  ) : (
                    <span className="text-xs text-text-muted">{task.progress || '准备中'}</span>
                  )}
                </div>

                {/* Running progress bar */}
                {task.status === 'running' && (
                  <div className="h-1 bg-bg-raised rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-brand-green rounded-full"
                      animate={{ x: ['-100%', '100%'] }}
                      transition={{ repeat: Infinity, duration: 1.5, ease: 'easeInOut' }}
                      style={{ width: '40%' }}
                    />
                  </div>
                )}

                {/* Meta row */}
                <div className="flex items-center gap-3 text-[10px] text-text-muted font-mono">
                  <span>{formatTime(task.created_at)}</span>
                  {task.status === 'completed' && task.finished_at && (
                    <span>耗时 {(() => {
                      try {
                        const ms = new Date(task.finished_at).getTime() - new Date(task.started_at || task.created_at).getTime()
                        if (ms < 60000) return `${(ms / 1000).toFixed(0)}s`
                        return `${(ms / 60000).toFixed(1)}m`
                      } catch { return '' }
                    })()}</span>
                  )}
                  {task.msg_count > 0 && <span>{task.msg_count} 条消息</span>}
                  {task.articles_count > 0 && <span>{task.articles_count} 篇文章</span>}
                  {task.push_status && (
                    <span className={task.push_status === 'success' ? 'text-brand-green/70' : task.push_status === 'partial' ? 'text-amber-400/80' : task.push_status === 'failed' ? 'text-[#d45656]/70' : ''}>
                      {task.push_status === 'success' ? '✓ 已推送' : task.push_status === 'partial' ? '△ 部分成功' : task.push_status === 'failed' ? '✗ 推送失败' : '推送中'}
                    </span>
                  )}
                  {/* 单条重推按钮（仅推送失败的任务显示） */}
                  {canRetry && (
                    <button
                      onClick={() => retryTask(task.id)}
                      disabled={!!retryingIds[task.id]}
                      title="重新推送该条消息"
                      className="ml-auto flex items-center gap-1 px-1.5 py-0.5 rounded-md
                                 text-sky-400 bg-sky-400/[0.05] border border-sky-400/[0.15]
                                 hover:bg-sky-400/[0.12] hover:border-sky-400/[0.3] hover:text-sky-300
                                 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-wait"
                    >
                      {retryingIds[task.id] ? (
                        <Spinner size={10} className="animate-spin" />
                      ) : (
                        <ArrowClockwise size={10} weight="bold" />
                      )}
                      <span className="text-[10px] font-medium">{retryingIds[task.id] ? '推送中' : '重推'}</span>
                    </button>
                  )}
                </div>
              </motion.div>
            )
          })}
        </div>
        {/* 重推浮窗：以待办清单展示各平台进度 */}
        {retryFloating && (
          <div className="absolute bottom-4 left-4 right-4 bg-bg-card border border-border-main rounded-xl shadow-xl p-3 z-10">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-text-main">
                {retryFloating.status === 'pushing' ? '正在推送已绑定的平台..' : retryFloating.status === 'partial' ? '重推完成：部分成功' : retryFloating.status === 'success' ? '重推完成' : '重推失败：所有平台均失败'}
              </span>
              <button onClick={() => setRetryFloating(null)} className="p-1 rounded hover:bg-bg-raised text-text-muted hover:text-text-main">
                <X size={12} />
              </button>
            </div>
            <div className="space-y-1.5">
              {retryFloating.platforms.length === 0 && retryFloating.status === 'pushing' && (
                <div className="text-xs text-text-muted">暂无已绑定平台</div>
              )}
              {retryFloating.platforms.map((p, i) => {
                const pushing = p.status === 'pushing'
                const pending = p.status === 'pending'
                const success = p.status === 'success'
                return (
                  <div key={p.platform || i} className="flex items-center gap-2 text-xs">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${success ? 'bg-status-ok' : p.status === 'failed' ? 'bg-status-error' : 'bg-text-muted/50'}`} />
                    {(pushing || pending) && <Spinner size={11} className="animate-spin text-sky-400" />}
                    <span className="text-text-main font-medium">{p.label}</span>
                    <span className={success ? 'text-brand-green' : p.status === 'failed' ? 'text-[#d45656]' : 'text-text-muted'}>
                      {success ? '推送成功' : p.status === 'failed' ? '推送失败' : pushing ? '推送中' : '待推送'}
                    </span>
                    {p.response && <span className="text-text-muted truncate flex-1" title={p.response}>{p.response.slice(0, 60)}</span>}
                  </div>
                )
              })}
              {retryFloating.error && retryFloating.platforms.length === 0 && (
                <p className="text-[11px] text-[#d45656] mt-1 truncate" title={retryFloating.error}>{retryFloating.error.slice(0, 80)}</p>
              )}
            </div>
          </div>
        )}
      </motion.div>
    </>
  )
}
