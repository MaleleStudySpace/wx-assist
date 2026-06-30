import { useState, useRef, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, PaperPlaneTilt, Sparkle, ChatCircle, Robot, User, ArrowsClockwise, MagnifyingGlass } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

/**
 * SNS AI Drawer — 朋友圈 AI 助手
 *
 * Two modes:
 * 1. Quick Summary: one-shot SSE stream via /api/sns/ai/summarize
 * 2. Free Chat: multi-turn via /api/ai/chat/start + /api/ai/chat/message
 */

const PRESET_LIMITS = [
  { value: 10, label: '10条' },
  { value: 20, label: '20条' },
  { value: 50, label: '50条' },
]

export default function SnsAiDrawer({ open, onClose, contacts = [] }) {
  // Mode: 'config' | 'summary' | 'chat'
  const [mode, setMode] = useState('config')
  const [limit, setLimit] = useState(20)
  const [username, setUsername] = useState('')
  const [contactSearch, setContactSearch] = useState('')
  const [contactDropdownOpen, setContactDropdownOpen] = useState(false)
  const contactDropdownRef = useRef(null)

  // Summary state
  const [summaryText, setSummaryText] = useState('')
  const [summaryStreaming, setSummaryStreaming] = useState(false)

  // Chat state (reuse AI chat session)
  const [chatSession, setChatSession] = useState(null)
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState('')
  const [chatStreaming, setChatStreaming] = useState(false)
  const [chatTokenUsage, setChatTokenUsage] = useState({ used: 0, budget: 0 })

  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [summaryText, chatMessages])

  // Reopen → restore previous conversation
  const prevOpen = useRef(open)
  useEffect(() => {
    if (open && !prevOpen.current) {
      // Drawer just reopened — restore previous chat or summary
      if (chatSession) {
        setMode('chat')
        // Focus input when returning to chat
        setTimeout(() => inputRef.current?.focus(), 100)
      } else if (summaryText) {
        setMode('summary')
      }
    }
    prevOpen.current = open
  }, [open, chatSession, summaryText])

  // Reset on close
  const handleClose = () => {
    if (abortRef.current) abortRef.current.abort()
    setSummaryStreaming(false)
    setChatStreaming(false)
    onClose()
  }

  // ── Quick Summary ──────────────────────────────────────────────
  async function startSummary() {
    setMode('summary')
    setSummaryText('')
    setSummaryStreaming(true)

    try {
      const res = await fetch(`${API_BASE}/api/sns/ai/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit, username }),
      })

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        let currentEvent = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'token' && data.content) {
                setSummaryText(prev => prev + data.content)
              } else if (currentEvent === 'done') {
                setSummaryStreaming(false)
                try { await reader.cancel() } catch {}
                return
              } else if (currentEvent === 'error') {
                setSummaryText(prev => prev + `❌ ${data.message || '未知错误'}`)
                setSummaryStreaming(false)
                try { await reader.cancel() } catch {}
                return
              } else if (data.message) {
                setSummaryText(prev => prev + `❌ ${data.message}`)
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setSummaryText(`❌ 请求失败: ${e.message}`)
      }
    } finally {
      setSummaryStreaming(false)
    }
  }

  // ── Free Chat ──────────────────────────────────────────────────
  async function startChat() {
    try {
      const res = await fetch(`${API_BASE}/api/ai/chat/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_type: 'moments',
          source_id: username,
          message_limit: limit,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        setChatSession(data)
        setChatMessages([])
        setChatInput('')
        setMode('chat')
      } else {
        alert(data.error || '启动对话失败')
      }
    } catch (e) {
      alert(`启动对话失败: ${e.message}`)
    }
  }

  async function sendChatMessage() {
    if (!chatInput.trim() || chatStreaming || !chatSession) return
    const msg = chatInput.trim()
    setChatInput('')
    setChatMessages(prev => [...prev, { role: 'user', content: msg }])
    setChatStreaming(true)

    try {
      const res = await fetch(`${API_BASE}/api/ai/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: chatSession.session_id, message: msg }),
      })

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullResponse = ''

      setChatMessages(prev => [...prev, { role: 'assistant', content: '', streaming: true }])

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        let currentEvent = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'token' && data.content) {
                fullResponse += data.content
                setChatMessages(prev => {
                  const next = [...prev]
                  next[next.length - 1] = { role: 'assistant', content: fullResponse, streaming: true }
                  return next
                })
              }
              if (currentEvent === 'done') {
                if (data.token_usage) setChatTokenUsage(data.token_usage)
                setChatMessages(prev => {
                  const next = [...prev]
                  next[next.length - 1] = { role: 'assistant', content: fullResponse, streaming: false }
                  return next
                })
                setChatStreaming(false)
                try { await reader.cancel() } catch {}
                return
              }
              if (currentEvent === 'error') {
                setChatMessages(prev => [...prev, { role: 'assistant', content: `❌ ${data.message || '未知错误'}`, isError: true }])
                setChatStreaming(false)
                try { await reader.cancel() } catch {}
                return
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      setChatMessages(prev => [...prev, { role: 'assistant', content: `❌ 请求失败: ${e.message}`, isError: true }])
    } finally {
      setChatStreaming(false)
    }
  }

  async function destroyChatSession() {
    if (chatSession) {
      try {
        await fetch(`${API_BASE}/api/ai/chat/destroy`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: chatSession.session_id }),
        })
      } catch {}
    }
    setChatSession(null)
    setChatMessages([])
    setChatInput('')
    setMode('config')
  }

  // ── Render ─────────────────────────────────────────────────────
  return (
    <>
      {/* Backdrop */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={handleClose}
          />
        )}
      </AnimatePresence>

      {/* Drawer panel */}
      <motion.div
        initial={{ x: '100%' }}
        animate={{ x: open ? 0 : '100%' }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="fixed right-0 top-0 h-full w-[420px] max-w-[calc(100vw-1rem)] bg-bg-main border-l border-border-main z-50 flex flex-col shadow-2xl"
        style={{ pointerEvents: open ? 'auto' : 'none' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-main shrink-0">
          <div className="flex items-center gap-2">
            <Sparkle size={18} className="text-brand-green" />
            <h3 className="text-sm font-semibold text-text-main truncate">朋友圈 AI 助手</h3>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-full hover:bg-bg-raised transition-colors text-text-muted hover:text-text-main"
          >
            <X size={18} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {mode === 'config' && (
            <div className="p-5 space-y-6">
              {/* Range selector */}
              <div>
                <label className="text-xs text-text-muted block mb-2">总结范围</label>
                <div className="flex gap-2">
                  {PRESET_LIMITS.map(p => (
                    <button
                      key={p.value}
                      onClick={() => setLimit(p.value)}
                      className={`text-sm px-4 py-2.5 rounded-lg font-medium transition-all cursor-pointer ${
                        limit === p.value
                          ? 'bg-brand-green-hover text-white shadow-sm'
                          : 'bg-bg-raised border border-border-main text-text-muted hover:border-brand-green/40'
                      }`}
                    >{p.label}</button>
                  ))}
                </div>
              </div>

              {/* Contact filter with search */}
              {contacts.length > 0 && (
                <div ref={contactDropdownRef}>
                  <label className="text-xs text-text-muted block mb-2">联系人筛选（可选）</label>
                  <button
                    onClick={() => setContactDropdownOpen(!contactDropdownOpen)}
                    className="w-full bg-bg-raised border border-border-main rounded-lg px-3 py-2.5 text-sm text-text-main text-left flex items-center justify-between cursor-pointer hover:border-brand-green/40 focus:border-brand-green focus:outline-none"
                  >
                    <span className="truncate">
                      {username
                        ? (contacts.find(c => (c.username || c) === username)?.nickname || username)
                        : '全部联系人'}
                    </span>
                    <span className="text-text-muted text-xs">▼</span>
                  </button>
                  <AnimatePresence>
                    {contactDropdownOpen && (
                      <motion.div
                        initial={{ opacity: 0, y: -4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        className="absolute z-50 mt-1.5 w-[calc(100%-40px)] bg-bg-card border border-border-main rounded-xl shadow-lg overflow-hidden"
                      >
                        <div className="p-2">
                          <div className="relative">
                            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                            <input
                              type="text"
                              value={contactSearch}
                              onChange={e => setContactSearch(e.target.value)}
                              placeholder="搜索联系人..."
                              className="w-full bg-bg-raised border border-border-main rounded-lg pl-8 pr-3 py-2 text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green"
                              autoFocus
                            />
                          </div>
                        </div>
                        <div className="max-h-48 overflow-y-auto">
                          <button
                            onClick={() => { setUsername(''); setContactDropdownOpen(false); setContactSearch('') }}
                            className="w-full text-left px-4 py-2.5 text-sm text-text-muted hover:bg-bg-raised transition-colors border-b border-border-main/50"
                          >全部联系人</button>
                          {contacts
                            .filter(c => {
                              const nick = c.nickname || c.username || c
                              const q = contactSearch.toLowerCase()
                              return !q || nick.toLowerCase().includes(q)
                            })
                            .slice(0, 50)
                            .map(c => (
                              <button
                                key={c.username || c}
                                onClick={() => { setUsername(c.username || c); setContactDropdownOpen(false); setContactSearch('') }}
                                className={`w-full text-left px-4 py-2.5 text-sm hover:bg-bg-raised transition-colors truncate ${
                                  (c.username || c) === username ? 'bg-brand-green/10 text-brand-green-hover' : 'text-text-main'
                                }`}
                              >
                                {c.nickname || c.username || c}
                              </button>
                            ))
                          }
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )}

              {/* Action buttons */}
              <div className="space-y-3 pt-2">
                <button
                  onClick={startSummary}
                  className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-brand-green-hover text-white text-sm font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer shadow-md shadow-brand-green/20"
                >
                  <Sparkle size={16} /> 快速总结
                </button>
                <button
                  onClick={startChat}
                  className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-bg-raised border border-border-main text-text-main text-sm font-medium hover:border-brand-green/40 transition-colors cursor-pointer"
                >
                  <ChatCircle size={16} /> 自由对话
                </button>
              </div>

              <p className="text-xs text-text-muted leading-relaxed">
                快速总结：一键生成朋友圈内容摘要。自由对话：可追问任何关于朋友圈的问题。
              </p>
            </div>
          )}

          {mode === 'summary' && (
            <div className="p-5 space-y-4">
              {/* Back to config */}
              <div className="flex items-center justify-between">
                <button
                  onClick={() => { setMode('config'); setSummaryText('') }}
                  className="text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer"
                >
                  ← 返回
                </button>
                {!summaryStreaming && summaryText && (
                  <button
                    onClick={startSummary}
                    className="text-xs text-brand-green-hover hover:underline cursor-pointer font-medium"
                  >
                    重新总结
                  </button>
                )}
              </div>

              {/* Summary content */}
              {summaryStreaming && !summaryText && (
                <div className="flex items-center gap-2 text-sm text-text-muted py-8 justify-center">
                  <div className="w-4 h-4 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
                  <span>AI 正在总结<span className="text-text-muted/60">（首次生成约 30-40 秒）</span></span>
                </div>
              )}
              <div className="prose prose-sm dark:prose-invert max-w-none text-text-main/90 leading-relaxed whitespace-pre-wrap">
                {summaryText}
                {summaryStreaming && (
                  <span className="inline-flex items-center gap-1.5 ml-0.5">
                    <span className="inline-block w-1.5 h-4 bg-brand-green/60 animate-pulse align-middle" />
                    <span className="text-[11px] text-text-muted/50">生成中</span>
                  </span>
                )}
              </div>

              {/* Continue to chat after summary */}
              {!summaryStreaming && summaryText && !chatSession && (
                <div className="pt-4 border-t border-border-main/50">
                  <button
                    onClick={startChat}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-bg-raised border border-border-main text-text-main text-sm font-medium hover:border-brand-green/40 transition-colors cursor-pointer"
                  >
                    <ChatCircle size={14} /> 继续追问
                  </button>
                </div>
              )}
            </div>
          )}

          {mode === 'chat' && (
            <div className="flex flex-col h-full">
              {/* Chat header */}
              <div className="px-5 py-2 border-b border-border-main/50 flex items-center justify-between shrink-0">
                <span className="text-xs text-text-muted">
                  {chatSession?.context_summary || `已加载 ${limit} 条朋友圈`}
                </span>
                <button
                  onClick={destroyChatSession}
                  className="text-xs text-text-muted hover:text-status-error transition-colors cursor-pointer"
                >
                  新对话
                </button>
              </div>

              {/* Messages */}
              <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                {chatMessages.length === 0 && (
                  <div className="text-center py-10">
                    <Sparkle size={24} className="text-text-muted mx-auto mb-2" />
                    <p className="text-xs text-text-muted">向 AI 提问关于朋友圈的任何问题</p>
                  </div>
                )}
                {chatMessages.map((m, i) => (
                  <div key={i} className={`flex gap-2.5 ${m.role === 'user' ? 'justify-end' : ''}`}>
                    {m.role === 'assistant' && (
                      <div className="w-7 h-7 rounded-lg bg-brand-green/10 flex items-center justify-center shrink-0 mt-0.5">
                        <Robot size={14} className="text-brand-green" />
                      </div>
                    )}
                    <div className={`max-w-[85%] px-3.5 py-2.5 rounded-xl text-sm leading-relaxed ${
                      m.role === 'user'
                        ? 'bg-brand-green-hover text-white'
                        : m.isError
                          ? 'bg-status-error/10 text-status-error'
                          : 'bg-bg-raised text-text-main'
                    }`}>
                      <div className="whitespace-pre-wrap">{m.content}</div>
                      {m.streaming && (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="inline-block w-1.5 h-4 bg-brand-green/60 animate-pulse align-middle" />
                          {!m.content && <span className="text-sm text-text-muted">AI 正在生成...</span>}
                        </span>
                      )}
                    </div>
                    {m.role === 'user' && (
                      <div className="w-7 h-7 rounded-lg bg-bg-raised flex items-center justify-center shrink-0 mt-0.5">
                        <User size={14} className="text-text-muted" />
                      </div>
                    )}
                  </div>
                ))}
                <div ref={messagesEndRef} />
              </div>

              {/* Input */}
              <div className="px-5 py-3 border-t border-border-main shrink-0">
                <div className="flex items-center gap-2">
                  <input
                    ref={inputRef}
                    type="text"
                    value={chatInput}
                    onChange={e => setChatInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage() } }}
                    placeholder="追问关于朋友圈的内容..."
                    disabled={chatStreaming}
                    className="flex-1 bg-bg-raised border border-border-main rounded-xl px-4 py-2.5 text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green disabled:opacity-50"
                  />
                  <button
                    onClick={sendChatMessage}
                    disabled={chatStreaming || !chatInput.trim()}
                    className="p-2.5 rounded-xl bg-brand-green-hover text-white hover:bg-[#0d8c5c] transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                  >
                    <PaperPlaneTilt size={16} />
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </motion.div>
    </>
  )
}
