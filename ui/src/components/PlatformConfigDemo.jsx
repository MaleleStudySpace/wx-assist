import { useEffect, useState } from 'react'
import { CheckCircle, Copy, FloppyDisk, ArrowLeft, Eye, EyeSlash, WarningCircle } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

function defaultConfig(platform) {
  return platform === 'qqbot'
    ? { enabled: false, app_id: '', client_secret: '' }
    : { enabled: false, app_id: '', app_secret: '', verification_token: '' }
}

function SecretInput({ label, value, onChange, placeholder }) {
  const [visible, setVisible] = useState(false)
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-text-main">{label}</span>
      <div className="relative">
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 pr-10 text-sm text-text-main outline-none transition focus:border-brand-green focus:ring-2 focus:ring-brand-green/10"
        />
        <button type="button" onClick={() => setVisible(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main">
          {visible ? <EyeSlash size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </label>
  )
}

function DemoField({ label, value, onChange, placeholder }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-text-main">{label}</span>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 text-sm text-text-main outline-none transition focus:border-brand-green focus:ring-2 focus:ring-brand-green/10"
      />
    </label>
  )
}

function PlatformConfigForm({ platform, config, onChange, onSaved, onBack }) {
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState('')
  const isQQ = platform === 'qqbot'
  const label = isQQ ? 'QQ' : '飞书'
  const webhookUrl = `${window.location.origin}/webhook/feishu`

  function update(key, value) {
    onChange({ ...config, [key]: value })
    setSaved(false)
  }

  async function save() {
    setSaving(true)
    setError('')
    setTestResult('')
    try {
      const response = await fetch(`${API_BASE}/api/platforms/${platform}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: config.enabled, ...config }),
      })
      const data = await response.json()
      if (!response.ok || !data.ok) throw new Error(data.error || '保存失败')
      setSaved(true)
      onSaved?.()
      window.setTimeout(() => setSaved(false), 2200)
    } catch (err) {
      setError(err.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function testConnection() {
    setTesting(true)
    setError('')
    setTestResult('')
    try {
      const response = await fetch(`${API_BASE}/api/platforms/${platform}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await response.json()
      if (!response.ok || !data.ok) throw new Error(data.error || '连接测试失败')
      setTestResult('连接测试成功')
    } catch (err) {
      setError(err.message || '连接测试失败')
    } finally {
      setTesting(false)
    }
  }

  async function copyWebhook() {
    try {
      await navigator.clipboard.writeText(webhookUrl)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 1800)
    } catch {
      // Clipboard permission is optional in the demo.
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <button type="button" onClick={onBack} className="mt-0.5 rounded-lg p-1.5 text-text-muted hover:bg-bg-raised hover:text-text-main">
          <ArrowLeft size={18} />
        </button>
        <div>
          <h4 className="text-[15px] font-semibold text-text-main">{label} 配置</h4>
          <p className="mt-1 text-xs text-text-muted">配置后，{label} 将作为微信助手可选的 IM 推送平台。</p>
        </div>
      </div>

      <div className="rounded-xl border border-status-info/20 bg-status-info/5 px-4 py-3 text-xs text-text-muted">
        当前使用后端平台配置接口。Secret 不会回显；留空时保持后端已有值不变。
      </div>

      <label className="flex items-center justify-between rounded-xl border border-border-main bg-bg-raised px-4 py-3">
        <div>
          <p className="text-sm font-medium text-text-main">启用{label}推送</p>
          <p className="mt-1 text-xs text-text-muted">启用后，该平台会出现在可选推送方式中。</p>
        </div>
        <button type="button" onClick={() => update('enabled', !config.enabled)} className={`relative h-6 w-11 rounded-full transition ${config.enabled ? 'bg-brand-green' : 'bg-border-main'}`} aria-label={`启用${label}`}>
          <span className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow transition ${config.enabled ? 'left-6' : 'left-1'}`} />
        </button>
      </label>

      <div className="grid gap-4 md:grid-cols-2">
        <DemoField label={isQQ ? 'App ID' : 'App ID'} value={config.app_id} onChange={v => update('app_id', v)} placeholder={isQQ ? 'QQ 机器人 App ID' : '飞书应用 App ID'} />
        <SecretInput label={isQQ ? 'Client Secret' : 'App Secret'} value={isQQ ? config.client_secret : config.app_secret} onChange={v => update(isQQ ? 'client_secret' : 'app_secret', v)} placeholder={isQQ ? 'QQ 机器人 Client Secret' : '飞书应用 App Secret'} />
      </div>

      {!isQQ && (
        <div className="space-y-4">
          <DemoField label="Verification Token" value={config.verification_token} onChange={v => update('verification_token', v)} placeholder="飞书事件订阅 Verification Token" />
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-text-main">事件订阅地址</span>
            <div className="flex gap-2">
              <input readOnly value={webhookUrl} className="min-w-0 flex-1 rounded-lg border border-border-main bg-bg-raised px-3 py-2.5 text-xs text-text-muted outline-none" />
              <button type="button" onClick={copyWebhook} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border-main px-3 text-xs font-medium text-text-main hover:bg-bg-raised">
                <Copy size={15} /> 复制
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-border-main pt-5">
        <div className="flex items-center gap-2 text-xs text-text-muted">
          {saved && <><CheckCircle size={16} weight="fill" className="text-brand-green" /> 配置已保存</>}
          {testResult && <><CheckCircle size={16} weight="fill" className="text-brand-green" /> {testResult}</>}
          {error && <span className="text-status-error">{error}</span>}
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={testConnection} disabled={testing || saving} className="rounded-lg border border-border-main px-4 py-2.5 text-sm font-semibold text-text-main hover:bg-bg-raised disabled:opacity-50">
            {testing ? '测试中...' : '测试连接'}
          </button>
          <button type="button" onClick={save} disabled={saving || testing} className="inline-flex items-center gap-2 rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-green-hover disabled:opacity-50">
            <FloppyDisk size={16} /> {saving ? '保存中...' : '保存并应用'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function PlatformConfigDemo({ platform, initialConfig, onBack, onSaved }) {
  const [config, setConfig] = useState(() => ({ ...defaultConfig(platform), ...(initialConfig || {}) }))
  useEffect(() => setConfig({ ...defaultConfig(platform), ...(initialConfig || {}) }), [platform, initialConfig])
  return <PlatformConfigForm platform={platform} config={config} onChange={setConfig} onSaved={onSaved} onBack={onBack} />
}
