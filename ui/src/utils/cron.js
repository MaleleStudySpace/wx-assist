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
