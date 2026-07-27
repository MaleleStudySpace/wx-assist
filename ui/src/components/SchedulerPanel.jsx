import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Clock, Play, Trash, Plus, Pencil, Pause, Eye, EyeSlash, X, CaretDown, CaretUp, ChatCircleText, Spinner } from '@phosphor-icons/react'
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
function TaskCard({ task, skills, onToggle, onDelete, onRunNow, onEdit, onCopy, runningNow }) {
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
            onClick={(e) => { e.stopPropagation(); if (!runningNow?.has(task.id)) onRunNow(task.id) }}
            disabled={runningNow?.has(task.id)}
            className={`p-1.5 rounded-full transition-colors cursor-pointer disabled:cursor-wait ${
              runningNow?.has(task.id)
                ? 'text-brand-green bg-brand-green-light/20 animate-pulse'
                : 'text-text-muted hover:text-brand-green hover:bg-brand-green-light/20'
            }`}
            title={runningNow?.has(task.id) ? '执行中...' : '立即执行'}
          >
            {runningNow?.has(task.id) ? <Spinner size={14} className="animate-spin" /> : <Play size={14} weight="fill" />}
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
  const formRef = useRef(null)
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

  // 挂载后自动滚动到表单
  useEffect(() => {
    formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  const selectedSkill = useMemo(
    () => skills.find(s => s.name === skill),
    [skills, skill]
  )

  // 解析 args（独立 useEffect，避免 render 期内 setState 导致死循环）
  // 注意：解析不依赖 touched，否则编辑模式初始值不显示
  useEffect(() => {
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
  }, [argsJson])

  // touched 后才显示校验错误（避免首次打开就一片红）
  const displayError = touched ? argsError : ''
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

  // 参数参考表（展示 skill 期望的 args schema）
  function ArgsRefTable() {
    const schema = selectedSkill?.args
    if (!schema || Object.keys(schema).length === 0) return null
    return (
      <div className="bg-bg-raised/60 border border-border-main rounded-lg overflow-hidden">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-border-main">
              <th className="text-left font-medium text-text-muted/70 px-3 py-1.5 w-[100px]">名称</th>
              <th className="text-left font-medium text-text-muted/70 px-3 py-1.5 w-[60px]">类型</th>
              <th className="text-left font-medium text-text-muted/70 px-3 py-1.5 w-[50px]"></th>
              <th className="text-left font-medium text-text-muted/70 px-3 py-1.5 w-[80px]">默认值</th>
              <th className="text-left font-medium text-text-muted/70 px-3 py-1.5">说明</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(schema).map(([name, def]) => (
              <tr key={name} className="border-b border-border-main/50 last:border-b-0">
                <td className="px-3 py-1.5">
                  <code className="font-mono text-brand-green/90 font-semibold">{name}</code>
                </td>
                <td className="px-3 py-1.5 text-text-muted font-mono">{def.type || 'any'}</td>
                <td className="px-3 py-1.5">
                  {def.required !== false
                    ? <span className="text-[#d45656] text-[10px] font-medium">必填</span>
                    : <span className="text-text-muted/50 text-[10px]">可选</span>}
                </td>
                <td className="px-3 py-1.5 text-text-muted font-mono">
                  {def.default !== undefined ? JSON.stringify(def.default) : '—'}
                </td>
                <td className="px-3 py-1.5 text-text-muted/80">{def.description || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

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
      ref={formRef}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="border border-brand-green/30 rounded-xl bg-bg-card overflow-hidden mb-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border-main">
        <div className="flex items-center gap-3 min-w-0">
          <h4 className="text-sm font-semibold text-text-main shrink-0">
            {initial?.id ? '编辑定时任务' : '新建定时任务'}
          </h4>
          {initial?.id && (
            <span className="text-[11px] text-text-muted truncate">
              {initial.last_run
                ? <>上次 {(() => { try { return formatLocalTime(new Date(initial.last_run)) } catch { return initial.last_run } })()}</>
                : '从未执行'}
              · 成功 <span className="text-brand-green">{initial.run_count || 0}</span>
              {(initial.error_count || 0) > 0 && (
                <> · 失败 <span className="text-[#d45656]">{initial.error_count}</span></>
              )}
            </span>
          )}
        </div>
        <button onClick={onCancel}
          className="p-1 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer shrink-0">
          <X size={14} />
        </button>
      </div>

      <div className="p-5 space-y-4">
        {/* Row: 名称 + Skill */}
        <div className="flex gap-3">
          <div className="flex-1 min-w-0">
            <label className="block text-xs text-text-muted mb-1.5">
              任务名称 <span className="text-[#d45656]">*</span>
            </label>
            <Input value={name} onChange={setName} placeholder="例：36氪早报" />
            {nameError && <p className="text-xs text-[#d45656] mt-1">{nameError}</p>}
          </div>
          <div className="flex-1 min-w-0">
            <label className="block text-xs text-text-muted mb-1.5">
              执行 Skill <span className="text-[#d45656]">*</span>
            </label>
            <select
              value={skill}
              onChange={(e) => setSkill(e.target.value)}
              disabled={!skills.length}
              className="w-full bg-bg-raised border border-border-main rounded-full px-4 py-2.5 text-sm text-text-main
                focus:outline-none focus:border-brand-green cursor-pointer disabled:opacity-50"
            >
              {!skills.length && <option>(暂无 skill)</option>}
              {skills.map(s => (
                <option key={s.name} value={s.name}>
                  {s.name} · {s.description?.slice(0, 50)}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Skill info badge */}
        {selectedSkill && (
          <div className="flex items-center gap-2 px-3 py-2.5 bg-bg-raised/60 rounded-lg">
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded bg-brand-green/15 text-brand-green uppercase tracking-wider">
              {selectedSkill.type}
            </span>
            <span className="text-xs text-text-muted leading-relaxed">{selectedSkill.description}</span>
          </div>
        )}

        {/* 参数参考表 + JSON 文本域 */}
        {selectedSkill?.args && Object.keys(selectedSkill.args).length > 0 && (
          <div>
            <label className="block text-xs text-text-muted mb-1.5">参数说明</label>
            <ArgsRefTable />
          </div>
        )}
        <div>
          <label className="block text-xs text-text-muted mb-1">
            参数值 <span className="text-text-muted/60 font-normal">· JSON</span>
          </label>
          <textarea
            value={argsJson}
            onChange={(e) => { setArgsJson(e.target.value); setTouched(true) }}
            onBlur={() => setTouched(true)}
            rows={6}
            placeholder='{"key": "value"}'
            className={`w-full bg-bg-raised border rounded-lg px-3 py-2 text-xs text-text-main font-mono
              focus:outline-none focus:ring-1 focus:ring-brand-green/30 resize-none
              ${displayError ? 'border-[#d45656]' : 'border-border-main focus:border-brand-green'}`}
          />
          {displayError ? (
            <p className="text-xs text-[#d45656] mt-1">⚠ {displayError}</p>
          ) : (
            <p className="text-[11px] text-text-muted/60 mt-0.5">按上方参数说明传入 JSON 对象</p>
          )}
        </div>

        <hr className="border-border-main" />

        {/* 触发时间 */}
        <div>
          <label className="block text-xs text-text-muted mb-2">触发时间 <span className="text-[#d45656]">*</span></label>

          <div className="flex flex-wrap gap-1.5 mb-2">
            {CRON_PRESETS.map((p, i) => (
              <button
                key={i}
                onClick={() => { setCronExpr(p.cron); setTouched(true) }}
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

          <div className="flex gap-2">
            <textarea
              value={cronExpr}
              onChange={(e) => { setCronExpr(e.target.value); setTouched(true) }}
              onBlur={() => setTouched(true)}
              rows={1}
              placeholder="0 8 * * *"
              className={`flex-1 bg-bg-raised border rounded-full px-4 py-2 text-sm text-text-main font-mono
                focus:outline-none focus:ring-1 focus:ring-brand-green/30 resize-none
                ${cronError ? 'border-[#d45656]' : 'border-border-main focus:border-brand-green'}`}
            />
            {!cronError && nextTriggers.length > 0 && (
              <span className="shrink-0 text-xs text-brand-green bg-brand-green/[0.06] rounded-lg px-3 py-2 flex items-center">
                ⚡ 下次执行: {nextTriggers.slice(0, 2).map(formatLocalTime).join(' · ')}
              </span>
            )}
          </div>

          {cronError && <p className="text-xs text-[#d45656] mt-1.5">⚠ {cronError}</p>}

          <details className="mt-1.5 text-[11px] text-text-muted cursor-pointer group">
            <summary className="hover:text-text-main transition-colors select-none">Cron 语法参考</summary>
            <div className="mt-1.5 pl-3 border-l border-border-main space-y-1 text-[12px] leading-relaxed">
              <p><code className="font-mono text-[11px] px-1 py-0.5 rounded bg-bg-raised text-text-secondary">分 时 日 月 周</code> · 周日=0</p>
              <p><code className="font-mono text-[11px] px-1 py-0.5 rounded bg-bg-raised">0 9 * * *</code> 每天 9:00</p>
              <p><code className="font-mono text-[11px] px-1 py-0.5 rounded bg-bg-raised">*/15 * * * *</code> 每 15 分钟</p>
              <p><code className="font-mono text-[11px] px-1 py-0.5 rounded bg-bg-raised">0 9 * * 1-5</code> 工作日 9:00</p>
              <p className="text-text-muted/60 mt-1">支持多行，每行一个 cron，任一行匹配即触发</p>
            </div>
          </details>
        </div>

        <hr className="border-border-main" />

        {/* 投递设置 */}
        <div>
          <label className="block text-xs text-text-muted mb-1">投递方式</label>
          <div className="flex items-center justify-between py-2.5">
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-text-main font-medium">推送到微信</span>
              <span className="text-[11px] text-text-muted/60">执行结果通过 iLink 推送</span>
            </div>
            <Toggle enabled={pushEnabled} onChange={setPushEnabled} />
          </div>
          <hr className="border-border-main my-0" />
          <div className="flex items-center justify-between py-2.5">
            <div className="flex flex-col gap-0.5">
              <span className="text-xs text-text-main font-medium">{initial?.id ? '启用' : '创建后立即启用'}</span>
              <span className="text-[11px] text-text-muted/60">暂停时不会触发调度</span>
            </div>
            <Toggle enabled={enabled} onChange={setEnabled} />
          </div>
        </div>

        {/* Footer */}
        <div className="flex gap-2 pt-1">
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
  const [filter, setFilter] = useState({ status: 'all', name: 'all', search: '' })
  const [detail, setDetail] = useState(null) // 详情弹窗选中的任务

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

  // 任务名列表（去重排序）
  const taskNames = useMemo(() => {
    const names = new Set(tasks.map(t => t.group_name).filter(Boolean))
    return Array.from(names).sort()
  }, [tasks])

  const filtered = useMemo(() => {
    let result = tasks
    if (filter.status !== 'all') {
      result = result.filter(t => t.status === filter.status)
    }
    if (filter.name !== 'all') {
      result = result.filter(t => t.group_name === filter.name)
    }
    if (filter.search.trim()) {
      const q = filter.search.toLowerCase()
      result = result.filter(t => {
        const haystack = `${t.progress || ''} ${t.result || ''} ${t.error || ''}`.toLowerCase()
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
      <div className="space-y-2 mb-3">
        {/* 筛选行 1：状态 + 任务名 */}
        <div className="flex items-center gap-2">
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
          <select
            value={filter.name}
            onChange={(e) => setFilter(f => ({ ...f, name: e.target.value }))}
            className="bg-bg-raised border border-border-main rounded-full px-3 py-1.5 text-xs text-text-main
              focus:outline-none focus:border-brand-green cursor-pointer max-w-[200px]"
          >
            <option value="all">全部定时任务</option>
            {taskNames.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <button
            onClick={load}
            className="text-xs px-3 py-1.5 rounded-full bg-bg-raised text-text-muted hover:text-text-main transition-colors cursor-pointer ml-auto"
          >
            刷新
          </button>
        </div>
        {/* 筛选行 2：结果搜索 */}
        <div className="flex items-center gap-2">
          <input
            value={filter.search}
            onChange={(e) => setFilter(f => ({ ...f, search: e.target.value }))}
            placeholder="🔍 搜索执行结果（输出/错误信息）..."
            className="w-full bg-bg-raised border border-border-main rounded-full px-3 py-1.5 text-xs text-text-main
              focus:outline-none focus:border-brand-green"
          />
        </div>
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
                      onClick={() => setDetail(t)}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg bg-bg-raised/40 hover:bg-bg-raised transition-colors text-xs cursor-pointer"
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

      {/* 详情弹窗 */}
      <AnimatePresence>
        {detail && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            onClick={() => setDetail(null)}
          >
            <div className="absolute inset-0 bg-black/50" />
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ duration: 0.12 }}
              onClick={(e) => e.stopPropagation()}
              className="relative bg-bg-card border border-border-main rounded-xl shadow-xl w-[560px] max-w-full max-h-[85vh] flex flex-col"
            >
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-main shrink-0">
                <h3 className="text-sm font-semibold text-text-main">
                  执行详情 · {detail.group_name || 'task'}
                </h3>
                <button onClick={() => setDetail(null)}
                  className="p-1 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer">
                  <X size={16} />
                </button>
              </div>

              {/* Body */}
              <div className="px-5 py-4 overflow-y-auto space-y-4">
                {/* 元信息 4 宫格 */}
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: '状态', val: detail.status === 'completed' ? '✅ 成功' : detail.status === 'failed' ? '❌ 失败' : '⏳ 运行中', cls: detail.status === 'failed' ? 'text-[#d45656]' : detail.status === 'completed' ? 'text-brand-green' : '' },
                    { label: '耗时', val: (() => { try { if (detail.finished_at && detail.created_at) return `${((new Date(detail.finished_at) - new Date(detail.created_at)) / 1000).toFixed(1)}s` } catch {} return '—' })() },
                    { label: '触发时间', val: detail.created_at ? detail.created_at.slice(11, 19) : '—' },
                    { label: '推送状态', val: detail.push_status === 'success' ? '✓ 已推送' : detail.push_status === 'failed' ? '✗ 推送失败' : '—', cls: detail.push_status === 'success' ? 'text-brand-green' : detail.push_status === 'failed' ? 'text-[#d45656]' : '' },
                  ].map((item, i) => (
                    <div key={i} className="bg-bg-raised/60 rounded-lg px-3 py-2.5">
                      <div className="text-[10px] font-medium text-text-muted/70 uppercase tracking-wider mb-0.5">{item.label}</div>
                      <div className={`text-sm font-medium ${item.cls || 'text-text-main'}`}>{item.val}</div>
                    </div>
                  ))}
                </div>

                {/* 任务配置 */}
                <div>
                  <div className="text-[11px] font-medium text-text-muted/70 uppercase tracking-wider mb-1">调用请求</div>
                  <pre className="bg-bg-raised/60 border border-border-main rounded-lg p-3 text-xs font-mono text-text-secondary whitespace-pre-wrap leading-relaxed">
                    {detail.config ? (
                      (() => { try { return JSON.stringify(JSON.parse(detail.config), null, 2) } catch { return detail.config } })()
                    ) : (
                      '(无配置快照)'
                    )}
                  </pre>
                </div>

                {/* 执行结果 */}
                <div>
                  <div className="text-[11px] font-medium text-text-muted/70 uppercase tracking-wider mb-1">
                    {detail.status === 'failed' ? '错误信息' : '执行结果'}
                  </div>
                  <pre className={`bg-bg-raised/60 border border-border-main rounded-lg p-3 text-xs font-mono whitespace-pre-wrap leading-relaxed max-h-[200px] overflow-y-auto ${
                    detail.status === 'failed' ? 'text-[#d45656]' : 'text-text-secondary'
                  }`}>
                    {(detail.error || detail.result || detail.progress || '(无输出)')}
                  </pre>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
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
  const [toast, setToast] = useState(null) // { type: 'success'|'error', message }
  const [runningNow, setRunningNow] = useState(new Set())

  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 3000)
      return () => clearTimeout(t)
    }
  }, [toast])

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
    setRunningNow(prev => new Set(prev).add(id))
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${id}/run`, { method: 'POST' })
      const data = await res.json()
      if (data.ok) {
        const result = data.data?.output || ''
        const preview = result.length > 120 ? result.slice(0, 120) + '...' : result
        setToast({ type: 'success', message: `✅ 执行完成${preview ? ': ' + preview : ''}` })
        loadTasks()
      } else {
        setToast({ type: 'error', message: '❌ 执行失败: ' + (data.error || '未知错误') })
      }
    } catch (e) {
      setToast({ type: 'error', message: '❌ 执行失败: ' + e.message })
    }
    setRunningNow(prev => { const next = new Set(prev); next.delete(id); return next })
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

      {/* Toast */}
      {toast && (
        <div className={`mb-4 px-4 py-2.5 rounded-lg text-xs font-medium transition-all ${
          toast.type === 'success' ? 'bg-brand-green/15 text-brand-green' : 'bg-[#d45656]/15 text-[#d45656]'
        }`}>
          {toast.message}
        </div>
      )}

      {/* Tasks Section */}
      {section === 'tasks' && (
        <>
          {showForm && (
            <TaskForm
              key={editingTask?.id || 'new'}
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
                  runningNow={runningNow}
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
