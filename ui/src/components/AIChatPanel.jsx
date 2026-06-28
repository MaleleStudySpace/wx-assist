import { useRef, useEffect, useCallback } from 'react'
import { PaperPlaneTilt, ArrowsClockwise, Robot, User, Sparkle } from '@phosphor-icons/react'
import { API_BASE } from './SharedComponents'

/**
 * Shared AI Chat Panel component for both Favorites and Group Chat.
 *
 * State is hoisted to the parent component — this component only handles
 * rendering and SSE logic. Closing the Drawer does NOT destroy the session;
 * only "开启新对话" (onNewChat) does.
 *
 * Props:
 *   sessionId: string        — session ID from /api/ai/chat/start
 *   sourceName: string       — "微信收藏" or group name
 *   contextSummary: string   — e.g. "已加载 47 条收藏内容"
 *   // Hoisted state (parent owns these)
 *   messages: array          — [{ role, content, streaming?, isError? }]
 *   inputText: string
 *   isStreaming: boolean
 *   tokenUsage: { used, budget }
 *   autoCompressed: boolean
 *   // State callbacks
 *   onMessagesChange: (messages) => void
 *   onInputTextChange: (text) => void
 *   onIsStreamingChange: (bool) => void
 *   onTokenUsageChange: ({ used, budget }) => void
 *   onAutoCompressedChange: (bool) => void
 *   // Actions
 *   onClose: () => void      — close Drawer (does NOT destroy session)
 *   onNewChat: () => void    — destroy session + reset state → back to config
 */
