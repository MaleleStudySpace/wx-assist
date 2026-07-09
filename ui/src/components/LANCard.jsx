import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { QRCodeSVG } from 'qrcode.react'
import { ShieldCheck, Warning, Trash, Clock } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

export default function LANCard() {
  const [enabled, setEnabled] = useState(false)
  const [token, setToken] = useState(null)
  const [lanIp, setLanIp] = useState('')
  const [port, setPort] = useState(17327)
  const [sessions, setSessions] = useState([])
  const [showQr, setShowQr] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { fetchStatus() }, [])

  // Poll session list when LAN is enabled
  useEffect(() => {
    if (!enabled) return
    const id = setInterval(fetchStatus, 3000)
    return () => clearInterval(id)
  }, [enabled])

  async function fetchStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/lan/status`)
      if (!res.ok) return
      const data = await res.json()
      setEnabled(data.lan_enabled)
      setLanIp(data.lan_ip || '')
      setPort(data.port)
      setSessions(data.sessions || [])
      // Restore pair token from backend (persists until used/expired)
      if (data.token) setToken(data.token)
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
        await fetchStatus()
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
      setSessions([])
      setShowQr(false)
    } catch {}
    setLoading(false)
  }

  async function handleKick(ip) {
    try {
      await fetch(`${API_BASE}/api/lan/kick`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip }),
      })
      await fetchStatus()
    } catch {}
  }

  const qrUrl = enabled && token && lanIp
    ? `http://${lanIp}:${port}/?lan=${token}`
    : null

  return (
    <div className="space-y-4">
      {/* Status + toggle */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[13px] text-text-secondary min-w-0">
          {enabled ? (
            <>
              <ShieldCheck size={16} className="text-brand-green flex-shrink-0" />
              <span className="truncate">已连接 {sessions.length} 台设备</span>
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

      {/* Token URL — always visible when enabled */}
      {enabled && qrUrl && (
        <p className="text-[12px] text-text-muted font-mono text-center break-all max-w-[300px] select-all mx-auto">
          {qrUrl}
        </p>
      )}

      {/* Connected devices list — click device IP to toggle QR */}
      {enabled && sessions.length > 0 && (
        <div className="space-y-1">
          <div
            onClick={() => setShowQr(!showQr)}
            className="flex items-center justify-between px-3 py-2 rounded-lg bg-bg-raised/50 border border-border-main cursor-pointer hover:bg-bg-raised transition-colors"
          >
            <span className="text-[12px] text-text-muted font-medium">
              {showQr ? '点击隐藏二维码' : '点击显示二维码'} · {sessions.length} 台设备
            </span>
            <div className="flex items-center gap-2">
              {sessions[0] && (
                <span className="text-[11px] font-mono text-text-muted/60">{sessions[0].ip}</span>
              )}
            </div>
          </div>
          <div className="space-y-1 max-h-28 overflow-y-auto pl-1">
            {sessions.map((s, i) => (
              <div key={s.ip + i} className="flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[12px] hover:bg-bg-raised/30 transition-colors group">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-brand-green flex-shrink-0" />
                  <span className="font-mono text-text-main truncate">{s.ip}</span>
                  <span className="text-text-muted/50 text-[11px] hidden sm:inline">{s.connected_at}</span>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); handleKick(s.ip) }}
                  className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-500/10 text-text-muted hover:text-red-500 transition-all cursor-pointer"
                  title="踢出设备"
                >
                  <Trash size={13} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* QR code — toggled by clicking device IP area */}
      <AnimatePresence>
        {enabled && showQr && qrUrl && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="flex flex-col items-center gap-2 overflow-hidden"
          >
            <div className="bg-white p-3 rounded-xl shadow-sm">
              <QRCodeSVG value={qrUrl} size={200} level="M" />
            </div>
            <p className="text-[11px] text-text-muted text-center max-w-[300px]">
              关闭远程访问前长期有效
            </p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty state */}
      {!enabled && (
        <p className="text-[13px] text-text-muted text-center py-2">
          开启后，同一局域网内的手机可扫码远程操作
        </p>
      )}

      {error && (
        <div className="flex items-center gap-1.5 text-[13px] text-red-500 bg-red-500/5 px-3 py-2 rounded-lg">
          <Warning size={16} />
          {error}
        </div>
      )}
    </div>
  )
}
