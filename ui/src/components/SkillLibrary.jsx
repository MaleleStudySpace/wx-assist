import { useState, useEffect, useMemo } from 'react'
import { ArrowsClockwise } from '@phosphor-icons/react'

// ── Skill 开发指南卡片 ─────────────────────────────────────────────
function SkillGuideCard({ embedded }) {
  const content = (
    <div className="text-sm text-text-secondary leading-relaxed">

      {/* 目录结构 */}
      <div className="mb-6">
        <p className="text-text-main font-semibold mb-3 text-sm">目录结构</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto">
{`data/skills/{skill-name}/
├── SKILL.md         ← 元数据（YAML frontmatter）
├── scripts/         ← script 类型脚本目录（仅 .py）
└── examples/        ← 可选：使用示例`}
        </div>
      </div>

      {/* 类型速览 */}
      <div className="mb-6">
        <p className="text-text-main font-semibold mb-3 text-sm">两种类型</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 space-y-4">
          <div className="flex items-start gap-3">
            <span className="shrink-0 mt-0.5 px-2.5 py-1 rounded bg-brand-green/15 text-brand-green font-mono text-[12px] font-semibold">script</span>
            <span className="text-sm text-text-secondary leading-relaxed">子进程跑 Python 脚本。<code className="font-mono px-1.5 py-0.5 rounded bg-bg-raised text-sm">--key value</code> 传参，stdout 出结果。</span>
          </div>
          <div className="flex items-start gap-3">
            <span className="shrink-0 mt-0.5 px-2.5 py-1 rounded bg-[#a78bfa]/20 text-[#a78bfa] font-mono text-[12px] font-semibold">prompt</span>
            <span className="text-sm text-text-secondary leading-relaxed">单次 prompt 喂给 LLM 出结果，不开子进程、不保留对话历史。</span>
          </div>
        </div>
        <div className="bg-[#d45656]/10 border border-[#d45656]/40 rounded-xl px-4 py-3 mt-3">
          <p className="text-sm font-semibold text-[#d45656]">⚠️ script 类型 skill 只支持 Python</p>
        </div>
      </div>

      {/* 使用场景 */}
      <div className="mb-6">
        <p className="text-text-main font-semibold mb-3 text-sm">使用场景</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 space-y-3">
          <p className="flex items-center gap-3 text-sm text-text-secondary">
            <span className="text-lg shrink-0">🕐</span>
            <span>定时调度 — CronScheduler 按 cron 表达式触发，结果自动推微信</span>
          </p>
          <p className="flex items-center gap-3 text-sm text-text-secondary">
            <span className="text-lg shrink-0">🤖</span>
            <span>AI 助手 — 用户在对话中让 Agent 调用，立即执行</span>
          </p>
        </div>
      </div>

      {/* ─── script 类型教程 ──────────────────────────────── */}
      <div className="mb-6 pt-2">
        <div className="flex items-center gap-3 mb-3">
          <span className="px-3 py-1 rounded bg-brand-green/15 text-brand-green font-mono text-[13px] font-semibold">script</span>
          <p className="text-text-main font-semibold text-sm">类型教程：跑 Python 脚本</p>
        </div>

        {/* 目录骨架 */}
        <p className="text-xs text-text-muted mb-2">目录骨架：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`data/skills/{skill-name}/
├── SKILL.md              ← 元数据
└── scripts/              ← 放 Python 脚本
    └── myscript.py`}
        </div>

        {/* SKILL.md 模板 */}
        <p className="text-xs text-text-muted mb-2">SKILL.md 模板：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`---
name: my-skill            # 唯一标识
type: script              # ← 写 script
description: 做什么用的
command: myscript.py      # ← 相对 scripts/ 的脚本名
timeout: 30               # 超时秒，默认 30
args:
  location:
    type: string
    required: true
    description: 城市名
  days:
    type: integer
    default: 2
---`}
        </div>

        {/* 参数映射 */}
        <p className="text-xs text-text-muted mb-2">参数 → CLI 映射：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`# SKILL.md 写 args = {location: "北京", days: 3, verbose: true}
# 引擎拼成:
python scripts/myscript.py --location 北京 --days 3 --verbose

# 规则:
#   string/integer/number → --key <值>
#   True (boolean)         → --key（只传开关，无值）
#   False (boolean)        → 整个 --key 不传
#   数组/对象              → str() 后传（JSON 字符串，脚本自己 parse）`}
        </div>

        {/* Python 脚本示例 */}
        <p className="text-xs text-text-muted mb-2">Python 脚本里这样接（推荐 argparse）：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`import argparse, sys

p = argparse.ArgumentParser()
p.add_argument("--location", required=True)
p.add_argument("--days", type=int, default=2)
p.add_argument("--verbose", action="store_true")
args = p.parse_args()

print(f"{args.location} 预报 {args.days} 天（verbose={args.verbose}）")
sys.exit(0)

# 若本次无新内容可推送，请输出 [SILENT]，调度器将跳过本轮推送`}
        </div>

        {/* 适用场景 */}
        <p className="text-xs text-text-muted mb-2">适用：拉数据 / 调外部 API / 跑本地计算 / 系统命令封装</p>
      </div>

      {/* ─── prompt 类型教程 ──────────────────────────────── */}
      <div className="mb-6 pt-2">
        <div className="flex items-center gap-3 mb-3">
          <span className="px-3 py-1 rounded bg-[#a78bfa]/20 text-[#a78bfa] font-mono text-[13px] font-semibold">prompt</span>
          <p className="text-text-main font-semibold text-sm">类型教程：单次 prompt 驱动 LLM</p>
        </div>

        {/* 目录骨架 */}
        <p className="text-xs text-text-muted mb-2">目录骨架（不需要 scripts/）：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`data/skills/{skill-name}/
└── SKILL.md              ← prompt 写在 SKILL.md 里`}
        </div>

        {/* SKILL.md 模板 */}
        <p className="text-xs text-text-muted mb-2">SKILL.md 模板：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`---
name: my-skill            # 唯一标识
type: prompt              # ← 写 prompt
description: 做什么用的
timeout: 60               # 留给 LLM 的思考时间（默认 60）
args:
  topic:
    type: string
    required: true
    description: 话题
  style:
    type: string
    default: 幽默
    description: 风格
prompt: |
  你是 XXX 助手。用户会给你一个话题（topic 参数）和风格（style 参数）。

  请按以下步骤执行：
  1. 先复述话题，确认理解
  2. 按 style 风格输出 3 条候选
  3. 每条 1-2 句话，不超过 80 字

  若话题不合适处理，输出 [SILENT]。
---`}
        </div>

        {/* args 注入方式 */}
        <p className="text-xs text-text-muted mb-2">参数注入方式：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 font-mono text-sm leading-loose whitespace-pre overflow-x-auto mb-4">
{`# 调用时传 args = {topic: "AI", style: "严肃"}
# 引擎会自动在 prompt 末尾追加:

## 参数
{
  "topic": "AI",
  "style": "严肃"
}

# prompt 里直接引用 topic / style 即可，引擎会把 args 字典
# 转成 JSON 块喂给 LLM（ensure_ascii=False 中文可读）`}
        </div>

        {/* prompt 写法要点 */}
        <p className="text-xs text-text-muted mb-2">prompt 写法要点：</p>
        <div className="bg-[#0a0a0e] border border-border-main rounded-xl p-5 text-sm leading-relaxed space-y-2">
          <p>• <strong className="text-text-main">写步骤</strong>：让 LLM 按 1/2/3 走，比笼统指令稳定</p>
          <p>• <strong className="text-text-main">指明输出格式</strong>：Markdown / 列表 / 字数限制，避免自由发挥</p>
          <p>• <strong className="text-text-main">给反例</strong>：&ldquo;不要&rdquo;比 &ldquo;应该&rdquo;管用</p>
          <p>• <strong className="text-text-main">无内容就 [SILENT]</strong>：监控类任务让 LLM 输出 <code className="font-mono px-1 py-0.5 rounded bg-bg-raised text-xs">[SILENT]</code> 跳过推送</p>
        </div>

        {/* 适用场景 */}
        <p className="text-xs text-text-muted mt-3 mb-2">适用：内容生成 / 文案润色 / 数据分析 / 分类汇总 / 翻译总结</p>
      </div>

      {/* 热更新 */}
      <p className="text-text-muted/60 text-xs pt-2 border-t border-border-main/50">创建/修改 SKILL.md 后下次执行立即生效，无需重启 bot。</p>
    </div>
  )

  if (embedded) {
    return (
      <div className="text-left border border-brand-green/25 rounded-xl bg-bg-card p-6 max-h-[80vh] overflow-y-auto w-full">
        {content}
      </div>
    )
  }
  return content
}

