import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Play, Stop, Key, Spinner, CheckCircle, XCircle, ArrowsClockwise, WarningOctagon, Clock, ChatCircle, Newspaper, Database, WechatLogo, Brain, Robot, Cube, Lightning, ArrowRight, PaperPlaneTilt, Bell } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'
import LANCard from './LANCard'

const spring = { type: 'spring', stiffness: 100, damping: 20 }
const easeOut = [0.16, 1, 0.3, 1]

/* ── Status check tile ─── */
function StatusTile({ icon: Icon, label, ok, okText, errText, detail }) {
  return (
    <motion.div
      whileHover={{ y: -1, transition: { duration: 0.15 } }}
      className={`flex items-center gap-2.5 px-4 py-3 rounded-xl transition-colors cursor-default ${
        ok
          ? 'bg-bg-raised hover:bg-border-main/30'
          : 'bg-status-error/[0.04] dark:bg-status-error/[0.06] hover:bg-status-error/[0.08]'
      }`}
    >
      <Icon size={16} weight="fill" className={ok ? 'text-brand-green' : 'text-status-error/60'} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm text-text-main font-semibold">{label}</span>
          <AnimatePresence mode="wait">
            <motion.span
              key={ok ? 'ok' : 'err'}
              initial={{ scale: 0.7, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.7, opacity: 0 }}
              transition={{ duration: 0.2, type: 'spring', stiffness: 400 }}
              className={`text-xs font-mono font-bold ${ok ? 'text-brand-green' : 'text-status-error'}`}
            >
              {ok ? okText : errText}
            </motion.span>
          </AnimatePresence>
        </div>
        {detail && <p className="text-xs text-text-muted truncate mt-0.5">{detail}</p>}
      </div>
    </motion.div>
  )
}

/* ── Scheduled Task Card — neutral borders, no colored bg ─── */
const TASK_TYPE_META = {
  group_digest: {
    icon: ChatCircle,
    label: '群聊摘要',
    accent: 'text-brand-green',
    badge: 'bg-brand-green/[0.08] text-brand-green dark:bg-brand-green/[0.10]',
    leftBorder: 'border-l-brand-green/40',
  },
  oa_digest: {
    icon: Newspaper,
    label: '公众号摘要',
    accent: 'text-[#8b5cf6]',
    badge: 'bg-[#8b5cf6]/[0.08] text-[#8b5cf6] dark:bg-[#8b5cf6]/[0.10]',
    leftBorder: 'border-l-[#8b5cf6]/40',
  },
}

