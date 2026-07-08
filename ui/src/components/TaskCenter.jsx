import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, ChatCircleDots, Newspaper, Clock, Spinner } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

const TASK_TYPES = {
  group_digest: { label: '群聊摘要', icon: ChatCircleDots, color: 'text-brand-green' },
  oa_digest: { label: '公众号摘要', icon: Newspaper, color: 'text-blue-400' },
}

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
  const refreshTimer = useRef(null)

  // Fetch tasks
  async function loadTasks() {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filter !== 'all') params.set('status', filter)
      params.set('limit', '50')
      const res = await fetch(`${API_BASE}/api/tasks?${params}`)
      const data = await res.json()
      if (data.ok) setTasks(data.tasks || [])
    } catch {}
    setLoading(false)
  }

  // Load on open + periodic refresh
  useEffect(() => {
    if (open) {
      loadTasks()
      refreshTimer.current = setInterval(loadTasks, 10000)
    } else {
      clearInterval(refreshTimer.current)
    }
    return () => clearInterval(refreshTimer.current)
  }, [open, filter])

  // WebSocket for task_update events
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
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
      ws = new WebSocket(`ws://${API_BASE.replace(/^https?:\/\//, '')}/ws`)
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
          <h3 className="text-sm font-semibold text-text-main">任务中心</h3>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-bg-raised transition-colors text-text-muted hover:text-text-main cursor-pointer">
            <X size={18} />
          </button>
        </div>

        {/* Filter tabs */}
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

          {tasks.map(task => {
            const typeMeta = TASK_TYPES[task.task_type] || TASK_TYPES.group_digest
            const statusMeta = STATUS_STYLES[task.status] || STATUS_STYLES.pending
            const TypeIcon = typeMeta.icon

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
                    <span className="text-xs text-text-muted">{task.result || '完成'}</span>
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
                    <span className={task.push_status === 'success' ? 'text-brand-green/70' : task.push_status === 'failed' ? 'text-[#d45656]/70' : ''}>
                      {task.push_status === 'success' ? '✓ 已推送' : task.push_status === 'failed' ? '✗ 推送失败' : '推送中'}
                    </span>
                  )}
                </div>
              </motion.div>
            )
          })}
        </div>
      </motion.div>
    </>
  )
}
