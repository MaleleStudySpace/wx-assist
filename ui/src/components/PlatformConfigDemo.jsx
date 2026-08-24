import { useEffect, useState } from 'react'
import { CheckCircle, Copy, FloppyDisk, ArrowLeft, Eye, EyeSlash, WarningCircle } from '@phosphor-icons/react'

const STORAGE_KEY = 'wx-assist-im-platform-demo'

const EMPTY_CONFIG = {
  qqbot: { enabled: false, app_id: '', client_secret: '' },
  feishu: { enabled: false, app_id: '', app_secret: '', verification_token: '' },
}

function loadDemoConfig() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      qqbot: { ...EMPTY_CONFIG.qqbot, ...(value.qqbot || {}) },
      feishu: { ...EMPTY_CONFIG.feishu, ...(value.feishu || {}) },
    }
  } catch {
    return { ...EMPTY_CONFIG }
  }
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
  const isQQ = platform === 'qqbot'
  const label = isQQ ? 'QQ' : '飞书'
  const webhookUrl = `${window.location.origin}/webhook/feishu`

  function update(key, value) {
    onChange({ ...config, [key]: value })
    setSaved(false)
  }

  function save() {
    const next = loadDemoConfig()
    next[platform] = config
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setSaved(true)
    onSaved?.()
    window.setTimeout(() => setSaved(false), 2200)
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

      <div className="rounded-xl border border-status-warn/30 bg-status-warn/5 px-4 py-3 text-xs text-text-muted">
        <div className="flex items-start gap-2">
          <WarningCircle size={16} weight="fill" className="mt-0.5 shrink-0 text-status-warn" />
          <span>当前为前端演示配置，凭证仅保存在浏览器 localStorage，不会发送到后端。正式接入后将改为安全保存并由后端启动平台连接。</span>
        </div>
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
          {saved && <><CheckCircle size={16} weight="fill" className="text-brand-green" /> 演示配置已保存</>}
        </div>
        <button type="button" onClick={save} className="inline-flex items-center gap-2 rounded-lg bg-brand-green px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-green-hover">
          <FloppyDisk size={16} /> 保存演示配置
        </button>
      </div>
    </div>
  )
}

export default function PlatformConfigDemo({ platform, onBack, onSaved }) {
  const [config, setConfig] = useState(() => loadDemoConfig()[platform] || {})
  useEffect(() => setConfig(loadDemoConfig()[platform] || {}), [platform])
  return <PlatformConfigForm platform={platform} config={config} onChange={setConfig} onSaved={onSaved} onBack={onBack} />
}

export function getDemoPlatformConfig() {
  return loadDemoConfig()
}
