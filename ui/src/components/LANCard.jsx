import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { QRCodeSVG } from 'qrcode.react'
import { QrCode, ShieldCheck, Eye, EyeSlash, Warning, CaretDown, CaretRight } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

export default function LANCard() {
  const [enabled, setEnabled] = useState(false)
  const [token, setToken] = useState(null)
  const [lanIp, setLanIp] = useState('')
  const [port, setPort] = useState(17327)
  const [activeSessions, setActiveSessions] = useState(0)
  const [expanded, setExpanded] = useState(false)
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
        setExpanded(true)
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
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-surface border border-border rounded-xl overflow-hidden"
    >
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-bg-raised transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <QrCode size={20} className={enabled ? 'text-brand-green' : 'text-text-secondary'} />
          <span className="text-[14px] font-semibold text-text-main">LAN 远程访问</span>
          {enabled && (
            <span className="text-[11px] text-brand-green bg-brand-green/10 px-2 py-0.5 rounded-full font-medium">
              已开启
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {!expanded && activeSessions > 0 && (
            <span className="text-[12px] text-text-muted">{activeSessions} 台设备</span>
          )}
          {expanded ? <CaretDown size={16} className="text-text-secondary" /> : <CaretRight size={16} className="text-text-secondary" />}
        </div>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div className="px-5 pb-5 space-y-4 border-t border-border pt-4">
              {/* Status line */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-[13px] text-text-secondary">
                  {enabled && (
                    <>
                      <ShieldCheck size={16} className="text-brand-green" />
                      <span>已连接设备：{activeSessions}</span>
                    </>
                  )}
                  {enabled && lanIp && (
                    <span className="ml-1 font-mono text-text-muted">{lanIp}:{port}</span>
                  )}
                </div>
                <button
                  onClick={enabled ? handleDisable : handleEnable}
                  disabled={loading}
                  className={`px-3 py-1.5 rounded-lg text-[13px] font-medium transition-colors disabled:opacity-50 ${
                    enabled
                      ? 'bg-red-500/10 text-red-500 hover:bg-red-500/20'
                      : 'bg-brand-green/10 text-brand-green hover:bg-brand-green/20'
                  }`}
                >
                  {loading ? '...' : enabled ? '关闭远程访问' : '开启远程访问'}
                </button>
              </div>

              {/* QR code */}
              {enabled && qrUrl && (
                <div className="flex flex-col items-center gap-3 pt-2">
                  <button
                    onClick={() => setShowQr(!showQr)}
                    className="flex items-center gap-1.5 text-[13px] text-text-secondary hover:text-text-main transition-colors"
                  >
                    {showQr ? <EyeSlash size={16} /> : <Eye size={16} />}
                    {showQr ? '点击隐藏二维码' : '点击显示二维码'}
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
                          <QRCodeSVG value={qrUrl} size={180} level="M" />
                        </div>
                        <p className="text-[12px] text-text-muted break-all text-center max-w-[280px] font-mono">
                          {qrUrl}
                        </p>
                        <p className="text-[11px] text-text-muted text-center max-w-[280px]">
                          使用手机扫描二维码即可远程操作，二维码 60 秒有效，再次点击显示可刷新。
                        </p>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Show URL always for manual entry */}
                  {!showQr && (
                    <p className="text-[12px] text-text-muted font-mono text-center">
                      {lanIp}:{port}
                    </p>
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
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
