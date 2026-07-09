import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { QRCodeSVG } from 'qrcode.react'
import { ShieldCheck, Eye, EyeSlash, Warning } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

export default function LANCard() {
  const [enabled, setEnabled] = useState(false)
  const [token, setToken] = useState(null)
  const [lanIp, setLanIp] = useState('')
  const [port, setPort] = useState(17327)
  const [activeSessions, setActiveSessions] = useState(0)
  const [showQr, setShowQr] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { fetchStatus() }, [])

  async function fetchStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/lan/status`)
      if (!res.ok) return
      const data = await res.json()
      setEnabled(data.lan_enabled)
      setLanIp(data.lan_ip || '')
      setPort(data.port)
      setActiveSessions(data.active_sessions || 0)
    } catch { /* server may not be ready */ }
  }

  async function handleEnable() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/api/lan/enable`, { method: 'POST' })
      const data = await res.json()
      if (data.ok) {
        setEnabled(true)
        setToken(data.token)
        setLanIp(data.lan_ip)
        setPort(data.port)
        setShowQr(true)
      } else {
        setError(data.error || '开启失败')
      }
    } catch { setError('请求失败，请检查后端是否运行') }
    setLoading(false)
  }

  async function handleDisable() {
    setLoading(true)
    try {
      await fetch(`${API_BASE}/api/lan/disable`, { method: 'POST' })
      setEnabled(false)
      setToken(null)
      setActiveSessions(0)
      setShowQr(false)
    } catch {}
    setLoading(false)
  }

  const qrUrl = enabled && token && lanIp
    ? `http://${lanIp}:${port}/?lan=${token}`
    : null

  return (
    <div className="space-y-5">
      {/* Status + toggle */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-text-secondary min-w-0">
          {enabled ? (
            <>
              <ShieldCheck size={16} className="text-brand-green flex-shrink-0" />
              <span className="truncate">
                已连接设备：{activeSessions}
              </span>
              {lanIp && (
                <span className="font-mono text-text-muted hidden sm:inline">{lanIp}:{port}</span>
              )}
            </>
          ) : (
            <span className="text-text-muted">未开启</span>
          )}
        </div>
        <button
          onClick={enabled ? handleDisable : handleEnable}
          disabled={loading}
          className={`flex-shrink-0 px-3 py-1.5 rounded-lg text-[13px] font-medium transition-colors disabled:opacity-50 ${
            enabled
              ? 'bg-red-500/10 text-red-500 hover:bg-red-500/20'
              : 'bg-brand-green/10 text-brand-green hover:bg-brand-green/20'
          }`}
        >
          {loading ? '...' : enabled ? '关闭' : '开启'}
        </button>
      </div>

      {/* QR code section - only when enabled */}
      {enabled && qrUrl && (
        <div className="flex flex-col items-center gap-3">
          <button
            onClick={() => setShowQr(!showQr)}
            className="flex items-center gap-1.5 text-[13px] text-text-secondary hover:text-text-main transition-colors"
          >
            {showQr ? <EyeSlash size={16} /> : <Eye size={16} />}
            {showQr ? '隐藏二维码' : '显示二维码'}
          </button>

          <AnimatePresence>
            {showQr && (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="flex flex-col items-center gap-3"
              >
                <div className="bg-white p-3 rounded-xl shadow-sm">
                  <QRCodeSVG value={qrUrl} size={200} level="M" />
                </div>
                <p className="text-[12px] text-text-muted break-all text-center max-w-[300px] font-mono">
                  {qrUrl}
                </p>
                <p className="text-[11px] text-text-muted text-center max-w-[300px]">
                  使用手机扫描二维码即可远程操作，二维码 60 秒有效，关闭后重新开启可刷新。
                </p>
              </motion.div>
            )}
          </AnimatePresence>

          {!showQr && (
            <p className="text-[12px] text-text-muted font-mono text-center">{lanIp}:{port}</p>
          )}
        </div>
      )}

      {/* Empty state */}
      {!enabled && (
        <p className="text-[13px] text-text-muted text-center py-2">
          开启后，同一局域网内的手机可扫描二维码远程操作本机 wx-assist
        </p>
      )}

      {error && (
        <div className="flex items-center gap-1.5 text-[13px] text-red-500 bg-red-500/5 px-3 py-2 rounded-lg">
          <Warning size={16} />
          {error}
        </div>
      )}

      {/* Manual input hint */}
      {enabled && lanIp && (
        <p className="text-[11px] text-text-muted text-center">
          或在手机浏览器输入上方地址访问
        </p>
      )}
    </div>
  )
}
