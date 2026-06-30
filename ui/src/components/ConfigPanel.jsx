import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle, Warning, FloppyDisk, Info, DownloadSimple, UploadSimple, CircleNotch, MagnifyingGlass, Lightning, PaperPlaneTilt, QrCode, SignOut, TestTube, ChatCircle, Trash, CaretDown, CaretRight, X } from '@phosphor-icons/react'
import { QRCodeSVG } from 'qrcode.react'
import { spring, Field, Toggle, Select, Input, API_BASE } from './SharedComponents'
import ChatDrawer from './ChatDrawer'

const pageTransition = {
  initial: { opacity: 0, x: 12 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -12 },
}

function TypewriterText({ text, speed = 15 }) {
  const [displayedText, setDisplayedText] = useState('')

  useEffect(() => {
    setDisplayedText('')
    let i = 0
    const interval = setInterval(() => {
      setDisplayedText((prev) => prev + text.charAt(i))
      i++
      if (i >= text.length) {
        clearInterval(interval)
      }
    }, speed)
    return () => clearInterval(interval)
  }, [text, speed])

  return <span>{displayedText}</span>
}

function AiSection({ form, update, onOpenSandbox }) {
  const [detecting, setDetecting] = useState(false)
  const [detectResult, setDetectResult] = useState(null)  // { provider_type, available_models, error }
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [fullUrlMode, setFullUrlMode] = useState(false)  // toggle: full URL vs root address

  // When toggling fullUrlMode, also update provider_type to custom
  function toggleFullUrlMode(val) {
    setFullUrlMode(val)
    if (val) {
      update('ai_provider_type', 'custom')
    } else {
      // Reset to openai when turning off full URL mode
      if (form.ai_provider_type === 'custom') {
        update('ai_provider_type', 'openai')
      }
    }
    setDetectResult(null)
  }

  async function handleDetect() {
    if (!form.ai_provider_base_url || !form.ai_provider_api_key) return
    setDetecting(true)
    setDetectResult(null)
    // If fullUrlMode is on, don't detect — user provided complete URL
    if (fullUrlMode) {
      setDetectResult({ provider_type: 'custom', available_models: [], error: '' })
      setDetecting(false)
      return
    }
    try {
      const res = await fetch(`${API_BASE}/api/assistant/ai/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: form.ai_provider_base_url,
          api_key: form.ai_provider_api_key,
          provider_type: form.ai_provider_type || 'openai',
        }),
      })
      const data = await res.json()
      setDetectResult(data)
      if (data.provider_type) {
        update('ai_provider_type', data.provider_type)
        if (data.available_models?.length > 0 && !form.ai_provider_model) {
          update('ai_provider_model', data.available_models[0])
        }
        // Suggest extra_body for DeepSeek models
        if (data.available_models?.some(m => m.startsWith('deepseek')) && !form.ai_provider_extra_body) {
          update('ai_provider_extra_body', '{"thinking":{"type":"disabled"}}')
        }
      }
    } catch {
      setDetectResult({ error: '网络请求失败，请检查站点 URL' })
    } finally {
      setDetecting(false)
    }
  }

  const providerLabel = { openai: 'OpenAI 兼容', anthropic: 'Anthropic 兼容', custom: '自定义端点' }
  const providerBadgeColor = { openai: 'bg-emerald-50 border-emerald-200 text-emerald-700', anthropic: 'bg-purple-50 border-purple-200 text-purple-700', custom: 'bg-amber-50 border-amber-200 text-amber-700' }

  const models = detectResult?.available_models?.length
    ? detectResult.available_models
    : (form.ai_provider_model ? [form.ai_provider_model] : [])

  const apiFormatOptions = [
    { value: 'openai', desc: 'OpenAI Chat Completions 格式' },
    { value: 'anthropic', desc: 'Anthropic 格式' },
  ]

  return (
    <div>
      <Field label="AI 站点 URL" hint={fullUrlMode
        ? '请填写完整请求 URL，将直接使用此 URL，不拼接路径'
        : '输入 API 根地址，不要以斜杠结尾，例如 https://api.deepseek.com'}>
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <Input
              value={form.ai_provider_base_url}
              onChange={v => { update('ai_provider_base_url', v); setDetectResult(null) }}
              placeholder={fullUrlMode ? 'https://api.example.com/v2/chat/completions' : 'https://api.deepseek.com'}
            />
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-text-muted whitespace-nowrap">完整 URL</span>
            <Toggle
              enabled={fullUrlMode}
              onChange={toggleFullUrlMode}
            />
          </div>
        </div>
      </Field>

      <Field label="API Key" hint="该站点的 API Key / Token">
        <Input
          type="password"
          value={form.ai_provider_api_key}
          onChange={v => { update('ai_provider_api_key', v); setDetectResult(null) }}
          placeholder="sk-xxxxxxxxxxxxxxxx"
        />
      </Field>

      {/* Detect + Test buttons side by side */}
      <div className="flex items-center gap-3 mb-5">
        <motion.button
          type="button"
          whileTap={{ scale: 0.97 }}
          whileHover={{ scale: 1.02 }}
          onClick={handleDetect}
          disabled={detecting || !form.ai_provider_base_url || !form.ai_provider_api_key}
          className={`flex-1 py-2.5 rounded-full text-[14px] font-semibold tracking-wide shadow-sm transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer
            ${detecting || !form.ai_provider_base_url || !form.ai_provider_api_key
              ? 'bg-bg-raised border border-border-main text-text-muted cursor-not-allowed'
              : 'bg-brand-green-light border border-brand-green/20 text-brand-green-hover hover:shadow-md'}`}
        >
          {detecting ? (
            <><CircleNotch size={16} className="animate-spin" />检测中...</>
          ) : (
            <><MagnifyingGlass size={16} />检测模型</>
          )}
        </motion.button>
        <motion.button
          type="button"
          whileTap={{ scale: 0.97 }}
          whileHover={{ scale: 1.02 }}
          onClick={onOpenSandbox}
          disabled={!form.ai_provider_base_url || !form.ai_provider_api_key}
          className={`flex-1 py-2.5 rounded-full text-[14px] font-semibold tracking-wide shadow-sm transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer
            ${!form.ai_provider_base_url || !form.ai_provider_api_key
              ? 'bg-bg-raised border border-border-main text-text-muted cursor-not-allowed'
              : 'bg-bg-raised border border-border-main text-brand-green-hover hover:border-brand-green/30 hover:bg-brand-green-light/30'}`}
        >
          <ChatCircle size={16} />
          测试对话
        </motion.button>
      </div>

      {/* Detection result */}
      {detectResult && (
        <div style={{ marginBottom: 20 }}>
          {detectResult.provider_type ? (
            <div className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border ${providerBadgeColor[detectResult.provider_type] || 'bg-bg-raised border-border-main text-text-main'}`}>
              <Lightning size={14} weight="fill" />
              检测成功：{providerLabel[detectResult.provider_type] || detectResult.provider_type}
            </div>
          ) : detectResult.error ? (
            <p className="text-xs text-status-error flex items-center gap-1">
              <Warning size={12} />{detectResult.error}
            </p>
          ) : null}
        </div>
      )}

      {/* Model selection — always Input, detected models as clickable chips */}
      <Field label="模型" hint={models.length > 1
        ? '点击下方模型标签选择，也可直接输入任意模型 ID'
        : '输入模型 ID，如 deepseek-chat、gpt-4o、mimo-v2.5'}>
        <Input
          value={form.ai_provider_model}
          onChange={v => update('ai_provider_model', v)}
          placeholder="输入或选择模型 ID"
        />
        {models.length > 1 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            <span className="text-[10px] text-text-muted">检测到：</span>
            {models.map(m => (
              <button
                key={m}
                type="button"
                onClick={() => update('ai_provider_model', m)}
                className={`px-2 py-0.5 rounded-full text-[11px] font-mono cursor-pointer transition-colors border ${
                  form.ai_provider_model === m
                    ? 'bg-brand-green-light text-brand-green border-brand-green/20 font-semibold'
                    : 'bg-bg-raised text-text-muted border-border-main hover:border-text-muted/30'
                }`}
              >{m}</button>
            ))}
          </div>
        )}
      </Field>

      {/* Advanced options — collapsed by default */}
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-main transition-colors mt-5 mb-3 cursor-pointer"
      >
        {showAdvanced ? <CaretDown size={12} /> : <CaretRight size={12} />}
        高级选项
      </button>

      <AnimatePresence>
        {showAdvanced && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="space-y-4 pl-1">
              <Field label="API 格式">
                <Select
                  value={form.ai_provider_type === 'custom' ? 'openai' : (form.ai_provider_type || 'openai')}
                  onChange={v => update('ai_provider_type', v)}
                  options={apiFormatOptions}
                />
              </Field>

              <Field label="附加参数 (JSON)" hint='合并到请求体，覆盖默认值。如 {"temperature": 0.8, "top_p": 0.9}。常用: temperature, top_p, max_tokens, stop, presence_penalty, frequency_penalty'>
                <Input
                  value={form.ai_provider_extra_body || ''}
                  onChange={v => update('ai_provider_extra_body', v)}
                  placeholder='{"thinking":{"type":"disabled"}}'
                />
              </Field>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <p className="text-xs text-text-muted mt-4 flex items-center gap-1.5">
        <Info size={14} />
        填好 URL 和 Key 后点击「检测模型」自动识别 API 类型，也可直接选模型后「测试对话」验证连通性。
      </p>
    </div>
  )
}

