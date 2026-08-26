import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, Copy, FloppyDisk, QrCode, SignOut, TestTube, WarningCircle } from '@phosphor-icons/react'
import { QRCodeSVG } from 'qrcode.react'
import { API_BASE } from './SharedComponents'

function defaultConfig(platform) {
  return platform === 'qqbot'
    ? { app_id: '', client_secret: '' }
    : { app_id: '', app_secret: '', verification_token: '' }
}

function SecretInput({ label, value, onChange, placeholder }) {
  return <label className="block space-y-1.5">
    <span className="text-xs font-medium text-text-main">{label}</span>
    <input type="text" value={value ?? ''} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 text-sm text-text-main outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/10" />
  </label>
}

function TextField({ label, value, onChange, placeholder }) {
  return <label className="block space-y-1.5">
    <span className="text-xs font-medium text-text-main">{label}</span>
    <input value={value ?? ''} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 text-sm text-text-main outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/10" />
  </label>
}

function QRPreview({ value, label }) {
  if (!value) return <span className="text-xs text-text-muted">二维码地址未返回</span>
  if (/^(data:image\/|https?:\/\/.*\.(png|jpe?g|gif|webp)(\?.*)?$)/i.test(value)) {
    return <img src={value} alt={`${label}扫码二维码`} className="max-h-full max-w-full" />
  }
  return <QRCodeSVG value={value} size={192} level="M" includeMargin bgColor="#ffffff" fgColor="#111827" />
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
  const [testSending, setTestSending] = useState(false)
  const webhookUrl = `${window.location.origin}/webhook/feishu`
  const isBound = Boolean(config.bound || config.user_openid || config.open_id || config.default_target)

  useEffect(() => setConfig({ ...defaultConfig(platform), ...(initialConfig || {}) }), [platform, initialConfig])
  const update = (key, value) => { setConfig(prev => ({ ...prev, [key]: value })); setMessage(''); setError('') }

  async function startOnboarding() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/onboard/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain: 'feishu' }),
      })
      const data = await res.json()
      if (!res.ok || !data.ok || !data.qr_url) throw new Error(data.error || '平台没有返回有效二维码地址')
      setOnboard({ ...data, status: 'pending' })
    } catch (err) { setError(err.message || '无法创建扫码任务') } finally { setBusy(false) }
  }

  async function cancelOnboarding() {
    if (!onboard?.task_id) return
    await fetch(`${API_BASE}/api/platforms/${platform}/onboard/${onboard.task_id}/cancel`, { method: 'POST' }).catch(() => {})
    setOnboard(null)
  }

  async function unbind() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/unbind`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '解除绑定失败')
      setConfig(prev => ({ ...prev, bound: false, user_openid: '', open_id: '', default_target: '' }))
      setMessage('已解除绑定'); onSaved?.()
    } catch (err) { setError(err.message || '解除绑定失败') } finally { setBusy(false) }
  }

  useEffect(() => {
    if (!onboard?.task_id || onboard.status === 'completed') return undefined
    const timer = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/platforms/${platform}/onboard/${onboard.task_id}/status`)
        const data = await res.json()
        if (data.status === 'completed') {
          setOnboard(data)
          setConfig(prev => ({
            ...prev,
            ...(isQQ ? {
              app_id: data.app_id || prev.app_id,
              client_secret: data.client_secret || prev.client_secret,
              user_openid: data.user_openid || prev.user_openid,
              bound: true,
            } : {
              app_id: data.app_id || prev.app_id,
              app_secret: data.app_secret || prev.app_secret,
              open_id: data.open_id || prev.open_id,
              bound: true,
            }),
          }))
          setMessage(data.applied === false ? '扫码成功，配置已保存；启动助手后生效' : '扫码绑定成功，平台已自动启用')
          onSaved?.()
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
      const res = await fetch(`${API_BASE}/api/platforms/${platform}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '保存失败')
      setMessage('配置已保存并应用'); onSaved?.()
    } catch (err) { setError(err.message || '保存失败') } finally { setBusy(false) }
  }

  async function testConnection() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '连接测试失败')
      setMessage('连接测试成功')
    } catch (err) { setError(err.message || '连接测试失败') } finally { setBusy(false) }
  }

  async function sendTestMessage() {
    setTestSending(true); setError(''); setMessage('正在发送测试消息...')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/test-message`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '测试消息发送失败')
      setMessage(`测试消息已发送${data.message_id ? `（消息 ID: ${data.message_id}）` : ''}，请检查${label}客户端`)
    } catch (err) { setError(err.message || '测试消息发送失败'); setMessage('') }
    finally { setTestSending(false) }
  }

  function copyWebhook() {
    navigator.clipboard.writeText(webhookUrl).then(() => setMessage('Webhook 地址已复制')).catch(() => setError('复制失败，请手动复制'))
  }

  return <div className="space-y-6">
    <div className="flex items-start gap-3">
      <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-1.5 text-text-muted hover:bg-bg-raised hover:text-text-main"><ArrowLeft size={18} /></button>
      <div><h4 className="text-[15px] font-semibold text-text-main">{label} 配置</h4><p className="mt-1 text-xs text-text-muted">绑定后自动启用平台推送，不需要额外开关。</p></div>
    </div>
    <div className="rounded-xl border border-status-info/20 bg-status-info/5 px-4 py-3 text-xs text-text-muted">Secret 按本地配置正常回显，仅保存在本机配置中；请勿截图或提交到代码仓库。</div>
    <div className="flex items-center gap-1 rounded-lg border border-border-main p-1 w-fit">
      <button type="button" onClick={() => setMode('qr')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'qr' ? 'bg-brand-green text-white' : 'text-text-muted'}`}>扫码绑定</button>
      <button type="button" onClick={() => setMode('manual')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'manual' ? 'bg-brand-green text-white' : 'text-text-muted'}`}>手动配置</button>
    </div>
    {mode === 'qr' && <div className="rounded-xl border border-border-main bg-bg-raised p-5 text-center space-y-4">
      {isBound && !onboard ? <>
        <div className="flex items-start gap-3 rounded-xl border border-brand-green/20 bg-brand-green-light/30 px-4 py-3 text-left"><CheckCircle size={20} weight="fill" className="mt-0.5 shrink-0 text-brand-green" /><div><p className="text-sm font-semibold text-text-main">已绑定</p><p className="mt-1 break-all text-xs text-text-muted">{isQQ ? `Bot ID: ${config.app_id || '—'} · 用户: ${config.user_openid || '—'}` : `应用: ${config.app_id || '—'} · 用户: ${config.open_id || '—'}`}</p></div></div>
        <div className="flex flex-wrap justify-center gap-2"><button type="button" disabled={testSending || busy} onClick={sendTestMessage} className="inline-flex items-center gap-1.5 rounded-full bg-brand-green-hover px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"><TestTube size={14} />{testSending ? '发送中...' : '发送测试消息'}</button><button type="button" disabled={busy} onClick={unbind} className="inline-flex items-center gap-1.5 rounded-full border border-border-main px-4 py-2 text-xs font-semibold text-text-muted hover:text-status-error disabled:opacity-50"><SignOut size={14} />解除绑定</button></div>
      </> : !onboard ? <><QrCode size={28} className="mx-auto text-brand-green" /><p className="text-sm font-medium text-text-main">使用{label}扫码绑定机器人</p><p className="text-xs text-text-muted">后端返回绑定 URL，页面将把它生成真正可扫描的二维码。</p><button type="button" disabled={busy} onClick={startOnboarding} className="rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-green-hover disabled:opacity-50">{busy ? '创建中...' : '生成二维码'}</button></> : <><div className="mx-auto flex h-52 w-52 items-center justify-center rounded-lg border border-border-main bg-white p-2"><QRPreview value={onboard.qr_url} label={label} /></div><p className="text-sm text-text-main">{onboard.status === 'completed' ? '扫码绑定成功' : onboard.status === 'scaned' ? '已扫码，请在手机上确认' : '等待扫码授权...'}</p>{onboard.user_code && <p className="text-xs text-text-muted">验证码：{onboard.user_code}</p>}{onboard.status !== 'completed' && <button type="button" onClick={cancelOnboarding} className="rounded-lg border border-border-main px-4 py-2 text-sm text-text-muted">取消</button>}</>}
      {error && <p className="text-xs text-status-error">{error}</p>}
    </div>}
    {mode === 'manual' && <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2"><TextField label="App ID" value={config.app_id} onChange={v => update('app_id', v)} /><SecretInput label={isQQ ? 'Client Secret' : 'App Secret'} value={isQQ ? config.client_secret : config.app_secret} onChange={v => update(isQQ ? 'client_secret' : 'app_secret', v)} /></div>
      {!isQQ && <><TextField label="Verification Token" value={config.verification_token} onChange={v => update('verification_token', v)} /><div><span className="text-xs font-medium text-text-main">事件订阅地址</span><div className="mt-1.5 flex gap-2"><input readOnly value={webhookUrl} className="min-w-0 flex-1 rounded-lg border border-border-main bg-bg-raised px-3 py-2 text-xs text-text-muted" /><button type="button" onClick={copyWebhook} className="rounded-lg border border-border-main px-3 text-xs text-text-main"><Copy size={15} /></button></div></div></>}
      <div className="flex items-center justify-end gap-2 border-t border-border-main pt-5"><button type="button" onClick={testConnection} disabled={busy} className="rounded-lg border border-border-main px-4 py-2.5 text-sm font-semibold text-text-main">{busy ? '处理中...' : '测试连接'}</button><button type="button" onClick={save} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white"><FloppyDisk size={16} />保存并应用</button></div>
    </div>}
    {(message || error) && <div className={`flex items-center gap-2 text-xs ${error ? 'text-status-error' : 'text-brand-green'}`}>{error ? <WarningCircle size={16} /> : <CheckCircle size={16} weight="fill" />}{error || message}</div>}
  </div>
}
