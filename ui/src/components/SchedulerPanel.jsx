import { useState, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Clock, Play, Trash, Plus, Pencil, Pause, Eye, EyeSlash, X, CaretDown, CaretUp, ChatCircleText } from '@phosphor-icons/react'
import { Toggle, Input, API_BASE } from './SharedComponents'
import { CRON_PRESETS, validateCronExpr, getNextTriggers, formatLocalTime } from '../utils/cron'

// ── Status helpers ─────────────────────────────────────────────────
const STATUS_STYLES = {
  idle:      { dot: 'bg-text-muted',     text: 'text-text-muted',    label: '空闲' },
  running:   { dot: 'bg-brand-green',    text: 'text-brand-green',   label: '运行中' },
  error:     { dot: 'bg-[#d45656]',      text: 'text-[#d45656]',     label: '错误' },
  disabled:  { dot: 'bg-text-muted/50',  text: 'text-text-muted',    label: '已暂停' },
}

function StatusBadge({ task }) {
  const s = task.enabled === false
    ? STATUS_STYLES.disabled
    : STATUS_STYLES[task.status] || STATUS_STYLES.idle
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  )
}

// ── Args JSON display ─────────────────────────────────────────────
function ArgsDisplay({ args }) {
  if (!args || Object.keys(args).length === 0) {
    return <span className="text-text-muted text-xs">(无参数)</span>
  }
  return (
    <pre className="bg-bg-raised/60 border border-border-main rounded-lg p-2.5 text-xs font-mono text-text-secondary whitespace-pre-wrap leading-relaxed">
      {JSON.stringify(args, null, 2)}
    </pre>
  )
}

