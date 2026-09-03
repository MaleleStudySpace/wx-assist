/**
 * 共享 cron 工具 — SchedulerPanel 用
 *
 * 验证规则跟后端 src/utils/cron.py 的 validate_cron_syntax() 完全对齐
 * 服务端校验是权威，前端只是即时反馈避免不必要的网络往返
 */

export const CRON_PRESETS = [
  { label: '每分钟', cron: '* * * * *' },
  { label: '每5分钟', cron: '*/5 * * * *' },
  { label: '每30分钟', cron: '*/30 * * * *' },
  { label: '每小时', cron: '0 * * * *' },
  { label: '每天8点', cron: '0 8 * * *' },
  { label: '每天18点', cron: '0 18 * * *' },
  { label: '工作日9点', cron: '0 9 * * 1-5' },
]

// 字段范围（与服务端字段值范围完全对齐）
const FIELD_RANGES = [
  { lo: 0, hi: 59 },  // 分
  { lo: 0, hi: 23 },  // 时
  { lo: 1, hi: 31 },  // 日
  { lo: 1, hi: 12 },  // 月
  { lo: 0, hi: 6 },   // 周（0=周日）
]

/**
 * 校验单个字段的语法和数值范围
 * 支持的格式: *, N, N-M, N-M/K, step, list (用空格隔开避免注释结束)
 */
function validateField(field, lo, hi) {
  for (const part of field.split(',')) {
    const p = part.trim()
    if (!p) return false
    if (p === '*') continue

    if (p.includes('/')) {
      // 步进：*/N, N-M/K, N/K
      const [range, stepStr] = p.split('/', 1)[0] !== '*' && !p.startsWith('*')
        ? p.split('/')
        : [p.split('/')[0], p.split('/')[1]]
      if (!stepStr || !/^\d+$/.test(stepStr) || Number(stepStr) <= 0) return false

      if (range === '*') continue
      if (range.includes('-')) {
        const [a, b] = range.split('-')
        if (!/^\d+$/.test(a) || !/^\d+$/.test(b)) return false
        if (Number(a) < lo || Number(a) > hi || Number(b) < lo || Number(b) > hi) return false
      } else {
        if (!/^\d+$/.test(range)) return false
        if (Number(range) < lo || Number(range) > hi) return false
      }
    } else if (p.includes('-')) {
      const [a, b] = p.split('-')
      if (!/^\d+$/.test(a) || !/^\d+$/.test(b)) return false
      if (Number(a) < lo || Number(a) > hi || Number(b) < lo || Number(b) > hi) return false
    } else {
      if (!/^\d+$/.test(p)) return false
      if (Number(p) < lo || Number(p) > hi) return false
    }
  }
  return true
}

/**
 * 校验完整 cron 表达式
 * @returns {string} "" = 合法; 非空 = 错误信息
 */
export function validateCronExpr(cronExpr) {
  if (!cronExpr || !cronExpr.trim()) {
    return 'cron 表达式不能为空'
  }

  const lines = cronExpr.trim().split('\n').map(l => l.trim()).filter(Boolean)
  if (!lines.length) {
    return 'cron 表达式不能为空'
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fields = line.split(/\s+/)
    if (fields.length !== 5) {
      return `第${i + 1}行：必须有5个字段（分 时 日 月 周），当前=${line}`
    }
    for (let j = 0; j < 5; j++) {
      const [lo, hi] = [FIELD_RANGES[j].lo, FIELD_RANGES[j].hi]
      if (!validateField(fields[j], lo, hi)) {
        const names = ['分钟', '小时', '日', '月', '周']
        return `第${i + 1}行：${names[j]}=${fields[j]} 格式错误，应为 ${lo}-${hi} 的整数、*、步进、范围或列表`
      }
    }
  }
  return ''
}

/**
 * 把 cron 各字段解析成"匹配函数"，方便后续判断某个时间点是否触发
 * @returns {Array<function>} 5 个判别函数 [minuteFn, hourFn, dayFn, monthFn, weekFn]
 */
function parseCronToFieldFns(line) {
  const fields = line.split(/\s+/)
  return fields.map((field, idx) => {
    const [lo, hi] = [FIELD_RANGES[idx].lo, FIELD_RANGES[idx].hi]
    return buildFieldMatcher(field, lo, hi)
  })
}