/* ── Instant Alert Card — keyword alerts + OA monitors ─── */
function KeywordAlertCard({ onTabChange }) {
  const [alertGroups, setAlertGroups] = useState(null)
  const [oaMonitors, setOaMonitors] = useState(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/assistant/config`)
      .then(r => r.json())
      .then(res => {
        if (res?.ok) {
          setAlertGroups(res.config?.alert_groups || [])
          setOaMonitors(res.config?.oa_monitor_groups || [])
        } else {
          setAlertGroups([])
          setOaMonitors([])
        }
      })
      .catch(() => { setAlertGroups([]); setOaMonitors([]) })
  }, [])

  const loading = alertGroups === null
  const enabledAlerts = (alertGroups || []).filter(g => g.enabled).length
  const totalAlerts = (alertGroups || []).length
  const totalOa = (oaMonitors || []).length
  const hasAny = totalAlerts > 0 || totalOa > 0

  return (
    <div className="space-y-3">
      {/* Summary row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          {totalAlerts > 0 && (
            <span className="text-sm text-text-main font-semibold">{totalAlerts} 个提醒群</span>
          )}
          {totalOa > 0 && (
            <span className="text-sm text-text-main font-semibold">{totalOa} 个公众号</span>
          )}
          {enabledAlerts > 0 && (
            <span className="text-xs font-mono font-bold text-amber-600 bg-amber-500/[0.08] px-1.5 py-px rounded">{enabledAlerts} 启用</span>
          )}
        </div>
        {hasAny && (
          <button onClick={() => onTabChange?.('assistant')}
            className="flex items-center gap-1 text-xs text-amber-600 hover:text-amber-500 font-medium cursor-pointer group">
            查看全部 <ArrowRight size={10} className="group-hover:translate-x-0.5 transition-transform" />
          </button>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center gap-2 py-8 justify-center">
          <Spinner size={14} className="animate-spin text-text-muted" />
          <span className="text-xs text-text-muted">加载中</span>
        </div>
      ) : !hasAny ? (
        <div className="flex flex-col items-center py-8 gap-2">
          <Lightning size={20} weight="fill" className="text-amber-400/40" />
          <span className="text-sm text-text-muted">暂未配置即时提醒</span>
          <button onClick={() => onTabChange?.('assistant')}
            className="flex items-center gap-1 text-xs text-amber-600 hover:text-amber-500 font-medium cursor-pointer">
            前往配置 <ArrowRight size={10} />
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {/* Keyword alerts */}
          {(alertGroups || []).length > 0 && (
            <div>
              <p className="text-xs text-text-muted font-semibold mb-1 flex items-center gap-1">
                <Lightning size={10} /> 关键词
              </p>
              <div className="space-y-1.5">
                {(alertGroups || []).map((ag, i) => (
                  <motion.div
                    key={`kw-${i}`}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.25, delay: i * 0.04 }}
                    className="flex items-center gap-3 px-3.5 py-2.5 rounded-xl bg-bg-raised/40 dark:bg-bg-raised/20 border border-border-main/30 hover:border-border-main/60 transition-colors"
                  >
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-amber-500/[0.08] text-amber-500 dark:bg-amber-500/[0.10]">
                      <Lightning size={14} weight="fill" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm text-text-main font-semibold truncate">{ag.group_name || ag.chat_id || `提醒群 #${i + 1}`}</span>
                        {ag.push_target === 'ilink' && (
                          <span className="text-xs font-mono font-bold text-brand-green bg-brand-green/[0.08] dark:bg-brand-green/[0.12] px-1.5 py-px rounded flex items-center gap-0.5">
                            <PaperPlaneTilt size={8} />推送
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                        {(ag.keywords || []).map((kw, ki) => (
                          <span key={ki} className="text-xs font-mono font-medium px-1.5 py-px rounded bg-amber-500/[0.08] text-amber-600 dark:text-amber-400">{kw}</span>
                        ))}
                      </div>
                    </div>
                    <span className={`w-2 h-2 rounded-full flex-shrink-0 ${ag.enabled ? 'bg-brand-green' : 'bg-text-muted/30'}`} />
                  </motion.div>
                ))}
              </div>
            </div>
          )}

          {/* OA monitors */}
          {(oaMonitors || []).length > 0 && (
            <div>
              <p className="text-xs text-text-muted font-semibold mb-1 flex items-center gap-1">
                <Bell size={10} /> 公众号
              </p>
              <div className="space-y-1.5">
                {(oaMonitors || []).map((mg, i) => (
                  <motion.div
                    key={`oa-${mg.id}`}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.25, delay: i * 0.04 }}
                    className="flex items-center gap-3 px-3.5 py-2.5 rounded-xl bg-bg-raised/40 dark:bg-bg-raised/20 border border-border-main/30 hover:border-border-main/60 transition-colors"
                  >
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-amber-500/[0.08] text-amber-500 dark:bg-amber-500/[0.10]">
                      <Bell size={14} weight="fill" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm text-text-main font-semibold truncate">{mg.name || mg.id}</span>
                        <span className="text-xs text-text-muted">{(mg.accounts || []).length} 个号</span>
                        {mg.push_target === 'ilink' && (
                          <span className="text-xs font-mono font-bold text-brand-green bg-brand-green/[0.08] dark:bg-brand-green/[0.12] px-1.5 py-px rounded flex items-center gap-0.5">
                            <PaperPlaneTilt size={8} />推送
                          </span>
                        )}
                      </div>
                    </div>
                    <span className={`w-2 h-2 rounded-full flex-shrink-0 ${mg.enabled !== false ? 'bg-amber-500' : 'bg-text-muted/30'}`} />
                  </motion.div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── Scheduled Tasks Card — vertical list, scrollable ─── */
function ScheduledTasksCard() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/scheduled-tasks`)
      .then(r => r.json())
      .then(d => { if (d.ok) setData(d.data) })
      .catch(() => {})
  }, [])

  if (!data || data.total === 0) return (
    <div className="flex items-center gap-2 py-8 justify-center">
      <Clock size={18} className="text-text-muted" />
      <span className="text-sm text-text-muted">暂无定时任务</span>
    </div>
  )

  const enabledCount = data.tasks.filter(t => t.enabled).length

  return (
    <div className="space-y-3">
      {/* Summary row */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-text-main font-semibold">{data.total} 个任务</span>
        {enabledCount > 0 && (
          <span className="text-xs font-mono font-bold text-brand-green bg-brand-green/[0.08] px-1.5 py-px rounded">{enabledCount} 启用</span>
        )}
        {data.total - enabledCount > 0 && (
          <span className="text-xs font-mono text-text-muted bg-bg-raised px-1.5 py-px rounded">{data.total - enabledCount} 禁用</span>
        )}
      </div>

      {/* Vertical scrollable list */}
      <div className="space-y-1.5">
        {data.tasks.map((task, i) => (
          <TaskRow key={i} task={task} index={i} />
        ))}
      </div>
    </div>
  )
}

function TaskRow({ task, index }) {
  const meta = TASK_TYPE_META[task.type] || {
    icon: Clock, label: '定时任务',
    accent: 'text-brand-green', badge: 'bg-brand-green/[0.08] text-brand-green',
    leftBorder: 'border-l-brand-green/40',
  }
  const Icon = meta.icon
  const scheduleText = task.schedule || '手动触发'

  // Build detail tags for second line
  const detailTags = []
  if (task.type === 'group_digest' && task.lookback) {
    detailTags.push(task.mode === '仅未读' ? `近${task.lookback}未读` : `近${task.lookback}`)
  }
  if (task.type === 'oa_digest' && task.account_count > 0) {
    detailTags.push(`${task.account_count}个公众号`)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: task.enabled ? 1 : 0.5, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
      className={`flex items-center gap-3 px-3.5 py-2.5 rounded-xl border border-border-main/30 border-l-2 ${meta.leftBorder} bg-bg-raised/40 dark:bg-bg-raised/20 hover:border-border-main/60 transition-colors`}
    >
      <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${meta.badge}`}>
        <Icon size={13} weight="fill" />
      </div>
      <div className="flex-1 min-w-0">
        {/* Line 1: name + push tag */}
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-text-main font-semibold truncate">{task.name || meta.label}</span>
          {task.push && task.push !== '不推送' && (
            <span className="text-xs font-mono font-bold text-brand-green bg-brand-green/[0.08] dark:bg-brand-green/[0.12] px-1.5 py-px rounded flex items-center gap-0.5">
              <PaperPlaneTilt size={8} />推送
            </span>
          )}
        </div>
        {/* Line 2: schedule */}
        <div className="flex items-center gap-1 mt-0.5 text-xs">
          <Clock size={9} weight="fill" className="text-text-muted flex-shrink-0" />
          <span className="text-text-muted">{scheduleText}</span>
        </div>
        {/* Line 3: detail tags (lookback, account_count) */}
        {detailTags.length > 0 && (
          <div className="flex items-center gap-1.5 mt-0.5">
            {detailTags.map((tag, i) => (
              <span key={i} className="text-xs text-text-muted bg-bg-raised/60 dark:bg-bg-raised/40 px-1.5 py-px rounded">{tag}</span>
            ))}
          </div>
        )}
      </div>
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${task.enabled ? 'bg-brand-green' : 'bg-text-muted/30'}`} />
    </motion.div>
  )
}


/* ═══════════════════════════════════════════════════════
   Dashboard
   ═══════════════════════════════════════════════════════ */
export default function Dashboard({ status, onTabChange }) {
  const [busy, setBusy] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [diagResult, setDiagResult] = useState(null)

  const uptimeMin = Math.floor(status.uptime_sec / 60)
  const uptimeStr = uptimeMin < 60
    ? `${uptimeMin}m`
    : uptimeMin < 1440
      ? `${Math.floor(uptimeMin / 60)}h${uptimeMin % 60}m`
      : `${Math.floor(uptimeMin / 1440)}d${Math.floor((uptimeMin % 1440) / 60)}h`

  async function handleToggle() {
    setBusy(true)
    try {
      await fetch(`${API_BASE}${status.running ? '/api/stop' : '/api/start'}`, { method: 'POST' })
    } catch {}
    setTimeout(() => setBusy(false), 1000)
  }

  async function triggerDiagnostics() {
    setDiagnosing(true)
    setDiagResult(null)
    try {
      const res = await fetch(`${API_BASE}/api/onboarding/diagnose`)
      const d = await res.json()
      if (d.ok) {
        setDiagResult(d.diagnostics)
      } else {
        setDiagResult({ _error: d.error || '获取检查结果失败' })
      }
    } catch {
      setDiagResult({ _error: '无法连接后端' })
    }
    setTimeout(() => setDiagnosing(false), 850)
  }

  const groupCountStr = status.group_count < 0 ? '全部' : status.group_count === 0 ? '' : `${status.group_count} 群`

  return (
    <div className="relative z-10 space-y-5">

      {/* ── Error banner ─── */}
      {status.error && !status.error.includes('KEY_MISSING') && (
        <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2.5 px-5 py-3 bg-status-error-soft border border-status-error/20 rounded-xl text-[13px] text-status-error font-medium">
          <WarningOctagon size={14} weight="fill" />
          <span>{status.error}</span>
        </motion.div>
      )}
      {status.error && status.error.includes('KEY_MISSING') && <KeyExtractionBanner />}

      {/* ── Hero service card ─── */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...spring, duration: 0.6 }}
        className="bg-bg-card border border-border-main rounded-2xl overflow-hidden"
      >
        {/* Top accent line */}
        <div className={`h-[2px] transition-colors duration-700 ${status.running ? 'bg-brand-green/50' : 'bg-bg-inset'}`} />
        <div className="px-6 py-5 flex items-center justify-between">
          <div className="flex items-center gap-4">
            {/* Robot icon */}
            <div className={`relative w-14 h-14 rounded-2xl flex items-center justify-center transition-colors duration-500 ${
              status.running ? 'bg-brand-green/[0.08] dark:bg-brand-green/[0.06]' : 'bg-bg-inset'
            }`}>
              {status.running && (
                <motion.div
                  className="absolute inset-0 rounded-2xl border border-brand-green/15"
                  animate={{ scale: [1, 1.1, 1], opacity: [0.25, 0, 0.25] }}
                  transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }}
                />
              )}
              <Robot size={26} weight="fill" className={`relative z-10 transition-colors duration-500 ${status.running ? 'text-brand-green' : 'text-text-muted'}`} />
            </div>

            <div>
              <AnimatePresence mode="wait">
                <motion.h2
                  key={status.running ? 'on' : 'off'}
                  initial={{ y: 8, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: -8, opacity: 0 }}
                  transition={{ duration: 0.25 }}
                  className="text-[17px] font-semibold text-text-main leading-tight"
                >
                  {status.running ? '助手服务运行中' : '助手服务已停止'}
                </motion.h2>
              </AnimatePresence>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                {status.running && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-mono font-bold bg-brand-green/[0.08] text-brand-green dark:bg-brand-green/[0.10]">
                    <Cube size={9} weight="fill" />
                    {status.model_name || '-'}
                    {status.model_name && <span className="opacity-60 ml-0.5">{status.model_name}</span>}
                  </span>
                )}
                <span className="text-xs text-text-muted font-mono">
                  {status.messages_processed.toLocaleString()} 条消息
                </span>
                <span className="text-text-muted">|</span>
                <span className="text-xs text-text-muted font-mono">运行 {uptimeStr}</span>
                {groupCountStr && (
                  <>
                    <span className="text-text-muted">|</span>
                    <span className="text-xs text-text-muted font-mono">{groupCountStr}</span>
                  </>
                )}
              </div>
            </div>
          </div>

          <motion.button
            whileTap={{ scale: 0.96 }}
            whileHover={{ scale: 1.02 }}
            onClick={handleToggle} disabled={busy}
            className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-[13px] font-semibold transition-all disabled:opacity-50 cursor-pointer ${
              status.running
                ? 'bg-bg-raised text-text-main border border-border-main hover:bg-status-error-soft hover:text-status-error hover:border-status-error/20'
                : 'bg-brand-green text-white hover:opacity-90'
            }`}
          >
            {status.running ? <><Stop size={14} weight="fill" /> 停止服务</> : <><Play size={14} weight="fill" /> 启动服务</>}
          </motion.button>
        </div>
      </motion.div>

      {/* ── System health ─── */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...spring, delay: 0.08, duration: 0.5 }}
        className="bg-bg-card border border-border-main rounded-2xl overflow-hidden"
      >
        <div className="h-[2px] bg-status-info/20" />
        <div className="px-6 py-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-text-main">系统健康</h3>
          <button
            onClick={triggerDiagnostics}
            disabled={diagnosing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-text-muted bg-bg-raised border border-border-main/50 hover:text-brand-green hover:border-brand-green/20 transition-all cursor-pointer disabled:opacity-50"
          >
            <ArrowsClockwise size={12} className={diagnosing ? 'animate-spin' : ''} />
            环境检查
          </button>
        </div>

        <div className="px-6 pb-4 grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatusTile icon={Database} label="数据库" ok={status.db_ok} okText="正常" errText="异常" />
          <StatusTile icon={WechatLogo} label="微信推送" ok={status.wechat_online} okText="已连接" errText="未连接" />
          <StatusTile icon={Brain} label="AI 后端" ok={status.ai_ok} okText="可达" errText="未响应"
            detail={status.ai_ok ? (status.model_name || '') : '未检测或未成功调用'} />
          <StatusTile icon={Robot} label="助手服务" ok={status.running} okText="运行" errText="停止"
            detail={status.running ? `已运行 ${uptimeStr}` : ''} />
        </div>

        <div className="px-6 pb-3">
          <LANCard />
        </div>

        {diagResult && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            transition={{ duration: 0.3, ease: easeOut }}
            className="px-6 pb-4 pt-3 border-t border-border-main/50 space-y-1.5"
          >
            {diagResult._error ? (
              <span className="text-[13px] text-status-error font-medium">{diagResult._error}</span>
            ) : (
              Object.entries(diagResult).map(([key, item]) => (
                <div key={key} className="flex items-center gap-2">
                  {item.ok
                    ? <CheckCircle size={13} weight="fill" className="text-brand-green flex-shrink-0" />
                    : <XCircle size={13} weight="fill" className="text-status-error flex-shrink-0" />
                  }
                  <span className="text-sm text-text-main font-medium">{item.label || key}</span>
                  {!item.ok && item.detail && (
                    <span className="text-xs text-status-error/80 font-mono">{item.detail}</span>
                  )}
                </div>
              ))
            )}
          </motion.div>
        )}
      </motion.div>

      {/* ── Keyword alerts + Scheduled tasks — side by side ─── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Keyword alerts */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...spring, delay: 0.12, duration: 0.5 }}
          className="bg-bg-card border border-border-main rounded-2xl overflow-hidden flex flex-col"
          style={{ maxHeight: '55vh' }}
        >
          <div className="h-[2px] bg-amber-400/30 flex-shrink-0" />
          <div className="px-6 py-4 flex items-center gap-2 flex-shrink-0">
            <Lightning size={15} className="text-amber-500" weight="fill" />
            <h3 className="text-[14px] font-semibold text-text-main">即时提醒</h3>
          </div>
          <div className="px-6 pb-4 overflow-y-auto scrollbar-thin">
            <KeywordAlertCard onTabChange={onTabChange} />
          </div>
        </motion.div>

        {/* Scheduled tasks */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...spring, delay: 0.16, duration: 0.5 }}
          className="bg-bg-card border border-border-main rounded-2xl overflow-hidden flex flex-col"
          style={{ maxHeight: '55vh' }}
        >
          <div className="h-[2px] bg-brand-green/20 flex-shrink-0" />
          <div className="px-6 py-4 flex items-center gap-2 flex-shrink-0">
            <Clock size={15} className="text-text-muted" weight="fill" />
            <h3 className="text-[14px] font-semibold text-text-main">定时任务</h3>
          </div>
          <div className="px-6 pb-4 overflow-y-auto scrollbar-thin">
            <ScheduledTasksCard />
          </div>
        </motion.div>
      </div>
    </div>
  )
}