// ── Skill library ──────────────────────────────────────────────────
export default function SkillLibrary({ skills, onRefresh }) {
  const [selectedSkill, setSelectedSkill] = useState(skills[0]?.name || '')
  const [search, setSearch] = useState('')
  const [showGuide, setShowGuide] = useState(false)
  const [creating, setCreating] = useState(false)

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

  async function handleCreateSample() {
    setCreating(true)
    try {
      const res = await fetch('/api/skills?sample=1')
      const data = await res.json()
      if (data.ok) {
        onRefresh?.()
      } else {
        alert('创建失败: ' + (data.error || '未知错误'))
      }
    } catch (e) {
      alert('创建失败: ' + e.message)
    }
    setCreating(false)
  }

  // 无 skill 时空状态 — 指南默认展开
  if (!skills.length) {
    return (
      <div className="flex flex-col items-center py-12 px-6 text-center">
        <div className="w-14 h-14 rounded-xl bg-brand-green/[0.06] flex items-center justify-center text-3xl mb-4">🧩</div>
        <h3 className="text-base font-semibold text-text-main mb-1">还没有 Skill</h3>
        <p className="text-sm text-text-muted mb-5">Skill 是定时任务的可执行单元，放在 data/skills/ 目录下</p>
        <button
          onClick={handleCreateSample}
          disabled={creating}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold
            bg-brand-green text-white hover:brightness-110 transition-all cursor-pointer
            disabled:opacity-50 disabled:cursor-wait mb-6"
        >
          {creating ? '⏳ 创建中...' : '✨ 生成示例 Skill'}
        </button>
        <SkillGuideCard embedded />
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
      {/* 左侧：列表 + 教程入口 */}
      <div className="flex flex-col gap-3">
        <div className="border border-border-main rounded-xl bg-bg-card overflow-hidden">
          <div className="p-3 border-b border-border-main">
            <div className="flex items-center gap-2">
              <input value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="🔍 搜索 skill..."
                className="flex-1 bg-bg-raised border border-border-main rounded-lg px-4 py-2.5 text-sm text-text-main focus:outline-none focus:border-brand-green" />
              <button
                type="button"
                onClick={onRefresh}
                title="刷新 skill 列表"
                className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-lg
                  bg-bg-raised border border-border-main text-text-muted
                  hover:text-brand-green hover:border-brand-green/30
                  transition-all cursor-pointer"
              >
                <ArrowsClockwise size={16} />
              </button>
            </div>
          </div>
          <div className="max-h-[420px] overflow-y-auto p-2">
            {filteredSkills.map(s => (
              <button key={s.name}
                onClick={() => { setSelectedSkill(s.name); setShowGuide(false) }}
                className={`w-full text-left p-3 rounded-lg mb-1 transition-colors cursor-pointer
                  ${selectedSkill === s.name ? 'bg-brand-green-light/15 border border-brand-green/30' : 'hover:bg-bg-raised border border-transparent'}`}>
                <div className={`text-sm font-semibold ${selectedSkill === s.name ? 'text-brand-green' : 'text-text-main'}`}>
                  {s.name}
                  <span className={`ml-2 inline-block text-xs px-2 py-0.5 rounded font-medium
                    ${s.type === 'script' ? 'bg-brand-green/15 text-brand-green' : 'bg-[#a78bfa]/20 text-[#a78bfa]'}`}>
                    {s.type}
                  </span>
                </div>
                {s.description && <div className="text-xs text-text-muted mt-1 line-clamp-2 leading-relaxed">{s.description}</div>}
              </button>
            ))}
          </div>
        </div>

        {/* 教程入口卡片 */}
        <button onClick={() => setShowGuide(!showGuide)}
          className={`w-full text-left border rounded-xl p-4 transition-all cursor-pointer
            ${showGuide ? 'border-brand-green/40 bg-brand-green/[0.04]' : 'border-brand-green/20 bg-brand-green/[0.02] hover:bg-brand-green/[0.05] hover:border-brand-green/40'}`}>
          <div className="flex items-center gap-3">
            <span className="text-xl">📖</span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-brand-green">Skill 开发指南</div>
              <div className="text-xs text-text-muted mt-0.5">目录结构 & SKILL.md 格式 & 类型说明</div>
            </div>
            <span className={`text-brand-green text-base transition-transform ${showGuide ? 'rotate-90' : ''}`}>▸</span>
          </div>
          <span className="inline-block text-[10px] font-semibold uppercase tracking-wider mt-2 px-2 py-0.5 rounded bg-brand-green/15 text-brand-green">教程</span>
        </button>
      </div>

      {/* 右侧详情 / 指南 */}
      <div className="border border-border-main rounded-xl bg-bg-card p-6 max-h-[600px] overflow-y-auto">
        {showGuide ? (
          <div className="space-y-4">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-xl">📖</span>
              <h3 className="text-base font-semibold text-text-main">Skill 开发指南</h3>
              <button onClick={() => setShowGuide(false)}
                className="ml-auto text-sm text-text-muted hover:text-text-main transition-colors cursor-pointer bg-bg-raised px-3 py-1.5 rounded-lg">
                返回
              </button>
            </div>
            <SkillGuideCard />
          </div>
        ) : selected ? (
          <div className="space-y-5">
            <div className="flex items-center gap-3">
              <h3 className="text-base font-semibold text-text-main">{selected.name}</h3>
              <span className={`text-xs px-2.5 py-1 rounded font-medium
                ${selected.type === 'script' ? 'bg-brand-green/15 text-brand-green' : 'bg-[#a78bfa]/20 text-[#a78bfa]'}`}>
                {selected.type}
              </span>
            </div>
            <div className="space-y-4 text-sm">
              {selected.description && (
                <div>
                  <div className="text-xs font-medium text-text-muted/70 uppercase tracking-wider mb-1.5">描述</div>
                  <div className="text-text-secondary leading-relaxed">{selected.description}</div>
                </div>
              )}
              {selected.args && Object.keys(selected.args).length > 0 && (
                <div>
                  <div className="text-xs font-medium text-text-muted/70 uppercase tracking-wider mb-1.5">参数</div>
                  <div className="bg-bg-raised/60 border border-border-main rounded-lg overflow-hidden">
                    {Object.entries(selected.args).map(([name, def], i, arr) => (
                      <div key={name} className={`px-4 py-3 ${i < arr.length - 1 ? 'border-b border-border-main/50' : ''}`}>
                        <div className="flex items-center gap-2 flex-wrap">
                          <code className="font-mono text-sm font-semibold text-brand-green/90">{name}</code>
                          <span className="text-xs text-text-muted font-mono">{def.type || 'any'}</span>
                          {def.required && <span className="text-[11px] font-medium text-[#d45656]">⦿ 必填</span>}
                          {def.default !== undefined && <span className="text-xs text-text-muted">= <code className="font-mono">{JSON.stringify(def.default)}</code></span>}
                        </div>
                        {def.description && <p className="text-xs text-text-muted/70 mt-1 leading-relaxed">{def.description}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <div className="text-xs font-medium text-text-muted/70 uppercase tracking-wider mb-1.5">路径</div>
                <code className="font-mono text-sm text-text-secondary bg-bg-raised/60 border border-border-main rounded-lg px-3 py-2 block">data/skills/{selected.name}/SKILL.md</code>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-16 text-text-muted text-sm">选择左侧 skill 查看详情</div>
        )}
      </div>
    </div>
  )
}