function buildFieldMatcher(field, lo, hi) {
  return (value) => {
    for (const part of field.split(',')) {
      const p = part.trim()
      if (p === '*') return true
      if (p.includes('/')) {
        const [range, stepStr] = p.split('/')
        const step = Number(stepStr)
        if (p.startsWith('*/') || p.startsWith('*')) {
          if (value % step === 0) return true
        } else if (range.includes('-')) {
          const [a, b] = range.split('-').map(Number)
          if (value >= a && value <= b && (value - a) % step === 0) return true
        } else {
          const start = Number(range)
          if (value >= start && (value - start) % step === 0) return true
        }
      } else if (p.includes('-')) {
        const [a, b] = p.split('-').map(Number)
        if (value >= a && value <= b) return true
      } else {
        if (Number(p) === value) return true
      }
    }
    return false
  }
}

/**
 * 把 cron 表达式转成可用的匹配规则
 * @returns {Array<Array<function>>} 多行表达式，每行 5 个字段判别函数
 */
function parseCronToRules(cronExpr) {
  const lines = cronExpr.trim().split('\n').map(l => l.trim()).filter(Boolean)
  return lines.map(parseCronToFieldFns)
}

/**
 * 判断某个 Date 是否匹配 cron（任一行匹配即返回 true）
 */
function cronMatchesAt(date, rules) {
  const minute = date.getMinutes()
  const hour = date.getHours()
  const day = date.getDate()
  const month = date.getMonth() + 1
  const week = date.getDay()  // 0 = Sunday

  for (const lineRules of rules) {
    if (lineRules[0](minute) && lineRules[1](hour) &&
        lineRules[2](day) && lineRules[3](month) && lineRules[4](week)) {
      return true
    }
  }
  return false
}

/**
 * 算出未来 n 次触发的本地时间
 * @param {string} cronExpr
 * @param {number} n
 * @param {Date} fromDate
 * @returns {Date[]}
 */
export function getNextTriggers(cronExpr, n = 3, fromDate = new Date()) {
  if (validateCronExpr(cronExpr)) return []

  const rules = parseCronToRules(cronExpr)
  const results = []

  // 从下一分钟开始
  const cur = new Date(fromDate)
  cur.setSeconds(0, 0)
  cur.setMinutes(cur.getMinutes() + 1)

  // 最长扫到 7 天后（cron 任意字段最细是分钟，7 天=10080 次）
  const maxIterations = 7 * 24 * 60 + 60
  let iter = 0

  while (results.length < n && iter < maxIterations) {
    if (cronMatchesAt(cur, rules)) {
      results.push(new Date(cur))
    }
    cur.setMinutes(cur.getMinutes() + 1)
    iter++
  }

  return results
}

/**
 * 把 Date 格式化为本地时间字符串 (例: "12-15 08:00")
 */