export default function AIChatPanel({
  sessionId,
  sourceName,
  contextSummary,
  messages,
  inputText,
  isStreaming,
  tokenUsage,
  autoCompressed,
  onMessagesChange,
  onInputTextChange,
  onIsStreamingChange,
  onTokenUsageChange,
  onAutoCompressedChange,
  onClose,
  onNewChat,
}) {
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const abortRef = useRef(null)

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort()
    }
  }, [])

  // ── Token usage bar ──────────────────────────────────────
  const tokenPct = tokenUsage.budget > 0
    ? Math.min((tokenUsage.used / tokenUsage.budget) * 100, 100)
    : 0
  const tokenColor = tokenPct > 90 ? 'bg-red-500' : tokenPct > 70 ? 'bg-yellow-500' : 'bg-brand-green'

  // ── Send message ─────────────────────────────────────────
  const handleSend = useCallback(async () => {
    const text = inputText.trim()
    if (!text || isStreaming) return

    // Add user message to UI immediately
    const userMsg = { role: 'user', content: text }
    const aiMsgIndex = messages.length + 1  // +1 for the user message just added
    const baseMessages = [...messages, userMsg, { role: 'assistant', content: '', streaming: true }]
    onMessagesChange(baseMessages)
    onInputTextChange('')
    onIsStreamingChange(true)
    onAutoCompressedChange(false)

    // Track current messages locally for SSE updates (avoids stale ref issues)
    let currentMessages = baseMessages

    // Timeout: if no token arrives within 60s, show error
    let firstTokenTimer = null

    try {
      const controller = new AbortController()
      abortRef.current = controller

      const resp = await fetch(`${API_BASE}/api/ai/chat/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message: text }),
        signal: controller.signal,
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
      }

      // Parse SSE stream
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let aiContent = ''
      let hasReceivedToken = false

      firstTokenTimer = setTimeout(() => {
        if (!hasReceivedToken) {
          try { reader.cancel() } catch {}
          const updated = [...currentMessages]
          updated[aiMsgIndex] = {
            role: 'assistant',
            content: '⚠️ AI 响应超时，请重试',
            streaming: false,
            isError: true,
          }
          onMessagesChange(updated)
          onIsStreamingChange(false)
        }
      }, 60000)

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE events
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''  // keep incomplete last line

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'token' && data.content) {
                if (!hasReceivedToken) {
                  hasReceivedToken = true
                  clearTimeout(firstTokenTimer)
                }
                aiContent += data.content
                const updated = [...currentMessages]
                updated[aiMsgIndex] = { role: 'assistant', content: aiContent, streaming: true }
                currentMessages = updated
                onMessagesChange(updated)
              } else if (currentEvent === 'done') {
                clearTimeout(firstTokenTimer)
                if (data.token_usage) onTokenUsageChange(data.token_usage)
                if (data.auto_compressed) onAutoCompressedChange(true)
                const updated = [...currentMessages]
                updated[aiMsgIndex] = { role: 'assistant', content: aiContent, streaming: false }
                currentMessages = updated
                onMessagesChange(updated)
                // The backend has already sent the terminal event. Unlock the input
                // immediately instead of waiting for the HTTP stream to fully close.
                onIsStreamingChange(false)
                try { await reader.cancel() } catch {}
                return
              } else if (currentEvent === 'error') {
                clearTimeout(firstTokenTimer)
                const updated = [...currentMessages]
                updated[aiMsgIndex] = {
                  role: 'assistant',
                  content: `⚠️ ${data.message || '未知错误'}`,
                  streaming: false,
                  isError: true,
                }
                currentMessages = updated
                onMessagesChange(updated)
                onIsStreamingChange(false)
                try { await reader.cancel() } catch {}
                return
              }
            } catch {
              // Skip malformed JSON
            }
            currentEvent = ''
          }
        }
      }
    } catch (e) {
      if (firstTokenTimer) clearTimeout(firstTokenTimer)
      if (e.name === 'AbortError') return
      console.error('AI chat error:', e)
      const updated = [...currentMessages]
      updated[aiMsgIndex] = {
        role: 'assistant',
        content: '⚠️ 网络错误，请重试',
        streaming: false,
        isError: true,
      }
      onMessagesChange(updated)
    } finally {
      if (firstTokenTimer) clearTimeout(firstTokenTimer)
      onIsStreamingChange(false)
      abortRef.current = null
    }
  }, [inputText, isStreaming, sessionId, messages.length, onMessagesChange, onInputTextChange, onIsStreamingChange, onTokenUsageChange, onAutoCompressedChange])

  // ── Compress ─────────────────────────────────────────────
  const handleCompress = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/ai/chat/compress`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      })
      const data = await resp.json()
      if (data.ok) {
        onTokenUsageChange(data.token_usage || tokenUsage)
      }
    } catch (e) {
      console.error('Compress error:', e)
    }
  }, [sessionId, tokenUsage, onTokenUsageChange])

  // ── Keyboard ─────────────────────────────────────────────
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Token usage bar */}
      <div className="px-5 py-2 border-b border-border-main bg-bg-raised shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <Sparkle size={12} className="text-text-muted" />
          <span className="text-xs text-text-muted">{contextSummary}</span>
          <span className="text-xs text-text-muted">
            {(tokenUsage.used / 1000).toFixed(1)}K / {(tokenUsage.budget / 1000).toFixed(0)}K
          </span>
        </div>
        <div className="h-1 bg-border-main rounded-full overflow-hidden">
          <div
            className={`h-full ${tokenColor} rounded-full transition-all duration-300`}
            style={{ width: `${Math.min(tokenPct, 100)}%` }}
          />
        </div>
        {tokenPct > 70 && (
          <div className="flex items-center gap-2 mt-1">
            {tokenPct > 90 && (
              <span className="text-xs text-red-500">上下文接近上限</span>
            )}
            <button
              onClick={handleCompress}
              className="text-xs text-brand-green hover:underline flex items-center gap-0.5 ml-auto"
            >
              <ArrowsClockwise size={10} />
              压缩历史
            </button>
          </div>
        )}
        {autoCompressed && (
          <div className="text-xs text-yellow-600 mt-1">
            已自动压缩早期对话
          </div>
        )}
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {/* Welcome message */}
        {messages.length === 0 && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-brand-green/10 flex items-center justify-center shrink-0 mt-0.5">
              <Robot size={16} className="text-brand-green" />
            </div>
            <div className="bg-bg-card border border-border-main rounded-2xl rounded-bl-sm px-4 py-3 max-w-[85%]">
              <p className="text-sm text-text-main leading-relaxed">
                你好！我是 <strong>{sourceName}</strong> 的 AI 助手。
              </p>
              <p className="text-sm text-text-muted mt-1">
                {contextSummary}。有什么想问的？
              </p>
            </div>
          </div>
        )}

        {/* Chat messages */}
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            {/* Avatar */}
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${
              msg.role === 'user'
                ? 'bg-brand-green/10'
                : 'bg-brand-green/10'
            }`}>
              {msg.role === 'user'
                ? <User size={16} className="text-text-main" />
                : <Robot size={16} className="text-brand-green" />
              }
            </div>
            {/* Bubble */}
            <div className={`max-w-[85%] px-4 py-2.5 ${
              msg.role === 'user'
                ? 'bg-[#95EC69] dark:bg-[#2b5a1e] text-[#0d0d0d] dark:text-[#f0f0f0] rounded-2xl rounded-br-sm'
                : msg.isError
                  ? 'bg-red-50 border border-red-200 text-red-700 rounded-2xl rounded-bl-sm'
                  : 'bg-bg-card border border-border-main text-text-main rounded-2xl rounded-bl-sm'
            }`}>
              <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                {msg.content}
                {msg.streaming && !msg.content && (
                  <span className="inline-flex items-center gap-1 ml-0.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-bounce" style={{ animationDelay: '300ms' }} />
                  </span>
                )}
              </p>
              {msg.streaming && msg.content && (
                <span className="inline-flex items-center gap-1.5 ml-0.5">
                  <span className="inline-block w-1.5 h-4 bg-text-muted animate-pulse align-text-bottom" />
                  <span className="text-[11px] text-text-muted/50">生成中</span>
                </span>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="px-5 py-4 border-t border-border-main bg-bg-main shrink-0">
        {onNewChat && (
          <div className="flex justify-center mb-2.5">
            <button
              onClick={onNewChat}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-medium border border-border-main bg-bg-raised text-text-muted hover:text-brand-green hover:border-brand-green/30 hover:bg-brand-green/5 transition-all cursor-pointer"
            >
              <ArrowsClockwise size={12} />
              开启新对话
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={inputText}
            onChange={(e) => onInputTextChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入问题…"
            disabled={isStreaming}
            rows={1}
            className="flex-1 resize-none rounded-xl border border-border-main bg-bg-card px-4 py-2.5 text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green/50 focus:ring-1 focus:ring-brand-green/20 disabled:opacity-50 transition-colors max-h-24"
            style={{ minHeight: '42px' }}
            onInput={(e) => {
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 96) + 'px'
            }}
          />
          <button
            onClick={handleSend}
            disabled={isStreaming || !inputText.trim()}
            className="w-10 h-10 rounded-full bg-brand-green-hover text-white flex items-center justify-center shrink-0 hover:bg-[#0d8c5c] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <PaperPlaneTilt size={18} />
          </button>
        </div>
      </div>
    </div>
  )
}