function IdentitySection({ form, update }) {
  // Local editable groups array — source of truth for rendering
  const [groups, setGroups] = useState([])

  // Initialize from form.wechat_groups on mount only
  useEffect(() => {
    const raw = (form.wechat_groups || '').trim()
    if (raw === '*' || raw === '') {
      setGroups(['*'])
    } else {
      setGroups(raw.split(',').map(s => {
        const trimmed = s.trim()
        if (!trimmed) return ''
        try { return decodeURIComponent(trimmed) } catch { return trimmed }
      }).filter(Boolean))
    }
  }, [])

  // Sync local groups array back to form.wechat_groups (comma-separated, URL-encoded)
  function syncToForm(newGroups) {
    const nonEmpty = newGroups.filter(g => g !== '')
    if (nonEmpty.length === 0 || nonEmpty.includes('*')) {
      update('wechat_groups', '*')
    } else {
      update('wechat_groups', nonEmpty.map(g => encodeURIComponent(g)).join(','))
    }
  }

  const isAll = groups.length === 1 && groups[0] === '*'

  function updateGroup(index, value) {
    const next = [...groups]
    next[index] = value
    if (groups.length === 1 && groups[0] === '*') {
      setGroups([value])
      syncToForm([value])
    } else {
      setGroups(next)
      syncToForm(next)
    }
  }

  function removeGroup(index) {
    const removed = groups[index]
    const next = groups.filter((_, i) => i !== index)
    if (next.length === 0) {
      if (removed === '*') {
        setGroups([''])
        syncToForm([''])
      } else {
        setGroups(['*'])
        syncToForm(['*'])
      }
    } else {
      setGroups(next)
      syncToForm(next)
    }
  }

  function addGroup() {
    const next = [...groups, '']
    setGroups(next)
    syncToForm(next)
  }

  function restoreAll() {
    setGroups(['*'])
    syncToForm(['*'])
  }

  return (
    <div>
      <Field label="目标群聊" hint={isAll ? '当前关注所有群聊。点击 × 删除「全部群聊」后可指定群名' : `关注 ${groups.filter(g => g !== '').length} 个群聊`}>
        <div className="flex flex-wrap gap-2 mb-2">
          {groups.map((name, i) => {
            if (name === '') return null
            return (
              <span key={i} className="inline-flex items-center gap-1 px-2.5 py-1 bg-brand-green-light border border-brand-green/20 rounded-lg text-[13px] text-brand-green-hover dark:text-brand-green">
                {name === '*' ? '全部群聊' : name}
                <button type="button" onClick={() => removeGroup(i)}
                  className="ml-0.5 text-brand-green-hover/60 hover:text-status-error transition-colors leading-none text-base">&times;</button>
              </span>
            )
          })}
          {!isAll && (
            <button type="button" onClick={addGroup}
              className="inline-flex items-center gap-1 px-2.5 py-1 bg-bg-raised border border-dashed border-border-main rounded-lg text-[13px] text-text-muted hover:border-brand-green hover:text-brand-green-hover transition-colors cursor-pointer">
              + 添加群聊
            </button>
          )}
        </div>
        {!isAll && (
          <button type="button" onClick={restoreAll}
            className="text-xs text-status-info hover:text-status-info transition-colors mb-2 cursor-pointer">
            恢复关注所有群聊
          </button>
        )}
        {!isAll && groups.map((name, i) => (
          <input key={`input-${i}`} type="text" value={name}
            onChange={e => updateGroup(i, e.target.value)}
            onBlur={() => { if (!name.trim()) removeGroup(i) }}
            placeholder={`群聊 ${i + 1} 的名称`}
            className="w-full bg-bg-raised border border-border-main rounded-lg px-4 py-2 text-[15px] text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all duration-200 hover:border-text-muted/30 mb-2" />
        ))}
        <p className="text-xs text-text-muted mt-1.5">
          ⚠ 请先将目标群聊添加到微信通讯录，否则无法通过搜索进入
        </p>
        {!isAll && (
          <p className="text-xs text-text-muted mt-1">💡 请输入微信中显示的完整群名，必须完全一致</p>
        )}
      </Field>

      <Field label="数据后端" hint="Windows 推荐本地数据源；macOS 推荐直连方式">
        <Select value={form.wechat_backend} onChange={v => update('wechat_backend', v)} options={[
          { value: 'wcdb', desc: '本地直连', hint: '推荐 · 本地数据源' },
          { value: 'mac_hybrid', desc: 'macOS 直连', hint: '推荐 · 本地读取' },
          { value: 'mac_ui', desc: 'macOS 界面', hint: '实验性' },
        ]} />
      </Field>
    </div>
  )
}

const paramPanel = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
  exit: { opacity: 0, y: 20, transition: { duration: 0.2 } },
}

function ParamRow({ label, hint, children }) {
  return (
    <div>
      <p className="text-[14px] text-text-main font-medium">{label}</p>
      <p className="text-xs text-text-muted mt-0.5 mb-2">{hint}</p>
      {children}
    </div>
  )
}

function FeaturesSection({ form, update }) {
  return (
    <div>
      {/* ── Log Level ── */}
      <div className="pt-4">
        <Field label="日志级别" hint="记录机器人运行日志的详细程度">
          <Select value={form.log_level} onChange={v => update('log_level', v)} options={[
            { value: 'DEBUG', desc: '调试信息', hint: '排查故障时使用' },
            { value: 'INFO', desc: '常规信息', hint: '日常使用（推荐）' },
            { value: 'WARNING', desc: '仅警告', hint: '长期稳定运行时使用' },
            { value: 'ERROR', desc: '仅错误', hint: '只关心故障时使用' },
          ]} />
        </Field>
      </div>
    </div>
  )
}

const sectionTitles = { ai: 'AI 后端配置', identity: '聊天范围', data: '数据路径', features: '功能开关', push: '微信推送', sandbox: 'AI 调试台' }
const sectionAccents = { ai: 'var(--brand-green)', identity: 'var(--status-info)', data: 'var(--brand-green)', features: 'var(--status-warn)', push: 'var(--brand-green)', sandbox: 'var(--color-purple-500, #8b5cf6)' }

// ── Data Path Section (微信数据目录配置) ──────────────────────────────