export function formatLocalTime(date) {
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${m}-${d} ${hh}:${mm}`
}

// ── cron → 中文标签 ────────────────────────────────────────────────
//
// 上面那套（validateCronExpr / getNextTriggers）接受完整 cron 语法；这一组只
// 服务于"把 cron 显示成人话"，规则刻意收窄到摘要/提醒功能实际支持的形态：
// 每行一个触发点、分与时是单个整数、日与月必须是 *、周是 * / 1-5 / 数字列表。
// 收窄不是因为偷懒 —— parseCronExpr 解析不了时不会失败而是**套默认值**，
// `* * * * *` 会被算成 hours=[9]、mins=[]，最后落到兜底 '09:00' 而显示成
// "每天 09:00"。所以 cronToLabel 先用 isLabelableShape 挡一道，
// 挡不住的一律返回原文，最坏情况也不会比裸打印更错。

export const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六']

/**
 * dow 字段 → 星期数字列表。支持 `1,4` 与 `1-3`；表达不了返回 []。
 * 必须与后端 api_handlers.py 的 _expand_dow 同口径 —— 旧实现只做
 * `dow.split(',').map(Number)`，`1-3` 会变成 NaN 被过滤掉，再落到
 * "custom 但无星期"的兜底 [1..5]，于是 `0 9 * * 1-3` 被标成"工作日"。
 */
function expandDow(dow) {
  if (!dow || dow === '*') return []
  const out = new Set()
  for (const seg of dow.split(',')) {
    const s = seg.trim()
    if (!s) return []
    if (s.includes('-')) {
      const [a, b] = s.split('-')
      if (!/^\d+$/.test(a) || !/^\d+$/.test(b)) return []
      const lo = Number(a)
      const hi = Number(b)
      if (lo > hi || lo < 0 || hi > 6) return []
      for (let d = lo; d <= hi; d++) out.add(d)
    } else if (/^\d+$/.test(s) && Number(s) <= 6) {
      out.add(Number(s))
    } else {
      return []
    }
  }
  return [...out].sort((a, b) => a - b)
}

/** `9` / `9,18` 这类"单个整数或整数列表"，且每段不超过 hi。步进与范围不算。 */
function isIntList(field, hi) {
  if (!field) return false
  return field.split(',').every(seg => /^\d+$/.test(seg) && Number(seg) <= hi)
}

/**
 * 这个 cron 能不能被 cronToLabel 无损表达。
 * 分钟级步进、小时范围、指定日/月、超出范围的星期一律 false
 * （例如每 5 分钟一次、9 到 18 点每小时）。
 */
function isLabelableShape(cronExpr) {
  const lines = cronExpr.trim().split('\n').map(l => l.trim()).filter(Boolean)
  if (!lines.length) return false
  return lines.every(line => {
    const f = line.split(/\s+/)
    if (f.length !== 5) return false
    const [min, hour, day, month, dow] = f
    if (!isIntList(min, 59) || !isIntList(hour, 23)) return false
    if (day !== '*' || month !== '*') return false
    return dow === '*' || expandDow(dow).length > 0
  })
}

/**
 * 把（可能多行的）cron 解析成 { freqMode, times, weekdays }。
 * 多行 = 多个触发点，times 与 weekdays 都跨行**合并**。
 */
export function parseCronExpr(cronExpr) {
  if (!cronExpr) return { freqMode: 'daily', times: ['09:00'], weekdays: [1, 2, 3, 4, 5] }

  const lines = cronExpr.trim().split(/\n/).map(l => l.trim()).filter(Boolean)
  let allTimes = []
  let allWeekdays = []
  // 先置 null，由第一行定调；行间冲突则降级为 custom
  let detectedFreq = null

  for (const line of lines) {
    const fields = line.split(/\s+/)
    if (fields.length !== 5) continue

    const [min, hour, , , dow] = fields
    const hours = hour === '*' ? [9] : hour.split(',').map(Number).filter(n => !isNaN(n))
    const mins = min === '*' ? [0] : min.split(',').map(Number).filter(n => !isNaN(n))

    // 单行里分/时是逗号列表时按 cron 语义展开成所有组合
    for (const h of hours) {
      for (const m of mins) {
        allTimes.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)
      }
    }

    let lineFreq
    let lineDays = []
    if (dow === '*') {
      lineFreq = 'daily'
    } else {
      lineDays = expandDow(dow)
      // 1-5（写成范围还是列表都一样）显示"工作日"，其余列出具体星期
      lineFreq = lineDays.length === 5 && lineDays.every(d => d >= 1 && d <= 5)
        ? 'weekday'
        : 'custom'
    }
    // 星期必须**合并**不能覆盖：`0 9 * * 1` + `0 9 * * 4` 是"周一和周四"，
    // 覆盖写法只留最后一行，标签就成了"周四 09:00"—— 静默丢掉周一，
    // 而且看着比裸 cron 更权威、更难发现。
    allWeekdays = [...new Set([...allWeekdays, ...lineDays])].sort((a, b) => a - b)

    if (detectedFreq === null) {
      detectedFreq = lineFreq
    } else if (detectedFreq !== lineFreq) {
      detectedFreq = 'custom'
    }
  }

  if (detectedFreq === null) detectedFreq = 'daily'

  allTimes = [...new Set(allTimes)].sort()
  if (!allTimes.length) allTimes = ['09:00']
  if (!allWeekdays.length && detectedFreq === 'custom') allWeekdays = [1, 2, 3, 4, 5]

  return { freqMode: detectedFreq, times: allTimes, weekdays: allWeekdays }
}

/**
 * cron → 中文标签。表达不了的形态原样返回，绝不猜。
 * @returns {string} "" 表示没有 cron（调用方自行决定显示"手动触发"还是别的）
 */
export function cronToLabel(cronExpr) {
  if (!cronExpr || !cronExpr.trim()) return ''
  if (!isLabelableShape(cronExpr)) return cronExpr
  const p = parseCronExpr(cronExpr)
  const timeLabel = p.times.join(' · ')
  if (p.freqMode === 'daily') return `每天 ${timeLabel}`
  if (p.freqMode === 'weekday') return `工作日 ${timeLabel}`
  if (p.freqMode === 'custom' && p.weekdays.length) {
    const days = p.weekdays.map(d => '周' + WEEKDAY_LABELS[d]).join(' ')
    return `${days} ${timeLabel}`
  }
  return cronExpr
}
