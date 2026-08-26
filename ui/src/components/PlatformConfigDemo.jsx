import { useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle, QrCode, SignOut, TestTube, WarningCircle } from '@phosphor-icons/react'
import { QRCodeSVG } from 'qrcode.react'
import { API_BASE } from './SharedComponents'

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
  const [onboard, setOnboard] = useState(null)
  const [config, setConfig] = useState(initialConfig || {})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [testSending, setTestSending] = useState(false)
  const isBound = Boolean(config.bound || config.user_openid || config.open_id || config.default_target)

  useEffect(() => {
    setConfig(initialConfig || {})
    setOnboard(null)
    setError('')
    setMessage('')
  }, [platform, initialConfig])

  async function startOnboarding() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/onboard/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: isQQ ? '{}' : JSON.stringify({ domain: 'feishu' }),
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

  async function sendTestMessage() {
    setTestSending(true); setError(''); setMessage('正在发送测试消息...')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/test-message`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '测试消息发送失败')
      setMessage(`测试消息已发送${data.message_id ? `（消息 ID: ${data.message_id}）` : ''}，请检查${label}客户端`)
    } catch (err) { setError(err.message || '测试消息发送失败'); setMessage('') }
    finally { setTestSending(false) }
  }

  async function unbind() {
    setBusy(true); setError(''); setMessage('')
    try {
      const res = await fetch(`${API_BASE}/api/platforms/${platform}/unbind`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok || !data.ok) throw new Error(data.error || '解除绑定失败')
      setConfig({})
      setOnboard(null)
      setMessage('已解除绑定，平台配置已清空')
      onSaved?.()
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
  }, [onboard?.task_id, onboard?.status, platform, isQQ, onSaved])

  return <div className="space-y-6">
    <div className="flex items-start gap-3">
      <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-1.5 text-text-muted hover:bg-bg-raised hover:text-text-main"><ArrowLeft size={18} /></button>
      <div><h4 className="text-[15px] font-semibold text-text-main">{label}绑定</h4><p className="mt-1 text-xs text-text-muted">仅支持扫码绑定，绑定后自动启用平台推送。</p></div>
    </div>
    {isBound && !onboard ? <div className="space-y-4 rounded-xl border border-border-main bg-bg-raised p-5">
      <div className="flex items-start gap-3 rounded-xl border border-brand-green/20 bg-brand-green-light/30 px-4 py-3"><CheckCircle size={20} weight="fill" className="mt-0.5 shrink-0 text-brand-green" /><div><p className="text-sm font-semibold text-text-main">已绑定</p><p className="mt-1 break-all text-xs text-text-muted">{isQQ ? `Bot ID: ${config.app_id || '—'} · 用户: ${config.user_openid || '—'}` : `应用: ${config.app_id || '—'} · 用户: ${config.open_id || '—'}`}</p></div></div>
      <div className="flex flex-wrap gap-2"><button type="button" disabled={testSending || busy} onClick={sendTestMessage} className="inline-flex items-center gap-1.5 rounded-full bg-brand-green-hover px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"><TestTube size={14} />{testSending ? '发送中...' : '发送测试消息'}</button><button type="button" disabled={busy} onClick={unbind} className="inline-flex items-center gap-1.5 rounded-full border border-border-main px-4 py-2 text-xs font-semibold text-text-muted hover:text-status-error disabled:opacity-50"><SignOut size={14} />解除绑定</button></div>
    </div> : <div className="space-y-4 rounded-xl border border-border-main bg-bg-raised p-5 text-center">
      {!onboard ? <><QrCode size={28} className="mx-auto text-brand-green" /><p className="text-sm font-medium text-text-main">使用{label}扫码绑定</p><p className="text-xs text-text-muted">二维码由{label}官方绑定地址生成，请使用对应客户端扫描。</p><button type="button" disabled={busy} onClick={startOnboarding} className="rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-green-hover disabled:opacity-50">{busy ? '创建中...' : '生成二维码'}</button></> : <><div className="mx-auto flex h-52 w-52 items-center justify-center rounded-lg border border-border-main bg-white p-2"><QRPreview value={onboard.qr_url} label={label} /></div><p className="text-sm text-text-main">{onboard.status === 'completed' ? '扫码绑定成功' : onboard.status === 'scaned' ? '已扫码，请在手机上确认' : '等待扫码授权...'}</p>{onboard.user_code && <p className="text-xs text-text-muted">验证码：{onboard.user_code}</p>}{onboard.status !== 'completed' && <button type="button" onClick={cancelOnboarding} className="rounded-lg border border-border-main px-4 py-2 text-sm text-text-muted">取消</button>}</>}
    </div>}
    {(message || error) && <div className={`flex items-center gap-2 text-xs ${error ? 'text-status-error' : 'text-brand-green'}`}>{error ? <WarningCircle size={16} /> : <CheckCircle size={16} weight="fill" />}{error || message}</div>}
  </div>
}
