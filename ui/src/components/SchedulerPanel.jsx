import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Clock, Play, Trash, Plus, Pencil, Pause } from '@phosphor-icons/react'
import { Toggle, Input, API_BASE } from './SharedComponents'

const TASK_TYPE_LABELS = {
  oa_digest: '公众号摘要',
  fav_export: '收藏导出',
}

const STATUS_STYLES = {
  idle: 'bg-bg-raised text-text-muted',
  running: 'bg-brand-green-light/30 text-brand-green',
  error: 'bg-status-error-soft text-status-error',
}

function TaskCard({ task, onDelete, onToggle }) {
  const [expanded, setExpanded] = useState(false)
  const typeLabel = TASK_TYPE_LABELS[task.task_type] || task.task_type
  const statusStyle = STATUS_STYLES[task.status] || STATUS_STYLES.idle

  return (
    <div className="border border-border-main rounded-xl overflow-hidden bg-bg-card">
      <div
        className="flex items-center gap-3 p-4 cursor-pointer hover:bg-bg-raised/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="w-8 h-8 rounded-lg bg-bg-raised flex items-center justify-center text-text-muted">
          <Clock size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-main">{task.name}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusStyle}`}>
              {task.status === 'idle' ? '空闲' : task.status === 'running' ? '运行中' : '错误'}
            </span>
          </div>
          <p className="text-xs text-text-muted">
            {typeLabel} · {task.cron_expr}
          </p>
        </div>
        <Toggle
          enabled={task.enabled}
          onChange={() => onToggle(task.id, !task.enabled)}
        />
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(task.id) }}
          className="p-2 rounded-full text-text-muted hover:text-status-error hover:bg-status-error-soft transition-colors cursor-pointer"
        >
          <Trash size={14} />
        </button>
      </div>

      {expanded && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          className="px-4 pb-4"
        >
          <div className="pt-3 border-t border-border-main space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">任务 ID</span>
              <span className="text-text-main font-mono">{task.id}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">类型</span>
              <span className="text-text-main font-mono">{typeLabel}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">Cron 表达式</span>
              <span className="text-text-main font-mono">{task.cron_expr}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">函数引用</span>
              <span className="text-text-main font-mono text-xs truncate ml-4">{task.function_ref}</span>
            </div>
            {task.last_run_time && (
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">上次运行</span>
                <span className="text-text-main font-mono">{task.last_run_time}</span>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </div>
  )
}

function TaskCreator({ onSave, onCancel }) {
  const [name, setName] = useState('')
  const [taskType, setTaskType] = useState('oa_digest')
  const [cronExpr, setCronExpr] = useState('0 9 * * *')

  const presets = [
    { label: '每天 9:00', value: '0 9 * * *' },
    { label: '每天 12:00', value: '0 12 * * *' },
    { label: '每天 18:00', value: '0 18 * * *' },
    { label: '每天 9:00 + 18:00', value: '0 9,18 * * *' },
  ]

  const functionRefs = {
    oa_digest: 'src.assistant.oa_digest.generate_oa_digest',
    fav_export: 'src.wechat.fav_reader.export_all_favorites',
  }

  return (
    <div className="border border-border-main rounded-xl bg-bg-card p-5 space-y-4">
      <h4 className="text-sm font-semibold text-text-main">新建定时任务</h4>

      <div>
        <label className="block text-xs text-text-muted mb-1">任务名称</label>
        <Input value={name} onChange={setName} placeholder="例如：早间公众号摘要" />
      </div>

      <div>
        <label className="block text-xs text-text-muted mb-1">任务类型</label>
        <select
          value={taskType}
          onChange={(e) => setTaskType(e.target.value)}
          className="w-full bg-bg-raised border border-border-main rounded-full px-4 py-2.5 text-sm text-text-main font-mono
            focus:outline-none focus:border-brand-green cursor-pointer"
        >
          <option value="oa_digest">公众号摘要</option>
          <option value="fav_export">收藏导出</option>
        </select>
      </div>

      <div>
        <label className="block text-xs text-text-muted mb-1">Cron 表达式</label>
        <Input value={cronExpr} onChange={setCronExpr} placeholder="0 9 * * *" />
        <div className="flex flex-wrap gap-1.5 mt-2">
          {presets.map(p => (
            <button
              key={p.value}
              onClick={() => setCronExpr(p.value)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors cursor-pointer
                ${cronExpr === p.value
                  ? 'bg-brand-green-light border-brand-green/30 text-brand-green-hover dark:text-brand-green'
                  : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'
                }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          onClick={() => onSave({
            name: name || '未命名任务',
            task_type: taskType,
            cron_expr: cronExpr,
            function_ref: functionRefs[taskType],
          })}
          disabled={!cronExpr}
          className="flex-1 py-2.5 rounded-full bg-brand-green-hover text-white text-sm font-semibold hover:bg-brand-green-hover transition-colors cursor-pointer disabled:opacity-50"
        >
          创建
        </button>
        <button
          onClick={onCancel}
          className="flex-1 py-2.5 rounded-full bg-bg-raised text-text-muted text-sm font-semibold hover:bg-bg-raised/80 transition-colors cursor-pointer"
        >
          取消
        </button>
      </div>
    </div>
  )
}

export default function SchedulerPanel() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreator, setShowCreator] = useState(false)

  useEffect(() => {
    loadTasks()
  }, [])

  async function loadTasks() {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks`)
      const data = await res.json()
      if (data.ok) setTasks(data.data || [])
    } catch {}
    setLoading(false)
  }

  async function handleCreateTask(data) {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      const result = await res.json()
      if (result.ok) {
        setShowCreator(false)
        loadTasks()
      }
    } catch {}
  }

  async function handleDeleteTask(id) {
    if (!confirm('确定删除此任务？')) return
    try {
      await fetch(`${API_BASE}/api/scheduler/tasks/${id}`, { method: 'DELETE' })
      loadTasks()
    } catch {}
  }

  async function handleToggleTask(id, enabled) {
    try {
      await fetch(`${API_BASE}/api/scheduler/tasks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      loadTasks()
    } catch {}
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm bg-[#a78bfa]" />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">定时任务</h3>
          <Clock size={16} className="text-text-muted" />
          <button
            onClick={() => setShowCreator(true)}
            className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
          >
            <Plus size={12} /> 新建
          </button>
        </div>
        <p className="text-xs text-text-muted leading-relaxed pl-4">管理公众号摘要和收藏导出的定时任务</p>
      </div>

      {/* Creator */}
      <AnimatePresence>
        {showCreator && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            className="mb-6"
          >
            <TaskCreator
              onSave={handleCreateTask}
              onCancel={() => setShowCreator(false)}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Tasks */}
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <div className="w-5 h-5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
        </div>
      ) : tasks.length === 0 ? (
        <div className="text-center py-8 text-text-muted text-sm">
          暂无定时任务
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map(task => (
            <TaskCard
              key={task.id}
              task={task}
              onDelete={handleDeleteTask}
              onToggle={handleToggleTask}
            />
          ))}
        </div>
      )}
    </div>
  )
}