function DataPathSection({ form, update, detectedDataDir }) {
  const [browseOpen, setBrowseOpen] = useState(false)
  const [browsePath, setBrowsePath] = useState('')
  const [browseEntries, setBrowseEntries] = useState([])
  const [browseLoading, setBrowseLoading] = useState(false)
  const [browseError, setBrowseError] = useState('')
  const [browseInput, setBrowseInput] = useState('')
  const [detectResult, setDetectResult] = useState(null)
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState('')

  // ── Browse API ────────────────────────────────────────────────

  async function loadBrowseDir(path) {
    setBrowseLoading(true)
    setBrowseError('')
    setDetectResult(null)
    try {
      const params = path ? `?path=${encodeURIComponent(path)}` : ''
      const res = await fetch(`${API_BASE}/api/browse${params}`)
      const d = await res.json()
      if (d.ok) {
        setBrowsePath(d.current_path || '')
        setBrowseInput(d.current_path || '')
        setBrowseEntries(d.entries || [])
      } else {
        setBrowseError(d.error || '无法读取目录')
      }
    } catch {
      setBrowseError('无法连接到服务器')
    }
    setBrowseLoading(false)
  }

  function openBrowse() {
    const initialPath = form.wechat_data_dir || detectedDataDir || ''
    setBrowseInput(initialPath)
    setBrowseOpen(true)
    loadBrowseDir(initialPath)
  }

  function handleBrowseGo() {
    const trimmed = browseInput.trim()
    if (trimmed) {
      setBrowseError('')
      loadBrowseDir(trimmed)
    }
  }

  function handleBrowseInputKeyDown(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleBrowseGo()
    }
  }

  function navigateUp() {
    const parent = browsePath.split('\\').slice(0, -1).join('\\')
    if (parent.length >= 1) {
      loadBrowseDir(parent)
    }
  }

  function navigateTo(entryPath) {
    loadBrowseDir(entryPath)
  }

  function selectCurrentPath() {
    update('wechat_data_dir', browsePath)
    setBrowseOpen(false)
    setDetectResult(null)
  }

  // ── Detect API ────────────────────────────────────────────────

  async function handleDetect() {
    const path = (typeof form.wechat_data_dir === 'string' ? form.wechat_data_dir : '').trim()
    if (!path) {
      setDetectError('请先输入或选择目录路径')
      setTimeout(() => setDetectError(''), 4000)
      return
    }
    setDetecting(true)
    setDetectError('')
    setDetectResult(null)
    try {
      const res = await fetch(`${API_BASE}/api/wechat-data-dir/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      const d = await res.json()
      if (d.ok) {
        setDetectResult(d)
      } else {
        setDetectError(d.error || '检测失败')
        setTimeout(() => setDetectError(''), 5000)
      }
    } catch {
      setDetectError('无法连接到服务器')
      setTimeout(() => setDetectError(''), 5000)
    }
    setDetecting(false)
  }

  const hasCustomPath = (typeof form.wechat_data_dir === 'string' ? form.wechat_data_dir : '').trim().length > 0

  return (
    <div>
      <Field label="微信数据目录"
        hint="微信聊天记录存储的父目录（包含 wxid_* 文件夹）。留空则自动从 Documents 检测。">
        <div className="flex items-start gap-2">
          <div className="flex-1 relative">
            <input
              type="text"
              value={form.wechat_data_dir || ''}
              onChange={e => { update('wechat_data_dir', e.target.value); setDetectResult(null) }}
              placeholder={detectedDataDir || '自动检测中...'}
              className="w-full bg-bg-raised border border-border-main rounded-full pl-5 pr-5 py-2.5 text-[14px] text-text-main
                         placeholder:text-text-muted font-mono tabular-nums
                         focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                         transition-all duration-200
                         hover:border-text-muted/30 dark:hover:border-text-muted/40"
            />
            {hasCustomPath && (
              <button
                type="button"
                onClick={() => { update('wechat_data_dir', ''); setDetectResult(null); setDetectError('') }}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-status-error text-lg leading-none transition-colors cursor-pointer"
                title="清除自定义路径"
              >&times;</button>
            )}
          </div>
          <button
            type="button"
            onClick={openBrowse}
            className="shrink-0 px-4 py-2.5 bg-bg-main border border-border-main rounded-full text-[13px] text-text-main font-medium hover:border-brand-green hover:text-brand-green-hover transition-colors cursor-pointer"
          >
            浏览...
          </button>
          {hasCustomPath && (
            <button
              type="button"
              onClick={handleDetect}
              disabled={detecting}
              className="shrink-0 px-4 py-2.5 bg-brand-green-light border border-brand-green/20 rounded-full text-[13px] text-brand-green-hover dark:text-brand-green font-semibold hover:bg-brand-green/10 transition-colors cursor-pointer disabled:opacity-50"
            >
              {detecting ? (
                <span className="flex items-center gap-1.5">
                  <svg className="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  检测中
                </span>
              ) : '检测'}</button>
          )}
        </div>
      </Field>

      {/* Detection result */}
      {detectResult && (
        <div className={`mt-3 p-4 rounded-2xl border ${
          detectResult.found
            ? 'bg-brand-green-light border-brand-green/20'
            : 'bg-status-warn-soft border-status-warn/20'
        }`}>
          {detectResult.found ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <CheckCircle size={18} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
                <span className="text-sm font-semibold text-brand-green-hover dark:text-brand-green">{detectResult.message}</span>
              </div>
              {detectResult.accounts.map((acct, i) => (
                <div key={i} className="flex items-center gap-3 text-xs font-mono bg-bg-main/60 border border-border-main rounded-xl px-3 py-2">
                  <span className="text-text-main font-semibold">{acct.wxid}</span>
                  <span className="text-text-muted">·</span>
                  <span className={acct.has_session_db ? 'text-brand-green-hover dark:text-brand-green' : 'text-status-error'}>
                    {acct.has_session_db ? '✓ session.db 已就绪' : '✗ 未找到 session.db'}
                  </span>
                </div>
              ))}
              <p className="text-xs text-text-muted mt-1">确认无误后点击下方「保存配置」并重启机器人即可生效</p>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Warning size={18} weight="fill" className="text-status-warn" />
              <span className="text-sm text-status-warn">{detectResult.message}</span>
            </div>
          )}
        </div>
      )}

      {detectError && (
        <div className="mt-3 flex items-center gap-2 px-4 py-2.5 bg-status-error-soft border border-status-error/20 rounded-full text-sm text-status-error">
          <Warning size={16} weight="fill" className="text-status-error" />
          <span>{detectError}</span>
        </div>
      )}

      {detectedDataDir && !hasCustomPath && (
        <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-brand-green-light border border-brand-green/20 rounded-full text-xs text-brand-green-hover dark:text-brand-green font-medium">
          <CheckCircle size={14} weight="fill" />
          <span className="truncate font-mono">自动检测: {detectedDataDir}</span>
        </div>
      )}
      {!detectedDataDir && !hasCustomPath && (
        <p className="text-xs text-text-muted mt-3 leading-relaxed">
          ⚠ 未检测到默认微信数据目录，请手动指定包含 <code className="bg-bg-raised px-1.5 py-0.5 rounded font-mono text-xs">wxid_*</code> 文件夹的父目录
        </p>
      )}
      {hasCustomPath && (
        <p className="text-xs text-text-muted mt-3 leading-relaxed">
          💡 已设置自定义路径。留空则恢复自动检测（{detectedDataDir || '默认 Documents 目录'}）
        </p>
      )}

      {/* ── Directory Browser Modal ────────────────────────────────── */}
      {browseOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-main/60 backdrop-blur-sm" onClick={() => setBrowseOpen(false)}>
          <div
            className="bg-bg-card border border-border-main rounded-2xl shadow-2xl w-[520px] max-h-[520px] flex flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-main/60">
              <h4 className="text-sm font-semibold text-text-main">选择微信数据目录</h4>
              <button
                type="button"
                onClick={() => setBrowseOpen(false)}
                className="text-text-muted hover:text-text-main transition-colors cursor-pointer leading-none text-lg"
              >&times;</button>
            </div>

            {/* Path input (paste-able) */}
            <div className="px-5 py-3 border-b border-border-main/40">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={browseInput}
                  onChange={e => setBrowseInput(e.target.value)}
                  onKeyDown={handleBrowseInputKeyDown}
                  placeholder="粘贴或输入路径，回车跳转..."
                  className="flex-1 bg-bg-raised border border-border-main rounded-full px-4 py-2 text-[13px] text-text-main placeholder:text-text-muted font-mono
                             focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                             transition-all duration-200 hover:border-text-muted/30"
                />
                <button
                  type="button"
                  onClick={handleBrowseGo}
                  disabled={!browseInput.trim()}
                  className="shrink-0 px-4 py-2 bg-brand-green-light border border-brand-green/20 rounded-full text-[13px] text-brand-green-hover dark:text-brand-green font-semibold hover:bg-brand-green/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
                >
                  跳转
                </button>
              </div>
            </div>

            {/* Path breadcrumb */}
            <div className="px-5 py-2.5 bg-bg-raised/50 border-b border-border-main/40">
              <div className="flex items-center gap-1.5 text-xs font-mono text-text-muted">
                <button
                  type="button"
                  onClick={navigateUp}
                  disabled={!browsePath || browsePath.length <= 3}
                  className="text-text-muted hover:text-text-main disabled:opacity-30 disabled:cursor-default cursor-pointer transition-colors"
                  title="上级目录"
                >↑</button>
                <span className="truncate">{browsePath || '此电脑'}</span>
              </div>
            </div>

            {/* Entry list */}
            <div className="flex-1 overflow-y-auto px-2 py-1.5">
              {browseLoading ? (
                <div className="flex items-center justify-center py-12">
                  <svg className="animate-spin h-5 w-5 text-text-muted" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                </div>
              ) : browseError ? (
                <div className="p-4 text-xs text-status-error text-center">{browseError}</div>
              ) : browseEntries.length === 0 ? (
                <div className="p-4 text-xs text-text-muted text-center">此目录为空</div>
              ) : (
                browseEntries.filter(e => e.is_dir).map((entry, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => navigateTo(entry.path)}
                    className="w-full text-left px-3 py-2 rounded-xl text-[13px] text-text-main hover:bg-bg-raised transition-colors cursor-pointer flex items-center gap-2.5 font-mono"
                  >
                    <span className="text-base shrink-0">📁</span>
                    <span className="truncate">{entry.name}</span>
                  </button>
                ))
              )}
            </div>

            {/* Footer */}
            <div className="px-5 py-3.5 border-t border-border-main/60 flex items-center justify-between">
              <p className="text-xs text-text-muted truncate max-w-[340px] font-mono">
                当前: {browsePath || '—'}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setBrowseOpen(false)}
                  className="px-4 py-2 rounded-full border border-border-main bg-bg-main text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer font-medium"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={selectCurrentPath}
                  className="px-4 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                >
                  选择此目录
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Push History (推送记录) ──────────────────────────────────────────

const PUSH_TYPE_LABELS = {
  keyword_alert: '关键词提醒',
  group_digest: '群定时摘要',
  oa_digest: '公众号摘要',
  oa_article_alert: '公众号即时',
}

/* Type badge styles — each type has a distinct color for quick identification */
const PUSH_TYPE_BADGE = {
  keyword_alert: 'bg-amber-500/[0.10] text-amber-600 dark:text-amber-400 border-amber-500/20',
  group_digest: 'bg-blue-500/[0.10] text-blue-600 dark:text-blue-400 border-blue-500/20',
  oa_digest: 'bg-brand-green/[0.10] text-brand-green-hover dark:text-brand-green border-brand-green/20',
  oa_article_alert: 'bg-orange-500/[0.10] text-orange-600 dark:text-orange-400 border-orange-500/20',
}

const PUSH_TYPE_ICONS = {
  keyword_alert: '⚡',
  group_digest: '📋',
  oa_digest: '📰',
  oa_article_alert: '🔔',
}

function PushHistory() {
  const [records, setRecords] = useState([])
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState({ type: '', push_status: '' })

  useEffect(() => { loadHistory() }, [filters.type, filters.push_status])

  // Listen for push result events to auto-refresh
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type?.endsWith('_push_result')) {
          loadHistory()
        }
      } catch {}
    }
    let ws = window.__push_history_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(`ws://${API_BASE.replace(/^https?:\/\//, '')}/ws`)
      window.__push_history_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  async function loadHistory() {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        ...(filters.type ? { type: filters.type } : {}),
        ...(filters.push_status ? { push_status: filters.push_status } : {}),
        limit: '50',
      })
      const res = await fetch(`${API_BASE}/api/push/history?${params}`)
      const data = await res.json()
      if (data.ok) setRecords(data.records || [])
    } catch {}
    setLoading(false)
  }

  return (
    <div className="py-4 border-t border-border-main/50">
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <PaperPlaneTilt size={18} className="text-brand-green" />
          <p className="text-[15px] text-text-main font-medium">推送记录</p>
        </div>
        <button onClick={loadHistory} className="text-xs text-brand-green-hover hover:underline cursor-pointer font-medium">
          刷新
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-2 mb-4">
        <select value={filters.type} onChange={e => setFilters(prev => ({ ...prev, type: e.target.value }))}
          className="bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-brand-green">
          <option value="">全部类型</option>
          <option value="keyword_alert">关键词提醒</option>
          <option value="group_digest">群定时摘要</option>
          <option value="oa_digest">公众号摘要</option>
          <option value="oa_article_alert">公众号即时</option>
        </select>
        <select value={filters.push_status} onChange={e => setFilters(prev => ({ ...prev, push_status: e.target.value }))}
          className="bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-sm text-text-main focus:outline-none focus:border-brand-green">
          <option value="">全部状态</option>
          <option value="success">推送成功</option>
          <option value="failed">推送失败</option>
        </select>
      </div>

      {/* Records */}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-text-muted py-8 justify-center">
          <CircleNotch size={14} className="animate-spin" /> 加载中...
        </div>
      ) : records.length === 0 ? (
        <div className="py-10 text-center">
          <PaperPlaneTilt size={28} className="text-text-muted mx-auto mb-2" />
          <p className="text-xs text-text-muted">暂无推送记录</p>
          <p className="text-xs text-text-muted mt-1">开启推送后，投递记录会出现在这里</p>
        </div>
      ) : (
        <div className="space-y-2 max-h-[480px] overflow-y-auto">
          {records.map(r => (
            <PushRecordCard key={r.id} record={r} />
          ))}
        </div>
      )}
    </div>
  )
}

