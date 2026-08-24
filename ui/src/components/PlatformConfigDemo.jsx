import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, Copy, Eye, EyeSlash, FloppyDisk, WarningCircle } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

function defaultConfig(platform) {
  return platform === 'qqbot'
    ? { enabled: false, app_id: '', client_secret: '' }
    : { enabled: false, app_id: '', app_secret: '', verification_token: '' }
}

function SecretInput({ label, value, onChange, placeholder }) {
  const [visible, setVisible] = useState(false)
  return <label className="block space-y-1.5">
    <span className="text-xs font-medium text-text-main">{label}</span>
    <div className="relative">
      <input type={visible ? 'text' : 'password'} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 pr-10 text-sm text-text-main outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/10" />
      <button type="button" onClick={() => setVisible(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main">
        {visible ? <EyeSlash size={17} /> : <Eye size={17} />}
      </button>
    </div>
  </label>
}

function TextField({ label, value, onChange, placeholder }) {
  return <label className="block space-y-1.5">
    <span className="text-xs font-medium text-text-main">{label}</span>
    <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 text-sm text-text-main outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/10" />
  </label>
}

export default function PlatformConfigDemo({ platform, initialConfig, onBack, onSaved }) {
  const label = platform === 'qqbot' ? 'QQ' : '飞书'
  const isQQ = platform === 'qqbot'
  const [mode, setMode] = useState('qr')
  const [config, setConfig] = useState({ ...defaultConfig(platform), ...(initialConfig || {}) })
  const [onboard, setOnboard] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const webhookUrl = `${window.location.origin}/webhook/feishu`

  useEffect(() => setConfig({ ...defaultConfig(platform), ...(initialConfig || {}) }), [platform, initialConfig])

  const update = (key, value) => { setConfig(prev => ({ ...prev, [key]: value })); setMessage(''); setError('') }

  async function startOnboarding() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/onboard/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain: 'feishu' }),
      })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '无法创建扫码任务')
      setOnboard({ ...data, status: 'pending' })
    } catch (err) { setError(err.message || '无法创建扫码任务') } finally { setBusy(false) }
  }

  async function cancelOnboarding() {
    if (!onboard?.task_id) return
    await fetch(`${API_BASE}/api/platforms/${platform}/onboard/${onboard.task_id}/cancel`, { method: 'POST' }).catch(() => {})
    setOnboard(null)
  }

  useEffect(() => {
    if (!onboard?.task_id || onboard.status === 'completed') return undefined
    const timer = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/platforms/${platform}/onboard/${onboard.task_id}/status`)
        const data = await res.json()
        if (data.status === 'completed') {
          setOnboard(data); setMessage(data.applied === false ? '扫码成功，配置已保存；启动助手后生效' : '扫码绑定成功，平台配置已保存'); onSaved?.()
        } else if (['expired', 'denied', 'cancelled'].includes(data.status)) {
          setOnboard(data); setError(data.error || '扫码任务已结束')
        } else setOnboard(prev => ({ ...prev, ...data }))
      } catch (err) { setError(err.message || '扫码状态查询失败') }
    }, platform === 'qqbot' ? 2000 : 5000)
    return () => window.clearInterval(timer)
  }, [onboard?.task_id, onboard?.status, platform, onSaved])

  async function save() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
      })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '保存失败')
      setMessage('配置已保存并应用'); onSaved?.()
    } catch (err) { setError(err.message || '保存失败') } finally { setBusy(false) }
  }

  async function testConnection() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
      })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '连接测试失败')
      setMessage('连接测试成功')
    } catch (err) { setError(err.message || '连接测试失败') } finally { setBusy(false) }
  }

  async function copyWebhook() {
    try { await navigator.clipboard.writeText(webhookUrl); setMessage('Webhook 地址已复制') } catch { setError('复制失败，请手动复制') }
  }

  return <div className="space-y-6">
    <div className="flex items-start gap-3">
      <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-1.5 text-text-muted hover:bg-bg-raised hover:text-text-main"><ArrowLeft size={18} /></button>
      <div><h4 className="text-[15px] font-semibold text-text-main">{label} 配置</h4><p className="mt-1 text-xs text-text-muted">扫码绑定优先，手动配置作为备用。</p></div>
    </div>
    <div className="rounded-xl border border-status-info/20 bg-status-info/5 px-4 py-3 text-xs text-text-muted">Secret 不会回显；扫码成功后凭证由后端保存并自动应用。</div>
    <div className="flex items-center gap-1 rounded-lg border border-border-main p-1 w-fit">
      <button type="button" onClick={() => setMode('qr')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'qr' ? 'bg-brand-green text-white' : 'text-text-muted'}`}>扫码绑定</button>
      <button type="button" onClick={() => setMode('manual')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'manual' ? 'bg-brand-green text-white' : 'text-text-muted'}`}>手动配置</button>
    </div>
    {mode === 'qr' && <div className="rounded-xl border border-border-main bg-bg-raised p-5 text-center space-y-4">
      {!onboard ? <><p className="text-sm font-medium text-text-main">使用{label}扫码自动获取机器人配置</p><p className="text-xs text-text-muted">官方扫码注册流程，成功后自动保存并启动平台。</p><button type="button" disabled={busy} onClick={startOnboarding} className="rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-green-hover disabled:opacity-50">{busy ? '创建中...' : '生成二维码'}</button></> : <><div className="mx-auto flex h-52 w-52 items-center justify-center rounded-lg border border-border-main bg-white p-2">{onboard.qr_url ? <img src={onboard.qr_url} alt={`${label}扫码二维码`} className="max-h-full max-w-full" /> : <span className="text-xs text-text-muted">二维码地址未返回</span>}</div><p className="text-sm text-text-main">{onboard.status === 'completed' ? '扫码绑定成功' : '等待扫码授权...'}</p>{onboard.status !== 'completed' && <button type="button" onClick={cancelOnboarding} className="rounded-lg border border-border-main px-4 py-2 text-sm text-text-muted">取消</button>}</>}
      {error && <p className="text-xs text-status-error">{error}</p>}
    </div>}
    {mode === 'manual' && <div className="space-y-5">
      <label className="flex items-center justify-between rounded-xl border border-border-main bg-bg-raised px-4 py-3"><div><p className="text-sm font-medium text-text-main">启用{label}推送</p><p className="mt-1 text-xs text-text-muted">启用后出现在推送方式中。</p></div><button type="button" onClick={() => update('enabled', !config.enabled)} className={`relative h-6 w-11 rounded-full ${config.enabled ? 'bg-brand-green' : 'bg-border-main'}`}><span className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow ${config.enabled ? 'left-6' : 'left-1'}`} /></button></label>
      <div className="grid gap-4 md:grid-cols-2"><TextField label="App ID" value={config.app_id} onChange={v => update('app_id', v)} /><SecretInput label={platform === 'qqbot' ? 'Client Secret' : 'App Secret'} value={platform === 'qqbot' ? config.client_secret : config.app_secret} onChange={v => update(platform === 'qqbot' ? 'client_secret' : 'app_secret', v)} /></div>
      {!isQQ && <><TextField label="Verification Token" value={config.verification_token} onChange={v => update('verification_token', v)} /><div><span className="text-xs font-medium text-text-main">事件订阅地址</span><div className="mt-1.5 flex gap-2"><input readOnly value={webhookUrl} className="min-w-0 flex-1 rounded-lg border border-border-main bg-bg-raised px-3 py-2 text-xs text-text-muted" /><button type="button" onClick={copyWebhook} className="rounded-lg border border-border-main px-3 text-xs text-text-main"><Copy size={15} /></button></div></div></>}
      <div className="flex items-center justify-end gap-2 border-t border-border-main pt-5"><button type="button" onClick={testConnection} disabled={busy} className="rounded-lg border border-border-main px-4 py-2.5 text-sm font-semibold text-text-main">{busy ? '处理中...' : '测试连接'}</button><button type="button" onClick={save} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white"><FloppyDisk size={16} />保存并应用</button></div>
    </div>}
    {(message || error) && <div className={`flex items-center gap-2 text-xs ${error ? 'text-status-error' : 'text-brand-green'}`}>{error ? <WarningCircle size={16} /> : <CheckCircle size={16} weight="fill" />}{error || message}</div>}
  </div>
}
