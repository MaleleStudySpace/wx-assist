import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle, ArrowRight, Spinner, XCircle, Warning, MagnifyingGlass, CircleNotch, Lightning, ChatCircle, CaretDown, CaretRight } from '@phosphor-icons/react'
import { Field, Select, Input, Toggle, spring, API_BASE } from './SharedComponents'

const API = API_BASE

// ── Step 1: Key Extraction & Diagnostics ──────────────────────────────

export function Step1Prepare({ data, updateData, onDone }) {
  const [phase, setPhase] = useState('idle') // idle | extracting | done | timeout | error
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [isManual, setIsManual] = useState(false)
  const [manualKey, setManualKey] = useState(data.key || '')
  const [manualWxid, setManualWxid] = useState(data.wxid || '')
  const [manualDbPath, setManualDbPath] = useState(data.db_path || '')

  // Save wechat config (formerly Step 2) before advancing to AI config
  async function saveWechatConfig(wxid, dbPath) {
    try {
      await fetch(`${API}/api/onboarding/step2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wechat_backend: 'wcdb',
          wxid: wxid || '',
          db_path: dbPath || '',
        }),
      })
    } catch {}
  }

  // Pre-flight diagnostics state
  const [diagnostics, setDiagnostics] = useState(null)
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(true)
  const [diagnosticsError, setDiagnosticsError] = useState('')

  async function fetchDiagnostics() {
    setDiagnosticsLoading(true)
    setDiagnosticsError('')
    try {
      const res = await fetch(`${API}/api/onboarding/diagnose`)
      const d = await res.json()
      if (d.ok) {
        setDiagnostics(d.diagnostics)
      } else {
        setDiagnosticsError(d.error || '获取诊断信息失败')
      }
    } catch {
      setDiagnosticsError('无法连接服务器，请确保机器人后端已启动')
    }
    setDiagnosticsLoading(false)
  }

  useEffect(() => {
    fetchDiagnostics()
  }, [])

  const isManualValid =
    manualKey.trim().length === 64 &&
    /^[0-9a-fA-F]+$/.test(manualKey.trim()) &&
    manualWxid.trim().length > 0 &&
    manualDbPath.trim().length > 0;

  async function handleExtract() {
    setBusy(true)
    setPhase('extracting')
    setMsg('')
    try {
      const startRes = await fetch(`${API}/api/onboarding/step1`, { method: 'POST' })
      const start = await startRes.json()
      if (!start.ok) {
        setPhase('error')
        setMsg(start.message || '启动失败')
        setBusy(false)
        return
      }

      const poll = setInterval(async () => {
        try {
          const res = await fetch(`${API}/api/onboarding/step1-status`)
          const s = await res.json()

          if (s.phase === 'waiting_exit' || s.phase === 'waiting_login'
              || s.phase === 'hooking' || s.phase === 'hooking_restart') {
            setMsg(s.message || '')
          } else if (s.phase === 'done' && s.result) {
            clearInterval(poll)
            updateData({ key: s.result.key, wxid: s.result.wxid, db_path: s.result.db_path })
            setPhase('done')
            setBusy(false)
            saveWechatConfig(s.result.wxid, s.result.db_path).then(onDone)
          } else if (s.phase === 'timeout' || s.phase === 'error') {
            clearInterval(poll)
            setPhase(s.phase === 'timeout' ? 'timeout' : 'error')
            setMsg(s.message || '提取失败')
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

  function handleManualSubmit() {
    const wxid = manualWxid.trim()
    const dbPath = manualDbPath.trim()
    updateData({
      key: manualKey.trim(),
      wxid: wxid,
      db_path: dbPath,
    })
    saveWechatConfig(wxid, dbPath).then(onDone)
  }

  function renderChecklist() {
    if (diagnosticsLoading) {
      return (
        <div className="flex flex-col items-center justify-center py-12 space-y-3 bg-bg-raised border border-border-main rounded-2xl">
          <Spinner size={28} weight="bold" className="animate-spin text-brand-green" />
          <p className="text-sm text-text-muted font-mono">正在分析本地系统就绪状态...</p>
        </div>
      )
    }

    if (diagnosticsError) {
      return (
        <div className="p-6 bg-status-error-soft border border-status-error/20 rounded-2xl text-sm text-status-error flex items-center justify-between">
          <div className="flex items-center gap-2">
            <XCircle size={20} weight="fill" />
            <span>{diagnosticsError}</span>
          </div>
          <button
            onClick={fetchDiagnostics}
            className="px-4 py-2 bg-status-error/20 hover:bg-status-error/30 rounded-full text-xs font-semibold cursor-pointer transition-colors text-status-error"
          >
            重新连接
          </button>
        </div>
      )
    }

    if (!diagnostics) return null

    const items = [
      {
        key: 'python',
        title: 'Python 运行环境',
        desc: diagnostics.python.value,
        ok: diagnostics.python.ok,
        critical: true,
      },
      {
        key: 'requirements',
        title: 'Python 依赖库 (requirements.txt)',
        desc: diagnostics.requirements.ok
          ? diagnostics.requirements.value
          : `缺少依赖: ${diagnostics.requirements.missing.join(', ')}`,
        ok: diagnostics.requirements.ok,
        critical: true,
        help: !diagnostics.requirements.ok ? '请打开终端运行: pip install -r requirements.txt' : null,
      },
      {
        key: 'wechat',
        title: '微信电脑端状态',
        desc: diagnostics.wechat.value,
        ok: diagnostics.wechat.ok,
        critical: false,
        help: !diagnostics.wechat.ok ? '自动连接需要微信处于登录状态。若微信已登录但检测为未运行，请检查微信版本。' : null,
      },
      {
        key: 'env',
        title: '本地环境配置文件 (.env)',
        desc: diagnostics.env.value,
        ok: diagnostics.env.ok,
        critical: false,
      },
      {
        key: 'db',
        title: '本地数据库读写权限',
        desc: diagnostics.db.value,
        ok: diagnostics.db.ok,
        critical: true,
      }
    ]

    return (
      <div className="space-y-4">
        <div className="bg-bg-raised border border-border-main rounded-2xl divide-y divide-border-main/40">
          {items.map(item => (
            <div key={item.key} className="p-4 flex items-start justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-text-main">{item.title}</span>
                  {!item.ok && item.critical && (
                    <span className="text-xs bg-status-error-soft text-status-error px-2.5 py-0.5 rounded-full border border-status-error/30 font-bold">
                      阻塞项
                    </span>
                  )}
                </div>
                <p className={`text-xs ${item.ok ? 'text-text-muted' : 'text-status-warn'} font-mono`}>
                  {item.desc}
                </p>
                {item.help && (
                  <p className="text-[11px] text-text-muted bg-bg-main/45 p-2 rounded-2xl border border-border-main/30 font-mono mt-2 select-all leading-normal">
                    {item.help}
                  </p>
                )}
              </div>
              <div className="shrink-0 flex items-center h-5">
                {item.ok ? (
                  <CheckCircle size={20} weight="fill" className="text-brand-green" />
                ) : item.critical ? (
                  <XCircle size={20} weight="fill" className="text-status-error" />
                ) : (
                  <div className="w-5 h-5 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center">
                    <span className="text-status-warn text-xs font-mono font-bold">!</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="flex justify-between items-center bg-bg-raised/30 px-4 py-3 rounded-2xl border border-border-main/40">
          <span className="text-xs text-text-muted">环境诊断能保障自动提取及数据库访问正常。</span>
          <button
            onClick={fetchDiagnostics}
            className="text-xs text-brand-green-hover dark:text-brand-green hover:underline transition-colors font-medium flex items-center gap-1 cursor-pointer font-semibold"
          >
            重新检测
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
          <h3 className="text-base font-semibold tracking-tight text-text-main">环境准备与连接</h3>
        </div>
        <button
          onClick={() => setIsManual(!isManual)}
          className="text-xs text-brand-green-hover dark:text-brand-green hover:underline cursor-pointer font-medium"
        >
          {isManual ? '返回环境诊断' : '无法获取？手动配置'}
        </button>
      </div>

      <div className="bg-bg-card rounded-2xl p-1 space-y-6">
        {isManual ? (
          <div className="space-y-5">
            <p className="text-[14px] text-text-muted leading-relaxed">
              您在此可手动填写连接凭证和相关环境信息。
            </p>
            <Field label="连接凭证 (64位十六进制)" hint="获取到的64位 hex 凭证" error={manualKey && (manualKey.trim().length !== 64 || !/^[0-9a-fA-F]+$/.test(manualKey.trim())) ? '连接凭证格式不正确，必须为64位16进制字符' : null}>
              <Input
                type="password"
                value={manualKey}
                onChange={setManualKey}
                placeholder="例如：68a1f28b4c2..."
              />
            </Field>
            <Field label="微信账号 ID (wxid)" hint="当前微信账号的内部 ID (以 wxid_ 开头，或自定义微信号)">
              <Input
                value={manualWxid}
                onChange={setManualWxid}
                placeholder="例如：wxid_xxxxxxxxxxxxxx"
              />
            </Field>
            <Field label="聊天数据库路径 (db_path)" hint="微信本地 session.db 或 MSG.db 的绝对路径">
              <Input
                value={manualDbPath}
                onChange={setManualDbPath}
                placeholder="例如：C:\Users\Username\Documents\WeChat Files\wxid_...\db_storage\session\session.db"
              />
            </Field>
            <motion.button
              whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
              onClick={handleManualSubmit}
              disabled={!isManualValid}
              className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer ${
                isManualValid
                  ? 'bg-brand-green-hover text-white hover:opacity-90'
                  : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
              }`}
            >
              <ArrowRight size={18} /> 保存并下一步
            </motion.button>
          </div>
        ) : (
          <>
            {phase === 'idle' && (
              <div className="space-y-6">
                <p className="text-[14px] text-text-muted leading-relaxed">
                  摘星需要与微信建立连接以读取聊天记录。连接过程无侵入，不会影响微信正常使用。
                </p>

                {renderChecklist()}

                <div className="pt-4 border-t border-border-main/40 flex items-center gap-4">
                  <motion.button
                    whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
                    onClick={handleExtract}
                    disabled={diagnosticsLoading || (diagnostics && !diagnostics.wechat.ok)}
                    className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer ${
                      diagnostics && diagnostics.wechat.ok
                        ? 'bg-brand-green-hover text-white hover:opacity-90 animate-pulse'
                        : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
                    }`}
                  >
                    开始自动获取
                  </motion.button>
                  {diagnostics && !diagnostics.wechat.ok && (
                    <span className="text-xs text-status-warn bg-status-warn-soft border border-status-warn/20 px-4 py-2 rounded-full font-medium">
                      请登录微信电脑端，否则无法自动获取
                    </span>
                  )}
                </div>
              </div>
            )}

            {phase === 'extracting' && (
              <div className="space-y-6">
                <p className="text-[14px] text-text-muted leading-relaxed">
                  正在协同微信进程，获取本地数据连接凭证，请勿关闭微信。
                </p>
                <div className="bg-bg-raised border border-border-main rounded-2xl p-6 flex items-start gap-4">
                  <Spinner size={24} weight="bold" className="animate-spin text-brand-green shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-text-main">自动连接中...</p>
                    <p className="text-xs text-text-muted font-mono">{msg || '等待微信窗口激活...'}</p>
                  </div>
                </div>
                {/* Mini-terminal keeps dark layout for developer style */}
                <div className="bg-bg-card border border-border-main rounded-2xl p-4 font-mono text-xs text-text-muted space-y-1">
                  <div className="flex justify-between border-b border-border-main/30 pb-1 mb-2 text-text-muted font-semibold">
                    <span>连接状态</span>
                    <span>ACTIVE</span>
                  </div>
                  <div>[1] 正在建立连接...</div>
                  {msg.includes('waiting_exit') && <div className="text-status-warn">[!] 检测到微信在运行，请先退出微信以便重新挂钩...</div>}
                  {msg.includes('waiting_login') && <div className="text-brand-green">[+] 微信已重新挂钩，请在弹出的微信界面进行登录...</div>}
                  {msg && <div className="text-text-muted">&gt; {msg}</div>}
                </div>
                <button
                  onClick={() => setPhase('idle')}
                  className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-xs text-text-muted hover:text-text-main rounded-full border border-border-main cursor-pointer transition-colors"
                >
                  取消并返回
                </button>
              </div>
            )}

            {phase === 'timeout' && (
              <div className="space-y-6">
                <div className="bg-status-error-soft border border-status-error/20 rounded-2xl p-6 flex items-start gap-4 text-status-error">
                  <XCircle size={24} weight="fill" className="text-status-error shrink-0" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-status-error">连接超时</p>
                    <p className="text-xs text-text-muted">{msg || '连接超时，请确保您成功登录了微信。'}</p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleExtract}
                    className="px-4 py-2 bg-brand-green-hover text-white hover:opacity-90 text-sm font-semibold rounded-full cursor-pointer transition-colors"
                  >
                    重试自动获取
                  </button>
                  <button
                    onClick={() => setPhase('idle')}
                    className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-sm text-text-muted rounded-full border border-border-main cursor-pointer transition-colors"
                  >
                    返回诊断
                  </button>
                </div>
              </div>
            )}

            {phase === 'error' && (
              <div className="space-y-6">
                <div className="bg-status-error-soft border border-status-error/20 rounded-2xl p-6 flex items-start gap-4 text-status-error">
                  <XCircle size={24} weight="fill" className="text-status-error shrink-0" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-status-error">自动提取失败</p>
                    <p className="text-xs text-text-muted">{msg}</p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleExtract}
                    className="px-4 py-2 bg-brand-green-hover text-white hover:opacity-90 text-sm font-semibold rounded-full cursor-pointer transition-colors"
                  >
                    重新获取
                  </button>
                  <button
                    onClick={() => setPhase('idle')}
                    className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-sm text-text-muted rounded-full border border-border-main cursor-pointer transition-colors"
                  >
                    返回诊断
                  </button>
                </div>
              </div>
            )}

            {phase === 'done' && (
              <div className="space-y-5">
                <div className="bg-brand-green-light border border-brand-green/20 rounded-2xl p-5 flex items-center gap-3">
                  <CheckCircle size={24} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
                  <div>
                    <p className="text-sm font-semibold text-brand-green-hover dark:text-brand-green">连接成功</p>
                    <p className="text-xs text-text-muted">系统配置已就绪</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
                    <p className="text-xs text-text-muted mb-1 font-semibold">微信账号 wxid</p>
                    <p className="text-sm font-mono text-text-main truncate font-bold">{data.wxid || '—'}</p>
                  </div>
                  <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
                    <p className="text-xs text-text-muted mb-1 font-semibold">数据库文件</p>
                    <p className="text-xs font-mono text-text-main truncate">{data.db_path ? data.db_path.split('\\').slice(-2).join('\\') : '—'}</p>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Step 2: WeChat Config ────────────────────────────────────────────

function Step2WeChatConfig({ data, updateData, onDone }) {
  const [busy, setBusy] = useState(false)
  const valid = true  // no required fields

  async function handleNext() {
    setBusy(true)
    try {
      await fetch(`${API}/api/onboarding/step2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wechat_backend: 'wcdb',
          wxid: data.wxid || '',
          db_path: data.db_path || '',
        }),
      })
      onDone()
    } catch {}
    setBusy(false)
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">微信配置</h3>
      </div>

      <div className="space-y-6 mt-4">
        {/* Read-only info from step 1 */}
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
            <p className="text-xs text-text-muted font-semibold mb-1">检测到的微信账号</p>
            <p className="text-sm font-mono font-bold text-text-main truncate">{data.wxid || '未检测到'}</p>
          </div>
          <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
            <p className="text-xs text-text-muted font-semibold mb-1">数据路径</p>
            <p className="text-xs font-mono text-text-muted truncate" title={data.db_path}>{data.db_path || '—'}</p>
          </div>
        </div>

        <motion.button
          whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
          onClick={handleNext}
          disabled={!valid || busy}
          className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50 ${
            valid
              ? 'bg-brand-green-hover text-white hover:opacity-90'
              : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
          }`}
        >
          {busy ? <Spinner size={18} weight="bold" className="animate-spin" /> : <><ArrowRight size={18} /> 下一步</>}
        </motion.button>
      </div>
    </div>
  )
}

// ── Step 3: AI Backend ────────────────────────────────────────────────

export function Step3AIConfig({ data, updateData, onDone }) {
  const [detecting, setDetecting] = useState(false)
  const [detectResult, setDetectResult] = useState(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [fullUrlMode, setFullUrlMode] = useState(false)

  // When toggling fullUrlMode, also update provider_type to custom
  function toggleFullUrlMode(val) {
    setFullUrlMode(val)
    if (val) {
      updateData({ ai_provider_type: 'custom' })
    } else {
      if (data.ai_provider_type === 'custom') {
        updateData({ ai_provider_type: 'openai' })
      }
    }
    setDetectResult(null)
  }

  async function handleDetect() {
    if (!data.ai_provider_base_url || !data.ai_provider_api_key) return
    setDetecting(true)
    setDetectResult(null)
    // If fullUrlMode is on, don't detect — user provided complete URL
    if (fullUrlMode) {
      setDetectResult({ provider_type: 'custom', available_models: [], error: '' })
      setDetecting(false)
      return
    }
    try {
      const res = await fetch(`${API}/api/assistant/ai/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: data.ai_provider_base_url,
          api_key: data.ai_provider_api_key,
          provider_type: data.ai_provider_type || 'openai',
        }),
      })
      const result = await res.json()
      setDetectResult(result)
      if (result.provider_type) {
        updateData({ ai_provider_type: result.provider_type })
        if (result.available_models?.length > 0 && !data.ai_provider_model) {
          updateData({ ai_provider_model: result.available_models[0] })
        }
        if (result.available_models?.some(m => m.startsWith('deepseek')) && !data.ai_provider_extra_body) {
          updateData({ ai_provider_extra_body: '{"thinking":{"type":"disabled"}}' })
        }
      }
    } catch {
      setDetectResult({ error: '网络请求失败，请检查站点 URL' })
    } finally {
      setDetecting(false)
    }
  }

  async function handleNext() {
    setDetecting(true)
    try {
      await fetch(`${API}/api/onboarding/step3`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ai_provider_base_url: data.ai_provider_base_url || '',
          ai_provider_api_key: data.ai_provider_api_key || '',
          ai_provider_type: data.ai_provider_type || 'openai',
          ai_provider_model: data.ai_provider_model || '',
          ai_provider_extra_body: data.ai_provider_extra_body || '',
        }),
      })
      onDone()
    } catch {}
    setDetecting(false)
  }

  function handleSkip() {
    onDone()
  }

  const providerLabel = { openai: 'OpenAI 兼容', anthropic: 'Anthropic 兼容', custom: '自定义端点' }
  const providerBadgeColor = { openai: 'bg-emerald-50 border-emerald-200 text-emerald-700', anthropic: 'bg-purple-50 border-purple-200 text-purple-700', custom: 'bg-amber-50 border-amber-200 text-amber-700' }
  const models = detectResult?.available_models?.length
    ? detectResult.available_models
    : (data.ai_provider_model ? [data.ai_provider_model] : [])
  const hasKey = (data.ai_provider_base_url || '').trim() && (data.ai_provider_api_key || '').trim()

  const apiFormatOptions = [
    { value: 'openai', desc: 'OpenAI Chat Completions 格式' },
    { value: 'anthropic', desc: 'Anthropic 格式' },
  ]

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-status-info" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">AI 后端配置</h3>
        <span className="text-xs text-text-muted ml-1">可跳过，稍后在系统配置中设置</span>
      </div>

      <div className="space-y-6 mt-4">
        <Field label="AI 站点 URL" hint={fullUrlMode
          ? '请填写完整请求 URL，将直接使用此 URL，不拼接路径'
          : '输入 API 根地址，不要以斜杠结尾，例如 https://api.deepseek.com'}>
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <Input
                value={data.ai_provider_base_url || ''}
                onChange={v => { updateData({ ai_provider_base_url: v }); setDetectResult(null) }}
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
            value={data.ai_provider_api_key || ''}
            onChange={v => { updateData({ ai_provider_api_key: v }); setDetectResult(null) }}
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
            disabled={detecting || !hasKey}
            className={`flex-1 py-2.5 rounded-full text-[14px] font-semibold tracking-wide shadow-sm transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer
              ${detecting || !hasKey
                ? 'bg-bg-raised border border-border-main text-text-muted cursor-not-allowed'
                : 'bg-brand-green-light border border-brand-green/20 text-brand-green-hover hover:shadow-md'}`}
          >
            {detecting ? (
              <><CircleNotch size={16} className="animate-spin" />检测中...</>
            ) : (
              <><MagnifyingGlass size={16} />检测模型</>
            )}
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
            value={data.ai_provider_model || ''}
            onChange={v => updateData({ ai_provider_model: v })}
            placeholder="输入或选择模型 ID"
          />
          {models.length > 1 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              <span className="text-[10px] text-text-muted">检测到：</span>
              {models.map(m => (
                <button
                  key={m}
                  type="button"
                  onClick={() => updateData({ ai_provider_model: m })}
                  className={`px-2 py-0.5 rounded-full text-[11px] font-mono cursor-pointer transition-colors border ${
                    data.ai_provider_model === m
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

        {showAdvanced && (
          <div className="space-y-4 pl-1">
            <Field label="API 格式">
              <Select
                value={data.ai_provider_type === 'custom' ? 'openai' : (data.ai_provider_type || 'openai')}
                onChange={v => updateData({ ai_provider_type: v })}
                options={apiFormatOptions}
              />
            </Field>

            <Field label="附加参数 (JSON)" hint='合并到请求体，覆盖默认值。如 {"temperature": 0.8, "top_p": 0.9}。常用: temperature, top_p, max_tokens, stop, presence_penalty, frequency_penalty'>
              <Input
                value={data.ai_provider_extra_body || ''}
                onChange={v => updateData({ ai_provider_extra_body: v })}
                placeholder='{"thinking":{"type":"disabled"}}'
              />
            </Field>
          </div>
        )}

        <div className="flex items-center gap-3">
          <motion.button
            whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
            onClick={handleNext}
            disabled={!hasKey || detecting}
            className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50 ${
              hasKey
                ? 'bg-brand-green-hover text-white hover:opacity-90'
                : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
            }`}
          >
            {detecting ? <CircleNotch size={18} className="animate-spin" /> : <><ArrowRight size={18} /> 下一步</>}
          </motion.button>

          <motion.button
            whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
            onClick={handleSkip}
            className="px-5 py-2.5 rounded-full text-[14px] font-medium text-text-muted hover:text-text-main transition-colors cursor-pointer"
          >
            跳过，稍后配置
          </motion.button>
        </div>
      </div>
    </div>
  )
}

// ── Step 4: Feature Overview ──────────────────────────────────────────

const CAPABILITY_CARDS = [
  {
    icon: '💬',
    title: '会话管理',
    desc: '浏览聊天记录，AI 对话查询聊天记录，聊天气泡富渲染，聊天导出归档。',
  },
  {
    icon: '🤖',
    title: '智能助手',
    desc: '群聊关键词提醒 + 定时 AI 摘要 + 公众号即时提醒。每个群独立配置，摘要可推送通知。',
  },
  {
    icon: '⭐',
    title: '收藏 & 朋友圈',
    desc: '收藏按类型/标签/关键词筛选导出；朋友圈浏览、图片灯箱、视频下载、HTML 归档。',
  },
  {
    icon: '📊',
    title: '运行状态',
    desc: '实时日志流、AI 调试台、系统配置中心。启停 Bot，查看消息统计和 AI 调用延迟。',
  },
]

export function Step4Features({ data, updateData, onComplete }) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  async function handleFinish() {
    setBusy(true)
    try {
      await fetch(`${API}/api/onboarding/step4`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
    } catch {}
    setDone(true)
    setTimeout(onComplete, 1200)
    setBusy(false)
  }

  if (done) {
    return (
      <motion.div initial={{opacity:0,scale:0.95}} animate={{opacity:1,scale:1}} transition={spring}
        className="flex flex-col items-center justify-center py-16">
        <motion.div initial={{scale:0}} animate={{scale:1}} transition={{delay:0.1,...spring}}
          className="w-20 h-20 rounded-full bg-brand-green/10 border border-brand-green/20 flex items-center justify-center mb-6 shadow-sm">
          <CheckCircle size={38} weight="fill" className="text-brand-green" />
        </motion.div>
        <h2 className="text-lg font-bold text-text-main mb-2">配置就绪</h2>
        <p className="text-sm text-text-muted font-medium">正在启动夜航控制台仪表盘...</p>
      </motion.div>
    )
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">功能概览</h3>
      </div>

      <p className="text-sm text-text-muted mb-5 leading-relaxed">
        微信助手的核心能力一览，所有功能无需额外配置，进入主界面即可使用。
      </p>

      <div className="grid grid-cols-2 gap-4">
        {CAPABILITY_CARDS.map(card => (
          <div key={card.title} className="bg-bg-raised border border-border-main rounded-2xl p-5">
            <div className="text-xl mb-3">{card.icon}</div>
            <div className="text-sm font-semibold text-text-main mb-1.5">{card.title}</div>
            <div className="text-xs text-text-muted leading-relaxed">{card.desc}</div>
          </div>
        ))}
      </div>

      <div className="mt-8 pt-4">
        <motion.button
          whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
          onClick={handleFinish}
          disabled={busy}
          className="w-56 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer bg-brand-green-hover text-white hover:opacity-90 animate-pulse"
        >
          {busy ? <Spinner size={18} weight="bold" className="animate-spin" /> : <><CheckCircle size={18} /> 开始使用</>}
        </motion.button>
      </div>
    </div>
  )
}
