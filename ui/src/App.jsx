import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Gear, ChartLine, Scroll, Spinner, Sun, Moon, ChatCircleDots, Star, Eye, Newspaper, Chats, PaperPlaneTilt, Bell, QrCode, X } from '@phosphor-icons/react'
import { API_BASE, getWsUrl } from './components/SharedComponents'
import Dashboard from './components/Dashboard'
import ConfigPanel from './components/ConfigPanel'
import AssistantPanel from './components/AssistantPanel'
import LogViewer from './components/LogViewer'
import Onboarding from './components/Onboarding'
import FavoritesTab from './components/FavoritesTab'
import MomentsTab from './components/MomentsTab'
import OATab from './components/OATab'
import ChatTab from './components/ChatTab'
import FeatureGuide from './components/FeatureGuide'
import TaskCenter from './components/TaskCenter'
import LANCard from './components/LANCard'
import { AmbientWaveBackground } from './components/AmbientBackground'

const iconVariants = {
  hover: { y: -1.5, scale: 1.05, transition: { type: 'spring', stiffness: 300, damping: 15 } }
}

const TABS = [
  { id: 'dashboard', label: '运行状态', icon: ChartLine },
  {
    id: 'config', label: '系统配置', icon: Gear,
    subs: [
      { id: 'ai', label: 'AI 后端配置' },
      { id: 'data', label: '数据配置' },
      { id: 'push', label: '消息推送' },
    ],
  },
  { id: 'assistant', label: '群聊助手', icon: ChatCircleDots },
  { id: 'chats', label: '会话管理', icon: Chats },
  { id: 'favorites', label: '收藏助手', icon: Star },
  { id: 'moments', label: '朋友圈助手', icon: Eye },
  { id: 'oa', label: '公众号助手', icon: Newspaper },
  { id: 'logs', label: '运行日志', icon: Scroll },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [configSection, setConfigSection] = useState('ai')
  const [botStatus, setBotStatus] = useState(null)
  const [onboardingDone, setOnboardingDone] = useState(null) // null = loading
  const [authError, setAuthError] = useState(false) // true = LAN unauthorized
  const [showGuide, setShowGuide] = useState(false) // FeatureGuide only shows right after onboarding
  const [wsConnected, setWsConnected] = useState(false)
  const [showTaskCenter, setShowTaskCenter] = useState(false)
  const [showLAN, setShowLAN] = useState(false)
  const [runningTaskCount, setRunningTaskCount] = useState(0)

  // Listen for open-task-center custom events from other components
  useEffect(() => {
    const handler = () => setShowTaskCenter(true)
    window.addEventListener('open-task-center', handler)
    return () => window.removeEventListener('open-task-center', handler)
  }, [])

  // Poll running task count periodically
  useEffect(() => {
    if (!onboardingDone) return
    function poll() {
      fetch(`${API_BASE}/api/tasks?status=running&limit=50`)
        .then(r => r.json())
        .then(d => { if (d.ok) setRunningTaskCount(d.tasks?.length || 0) })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [onboardingDone])

  // Theme state: default to 'dark' (Version 1: 夜航控制台) but can toggle to 'light' (正常模式)
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')

  // Apply theme class to HTML root
  useEffect(() => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    localStorage.setItem('theme', theme)
  }, [theme])

  // Check onboarding status on mount
  useEffect(() => {
    async function check() {
      try {
        const [statusRes, configRes] = await Promise.all([
          fetch(`${API_BASE}/api/onboarding/status`),
          fetch(`${API_BASE}/api/load-config`)
        ])
        // If API returns 401/403, show unauthorized page instead of onboarding
        if (!statusRes.ok) {
          if (statusRes.status === 401 || statusRes.status === 403) {
            setAuthError(true)
            return
          }
        }
        const d = await statusRes.json()
        const config = await configRes.json()
        setOnboardingDone(d.onboarding_done)

        // Auto-start bot if onboarding done AND WECHAT_DATA_DIR + WCDB_KEY both have values
        if (d.onboarding_done) {
          const hasWechatDataDir = config.config?.wechat_data_dir?.length > 0
          const hasWcdbKey = config.config?.has_key
          if (hasWechatDataDir && hasWcdbKey) {
            try {
              await fetch(`${API_BASE}/api/start`, { method: 'POST' })
            } catch {}
          }
        }
      } catch {
        setTimeout(check, 1000) // Retry every 1s until server is ready
      }
    }
    check()
  }, [])

  // Connect WebSocket only after onboarding is confirmed
  useEffect(() => {
    if (!onboardingDone) return
    let reconnectTimer = null
    let socket = null

    function connectWS() {
      socket = new WebSocket(getWsUrl())
      socket.onopen = () => {
        setWsConnected(true)
      }
      socket.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          // task_update events are handled by TaskCenter component directly
          if (data.type === 'task_update') return
          // Only update status if it's NOT a custom event (events have a 'type' field)
          // Status broadcasts don't have 'type', events like fav_export_progress do
          if (!data.type) {
            setBotStatus(data)
          }
        } catch {}
      }
      socket.onclose = () => {
        setWsConnected(false)
        reconnectTimer = setTimeout(connectWS, 3000)
      }
      socket.onerror = () => {
        setWsConnected(false)
        socket?.close()
      }
    }
    connectWS()

    return () => {
      clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [onboardingDone])

  const status = botStatus || {
    running: false,
    uptime_sec: 0,
    messages_processed: 0,
    wechat_backend: 'wcdb',
    db_ok: false,
    wechat_online: false,
    ai_ok: false,
    ai_verified: false,
    model_name: '',
    group_count: 0,
    last_api_call_sec_ago: -1,
    last_api_call_time: 0,
    timestamp: '',
    error: '',
    avatar_url: '',
    wx_name: '',
    restricted_features_enabled: false,
  }

  // Loading state
  if (onboardingDone === null) {
    return (
      <div className="min-h-[100dvh] bg-bg-main flex items-center justify-center">
        <div className="text-center">
          <Spinner size={32} weight="bold" className="animate-spin text-brand-green mx-auto mb-4" />
          <p className="text-sm text-text-muted font-mono">正在加载...</p>
        </div>
      </div>
    )
  }

  // Unauthorized access (phone without LAN session)
  if (authError) {
    return (
      <div className="min-h-[100dvh] bg-bg-main flex items-center justify-center p-8">
        <div className="text-center max-w-md">
          <div className="w-16 h-16 rounded-full bg-status-error/10 flex items-center justify-center mx-auto mb-5">
            <span className="text-2xl">🔒</span>
          </div>
          <h1 className="text-xl font-bold text-text-main mb-2">无权访问</h1>
          <p className="text-sm text-text-secondary leading-relaxed">
            当前设备未通过局域网认证。请在桌面端开启「LAN 远程访问」后，使用手机扫描二维码连接。
          </p>
          <p className="text-xs text-text-muted mt-6">
            wx-assist · LAN 远程访问
          </p>
        </div>
      </div>
    )
  }

  // Onboarding
  if (!onboardingDone) {
    return <Onboarding onComplete={() => { setShowGuide(true); setOnboardingDone(true) }} />
  }

  return (
    <div className="min-h-[100dvh] bg-bg-main text-text-main font-sans transition-colors duration-200 relative overflow-hidden">
      {/* Ambient wave background */}
      <AmbientWaveBackground />

      {/* Sidebar — hidden on mobile, always visible on desktop */}
      <div className="hidden lg:block fixed left-0 top-0 h-full w-56 bg-bg-main border-r border-border-main z-40">
        <div className="p-5 flex flex-col h-full justify-between">
          <div>
            <div className="flex items-center gap-3 mb-8">
              <div className="relative">
                <img src={status.avatar_url || '/logo-128.png'} alt="wx-assist" className="w-9 h-9 rounded-full border border-border-main object-cover" />
                <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-bg-main transition-colors duration-300 ${!wsConnected ? 'bg-[#d45656] animate-pulse' : (status.running ? 'bg-brand-green' : 'bg-slate-500')}`} />
              </div>
              <div>
                <h1 className="text-sm font-semibold tracking-tight text-text-main truncate max-w-[120px]" title={status.wx_name ? `${status.wx_name}的微信助手` : '微信助手'}>
                  {status.wx_name ? <>{status.wx_name}<span className="font-normal">的</span>微信助手</> : '微信助手'}
                </h1>
                <p className="text-xs text-text-muted font-mono font-medium">{!wsConnected ? '连接已断开' : (status.running ? '运行中' : '已停止')}</p>
              </div>
            </div>

            <nav className="space-y-1">
              {TABS.map(({ id, label, icon: Icon, subs }) => (
                <div key={id}>
                  <motion.button
                    whileHover="hover"
                    whileTap={{ scale: 0.98 }}
                    onClick={() => setActiveTab(id)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-[14px] rounded-full transition-all duration-200 cursor-pointer relative ${
                      activeTab === id
                        ? 'text-brand-green-hover dark:text-brand-green font-semibold'
                        : 'text-text-muted font-medium hover:text-text-main hover:bg-bg-raised/60'
                    }`}
                  >
                    {activeTab === id && (
                      <motion.div
                        layoutId="activeTabBackground"
                        className="absolute inset-0 bg-brand-green-light rounded-full -z-10"
                        transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                      />
                    )}
                    <motion.div variants={iconVariants} className="flex items-center z-10">
                      <Icon weight={activeTab === id ? 'fill' : 'regular'} size={18} className={activeTab === id ? 'text-brand-green-hover dark:text-brand-green' : 'text-text-muted'} />
                    </motion.div>
                    <span className="z-10">{label}</span>
                  </motion.button>
                  {/* Config sub-nav: animates height and opacity on toggle */}
                  {subs && (
                    <AnimatePresence initial={false}>
                      {activeTab === id && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2, ease: 'easeInOut' }}
                          className="ml-6 mt-1 border-l border-border-main pl-4 space-y-0.5 overflow-hidden font-mono"
                        >
                          {subs.map(sub => (
                            <button
                              key={sub.id}
                              onClick={() => { setActiveTab(id); setConfigSection(sub.id) }}
                              className={`w-full text-left py-1.5 text-xs font-semibold transition-all cursor-pointer relative pl-3.5 ${
                                activeTab === id && configSection === sub.id
                                  ? 'text-brand-green-hover dark:text-brand-green'
                                  : 'text-text-muted hover:text-text-main'
                              }`}
                            >
                              {activeTab === id && configSection === sub.id && (
                                <motion.div
                                  layoutId="activeConfigSub"
                                  className="absolute left-0 top-1.5 w-1 h-3 bg-brand-green rounded-full"
                                  transition={{ type: 'spring', stiffness: 300, damping: 20 }}
                                />
                              )}
                              <span className="pl-1.5">{sub.label}</span>
                            </button>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  )}
                </div>
              ))}
            </nav>
          </div>

          <div className="border-t border-border-main pt-4 mt-auto">
            <div className="flex items-center gap-2.5 px-4 py-2 bg-bg-raised/80 rounded-full border border-border-main">
              <div className={`w-2 h-2 rounded-full relative ${!wsConnected ? 'bg-[#d45656]' : (status.running ? 'bg-brand-green' : 'bg-slate-500')}`}>
                {!wsConnected && <span className="absolute inset-0 rounded-full bg-[#d45656] animate-ping opacity-75" />}
                {wsConnected && status.running && <span className="absolute inset-0 rounded-full bg-brand-green animate-ping opacity-75" />}
              </div>
              <span className="text-[11px] text-text-muted font-semibold font-mono tracking-wider">
                {!wsConnected ? 'OFFLINE' : (status.running ? `ONLINE ${status.uptime_sec ? Math.floor(status.uptime_sec / 60) : 0}M` : 'STOPPED')}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="lg:ml-56">
        {showGuide ? (
          /* Feature Guide — only shown once immediately after onboarding completes */
          <FeatureGuide
            onTabChange={(tabId) => setActiveTab(tabId)}
            onComplete={() => setShowGuide(false)}
            restrictedEnabled={!!status.restricted_features_enabled}
          />
        ) : (
          <>
            <div className="sticky top-0 z-30 bg-bg-main/80 backdrop-blur-md px-8 py-4 flex items-center justify-between border-b border-border-main transition-colors duration-300">
              <h2 className="text-sm font-semibold tracking-tight text-text-main">
                {TABS.find(t => t.id === activeTab)?.label}
              </h2>
              <div className="flex items-center gap-3">
                {/* Task Center bell icon with numeric badge */}
                <button
                  onClick={() => setShowTaskCenter(true)}
                  className="p-2 rounded-full bg-bg-main border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-colors cursor-pointer relative"
                  title="任务中心"
                >
                  <Bell size={18} />
                  {runningTaskCount > 0 && (
                    <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-brand-green text-[10px] text-white font-bold flex items-center justify-center leading-none">
                      {runningTaskCount > 99 ? '99+' : runningTaskCount}
                    </span>
                  )}
                </button>

                {/* LAN remote access */}
                <button
                  onClick={() => setShowLAN(true)}
                  className="p-2 rounded-full bg-bg-main border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-colors cursor-pointer"
                  title="LAN 远程访问"
                >
                  <QrCode size={18} />
                </button>

                {/* Theme switcher toggle button */}
                <button
                  onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                  className="p-2 rounded-full bg-bg-main border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-colors cursor-pointer"
                  title={theme === 'dark' ? '切换到正常模式 (Light Mode)' : '切换到夜航控制台 (Dark Mode)'}
                >
                  {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                </button>

                <span className="hidden md:inline text-xs text-text-muted font-mono bg-bg-main border border-border-main px-4 py-1.5 rounded-full">
                  已处理 {(status.messages_processed ?? 0).toLocaleString()} 条消息
                </span>
                {!wsConnected ? (
                  <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-semibold bg-[#d45656]/10 text-[#d45656] border border-[#d45656]/20 animate-pulse">
                    <div className="w-1.5 h-1.5 rounded-full bg-[#d45656]" />
                    <span className="hidden sm:inline">服务器离线</span>
                  </div>
                ) : (
                  <div className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-semibold transition-all border ${
                      status.running
                        ? 'bg-brand-green-light text-brand-green-hover dark:text-brand-green border-brand-green/20'
                        : 'bg-bg-raised text-text-muted border-border-main'
                    }`}>
                    <div className={`w-1.5 h-1.5 rounded-full ${status.running ? 'bg-brand-green animate-pulse' : 'bg-slate-500'}`} />
                    <span className="hidden sm:inline">{status.running ? '服务运行中' : '服务已停'}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Mobile tab strip */}
            <div className="lg:hidden overflow-x-auto border-b border-border-main bg-bg-main/60 backdrop-blur-md">
              <div className="flex gap-1 px-2 py-1.5 min-w-max">
                {TABS.map(({ id, label, icon: Icon, subs }) => (
                  <div key={id} className="flex-shrink-0">
                    <button
                      onClick={() => { setActiveTab(id); if (subs) setConfigSection(subs[0].id) }}
                      className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-[12px] font-medium whitespace-nowrap transition-colors cursor-pointer ${
                        activeTab === id
                          ? 'bg-brand-green/15 text-brand-green font-semibold'
                          : 'text-text-muted hover:text-text-main hover:bg-bg-raised/50'
                      }`}
                    >
                      <Icon size={13} weight={activeTab === id ? 'fill' : 'regular'} />
                      {label}
                    </button>
                    {/* Config sub-tabs (mobile: only AI + Push) */}
                    {activeTab === id && subs && (
                      <div className="flex gap-0.5 mt-0.5 ml-0.5">
                        {subs.filter(sub => sub.id === 'ai' || sub.id === 'push').map(sub => (
                          <button
                            key={sub.id}
                            onClick={() => setConfigSection(sub.id)}
                            className={`px-2 py-0.5 rounded text-[10px] font-medium whitespace-nowrap transition-colors cursor-pointer ${
                              configSection === sub.id
                                ? 'bg-brand-green/20 text-brand-green'
                                : 'text-text-muted/70 hover:text-text-main'
                            }`}
                          >
                            {sub.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <AnimatePresence mode="wait">
              <motion.div
                key={activeTab}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.2 }}
                className="p-4 lg:p-8"
              >
                {activeTab === 'dashboard' && <Dashboard status={status} onTabChange={setActiveTab} />}
                {activeTab === 'config' && <ConfigPanel activeSection={configSection} onNavigate={setConfigSection} />}
                {activeTab === 'assistant' && <AssistantPanel />}
                {activeTab === 'chats' && <ChatTab />}
                {activeTab === 'favorites' && <FavoritesTab />}
                {activeTab === 'moments' && <MomentsTab />}
                {activeTab === 'oa' && <OATab />}
                {activeTab === 'logs' && <LogViewer />}
              </motion.div>
            </AnimatePresence>
          </>
        )}
      </div>

      {/* Task Center Drawer */}
      <TaskCenter open={showTaskCenter} onClose={() => setShowTaskCenter(false)} />

      {/* LAN Modal */}
      <AnimatePresence>
        {showLAN && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
          >
            <div className="absolute inset-0 bg-black/40" onClick={() => setShowLAN(false)} />
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.15 }}
              className="relative bg-bg-card border border-border-main rounded-xl shadow-xl max-w-sm w-full max-h-[85vh] overflow-y-auto"
            >
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-main">
                <div className="flex items-center gap-2">
                  <QrCode size={18} className="text-text-secondary" />
                  <span className="text-[14px] font-semibold text-text-main">LAN 远程访问</span>
                </div>
                <button
                  onClick={() => setShowLAN(false)}
                  className="p-1.5 rounded-full hover:bg-bg-raised text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="p-5">
                <LANCard />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