// ── Key extraction banner ─────────────────────────────

const API = API_BASE
const EXTRACTION_PHASE_MAP = {
  hooking:         { label: '正在尝试连接...' },
  waiting_exit:    { label: '请退出程序' },
  waiting_login:   { label: '等待登录' },
  hooking_restart: { label: '正在连接...' },
}

function KeyExtractionBanner() {
  const [phase, setPhase] = useState('idle')
  const [msg, setMsg] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const pollRef = useRef(null)

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  async function handleExtract() {
    setBusy(true)
    setPhase('extracting')
    setMsg('正在准备...')
    setResult(null)
    try {
      await fetch(`${API}/api/onboarding/reset`, { method: 'POST' })
      const startRes = await fetch(`${API}/api/onboarding/step1`, { method: 'POST' })
      const start = await startRes.json()
      if (!start.ok) {
        setPhase('error')
        setMsg(start.message || '启动失败，请稍后重试')
        setBusy(false)
        return
      }

      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`${API}/api/onboarding/step1-status`)
          const s = await res.json()

          if (s.phase === 'waiting_exit' || s.phase === 'waiting_login'
              || s.phase === 'hooking' || s.phase === 'hooking_restart') {
            setPhase(s.phase)
            setMsg(s.message || '')
          } else if (s.phase === 'done' && s.result) {
            clearInterval(pollRef.current)
            pollRef.current = null
            setPhase('done')
            setMsg('')
            setResult(s.result)
            setBusy(false)
          } else if (s.phase === 'timeout' || s.phase === 'error') {
            clearInterval(pollRef.current)
            pollRef.current = null
            setPhase(s.phase)
            setMsg(s.message || (s.phase === 'timeout' ? '超时，请重试' : '提取失败'))
            setBusy(false)
          }
        } catch {}
      }, 1000)
    } catch {
      setPhase('error')
      setMsg('无法连接服务器')
      setBusy(false)
    }
  }

  const phaseMeta = EXTRACTION_PHASE_MAP[phase]
  const isDone = phase === 'done'

  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex flex-col gap-3.5 p-5 rounded-xl border transition-all duration-500 ${
        isDone
          ? 'bg-brand-green/[0.06] border-brand-green/20 text-brand-green-hover dark:text-brand-green'
          : 'bg-status-error-soft border-status-error/20 text-status-error'
      }`}
    >
      <div className="flex items-center gap-2 text-[13px] font-semibold">
        {isDone ? (
          <CheckCircle size={16} weight="fill" className="text-brand-green" />
        ) : (
          <WarningOctagon size={14} weight="fill" />
        )}
        <span>{isDone ? '连接成功 - 请重启机器人' : '未连接 - 需要获取连接凭证才能读取消息'}</span>
      </div>

      {phase !== 'idle' && phase !== 'done' && phase !== 'timeout' && phase !== 'error' && phaseMeta && (
        <motion.div initial={{opacity:0}} animate={{opacity:1}}
          className="flex items-center gap-3 p-3.5 rounded-lg border border-status-info/20 bg-status-info-soft">
          <Spinner size={18} weight="bold" className="animate-spin text-status-info" />
          <div>
            <p className="text-[13px] font-semibold text-status-info">{phaseMeta.label}</p>
            <p className="text-xs text-status-info/80 mt-0.5 font-medium">{msg}</p>
          </div>
        </motion.div>
      )}

      {phase === 'done' && result && (
        <motion.div initial={{opacity:0,y:-4}} animate={{opacity:1,y:0}} className="grid grid-cols-2 gap-3">
          <div className="bg-bg-raised border border-border-main rounded-lg p-3.5">
            <p className="text-xs text-text-muted mb-1 font-medium">微信账号</p>
            <p className="text-sm font-mono text-text-main font-bold truncate">{result.wxid || '-'}</p>
          </div>
          <div className="bg-bg-raised border border-border-main rounded-lg p-3.5">
            <p className="text-xs text-text-muted mb-1 font-medium">数据配置</p>
            <p className="text-xs font-mono text-text-main font-semibold truncate">{result.db_path ? result.db_path.split('\\').slice(-2).join('\\') : '-'}</p>
          </div>
        </motion.div>
      )}

      {(phase === 'error' || phase === 'timeout') && (
        <motion.div initial={{opacity:0,y:-4}} animate={{opacity:1,y:0}}
          className="flex items-start gap-2.5 p-3.5 bg-status-warn-soft border border-status-warn/20 rounded-lg">
          <XCircle size={18} weight="fill" className="text-status-warn shrink-0 mt-0.5" />
          <div>
            <p className="text-[13px] text-status-warn font-semibold">{phase === 'timeout' ? '获取超时' : '提取失败'}</p>
            <p className="text-xs text-status-warn/85 mt-0.5 font-medium">{msg}</p>
          </div>
        </motion.div>
      )}

      {phase !== 'done' && (
        <motion.button
          whileTap={{ scale: 0.96 }} whileHover={{ scale: 1.02 }}
          onClick={handleExtract}
          disabled={busy}
          className="flex items-center justify-center gap-2 w-44 py-2.5 rounded-lg text-[13px] font-semibold transition-all duration-200 disabled:opacity-50 cursor-pointer bg-status-error hover:opacity-90 text-white"
        >
          {busy ? (
            <><Spinner size={13} weight="bold" className="animate-spin" /> 连接中...</>
          ) : phase === 'timeout' || phase === 'error' ? (
            <><Key size={13} weight="fill" /> 重试</>
          ) : (
            <><Key size={13} weight="fill" /> 重新连接</>
          )}
        </motion.button>
      )}
    </motion.div>
  )
}