function PushRecordCard({ record }) {
  const [expanded, setExpanded] = useState(false)
  const isFailed = record.push_status === 'failed'
  const isSuccess = record.push_status === 'success'
  const isSessionExpired = (record.push_error || '').includes('session_expired')
  const typeLabel = PUSH_TYPE_LABELS[record.type] || record.type
  const typeBadge = PUSH_TYPE_BADGE[record.type] || 'bg-bg-raised text-text-muted border-border-main'
  const typeIcon = PUSH_TYPE_ICONS[record.type] || '📌'
  const groupName = record.group_name || record.chat_id || ''
  const rawContent = record.content || ''

  // Format time: remove T between date and time
  const rawTime = record.push_at || record.created_at || ''
  const displayTime = rawTime.replace('T', ' ')

  // ── Parse JSON content (new format) or fall back to plain text ──
  let parsed = null
  if (rawContent.startsWith('{')) {
    try { parsed = JSON.parse(rawContent) } catch {}
  }

  // Legacy plain text parsing (backwards compat, old DB entries)
  let kwSender = ''
  let kwKeywords = ''
  let kwBody = ''
  if (record.type === 'keyword_alert' && !parsed) {
    const lines = rawContent.split('\n')
    let bodyStart = -1
    lines.forEach((line, i) => {
      if (line.startsWith('[发送人]')) kwSender = line.replace('[发送人]', '').trim()
      else if (line.startsWith('发送人:')) kwSender = line.replace('发送人:', '').trim()
      if (line.startsWith('[关键词]')) kwKeywords = line.replace('[关键词]', '').trim()
      else if (line.startsWith('关键词:')) kwKeywords = line.replace('关键词:', '').trim()
      else if (line.startsWith('命中关键词:')) kwKeywords = line.replace('命中关键词:', '').trim()
      if (line.startsWith('[消息]')) {
        const rest = line.replace('[消息]', '').trim()
        if (rest) { kwBody = rest; bodyStart = -1 }
        else { bodyStart = i + 1 }
      } else if (line.startsWith('消息内容:')) { bodyStart = i + 1 }
    })
    if (bodyStart >= 0) {
      kwBody = lines.slice(bodyStart).join('\n').trim()
      kwBody = kwBody.replace(/^wxid_[a-zA-Z0-9_]+:\s*\n?/m, '').trim()
    }
  }

  // ── Parse group_digest content ──
  let digestMsgCount = ''
  if (record.type === 'group_digest') {
    const m = (record.title || '').match(/\((\d+)条消息\)/)
    if (m) digestMsgCount = m[1]
  }

  // ── Parse oa_digest content ──
  let oaArticleCount = ''
  if (record.type === 'oa_digest') {
    const m = (record.title || '').match(/(\d+)篇/)
    if (m) oaArticleCount = m[1]
  }

  // ── Expand/collapse logic ──
  const mainContent = record.type === 'keyword_alert' ? kwBody : rawContent
  const needsExpand = mainContent.length > 100
  const displayContent = expanded ? mainContent : (needsExpand ? mainContent.slice(0, 100) + '...' : mainContent)

  return (
    <div className={`bg-bg-card border rounded-xl px-[18px] py-4 transition-all hover:border-border-main/80 ${
      isFailed ? 'border-l-[3px] border-l-status-error/60 border-border-main/50' : 'border-border-main/50'
    }`}>
      {/* Row 1: type badge + status + time */}
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className={`text-[11px] px-2 py-0.5 rounded-md font-semibold ${typeBadge}`}>
          {typeIcon} {typeLabel}
        </span>
        <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${
          isSuccess ? 'text-brand-green' : isFailed ? 'text-status-error' : 'text-status-warn'
        }`}>
          <span className={`w-[5px] h-[5px] rounded-full ${
            isSuccess ? 'bg-brand-green' : isFailed ? 'bg-status-error' : 'bg-status-warn'
          }`} />
          {isSuccess ? '推送成功' : isFailed ? '推送失败' : record.push_status}
        </span>
        <span className="text-[11px] text-text-muted">{displayTime}</span>
      </div>

      {/* Row 2: group name */}
      {groupName && (
        <p className="text-[13px] text-text-main font-semibold mb-1.5">{groupName}</p>
      )}

      {/* Row 3: type-specific content */}
      <div className="text-[12px] text-text-muted leading-relaxed">
        {parsed ? (() => {
          if (record.type === 'keyword_alert') {
            return <div className="space-y-1"><div className="font-medium text-text-main/90">{parsed.sender}</div><div className="bg-bg-inset rounded-lg p-2.5 border border-border-main/40 text-text-main/80 whitespace-pre-wrap">{parsed.message}</div><div className="flex flex-wrap gap-1">{(parsed.keywords || []).map((kw,i) => <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-status-warn-soft text-status-warn font-medium">{kw}</span>)}</div></div>
          }
          if (record.type === 'group_digest') {
            return <div className="space-y-1"><div className="font-medium text-text-main/90">{parsed.group}</div><div className="bg-bg-inset rounded-lg p-2.5 border border-border-main/40 text-text-main/80 max-h-48 overflow-y-auto">{parsed.digest}</div><div className="text-text-muted">{parsed.msg_count ?? 0} 条 · 近{parsed.lookback_hours}h</div></div>
          }
          if (record.type === 'oa_digest') {
            return <div className="space-y-1"><div className="font-medium text-text-main/90">{parsed.group}</div><div className="bg-bg-inset rounded-lg p-2.5 border border-border-main/40 text-text-main/80 max-h-48 overflow-y-auto">{parsed.digest}</div><div className="text-text-muted">{parsed.articles_count ?? 0} 篇文章</div></div>
          }
          if (record.type === 'oa_article_alert') {
            return <div className="space-y-1"><div className="font-medium text-text-main/90">{parsed.article_title || parsed.title}</div><div className="text-text-muted">{parsed.group}{parsed.time ? ' · ' + parsed.time : ''}</div>{parsed.digest && <div className="text-text-muted line-clamp-2">{parsed.digest}</div>}{parsed.url && <a href={parsed.url} target='_blank' className='text-brand-green hover:underline break-all inline-block mt-0.5'>{parsed.url}</a>}</div>
          }
          return <div className="text-text-main/80">{parsed.display || displayContent}</div>
        })() : (
          <>
            {/* Group digest / OA digest: summary content */}
            <span className="text-text-main/70">{displayContent}</span>
            {needsExpand && (
              <button onClick={() => setExpanded(!expanded)}
                className="float-right text-[11px] text-brand-green-hover dark:text-brand-green font-medium hover:underline cursor-pointer ml-2">
                {expanded ? '收起' : '展开'}
              </button>
            )}
            {(digestMsgCount || oaArticleCount) && (
              <span className="block mt-0.5">
                {digestMsgCount && `共 ${digestMsgCount} 条有效消息`}
                {oaArticleCount && `共 ${oaArticleCount} 篇文章`}
              </span>
            )}
          </>
        )}
      </div>

      {/* Error block */}
      {isFailed && record.push_error && (
        <div className="mt-2 px-2.5 py-2 bg-status-error/5 rounded-md text-[11px] text-status-error leading-relaxed">
          {record.push_error}
          {isSessionExpired && (
            <span className="block mt-1 text-status-warn">
              ⚠️ 请在微信中给机器人发一条消息恢复推送
            </span>
          )}
        </div>
      )}
    </div>
  )
}


// ── Push Section (微信推送 iLink Bot) ──────────────────────────────

function PushSection() {
  const [ilinkStatus, setIlinkStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [binding, setBinding] = useState(false)
  const [qrcodeUrl, setQrcodeUrl] = useState('')
  const [qrcodeId, setQrcodeId] = useState('')
  const [qrStatus, setQrStatus] = useState('')
  const [testResult, setTestResult] = useState('')
  const [persistentPushError, setPersistentPushError] = useState('')
  const [pushProgress, setPushProgress] = useState([])  // { attempt, max_retries, delay, message }
  const [pushModalVisible, setPushModalVisible] = useState(false)
  const [unbindConfirm, setUnbindConfirm] = useState(false)
  const [showActivateModal, setShowActivateModal] = useState(false)

  useEffect(() => {
    loadStatus()
  }, [])

  async function loadStatus() {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/ilink/status`)
      const data = await res.json()
      setIlinkStatus(data)
      // Load persistent push error from backend (survives page reopen)
      if (data.push_error_message) {
        setPersistentPushError(data.push_error_message)
      }
    } catch {}
    setLoading(false)
  }

  async function startBinding() {
    setBinding(true)
    setQrStatus('loading')
    setTestResult('')
    try {
      const res = await fetch(`${API_BASE}/api/ilink/qrcode`)
      const data = await res.json()
      if (data.ok) {
        setQrcodeUrl(data.qrcode_url)
        setQrcodeId(data.qrcode_id)
        setQrStatus('waiting')
        // Start polling
        pollQrStatus(data.qrcode_id)
      } else {
        setQrStatus('error')
        setTestResult(data.error || '获取二维码失败')
        setBinding(false)
      }
    } catch (e) {
      setQrStatus('error')
      setTestResult('网络请求失败')
      setBinding(false)
    }
  }

  async function pollQrStatus(qId) {
    let attempts = 0
    const maxAttempts = 180 // safety cap ~3 minutes (1s × 180)
    const POLL_INTERVAL = 1000
    // iLink quirk: expired QR codes cause the status API to timeout
    // instead of returning {"status": "expired"}. One timeout = expired.
    const poll = async () => {
      if (attempts >= maxAttempts) {
        setQrStatus('expired')
        return
      }
      attempts++
      try {
        const res = await fetch(`${API_BASE}/api/ilink/qrcode-status?qrcode=${encodeURIComponent(qId)}`)
        const data = await res.json()
        if (data.status === 'confirmed') {
          setQrStatus('confirmed')
          await loadStatus()
          setTestResult('')
          setShowActivateModal(true)
          setTimeout(() => setBinding(false), 2000)
          return
        }
        if (data.status === 'scaned') {
          setQrStatus('scaned')
          setTimeout(poll, POLL_INTERVAL)
          return
        }
        if (data.status === 'expired') {
          setQrStatus('expired')
          return
        }
        if (data.status === 'error') {
          // iLink timeout on status check → QR has expired
          if (data.code === 'timeout') {
            setQrStatus('expired')
            return
          }
          // Connection error — stop immediately
          if (data.code === 'connection') {
            setQrStatus('error')
            setTestResult(data.error || '网络连接不稳定')
            return
          }
          // Business errors — stop immediately
          setQrStatus('error')
          const friendlyErrors = {
            'not_support': '当前微信版本不支持此功能',
            'forbid': '操作被拒绝，请稍后重试',
            'reject': '扫码被拒绝',
          }
          setTestResult(friendlyErrors[data.code] || data.error || '扫码失败')
          return
        }
        // wait — continue polling
        setTimeout(poll, POLL_INTERVAL)
      } catch {
        setQrStatus('error')
        setTestResult('网络请求失败，请检查后重试')
      }
    }
    poll()
  }

  async function handleTestPush() {
    setTestResult('')
    setPushProgress([])
    setPushModalVisible(true)

    try {
      const res = await fetch(`${API_BASE}/api/ilink/test-push`, { method: 'POST' })
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Parse SSE events from buffer
        const lines = buffer.split('\n')
        buffer = ''
        let currentEvent = ''
        let currentData = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim()
          } else if (line === '') {
            // Empty line = end of event
            if (currentEvent && currentData) {
              try {
                const data = JSON.parse(currentData)
                if (currentEvent === 'retry') {
                  setPushProgress(prev => [...prev, data])
                } else if (currentEvent === 'success') {
                  setTestResult('success')
                  setPushModalVisible(false)
                  setPersistentPushError('')
                } else if (currentEvent === 'error') {
                  const errMsg = data.error || data.detail || '推送失败'
                  setTestResult(errMsg)
                  setPersistentPushError(errMsg)
                  // 不自动关闭弹窗，让用户看到错误后手动关闭
                }
              } catch {}
            }
            currentEvent = ''
            currentData = ''
          } else {
            // Incomplete line — save back to buffer
            buffer = line + '\n' + buffer
          }
        }
      }
    } catch (e) {
      setTestResult('网络请求失败')
      // 不自动关闭弹窗，让用户看到错误后手动关闭
    }
  }

  async function handleUnbind() {
    if (!unbindConfirm) {
      setUnbindConfirm(true)
      setTimeout(() => setUnbindConfirm(false), 5000)
      return
    }
    try {
      await fetch(`${API_BASE}/api/ilink/unbind`, { method: 'POST' })
      setUnbindConfirm(false)
      await loadStatus()
      setTestResult('')
    } catch {}
  }

  const isBound = ilinkStatus?.bound === true

  return (
    <div className="space-y-6">
      {/* ── iLink 绑定状态 ── */}
      <div className="py-4 border-b border-border-main/50">
        <div className="flex items-center gap-2 mb-3">
          <PaperPlaneTilt size={18} className="text-brand-green" />
          <p className="text-[15px] text-text-main font-medium">iLink Bot 推送通道</p>
        </div>
        <p className="text-sm text-text-muted leading-relaxed mb-4">
          绑定微信 iLink Bot 后，群聊摘要和公众号摘要可直接推送到你的微信私聊。
          你需要在微信中扫码绑定 Bot，然后给 Bot 发一条消息激活。
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-text-muted text-sm">
            <CircleNotch size={16} className="animate-spin" />
            加载中...
          </div>
        ) : isBound ? (
          <div className="space-y-4">
            {/* 已绑定状态 */}
            <div className="flex items-center gap-3 p-4 bg-brand-green-light/30 border border-brand-green/20 rounded-xl">
              <CheckCircle size={20} weight="fill" className="text-brand-green flex-shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-medium text-text-main">已绑定</p>
                <p className="text-xs text-text-muted mt-0.5">
                  Bot ID: {ilinkStatus.account_id || '—'} · 用户: {ilinkStatus.user_id || '—'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2.5">
              <button
                onClick={handleTestPush}
                disabled={pushModalVisible}
                className={`flex items-center gap-1.5 px-4 py-2 rounded-full text-white text-xs font-semibold transition-colors ${
                  pushModalVisible
                    ? 'bg-brand-green-hover/50 cursor-not-allowed'
                    : 'bg-brand-green-hover hover:bg-[#0d8c5c] cursor-pointer'
                }`}
              >
                <TestTube size={14} /> {pushModalVisible ? '推送中...' : '发送测试消息'}
              </button>
              <button
                onClick={handleUnbind}
                className={`flex items-center gap-1.5 px-4 py-2 rounded-full border text-xs font-semibold transition-colors cursor-pointer ${
                  unbindConfirm
                    ? 'bg-status-error-soft border-status-error/30 text-status-error'
                    : 'border-border-main bg-bg-raised text-text-muted hover:text-status-error hover:border-status-error/30'
                }`}
              >
                <SignOut size={14} /> {unbindConfirm ? '确认解除绑定？' : '解除绑定'}
              </button>
            </div>

            {/* 推送异常提示 — 微信可能静默吞消息 */}
            <div className="mt-3 p-3 rounded-xl bg-status-warn-soft/40 border border-status-warn/15">
              <p className="text-[11px] text-text-muted leading-relaxed">
                💡 如果收不到推送消息，可能是推送通道过期。请尝试：
              </p>
              <ul className="text-[11px] text-text-muted leading-relaxed mt-1 list-disc list-inside space-y-0.5">
                <li>给 Bot 的微信私聊发送任意消息重新激活</li>
                <li>或解除绑定后重新扫码绑定</li>
              </ul>
            </div>

            {/* 推送进度弹窗 — SSE 流式展示每次重试状态 */}
            {pushModalVisible && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" style={{ backdropFilter: 'blur(4px)' }}>
                <div className="w-[380px] rounded-2xl overflow-hidden" style={{
                  background: 'var(--bg-card, #1a1a1a)',
                  border: '1px solid var(--border-main, rgba(255,255,255,0.1))',
                  boxShadow: '0 24px 80px rgba(0,0,0,0.4)',
                }}>
                  <div className="p-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center"
                          style={{ background: testResult && testResult !== 'success' ? 'var(--status-error-soft, rgba(239,68,68,0.15))' : 'var(--brand-green-light, rgba(16,185,129,0.15))' }}>
                          {testResult && testResult !== 'success' ? (
                            <X size={16} className="text-status-error" />
                          ) : (
                            <CircleNotch size={16} className="animate-spin text-brand-green" />
                          )}
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-text-main">
                            {testResult && testResult !== 'success' ? '推送测试失败' : '正在推送测试消息'}
                          </p>
                          {testResult && testResult !== 'success' ? (
                            <p className="text-xs text-status-error mt-0.5">{testResult}</p>
                          ) : (
                            <p className="text-xs text-text-muted mt-0.5">iLink 限流保护中，请稍候...</p>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => setPushModalVisible(false)}
                        className="text-text-muted hover:text-text-main cursor-pointer p-1.5 rounded-lg hover:bg-bg-raised transition-colors"
                        title="关闭"
                      >
                        <X size={18} />
                      </button>
                    </div>

                    {/* 重试进度列表 */}
                    <div className="space-y-2">
                      {pushProgress.length === 0 && !(testResult && testResult !== 'success') && (
                        <div className="flex items-center gap-2 py-2">
                          <div className="w-4 h-4 rounded-full border-2 border-border-strong border-t-brand-green animate-spin" />
                          <span className="text-xs text-text-muted">连接推送服务中...</span>
                        </div>
                      )}
                      {pushProgress.map((p, i) => (
                        <div key={i} className="flex items-start gap-2.5 py-2 border-t border-border-main/50">
                          <div className="w-5 h-5 rounded-full bg-status-warn-soft flex items-center justify-center shrink-0 mt-0.5">
                            <span className="text-[10px] text-status-warn font-bold">{p.attempt}</span>
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-xs text-text-muted">
                              第 {p.attempt}/{p.max_retries} 次重试
                            </p>
                            <p className="text-[11px] text-text-muted/60 mt-0.5">
                              {p.message}
                            </p>
                          </div>
                          <div className="shrink-0">
                            <div className="w-16 h-1.5 rounded-full bg-bg-inset overflow-hidden">
                              <div
                                className="h-full rounded-full bg-status-warn transition-all"
                                style={{ width: `${(p.attempt / p.max_retries) * 100}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* 错误时底部关闭按钮 */}
                    {testResult && testResult !== 'success' && (
                      <div className="mt-4 pt-3 border-t border-border-main/40">
                        <button
                          onClick={() => setPushModalVisible(false)}
                          className="w-full py-2 rounded-xl bg-bg-raised hover:bg-bg-inset text-text-main text-sm font-medium transition-colors cursor-pointer"
                        >
                          关闭
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {testResult === 'success' && (
              <p className="text-xs text-brand-green-hover dark:text-brand-green font-medium">
                ✅ 测试消息已发送，请检查微信
              </p>
            )}
            {testResult && testResult !== 'success' && (
              <p className="text-xs text-status-error font-medium">
                ❌ {testResult}
              </p>
            )}
            {/* 持久化推送错误 — 即使关闭弹窗或重新打开页面仍显示，直到再次推送成功 */}
            {!testResult && persistentPushError && (
              <div className="p-3 rounded-xl bg-status-error-soft/40 border border-status-error/15">
                <p className="text-xs text-status-error font-medium">
                  ❌ {persistentPushError}
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {/* 未绑定状态 */}
            {binding ? (
              <div className="space-y-4">
                <div className="flex flex-col items-center p-6 bg-bg-raised border border-border-main rounded-xl">
                  {(qrStatus === 'waiting' || qrStatus === 'scaned') && qrcodeUrl && (
                    <>
                      <QRCodeSVG
                        value={qrcodeUrl}
                        size={192}
                        level="M"
                        bgColor="transparent"
                        fgColor="currentColor"
                        className={`text-text-main mb-3 transition-opacity duration-300 ${qrStatus === 'scaned' ? 'opacity-40' : ''}`}
                      />
                      {qrStatus === 'scaned' ? (
                        <>
                          <p className="text-sm text-brand-green font-medium">已扫码，请在手机上确认</p>
                          <p className="text-xs text-text-muted mt-1">等待确认中...</p>
                        </>
                      ) : (
                        <>
                          <p className="text-sm text-text-main font-medium">请用微信扫描二维码</p>
                          <p className="text-xs text-text-muted mt-1">等待扫码（二维码可能较快过期，请及时扫描）</p>
                        </>
                      )}
                    </>
                  )}
                  {qrStatus === 'confirmed' && (
                    <div className="flex items-center gap-2 text-brand-green-hover dark:text-brand-green">
                      <CheckCircle size={20} weight="fill" />
                      <p className="text-sm font-medium">绑定成功！</p>
                    </div>
                  )}
                  {qrStatus === 'expired' && (
                    <div className="text-center">
                      <p className="text-sm text-status-error mb-1">扫码超时了，再试试吧</p>
                      <p className="text-xs text-text-muted mb-3">二维码已过期，请重新获取</p>
                      <button
                        onClick={startBinding}
                        className="px-4 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                      >
                        重新获取二维码
                      </button>
                    </div>
                  )}
                  {qrStatus === 'error' && (
                    <div className="text-center">
                      <p className="text-sm text-status-error mb-1">{testResult || '获取二维码失败'}</p>
                      <p className="text-xs text-text-muted mb-3">请稍后重试</p>
                      <button
                        onClick={startBinding}
                        className="px-4 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                      >
                        重试
                      </button>
                    </div>
                  )}
                  {qrStatus === 'loading' && (
                    <div className="flex items-center gap-2 text-text-muted text-sm">
                      <CircleNotch size={16} className="animate-spin" />
                      获取二维码中...
                    </div>
                  )}
                </div>
                {(qrStatus === 'waiting' || qrStatus === 'scaned') && (
                  <button
                    onClick={() => { setBinding(false); setQrStatus('') }}
                    className="text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
                  >
                    取消
                  </button>
                )}
              </div>
            ) : (
              <div>
                <button
                  onClick={startBinding}
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                >
                  <QrCode size={14} /> 绑定微信 Bot
                </button>
                <p className="text-xs text-text-muted mt-2">
                  绑定后，需在微信中给 Bot 发一条消息激活推送通道
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 激活提示弹窗 ── */}
      <AnimatePresence>
        {showActivateModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-bg-main/70 backdrop-blur-sm"
            onClick={() => setShowActivateModal(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.9, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9, y: 20 }}
              transition={{ type: 'spring', stiffness: 300, damping: 25 }}
              className="bg-bg-card border border-brand-green/30 rounded-2xl shadow-2xl w-[420px] max-w-[90vw] overflow-hidden"
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-center pt-8 pb-4">
                <div className="w-16 h-16 rounded-full bg-brand-green-light flex items-center justify-center">
                  <CheckCircle size={32} weight="fill" className="text-brand-green" />
                </div>
              </div>
              {/* Body */}
              <div className="px-8 pb-3 text-center">
                <h3 className="text-lg font-semibold text-text-main mb-2">绑定成功</h3>
                <div className="p-4 bg-status-warn-soft border border-status-warn/20 rounded-xl mb-4">
                  <p className="text-sm text-status-warn font-semibold leading-relaxed">
                    请立即在微信中给 Bot 发一条消息
                  </p>
                  <p className="text-xs text-text-muted mt-2 leading-relaxed">
                    这是激活推送通道的必要步骤。不发消息，Bot 无法向你推送内容。
	                  </p>
	                  <p className="text-xs text-text-muted mt-2 leading-relaxed border-t border-border-main/30 pt-2">
	                    💡 iLink 推送通道需保持会话活跃。若 1-2 小时未主动给 Bot 发消息，推送会中断，发一条消息即可恢复。
                  </p>
                </div>
                <p className="text-xs text-text-muted leading-relaxed">
                  发送任意内容即可，例如「你好」。激活后可在下方点击「发送测试消息」验证。
                </p>
              </div>
              {/* Footer */}
              <div className="px-8 py-5 border-t border-border-main/40">
                <button
                  onClick={() => setShowActivateModal(false)}
                  className="w-full py-2.5 rounded-full bg-brand-green-hover text-white text-sm font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer"
                >
                  我知道了
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── 使用说明 ── */}
      <div className="py-4">
        <p className="text-[15px] text-text-main font-medium mb-3">推送使用说明</p>
        <div className="space-y-2 text-xs text-text-muted leading-relaxed">
          <p>1. 在此页面绑定 iLink Bot（扫描二维码）</p>
          <p>2. 在微信中给 Bot 发一条消息激活</p>
          <p>3. 在「群聊助手」或「公众号助手」中开启「推送到微信」</p>
          <p>4. 定时摘要触发后，内容会自动推送到你的微信私聊</p>
          <p className="text-text-muted mt-3 border-t border-border-main/30 pt-3">
            iLink 推送是独立通道，不影响现有的微信窗口操控功能。消息限制 4000 字符，超出自动截断。
          </p>
        </div>
      </div>

      {/* ── 推送记录 ── */}
      <PushHistory />
    </div>
  )
}


export default function ConfigPanel({ activeSection, onNavigate }) {
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [importSuccess, setImportSuccess] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // ── Sandbox drawer state ──
  const [sandboxOpen, setSandboxOpen] = useState(false)
  const [sandboxMessages, setSandboxMessages] = useState([])
  const [sandboxInput, setSandboxInput] = useState('')
  const [sandboxLoading, setSandboxLoading] = useState(false)

  async function handleSandboxSend() {
    const text = sandboxInput.trim()
    if (!text || sandboxLoading) return
    setSandboxInput('')
    setSandboxMessages(prev => [...prev, { role: 'user', text }])
    setSandboxLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/sandbox/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          sender_name: '我',
          group_name: '调试台',
          group_memory: '',
          ai_provider_base_url: form.ai_provider_base_url,
          ai_provider_api_key: form.ai_provider_api_key,
          ai_provider_type: form.ai_provider_type,
          ai_provider_model: form.ai_provider_model,
          ai_provider_extra_body: form.ai_provider_extra_body || '',
        })
      })
      const data = await res.json()
      if (data.ok) {
        setSandboxMessages(prev => [...prev, { role: 'ai', text: data.reply }])
      } else {
        setSandboxMessages(prev => [...prev, { role: 'error', text: data.error || '请求失败' }])
      }
    } catch (err) {
      setSandboxMessages(prev => [...prev, { role: 'error', text: err.message || '网络错误' }])
    } finally {
      setSandboxLoading(false)
    }
  }

  function handleSandboxClear() {
    setSandboxMessages([])
    setSandboxInput('')
  }
  const [form, setForm] = useState({
    ai_provider_base_url: '', ai_provider_api_key: '',
    ai_provider_type: 'auto', ai_provider_model: '',
    wechat_backend: 'wcdb', wechat_groups: '*',
    log_level: 'INFO', wechat_data_dir: '',
  })

  async function handleExportConfig() {
    try {
      const res = await fetch(`${API_BASE}/api/config/export`)
      if (!res.ok) throw new Error('导出请求失败')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const downloadAnchor = document.createElement('a')
      downloadAnchor.setAttribute("href", url)
      downloadAnchor.setAttribute("download", `wx-assist-config-${new Date().toISOString().slice(0, 10)}.json`)
      document.body.appendChild(downloadAnchor)
      downloadAnchor.click()
      downloadAnchor.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setSaveError('导出失败：' + e.message)
      setTimeout(() => setSaveError(''), 5000)
    }
  }

  async function handleImportConfig(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = async (event) => {
      try {
        const parsed = JSON.parse(event.target.result)
        const expectedKeys = ['ai_provider_base_url', 'wechat_backend']
        const hasKeys = expectedKeys.some(k => k in parsed)
        if (!hasKeys) {
          throw new Error('无效的配置文件格式')
        }
        // Update local form state immediately for UI feedback
        setForm(prev => ({ ...prev, ...parsed }))
        // Persist to server
        const res = await fetch(`${API_BASE}/api/config/import`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(parsed),
        })
        const data = await res.json()
        if (data.ok) {
          setImportSuccess(true)
          setSaved(false)
          setSaveError('')
          setTimeout(() => setImportSuccess(false), 5000)
        } else {
          throw new Error(data.error || '写入失败')
        }
      } catch (err) {
        setSaveError('导入失败：' + err.message)
        setTimeout(() => setSaveError(''), 5000)
      }
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  // Detected default data dir (auto-detected, shown as placeholder)
  const [detectedDataDir, setDetectedDataDir] = useState('')

  // Load current config from server on mount
  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/load-config`)
        const data = await res.json()
        if (data.ok && data.config) {
          setForm(prev => ({
            ...prev,
            ...data.config,
            wechat_groups: data.config.wechat_groups || '*',
          }))
          if (data.detected_data_dir) {
            setDetectedDataDir(data.detected_data_dir)
          }
        }
      } catch {}
      setLoaded(true)
    }
    load()
  }, [])

  function update(key, value) { setForm(prev => ({ ...prev, [key]: value })); setSaved(false); setSaveError('') }
  async function handleSave() {
    setSaved(false)
    setSaveError('')
    try {
      const res = await fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ai_provider_base_url: form.ai_provider_base_url,
          ai_provider_api_key: form.ai_provider_api_key,
          ai_provider_type: form.ai_provider_type,
          ai_provider_model: form.ai_provider_model,
          ai_provider_extra_body: form.ai_provider_extra_body || '',
          wechat_backend: form.wechat_backend,
          wechat_groups: form.wechat_groups,
          log_level: form.log_level,
          wechat_data_dir: form.wechat_data_dir,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        setSaved(true)
        setTimeout(() => setSaved(false), 3000)
      } else {
        setSaveError(data.error || '保存失败')
        setTimeout(() => setSaveError(''), 5000)
      }
    } catch (e) {
      setSaveError('无法连接到服务器，请确认机器人已启动')
      setTimeout(() => setSaveError(''), 5000)
    }
  }

  return (
    <>
    <div className="max-w-2xl">
      {/* Save status banner */}
      <AnimatePresence>
        {saved && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mb-4 flex items-center gap-2 px-5 py-2.5 bg-brand-green-light border border-brand-green/20 rounded-full text-sm text-brand-green-hover dark:text-brand-green font-medium shadow-sm"
          >
            <CheckCircle size={18} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
            <span>配置已保存。需要重启机器人才能生效。</span>
          </motion.div>
        )}
        {importSuccess && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mb-4 flex items-center gap-2 px-5 py-2.5 bg-brand-green-light border border-brand-green/20 rounded-full text-sm text-brand-green-hover dark:text-brand-green font-medium shadow-sm"
          >
            <CheckCircle size={18} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
            <span>备份配置导入成功！请确认无误后，点击"保存配置"。</span>
          </motion.div>
        )}
        {saveError && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="mb-4 flex items-center gap-2 px-5 py-2.5 bg-status-error-soft border border-status-error/20 rounded-full text-sm text-status-error font-medium shadow-sm"
          >
            <Warning size={18} weight="fill" className="text-status-error" />
            <span>{saveError}</span>
          </motion.div>
        )}
      </AnimatePresence>

      <div style={{ minHeight: 420 }}>
        <AnimatePresence mode="wait">
          <motion.div key={activeSection} variants={pageTransition} initial="initial" animate="animate" exit="exit" transition={{ duration: 0.18 }}>
            <div className="flex items-center gap-2.5 mb-5 pl-1">
              <div className="w-1.5 h-4.5 rounded-full shadow-sm" style={{ backgroundColor: sectionAccents[activeSection] }} />
              <h3 className="text-base font-bold tracking-tight text-text-main">{sectionTitles[activeSection]}</h3>
            </div>
            <div className="bg-bg-card border border-border-main rounded-2xl shadow-[rgba(0,0,0,0.03)_0px_2px_4px] dark:shadow-none">
              <div className="p-7">
                {activeSection === 'ai' && <AiSection form={form} update={update} onOpenSandbox={() => setSandboxOpen(true)} />}
                {activeSection === 'identity' && <IdentitySection form={form} update={update} />}
                {activeSection === 'data' && <DataPathSection form={form} update={update} detectedDataDir={detectedDataDir} />}
                {activeSection === 'features' && <FeaturesSection form={form} update={update} />}
                {activeSection === 'push' && <PushSection />}
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {activeSection !== 'push' && (
        <>
          <div className="mt-8 flex items-center gap-4">
            <motion.button
              whileTap={{ scale: 0.97 }}
              whileHover={{ scale: 1.02 }}
              onClick={handleSave}
              className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide shadow-sm transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer ${
                saved
                  ? 'bg-brand-green-light border border-brand-green/20 text-brand-green-hover dark:text-brand-green font-semibold'
                  : 'bg-brand-green-hover text-white hover:bg-[#0d8c5c]'
              }`}
            >
              {saved ? (
                <><CheckCircle size={18} weight="fill" className="text-brand-green-hover dark:text-brand-green" /> 已保存</>
              ) : (
                <><FloppyDisk size={18} /> 保存配置</>
              )}
            </motion.button>
            {saved ? (
              <span className="flex items-center gap-1.5 text-xs text-status-warn bg-status-warn-soft border border-status-warn/20 px-4 py-1.5 rounded-full font-medium">
                <Info size={14} className="text-status-warn" />
                配置已更新，重启机器人后生效
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-xs text-text-muted bg-bg-raised border border-border-main px-4 py-1.5 rounded-full font-medium">
                <Info size={14} className="text-text-muted opacity-80" />
                保存将应用所有模块的修改，重启后生效
              </span>
            )}
          </div>

          {/* Config Backup & Restore Card */}
          <div className="mt-12 pt-6 border-t border-border-main/50">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-bg-card/40 border border-border-main rounded-2xl p-5">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted">配置备份与导入</h4>
                <p className="text-xs text-text-muted mt-1">导出当前的机器人配置为 JSON 文件，或上传 JSON 备份恢复配置</p>
              </div>
              <div className="flex items-center gap-2.5">
                <button
                  onClick={handleExportConfig}
                  className="px-4 py-2 rounded-full border border-border-main bg-bg-main text-text-main text-xs font-semibold hover:border-text-muted/30 hover:bg-bg-raised transition-all cursor-pointer flex items-center gap-1.5"
                >
                  <DownloadSimple size={14} /> 导出备份
                </button>
                <label className="px-4 py-2 rounded-full border border-border-main bg-bg-main text-text-main text-xs font-semibold hover:border-text-muted/30 hover:bg-bg-raised transition-all cursor-pointer flex items-center gap-1.5">
                  <UploadSimple size={14} /> 导入恢复
                  <input
                    type="file"
                    accept=".json"
                    onChange={handleImportConfig}
                    className="hidden"
                  />
                </label>
              </div>
            </div>
          </div>
        </>
      )}
    </div>

    {/* ── Sandbox Chat Drawer ────────────────────── */}
    <ChatDrawer
      open={sandboxOpen}
      onClose={() => setSandboxOpen(false)}
      title={`AI 对话测试 · ${form.ai_provider_model || '未选模型'}`}
    >
      <div className="flex flex-col h-full">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {sandboxMessages.length === 0 && (
            <div className="flex items-center justify-center h-full text-text-muted text-sm">
              输入消息测试 AI 连通性
            </div>
          )}
          {sandboxMessages.map((m, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'bg-brand-green/12 text-text-main rounded-br-sm'
                  : m.role === 'error'
                    ? 'bg-status-error-soft text-status-error rounded-bl-sm'
                    : 'bg-bg-raised border border-border-main text-text-main rounded-bl-sm'
              }`}>
                {m.role === 'ai' && (
                  <span className="block text-[10px] font-semibold text-brand-green mb-1">AI</span>
                )}
                {m.role === 'ai' ? <TypewriterText text={m.text} /> : m.text}
              </div>
            </motion.div>
          ))}
          {sandboxLoading && (
            <div className="flex justify-start">
              <div className="bg-bg-raised border border-border-main px-4 py-2.5 rounded-2xl rounded-bl-sm">
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <svg className="animate-spin h-3 w-3" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  思考中...
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Input + clear button */}
        <div className="flex items-center gap-2 px-4 py-3 border-t border-border-main">
          {sandboxMessages.length > 0 && (
            <button
              onClick={handleSandboxClear}
              className="p-2 rounded-full text-text-muted hover:text-status-error hover:bg-status-error-soft transition-all cursor-pointer"
              title="清空对话"
            >
              <Trash size={16} />
            </button>
          )}
          <input
            type="text"
            value={sandboxInput}
            onChange={(e) => setSandboxInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSandboxSend() } }}
            placeholder="输入消息..."
            className="flex-1 bg-bg-raised border border-border-main rounded-full px-4 py-2 text-[13px] text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15 transition-all"
            disabled={sandboxLoading}
          />
          <motion.button
            whileTap={{ scale: 0.97 }}
            onClick={handleSandboxSend}
            disabled={sandboxLoading || !sandboxInput.trim()}
            className="px-4 py-2 rounded-full text-[13px] font-semibold bg-brand-green-hover text-white cursor-pointer hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
          >
            发送
          </motion.button>
        </div>
      </div>
    </ChatDrawer>
    </>
  )
}