// ── Task card ─────────────────────────────────────────────────────
function TaskCard({ task, skills, onToggle, onDelete, onRunNow, onEdit, onCopy }) {
  const [expanded, setExpanded] = useState(false)
  const skill = skills.find(s => s.name === task.skill)

  return (
    <div className={`border rounded-xl overflow-hidden transition-colors ${expanded ? 'border-brand-green/30 bg-bg-card' : 'border-border-main bg-bg-card hover:border-text-muted/30'}`}>
      <div className="flex items-center gap-3 p-4 cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div className="w-9 h-9 rounded-lg bg-brand-green-light/20 flex items-center justify-center text-brand-green flex-shrink-0">
          <Clock size={16} weight="regular" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-main">{task.name}</span>
            <StatusBadge task={task} />
          </div>
          <p className="text-xs text-text-muted mt-0.5 truncate">
            <code className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-bg-raised/60 text-brand-green/80">
              {task.skill}
            </code>
            {skill && <span className="ml-1">· {skill.description?.slice(0, 40)}</span>}
            <span className="ml-1">·</span>
            <code className="font-mono text-[11px] text-text-secondary">
              {task.cron?.replace(/\n/g, ' / ')}
            </code>
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="hidden sm:inline text-[11px] text-text-muted font-mono">
            执行 <strong className="text-text-main">{task.run_count || 0}</strong>
            {' · '}失败 <strong className={(task.error_count || 0) > 0 ? 'text-[#d45656]' : 'text-text-main'}>
              {task.error_count || 0}
            </strong>
          </span>
          <Toggle enabled={task.enabled !== false} onChange={() => onToggle(task.id, task.enabled === false)} />
          <button
            onClick={(e) => { e.stopPropagation(); onRunNow(task.id) }}
            className="p-1.5 rounded-full text-text-muted hover:text-brand-green hover:bg-brand-green-light/20 transition-colors cursor-pointer"
            title="立即执行"
          >
            <Play size={14} weight="fill" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onEdit(task) }}
            className="p-1.5 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
            title="编辑"
          >
            <Pencil size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(task) }}
            className="p-1.5 rounded-full text-text-muted hover:text-[#d45656] hover:bg-[#d45656]/10 transition-colors cursor-pointer"
            title="删除"
          >
            <Trash size={14} />
          </button>
        </div>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-main"
          >
            <div className="p-4 space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-text-muted mb-1">任务 ID</div>
                  <code className="font-mono text-text-secondary">{task.id}</code>
                </div>
                <div>
                  <div className="text-text-muted mb-1">创建时间</div>
                  <span className="text-text-secondary">{task.created_at || '—'}</span>
                </div>
                <div>
                  <div className="text-text-muted mb-1">推送目标</div>
                  <span className="text-text-secondary">
                    {task.push?.enabled === false ? '不推送' : (task.push?.target || 'iLink')}
                  </span>
                </div>
                <div>
                  <div className="text-text-muted mb-1">上次执行</div>
                  <span className="text-text-secondary">{task.last_run || '—'}</span>
                </div>
              </div>

              <div>
                <div className="text-text-muted mb-1">Cron 表达式</div>
                <pre className="bg-bg-raised/60 border border-border-main rounded-lg p-2 text-xs font-mono text-brand-green/90 whitespace-pre-wrap">
                  {task.cron}
                </pre>
              </div>

              <div>
                <div className="text-text-muted mb-1">参数 (args)</div>
                <ArgsDisplay args={task.args} />
              </div>

              <div className="flex items-center gap-2 pt-2 border-t border-border-main">
                <button
                  onClick={() => onToggle(task.id, task.enabled === false)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-bg-raised text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  {task.enabled === false ? <Play size={12} /> : <Pause size={12} />}
                  {task.enabled === false ? '启用' : '暂停'}
                </button>
                <button
                  onClick={() => onRunNow(task.id)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-bg-raised text-xs text-text-muted hover:text-brand-green transition-colors cursor-pointer"
                >
                  <Play size={12} /> 立即执行
                </button>
                <button
                  onClick={() => onCopy(task)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-bg-raised text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  <Plus size={12} /> 复制为新任务
                </button>
                <button
                  onClick={() => onDelete(task)}
                  className="ml-auto flex items-center gap-1 px-3 py-1.5 rounded-full bg-[#d45656]/10 text-xs text-[#d45656] hover:bg-[#d45656]/20 transition-colors cursor-pointer"
                >
                  <Trash size={12} /> 删除
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ── Task creator / editor ─────────────────────────────────────────
function TaskForm({ skills, initial, onSave, onCancel }) {
  const [name, setName] = useState(initial?.name || '')
  const [skill, setSkill] = useState(initial?.skill || (skills[0]?.name || ''))
  const [cronExpr, setCronExpr] = useState(initial?.cron || '0 8 * * *')
  const [pushEnabled, setPushEnabled] = useState(initial?.push?.enabled !== false)
  const [enabled, setEnabled] = useState(initial?.enabled !== false)
  const [argsJson, setArgsJson] = useState(
    initial?.args ? JSON.stringify(initial.args, null, 2) : '{}'
  )
  const [argsError, setArgsError] = useState('')
  const [parsedArgs, setParsedArgs] = useState({})
  const [touched, setTouched] = useState(false)

  const selectedSkill = useMemo(
    () => skills.find(s => s.name === skill),
    [skills, skill]
  )

  // 解析 args（独立 useEffect，避免 render 期内 setState 导致死循环）
  useEffect(() => {
    if (!touched) {
      setArgsError('')
      return
    }
    if (!argsJson.trim()) {
      setParsedArgs({})
      setArgsError('')
      return
    }
    try {
      const v = JSON.parse(argsJson)
      if (typeof v !== 'object' || Array.isArray(v) || v === null) {
        setParsedArgs({})
        setArgsError('参数必须是 JSON 对象')
      } else {
        setParsedArgs(v)
        setArgsError('')
      }
    } catch (e) {
      setParsedArgs({})
      setArgsError(`JSON 解析失败: ${e.message}`)
    }
  }, [argsJson, touched])

  const cronError = touched ? validateCronExpr(cronExpr) : ''
  const nameError = touched && !name.trim() ? '任务名称不能为空' : ''

  const nextTriggers = useMemo(() => {
    if (cronError) return []
    try {
      return getNextTriggers(cronExpr, 3)
    } catch {
      return []
    }
  }, [cronExpr, cronError])

  const canSave = name.trim() && skill && !cronError && !argsError

  function handleSubmit() {
    setTouched(true)
    if (!canSave) return
    onSave({
      name: name.trim(),
      skill,
      cron: cronExpr.trim(),
      args: parsedArgs,
      push_enabled: pushEnabled,
      push_target: 'ilink',
      enabled,
    })
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="border border-brand-green/30 rounded-xl bg-bg-card overflow-hidden mb-6"
    >
      <div className="flex items-center justify-between px-5 py-3 border-b border-border-main">
        <h4 className="text-sm font-semibold text-text-main">
          {initial?.id ? '编辑定时任务' : '新建定时任务'}
        </h4>
        <button onClick={onCancel} className="p-1 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer">
          <X size={14} />
        </button>
      </div>

      <div className="p-5 space-y-4">
        {/* 基本信息 */}
        <div>
          <div className="text-[11px] uppercase tracking-wider text-brand-green font-semibold mb-2.5">基本信息</div>
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">任务名称 <span className="text-[#d45656]">*</span></label>
              <Input value={name} onChange={setName} placeholder="例：36氪早报" />
              {nameError && <p className="text-xs text-[#d45656] mt-1">{nameError}</p>}
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Skill <span className="text-[#d45656]">*</span></label>
              <select
                value={skill}
                onChange={(e) => setSkill(e.target.value)}
                disabled={!skills.length}
                className="w-full bg-bg-raised border border-border-main rounded-full px-4 py-2.5 text-sm text-text-main
                  focus:outline-none focus:border-brand-green cursor-pointer disabled:opacity-50"
              >
                {!skills.length && <option>(暂无 skill，请先在 data/skills/ 创建)</option>}
                {skills.map(s => (
                  <option key={s.name} value={s.name}>
                    {s.name} · {s.description?.slice(0, 50)}
                  </option>
                ))}
              </select>
              {selectedSkill && (
                <p className="text-[11px] text-text-muted mt-1.5">
                  类型: <code className="font-mono">{selectedSkill.type}</code>
                  {selectedSkill.args && Object.keys(selectedSkill.args).length > 0 && (
                    <> · 参数 schema:
                      <code className="font-mono ml-1">
                        {Object.keys(selectedSkill.args).join(', ')}
                      </code>
                    </>
                  )}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Cron */}
        <div>
          <div className="text-[11px] uppercase tracking-wider text-brand-green font-semibold mb-2.5">时间设置</div>

          <div className="flex flex-wrap gap-1.5 mb-2.5">
            {CRON_PRESETS.map((p, i) => (
              <button
                key={i}
                onClick={() => setCronExpr(p.cron)}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors cursor-pointer
                  ${cronExpr === p.cron
                    ? 'bg-brand-green/15 border-brand-green/40 text-brand-green font-medium'
                    : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'
                  }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <label className="block text-xs text-text-muted mb-1">Cron 表达式 <span className="text-[#d45656]">*</span></label>
          <textarea
            value={cronExpr}
            onChange={(e) => { setCronExpr(e.target.value); setTouched(true) }}
            onBlur={() => setTouched(true)}
            rows={2}
            placeholder="0 8 * * *"
            className={`w-full bg-bg-raised border rounded-full px-4 py-2 text-sm text-text-main font-mono
              focus:outline-none focus:ring-1 focus:ring-brand-green/30 resize-none
              ${cronError ? 'border-[#d45656]' : 'border-border-main focus:border-brand-green'}`}
          />
          <p className="text-[11px] text-text-muted mt-1.5">
            格式: <code className="font-mono">分 时 日 月 周</code> · 周日=0 · 支持多行、*/N、N-M、N,M,K
          </p>
          {cronError ? (
            <p className="text-xs text-[#d45656] mt-1.5">⚠ {cronError}</p>
          ) : nextTriggers.length > 0 && (
            <p className="text-[11px] text-brand-green mt-1.5">
              ⚡ 下次触发: {nextTriggers.map(formatLocalTime).join(' · ')}
            </p>
          )}
        </div>

        {/* Args JSON */}
        <div>
          <div className="text-[11px] uppercase tracking-wider text-brand-green font-semibold mb-2.5">参数 (JSON)</div>
          <textarea
            value={argsJson}
            onChange={(e) => { setArgsJson(e.target.value); setTouched(true) }}
            onBlur={() => setTouched(true)}
            rows={4}
            placeholder='{"url": "https://..."}'
            className={`w-full bg-bg-raised border rounded-lg px-3 py-2 text-xs text-text-main font-mono
              focus:outline-none focus:ring-1 focus:ring-brand-green/30 resize-none
              ${argsError ? 'border-[#d45656]' : 'border-border-main focus:border-brand-green'}`}
          />
          {argsError ? (
            <p className="text-xs text-[#d45656] mt-1">⚠ {argsError}</p>
          ) : (
            <p className="text-[11px] text-text-muted mt-1">
              按所选 skill 的 args schema 填入，自由格式 JSON 对象
            </p>
          )}
        </div>

        {/* Toggles */}
        <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border-main">
          <div className="flex items-center gap-2.5">
            <Toggle enabled={pushEnabled} onChange={setPushEnabled} />
            <span className="text-xs text-text-main">推送到 iLink (微信)</span>
          </div>
          <div className="flex items-center gap-2.5">
            <Toggle enabled={enabled} onChange={setEnabled} />
            <span className="text-xs text-text-main">创建后立即启用</span>
          </div>
        </div>

        {/* Footer */}
        <div className="flex gap-2 pt-2">
          <button
            onClick={handleSubmit}
            disabled={!canSave}
            className="flex-1 py-2.5 rounded-full bg-brand-green text-white text-sm font-semibold hover:bg-brand-green-hover transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {initial?.id ? '保存修改' : '创建任务'}
          </button>
          <button
            onClick={onCancel}
            className="px-6 py-2.5 rounded-full bg-bg-raised text-text-muted text-sm font-medium hover:text-text-main transition-colors cursor-pointer"
          >
            取消
          </button>
        </div>
      </div>
    </motion.div>
  )
}

// ── Skill library ──────────────────────────────────────────────────
function SkillLibrary({ skills }) {
  const [selectedSkill, setSelectedSkill] = useState(skills[0]?.name || '')
  const [search, setSearch] = useState('')

  const filteredSkills = useMemo(() => {
    if (!search.trim()) return skills
    const q = search.toLowerCase()
    return skills.filter(s =>
      s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
    )
  }, [skills, search])

  const selected = useMemo(
    () => skills.find(s => s.name === selectedSkill),
    [skills, selectedSkill]
  )

  useEffect(() => {
    if (!selectedSkill && skills[0]) setSelectedSkill(skills[0].name)
    if (selected && !skills.find(s => s.name === selectedSkill)) {
      setSelectedSkill(skills[0]?.name || '')
    }
  }, [skills])

  if (!skills.length) {
    return (
      <div className="text-center py-12">
        <div className="text-text-muted text-sm">暂无可用 skill</div>
        <p className="text-xs text-text-muted/70 mt-2">
          在 <code className="font-mono px-1.5 py-0.5 bg-bg-raised rounded">data/skills/</code> 目录下创建 SKILL.md
        </p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-3">
      {/* 左侧列表 */}
      <div className="border border-border-main rounded-xl bg-bg-card overflow-hidden">
        <div className="p-2 border-b border-border-main">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="🔍 搜索 skill..."
            className="w-full bg-bg-raised border border-border-main rounded-full px-3 py-1.5 text-xs text-text-main
              focus:outline-none focus:border-brand-green"
          />
        </div>
        <div className="max-h-[500px] overflow-y-auto p-2">
          {filteredSkills.map(s => (
            <button
              key={s.name}
              onClick={() => setSelectedSkill(s.name)}
              className={`w-full text-left p-2.5 rounded-lg mb-1 transition-colors cursor-pointer
                ${selectedSkill === s.name
                  ? 'bg-brand-green-light/15 border border-brand-green/30'
                  : 'hover:bg-bg-raised border border-transparent'
                }`}
            >
              <div className={`text-[13px] font-medium ${selectedSkill === s.name ? 'text-brand-green' : 'text-text-main'}`}>
                {s.name}
                <span className={`ml-1.5 inline-block text-[10px] px-1.5 py-0.5 rounded font-medium
                  ${s.type === 'script' ? 'bg-brand-green/15 text-brand-green' : 'bg-[#a78bfa]/20 text-[#a78bfa]'}`}>
                  {s.type}
                </span>
              </div>
              {s.description && (
                <div className="text-[11px] text-text-muted mt-1 line-clamp-2">
                  {s.description}
                </div>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* 右侧详情 */}
      <div className="border border-border-main rounded-xl bg-bg-card p-5">
        {selected ? (
          <>
            <div className="flex items-center gap-2 mb-4">
              <h3 className="text-base font-semibold text-text-main">{selected.name}</h3>
              <span className={`text-[10px] px-2 py-0.5 rounded font-medium
                ${selected.type === 'script'
                  ? 'bg-brand-green/15 text-brand-green'
                  : 'bg-[#a78bfa]/20 text-[#a78bfa]'}`}>
                {selected.type}
              </span>
            </div>

            <div className="space-y-3 text-xs">
              {selected.description && (
                <div>
                  <div className="text-text-muted mb-1">描述</div>
                  <div className="text-text-secondary">{selected.description}</div>
                </div>
              )}

              {selected.args && Object.keys(selected.args).length > 0 && (
                <div>
                  <div className="text-text-muted mb-1">参数 schema</div>
                  <div className="bg-bg-raised/60 border border-border-main rounded-lg p-2.5 font-mono">
                    {Object.entries(selected.args).map(([name, def]) => (
                      <div key={name} className="py-1">
                        <span className="text-brand-green/90">{name}</span>
                        <span className="text-text-muted ml-2">{def.type || 'any'}</span>
                        {def.required && <span className="text-[#d45656] ml-1 text-[10px]">必填</span>}
                        {def.default !== undefined && (
                          <span className="text-text-muted ml-2 text-[10px]">= {JSON.stringify(def.default)}</span>
                        )}
                        {def.description && (
                          <div className="text-text-muted/70 text-[11px] mt-0.5">{def.description}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div className="text-text-muted mb-1">路径</div>
                <code className="font-mono text-text-secondary">
                  data/skills/{selected.name}/SKILL.md
                </code>
              </div>
            </div>
          </>
        ) : (
          <div className="text-center py-12 text-text-muted text-sm">
            选择左侧 skill 查看详情
          </div>
        )}
      </div>
    </div>
  )
}

// ── Execution history ──────────────────────────────────────────────
function ExecutionHistory() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState({ status: 'all', search: '' })

  async function load() {
    setLoading(true)
    try {
      // 只拉 cron 类型的任务（type=cron = task_type='cron'）
      const res = await fetch(`${API_BASE}/api/tasks?type=cron&limit=100`)
      const data = await res.json()
      if (data.ok) setTasks(data.tasks || [])
    } catch {}
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const filtered = useMemo(() => {
    let result = tasks
    if (filter.status !== 'all') {
      result = result.filter(t => t.status === filter.status)
    }
    if (filter.search.trim()) {
      const q = filter.search.toLowerCase()
      result = result.filter(t => {
        const haystack = `${t.group_name || ''} ${t.progress || ''} ${t.result || ''} ${t.error || ''}`.toLowerCase()
        return haystack.includes(q)
      })
    }
    return result
  }, [tasks, filter])

  // 按天分组
  const grouped = useMemo(() => {
    const groups = {}
    filtered.forEach(t => {
      if (!t.created_at) return
      const day = t.created_at.slice(0, 10)
      if (!groups[day]) groups[day] = []
      groups[day].push(t)
    })
    return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
  }, [filtered])

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        {[
          { id: 'all', label: '全部' },
          { id: 'completed', label: '✅ 成功' },
          { id: 'failed', label: '❌ 失败' },
          { id: 'running', label: '⏳ 运行中' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setFilter(f => ({ ...f, status: tab.id }))}
            className={`text-xs px-3 py-1.5 rounded-full transition-colors cursor-pointer
              ${filter.status === tab.id
                ? 'bg-brand-green/15 text-brand-green font-medium'
                : 'bg-bg-raised text-text-muted hover:text-text-main'}`}
          >
            {tab.label}
          </button>
        ))}
        <input
          value={filter.search}
          onChange={(e) => setFilter(f => ({ ...f, search: e.target.value }))}
          placeholder="🔍 按定时任务名搜索..."
          className="flex-1 bg-bg-raised border border-border-main rounded-full px-3 py-1.5 text-xs text-text-main
            focus:outline-none focus:border-brand-green"
        />
        <button
          onClick={load}
          className="text-xs px-3 py-1.5 rounded-full bg-bg-raised text-text-muted hover:text-text-main transition-colors cursor-pointer"
        >
          刷新
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-5 h-5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
        </div>
      ) : grouped.length === 0 ? (
        <div className="text-center py-12 text-text-muted text-sm">暂无执行记录</div>
      ) : (
        <div className="space-y-4">
          {grouped.map(([day, dayTasks]) => (
            <div key={day}>
              <div className="text-xs font-semibold text-text-muted mb-2 pb-1.5 border-b border-border-main">
                📅 {day}
              </div>
              <div className="space-y-1.5">
                {dayTasks.map(t => {
                  const isFail = t.status === 'failed'
                  const snippet = t.error || t.result || t.progress || '(无输出)'
                  return (
                    <div
                      key={t.id}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-raised/40 hover:bg-bg-raised transition-colors text-xs"
                    >
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isFail ? 'bg-[#d45656]' : 'bg-brand-green'}`} />
                      <span className="font-mono text-text-muted w-16 flex-shrink-0">
                        {t.created_at?.slice(11, 19) || '--:--:--'}
                      </span>
                      <span className="text-text-main flex-shrink-0 max-w-[120px] truncate">
                        {t.group_name || 'task'}
                      </span>
                      <span className={`flex-1 truncate font-mono ${isFail ? 'text-[#d45656]' : 'text-text-secondary'}`}>
                        {snippet.slice(0, 120)}
                      </span>
                      <span className="text-text-muted text-[10px] flex-shrink-0">
                        {t.finished_at && t.created_at && (() => {
                          try {
                            const ms = new Date(t.finished_at) - new Date(t.created_at)
                            return `${(ms / 1000).toFixed(1)}s`
                          } catch { return '' }
                        })()}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Confirm dialog (inline) ────────────────────────────────────────
function ConfirmDialog({ title, message, confirmLabel = '确认', danger = true, onConfirm, onCancel }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="absolute inset-0 bg-black/50" onClick={onCancel} />
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="relative bg-bg-card border border-border-main rounded-xl shadow-xl p-6 max-w-sm w-full"
      >
        <div className="flex items-center gap-3 mb-2">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0
            ${danger ? 'bg-[#d45656]/15 text-[#d45656]' : 'bg-brand-green/15 text-brand-green'}`}>
            ⚠
          </div>
          <h3 className="text-sm font-semibold text-text-main">{title}</h3>
        </div>
        <p className="text-xs text-text-muted leading-relaxed pl-12 mb-5">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-1.5 rounded-full bg-bg-raised text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer">
            取消
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-1.5 rounded-full text-xs font-medium transition-colors cursor-pointer
              ${danger ? 'bg-[#d45656] text-white hover:bg-[#d45656]/90' : 'bg-brand-green text-white hover:bg-brand-green-hover'}`}
          >
            {confirmLabel}
          </button>
        </div>
      </motion.div>
    </motion.div>
  )
}

// ── Main panel ─────────────────────────────────────────────────────
export default function SchedulerPanel({ section = 'tasks', onSectionChange = () => {} }) {
  const [tasks, setTasks] = useState([])
  const [skills, setSkills] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingTask, setEditingTask] = useState(null)
  const [deleteTask, setDeleteTask] = useState(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('enabled')

  useEffect(() => {
    loadAll()
  }, [])

  async function loadAll() {
    setLoading(true)
    try {
      const [tasksRes, skillsRes] = await Promise.all([
        fetch(`${API_BASE}/api/scheduler/tasks`).then(r => r.json()),
        fetch(`${API_BASE}/api/skills`).then(r => r.json()),
      ])
      if (tasksRes.ok) setTasks(tasksRes.data || [])
      if (skillsRes.ok) setSkills(skillsRes.data || [])
    } catch {}
    setLoading(false)
  }

  async function loadTasks() {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks`)
      const data = await res.json()
      if (data.ok) setTasks(data.data || [])
    } catch {}
  }

  const filteredTasks = useMemo(() => {
    let result = tasks
    if (statusFilter === 'enabled') result = result.filter(t => t.enabled !== false)
    if (statusFilter === 'disabled') result = result.filter(t => t.enabled === false)
    if (statusFilter === 'error') result = result.filter(t => (t.error_count || 0) > 0)
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(t => t.name?.toLowerCase().includes(q) || t.skill?.toLowerCase().includes(q))
    }
    return result
  }, [tasks, search, statusFilter])

  async function handleSave(formData) {
    try {
      const url = editingTask
        ? `${API_BASE}/api/scheduler/tasks/${editingTask.id}`
        : `${API_BASE}/api/scheduler/tasks`
      const method = editingTask ? 'PUT' : 'POST'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      })
      const data = await res.json()
      if (data.ok) {
        setShowForm(false)
        setEditingTask(null)
        loadTasks()
      } else {
        alert('保存失败: ' + (data.error || '未知错误'))
      }
    } catch (e) {
      alert('保存失败: ' + e.message)
    }
  }

  async function handleDelete(task) {
    setDeleteTask(task)
  }

  async function confirmDelete() {
    if (!deleteTask) return
    try {
      await fetch(`${API_BASE}/api/scheduler/tasks/${deleteTask.id}`, { method: 'DELETE' })
      setDeleteTask(null)
      loadTasks()
    } catch (e) {
      alert('删除失败: ' + e.message)
    }
  }

  async function handleToggle(id, shouldEnable) {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: shouldEnable }),
      })
      const data = await res.json()
      if (data.ok) loadTasks()
    } catch {}
  }

  async function handleRunNow(id) {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${id}/run`, { method: 'POST' })
      const data = await res.json()
      if (!data.ok) alert('执行失败: ' + (data.error || '未知错误'))
    } catch (e) {
      alert('执行失败: ' + e.message)
    }
  }

  function handleEdit(task) {
    setEditingTask(task)
    setShowForm(true)
  }

  function handleCopy(task) {
    setEditingTask({ ...task, id: null, name: `${task.name} (副本)` })
    setShowForm(true)
  }

  const enabledCount = tasks.filter(t => t.enabled !== false).length

  return (
    <div>
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm bg-brand-green" />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">定时任务</h3>
          <Clock size={16} className="text-text-muted" />
          <span className="text-xs text-text-muted">· 共 {tasks.length} 个任务 · {enabledCount} 个启用</span>
        </div>
        <p className="text-xs text-text-muted leading-relaxed pl-4">
          通用定时调度 · 通过 skill 执行
        </p>
      </div>

      {/* Tasks Section */}
      {section === 'tasks' && (
        <>
          {showForm && (
            <TaskForm
              skills={skills}
              initial={editingTask}
              onSave={handleSave}
              onCancel={() => { setShowForm(false); setEditingTask(null) }}
            />
          )}

          <div className="flex items-center gap-2 mb-3">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="🔍 搜索任务名或 skill..."
              className="flex-1 bg-bg-raised border border-border-main rounded-full px-4 py-2 text-sm text-text-main
                focus:outline-none focus:border-brand-green"
            />
            {[
              { id: 'all', label: '全部' },
              { id: 'enabled', label: '启用' },
              { id: 'disabled', label: '暂停' },
              { id: 'error', label: '⚠ 有失败' },
            ].map(f => (
              <button
                key={f.id}
                onClick={() => setStatusFilter(f.id)}
                className={`text-xs px-3 py-1.5 rounded-full transition-colors cursor-pointer
                  ${statusFilter === f.id
                    ? 'bg-brand-green/15 text-brand-green font-medium'
                    : 'bg-bg-raised text-text-muted hover:text-text-main'}`}
              >
                {f.label}
              </button>
            ))}
            <button
              onClick={() => { setEditingTask(null); setShowForm(true) }}
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-brand-green/10 border border-brand-green/30 text-brand-green hover:bg-brand-green/15 transition-colors cursor-pointer"
            >
              <Plus size={12} weight="bold" /> 新建任务
            </button>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-5 h-5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
            </div>
          ) : filteredTasks.length === 0 ? (
            <div className="border border-dashed border-border-main rounded-xl py-12 text-center">
              <Clock size={28} className="text-text-muted/40 mx-auto mb-2" />
              <p className="text-sm text-text-muted">
                {tasks.length === 0 ? '暂无定时任务' : '没有匹配的任务'}
              </p>
              {tasks.length === 0 && (
                <button
                  onClick={() => { setEditingTask(null); setShowForm(true) }}
                  className="mt-3 text-xs px-3 py-1.5 rounded-full bg-brand-green/10 border border-brand-green/30 text-brand-green hover:bg-brand-green/15 transition-colors cursor-pointer"
                >
                  + 新建第一个任务
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-2.5">
              {filteredTasks.map(task => (
                <TaskCard
                  key={task.id}
                  task={task}
                  skills={skills}
                  onToggle={handleToggle}
                  onDelete={handleDelete}
                  onRunNow={handleRunNow}
                  onEdit={handleEdit}
                  onCopy={handleCopy}
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* Skills Tab */}
      {section === 'skills' && <SkillLibrary skills={skills} />}

      {/* History Section */}
      {section === 'history' && <ExecutionHistory />}

      {/* Delete confirmation */}
      <AnimatePresence>
        {deleteTask && (
          <ConfirmDialog
            title="删除任务？"
            message={`「${deleteTask.name}」任务及所有执行记录将被永久删除，此操作不可撤销。`}
            confirmLabel="确认删除"
            onConfirm={confirmDelete}
            onCancel={() => setDeleteTask(null)}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
