import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Chats, ChatsCircle, CaretDown, MagnifyingGlass, Clock, Download, ArrowsClockwise, Play, Pause, X, FileText, Image, VideoCamera, Microphone, Link, Eye, User, ArrowDown, Users, ChatCircleDots, ShieldCheck, ShieldWarning } from '@phosphor-icons/react'
import { ImageLightbox, Avatar, API_BASE, getWsUrl } from './SharedComponents'
import ChatDrawer from './ChatDrawer'
import AIChatPanel from './AIChatPanel'
import AIChatConfig from './AIChatConfig'
import DatePicker from './DatePicker'

// ── Time formatting (WeFlow style) ──
function formatSessionTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const diffMs = now - d
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays === 0) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return d.toLocaleDateString('zh-CN', { weekday: 'short' })
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

function formatMsgTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatDateDivider(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const diffDays = Math.floor((now - d) / 86400000)
  const dateStr = d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
  if (diffDays === 0) return `今天 ${dateStr}`
  if (diffDays === 1) return `昨天 ${dateStr}`
  return dateStr
}

function shouldShowDateDivider(msg, prevMsg) {
  if (!prevMsg) return true
  const d1 = new Date((msg.create_time || 0) * 1000).toDateString()
  const d2 = new Date((prevMsg.create_time || 0) * 1000).toDateString()
  return d1 !== d2
}

// Show time if >5 minutes since previous message (WeFlow pattern)
function shouldShowTime(msg, prevMsg) {
  if (!prevMsg) return true
  const diff = (msg.create_time || 0) - (prevMsg.create_time || 0)
  return diff > 300 // 5 minutes
}

// ── Time range presets ──
const TIME_PRESETS = [
  { label: '全部', start: 0, end: 0 },
  { label: '今天', start: () => { const d = new Date(); d.setHours(0,0,0,0); return Math.floor(d.getTime()/1000) }, end: 0 },
  { label: '7天', start: () => Math.floor((Date.now() - 7*86400000)/1000), end: 0 },
  { label: '30天', start: () => Math.floor((Date.now() - 30*86400000)/1000), end: 0 },
]

// ── AI Chat time presets ──
const AI_CHAT_TIME_PRESETS = [
  { label: '7天', start: () => Math.floor((Date.now() - 7*86400000)/1000), end: 0 },
  { label: '30天', start: () => Math.floor((Date.now() - 30*86400000)/1000), end: 0 },
  { label: '90天', start: () => Math.floor((Date.now() - 90*86400000)/1000), end: 0 },
  { label: '全部', start: 0, end: 0 },
]

// ── Chat Record Card (type 49, appmsg type=19) ──
function ChatRecordCard({ records, talker, myWxid, msgCreateTime }) {
  const [expanded, setExpanded] = useState(false)
  const [zoomImg, setZoomImg] = useState(null)
  const items = records.items || []
  const title = records.title || '聊天记录'
  const previewLines = items.slice(0, 2).map(item => {
    const name = item.src_name || '未知'
    const t = parseInt(item.type || '0')
    if (t === 2) return `${name}: [图片]`
    if (t === 3) return `${name}: [语音]`
    if (t === 4) return `${name}: [视频]`
    if (t === 8) return `${name}: [文件]`
    if (t === 5) return `${name}: [链接]`
    return item.desc ? `${name}: ${item.desc.slice(0, 30)}` : `${name}: ...`
  })

  return (
    <div className="rounded-lg border border-border-main/50 overflow-hidden max-w-[90%]">
      <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-bg-raised cursor-pointer hover:bg-bg-card transition-colors"
        onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}>
        <ChatsCircle size={10} className="text-brand-green" weight="fill" />
        <span className="text-xs text-brand-green font-medium truncate flex-1">{title}</span>
        <span className="text-xs text-text-muted">{items.length}条</span>
        <CaretDown size={10} className={`text-text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </div>
      {!expanded && previewLines.length > 0 && (
        <div className="px-2.5 py-1.5 bg-bg-card/50 space-y-0.5">
          {previewLines.map((line, i) => <p key={i} className="text-xs text-text-muted truncate">{line}</p>)}
          {items.length > 2 && <p className="text-xs text-text-muted">...</p>}
        </div>
      )}
      <AnimatePresence>
        {expanded && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="px-2.5 py-2.5 space-y-2.5 max-h-72 overflow-y-auto bg-bg-card">
              {items.map((item, i) => {
                const t = parseInt(item.type || '0')
                const name = item.src_name || '未知'
                const initial = name[0]?.toUpperCase() || '?'
                return (
                  <div key={i} className="flex items-start gap-2">
                    {/* Avatar */}
                    <div className="w-6 h-6 rounded-sm overflow-hidden shrink-0 bg-brand-green-light/30 flex items-center justify-center text-[10px] text-brand-green font-medium">
                      {item.head_url ? (
                        <img src={item.head_url} className="w-full h-full object-cover" onError={(e) => { e.target.style.display = 'none'; e.target.parentElement.textContent = initial }} />
                      ) : initial}
                    </div>
                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-xs text-brand-green font-medium truncate">{name}</span>
                        {item.time && <span className="text-[10px] text-text-muted">{item.time}</span>}
                      </div>
                      {/* Text */}
                      {item.desc && t === 1 && (
                        <div className="mt-0.5 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-main leading-relaxed break-words max-w-[85%]">
                          {item.desc}
                        </div>
                      )}
                      {/* Image */}
                      {t === 2 && (
                        <div className="mt-0.5 max-w-[60%]">
                          {item.fullmd5 ? (
                            <img
                              src={`${API_BASE}/api/chat/image?fullmd5=${item.fullmd5}${item.fullsize ? '&fullsize=' + item.fullsize : ''}&talker=${encodeURIComponent(talker || '')}&create_time=${msgCreateTime || 0}`}
                              alt="图片"
                              className="rounded-md object-contain max-h-32 cursor-pointer hover:opacity-90 transition-opacity border border-border-main/30"
                              onClick={(e) => { e.stopPropagation(); setZoomImg(item.fullmd5) }}
                              onError={(e) => { e.target.onerror = null; e.target.style.display = 'none'; const p = e.target.parentElement; if (p && !p.querySelector('.img-fallback')) { const fb = document.createElement('div'); fb.className = 'img-fallback flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-muted'; fb.textContent = '[图片]'; p.appendChild(fb) } }}
                            />
                          ) : (
                            <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-muted">
                              <Image size={11} className="opacity-50" /> [图片]
                            </div>
                          )}
                        </div>
                      )}
                      {/* Voice */}
                      {t === 3 && (
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-muted">
                            <Microphone size={11} className="text-brand-green" />
                            <span className="text-brand-green">语音</span>
                            {item.duration && <span className="text-text-muted text-[10px]">{(item.duration / 1000).toFixed(1)}s</span>}
                          </div>
                          {item.dataid && (
                            <>
                              <button onClick={(e) => { e.stopPropagation(); const audio = document.getElementById(`crvoice-${i}`); if (audio) audio.paused ? audio.play().catch(() => {}) : audio.pause() }}
                                className="p-0.5 rounded bg-brand-green-light/30 hover:bg-brand-green-light/50 text-brand-green transition-colors text-xs" title="播放">▶</button>
                              <audio id={`crvoice-${i}`} src={`${API_BASE}/api/fav/voice/record?fav_id=0&dataid=${item.dataid}`} preload="metadata" style={{ display: 'none' }} />
                            </>
                          )}
                        </div>
                      )}
                      {/* File */}
                      {t === 8 && (
                        <div className="mt-0.5 flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-muted">
                          <File size={11} className="opacity-50" /> {item.desc || '文件'}
                        </div>
                      )}
                      {/* Link */}
                      {t === 5 && (item.link_url || item.desc) && (
                        <a href={item.link_url || '#'} target="_blank" rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="mt-0.5 flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-brand-green hover:bg-bg-raised/80 transition-colors max-w-[85%]">
                          <Link size={11} className="shrink-0" /> <span className="truncate">{item.link_title || item.desc || '链接'}</span>
                        </a>
                      )}
                      {/* Video */}
                      {t === 4 && (
                        <div className="mt-0.5 flex items-center gap-1 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-muted">
                          <VideoCamera size={11} className="opacity-50" /> [视频]
                        </div>
                      )}
                      {/* Fallback for unknown types */}
                      {![1, 2, 3, 4, 5, 8].includes(t) && item.desc && (
                        <div className="mt-0.5 px-2 py-1 rounded-md bg-bg-raised text-xs text-text-main max-w-[85%] break-words">
                          {item.desc}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      {/* Zoom overlay for images */}
      {zoomImg && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center" onClick={() => setZoomImg(null)}>
          <img src={`${API_BASE}/api/chat/image?fullmd5=${zoomImg}&talker=${encodeURIComponent(talker || '')}&create_time=${msgCreateTime || 0}`}
            className="max-w-[90vw] max-h-[85vh] object-contain rounded-lg" onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  )
}

// ── Chat Bubble ──
function ChatBubble({ msg, talker, myWxid, isGroupChat, onImageClick, isPlaying, onPlayStart, onPlayStop }) {
  const voiceRef = useRef(null)
  const isSelf = msg.is_self
  const localType = msg.localType

  // Sync play state from parent
  useEffect(() => {
    const audio = voiceRef.current
    if (!audio) return
    if (isPlaying) {
      audio.play().catch(() => {})
    } else {
      audio.pause()
      audio.currentTime = 0
    }
  }, [isPlaying])

  // System message
  if (localType === 10000) {
    return (
      <div className="flex justify-center py-1">
        <span className="text-xs text-text-muted bg-bg-raised/50 px-3 py-1 rounded-full">{msg.content}</span>
      </div>
    )
  }

  const avatarUrl = isSelf ? '' : (msg.sender_avatar || '')
  const avatarName = isSelf ? '我' : (msg.sender_name || msg.sender || '?')

  return (
    <div className={`flex gap-2 ${isSelf ? 'flex-row-reverse' : ''} mb-3`}>
      {/* Avatar */}
      <Avatar src={avatarUrl} name={avatarName} size={36} />

      {/* Body */}
      <div className={`max-w-[75%] ${isSelf ? 'items-end' : 'items-start'}`}>
        {/* Sender name (group chat only) */}
        {isGroupChat && !isSelf && msg.sender_name && (
          <p className="text-xs text-text-muted mb-0.5 ml-1">{msg.sender_name}</p>
        )}

        {/* Bubble */}
        <div className={`rounded-2xl px-3 py-2 text-[15px] leading-relaxed
          ${isSelf
            ? 'bg-[#95EC69] dark:bg-[#2b5a1e] text-[#0d0d0d] dark:text-[#f0f0f0] rounded-tr-sm'
            : 'bg-bg-card border border-border-main text-text-main rounded-tl-sm'
          }`}>

          {/* Text */}
          {localType === 1 && (
            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
          )}

          {/* Image */}
          {localType === 3 && msg.images?.map((img, i) => (
            <div key={i} className="-mx-1 -my-1">
              {img.fullmd5 ? (
                <img
                  src={`${API_BASE}/api/chat/image?fullmd5=${img.fullmd5}${img.fullsize ? '&fullsize=' + img.fullsize : ''}&talker=${encodeURIComponent(talker)}&create_time=${msg.create_time || 0}`}
                  alt="图片"
                  className="max-w-[200px] max-h-[200px] rounded-lg cursor-zoom-in object-cover"
                  loading="lazy"
                  onClick={(e) => onImageClick?.(e.target.src)}
                />
              ) : (
                <div className="w-[100px] h-[100px] rounded-lg bg-bg-raised/50 flex flex-col items-center justify-center text-text-muted gap-1">
                  <Image size={20} weight="thin" />
                  <span className="text-xs">加载失败</span>
                </div>
              )}
            </div>
          ))}

          {/* Voice */}
          {localType === 34 && msg.voice && (
            <button
              onClick={() => {
                if (isPlaying) { onPlayStop?.() }
                else { onPlayStart?.() }
              }}
              className={`flex items-center gap-2 py-1 min-w-[80px] ${isSelf ? 'flex-row-reverse' : ''}`}
            >
              {isPlaying ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
              <div className="flex gap-[2px]">
                {[8, 14, 10, 16, 12, 8, 14].map((h, i) => (
                  <div key={i} className={`w-[3px] rounded-full ${isPlaying ? 'animate-pulse' : ''}`}
                    style={{ height: h, backgroundColor: 'currentColor', opacity: 0.5 + i * 0.07 }} />
                ))}
              </div>
              <span className="text-xs opacity-70">语音</span>
              <audio
                ref={voiceRef}
                src={`${API_BASE}/api/voice?session_id=${encodeURIComponent(talker)}&create_time=${msg.voice.create_time}&local_id=${msg.voice.local_id}&svr_id=${msg.voice.server_id}&candidates=${encodeURIComponent(JSON.stringify([talker, msg.sender, myWxid].filter(Boolean)))}`}
                preload="metadata"
                onEnded={() => onPlayStop?.()}
              />
            </button>
          )}

          {/* Video */}
          {localType === 43 && msg.images?.map((img, i) => (
            <div key={i} className="-mx-1 -my-1">
              {img.fullmd5 ? (
                <video
                  src={`${API_BASE}/api/chat/image?fullmd5=${img.fullmd5}${img.fullsize ? '&fullsize=' + img.fullsize : ''}&talker=${encodeURIComponent(talker)}&create_time=${msg.create_time || 0}`}
                  controls
                  playsInline
                  preload="metadata"
                  className="w-full max-w-[300px] rounded-lg mt-1"
                />
              ) : (
                <div className="w-[140px] h-[100px] rounded-lg bg-bg-raised/50 flex flex-col items-center justify-center text-text-muted gap-1">
                  <VideoCamera size={20} weight="thin" />
                  <span className="text-xs">加载失败</span>
                </div>
              )}
            </div>
          ))}

          {/* Chat records (type 49, appmsg type=19) */}
          {localType === 49 && msg.chat_records && (
            <ChatRecordCard records={msg.chat_records} talker={talker} myWxid={myWxid} msgCreateTime={msg.create_time} />
          )}

          {/* Quote/Reply message (type 49, appmsg type=57) */}
          {localType === 49 && msg.quote && (
            <div className="space-y-1.5">
              <div className="flex items-start gap-1 px-2 py-1.5 rounded-md bg-bg-raised/50 border-l-2 border-text-muted/30 text-xs text-text-muted">
                <span className="truncate">{msg.quote.sender ? `${msg.quote.sender}: ` : ''}{msg.quote.content}</span>
              </div>
              {msg.reply_text && <p className="whitespace-pre-wrap break-words">{msg.reply_text}</p>}
              {!msg.reply_text && msg.content && <p className="whitespace-pre-wrap break-words">{msg.content}</p>}
            </div>
          )}

          {/* Regular link (type 49, has link, not chat records/quote) */}
          {localType === 49 && msg.link && !msg.chat_records && !msg.quote && msg.link.url && (
            <a
              href={msg.link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block -mx-1 -my-1 p-2 rounded-lg bg-bg-raised/30 hover:bg-bg-raised/50 transition-colors"
            >
              <div className="flex items-center gap-1.5">
                <Link size={12} className="text-brand-green flex-shrink-0" />
                <span className="text-xs text-brand-green line-clamp-2">{msg.link.title || msg.link.filename || msg.link.url}</span>
              </div>
            </a>
          )}

          {/* App message without URL (Bot/service account text appmsg) — show title as text */}
          {localType === 49 && msg.link && !msg.chat_records && !msg.quote && !msg.link.url && msg.link.title && (
            <p className="whitespace-pre-wrap break-words">{msg.link.title}</p>
          )}

          {/* App message with no link/chat_records/quote at all — show content text */}
          {localType === 49 && !msg.link && !msg.chat_records && !msg.quote && msg.content && !msg.content.trim().startsWith('<') && (
            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
          )}

          {/* Emoji */}
          {localType === 47 && <span className="text-text-muted text-xs">[表情]</span>}

          {/* Fallback for unknown types */}
          {![1, 3, 34, 43, 49, 47, 10000].includes(localType) && msg.content && !msg.content.trim().startsWith('<') && (
            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
          )}
        </div>

        {/* Time */}
        <p className={`text-xs text-text-muted mt-0.5 ${isSelf ? 'text-right mr-1' : 'ml-1'}`}>
          {formatMsgTime(msg.create_time)}
        </p>
      </div>
    </div>
  )
}

// ── Session Item ──

function SessionItem({ session, isActive, onSelect }) {
  const displayName = session.displayName || session.username
  const isGroup = session.username?.includes('@chatroom')
  const isFoldGroup = session.isFoldGroup
  const foldType = session.foldType  // 'oa' or 'foldgroup'
  const unread = session.unread_count || 0

  // Different icons/colors for fold types
  const foldLabel = foldType === 'oa' ? '公众号' : '折叠的聊天'
  const foldIcon = foldType === 'oa' ? <FileText size={18} /> : <Chats size={18} />

  return (
    <button
      onClick={() => onSelect(session)}
      className={`w-full text-left px-3 py-3 border-b border-border-main/30 transition-colors cursor-pointer
        ${isActive ? 'bg-brand-green-light/15 border-l-2 border-l-brand-green' : 'hover:bg-bg-raised/40 border-l-2 border-l-transparent'}`}
    >
      <div className="flex items-center gap-3">
        <div className="relative">
          {isFoldGroup ? (
            <div className={`rounded-full flex items-center justify-center text-xs font-semibold flex-shrink-0
              ${foldType === 'oa' ? 'bg-orange-500/20 text-orange-500' : 'bg-text-muted/20 text-text-muted'}`}
              style={{ width: 42, height: 42 }}>
              {foldIcon}
            </div>
          ) : (
            <Avatar
              src={session.avatarUrl}
              name={session.username || ''}
              size={42}
              className={isGroup ? 'group-avatar' : ''}
            />
          )}
          {unread > 0 && (
            <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-status-error text-white text-xs font-bold flex items-center justify-center">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-text-main truncate pr-2">
              {isFoldGroup ? foldLabel : displayName}
            </p>
            <span className="text-xs text-text-muted flex-shrink-0">
              {formatSessionTime(session.nTime)}
            </span>
          </div>
          {isFoldGroup && session.summary ? (
            <p className="text-xs text-text-muted truncate mt-0.5">{session.summary}</p>
          ) : session.last_sender_display_name ? (
            <p className="text-xs text-text-muted truncate mt-0.5">
              {session.last_sender_display_name}: ...
            </p>
          ) : null}
        </div>
      </div>
    </button>
  )
}

// ── Main Chat Tab ──
export default function ChatTab() {
  const [sessions, setSessions] = useState([])
  const [oaSessions, setOaSessions] = useState([])       // 公众号列表
  const [foldedSessions, setFoldedSessions] = useState([]) // 折叠群聊列表
  const [foldedView, setFoldedView] = useState(false)    // 折叠视图
  const [foldedViewType, setFoldedViewType] = useState('') // 'oa' or 'foldgroup'
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [selectedSession, setSelectedSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [timePreset, setTimePreset] = useState(0)
  const [customStart, setCustomStart] = useState('')
  const [customEnd, setCustomEnd] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportStatus, setExportStatus] = useState('')
  const [showExportPanel, setShowExportPanel] = useState(false)  // Export configuration panel
  const [exportMediaTypes, setExportMediaTypes] = useState({ images: true, voices: false, links: true, videos: false })
  const [filterRevoked, setFilterRevoked] = useState(false)    // Show only revoked messages
  const [antiRevokeStatus, setAntiRevokeStatus] = useState(null) // Anti-revoke trigger status
  const [togglingAntiRevoke, setTogglingAntiRevoke] = useState(false)
  const [showAntiRevoke, setShowAntiRevoke] = useState(false)   // Show anti-revoke card
  const [restrictedEnabled, setRestrictedEnabled] = useState(false) // Whether restricted features are enabled
  const [myWxid, setMyWxid] = useState('')
  const [lightboxSrc, setLightboxSrc] = useState(null)    // Image lightbox
  const [autoScroll, setAutoScroll] = useState(true)      // Step 1: auto scroll tracking
  const [currentPlayingVoice, setCurrentPlayingVoice] = useState(null)  // Step 9: voice coordination
  const [groupMembers, setGroupMembers] = useState([])      // Group member list
  const [memberSearch, setMemberSearch] = useState('')      // Member search keyword
  const groupFriends = groupMembers.filter(m => m.is_friend)  // Only friends in this group
  const [loadingMembers, setLoadingMembers] = useState(false)
  const [commonGroups, setCommonGroups] = useState([])      // Common groups with friend
  const [loadingCommonGroups, setLoadingCommonGroups] = useState(false)
  const [showMembers, setShowMembers] = useState(false)     // Toggle member panel
  const [showCommonGroups, setShowCommonGroups] = useState(false) // Toggle common groups panel
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [aiChatSessionsMap, setAiChatSessionsMap] = useState({})
  // Shape: { [talker]: { session, messages, inputText, isStreaming, tokenUsage, autoCompressed } }
  const [activeAiTalker, setActiveAiTalker] = useState(null)
  const [aiChatTimePreset, setAiChatTimePreset] = useState(0)  // 0=7天, 1=30天, 2=90天, 3=全部, -1=自定义
  const [aiChatCustomStart, setAiChatCustomStart] = useState('')
  const [aiChatCustomEnd, setAiChatCustomEnd] = useState('')
  const msgContainerRef = useRef(null)
  const searchTimerRef = useRef(null)   // Step 2: debounce fix
  const loadMoreRef = useRef(null)      // Step 4: infinite scroll sentinel
  const memberSearchTimerRef = useRef(null)  // Member search debounce

  useEffect(() => { loadSessions() }, [])

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/chat/anti-revoke/status`)
        const data = await res.json()
        if (data.disabled) { setRestrictedEnabled(false); return }
        setRestrictedEnabled(true)
        setAntiRevokeStatus(data)
      } catch {}
    }
    load()
  }, [])

  // Step 5: WebSocket with addEventListener (no listener accumulation)
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'chat_export_progress') {
          setExportStatus(data.status)
          if (data.status === 'completed' || data.status === 'error') {
            setExporting(false)
            if (data.status === 'completed') setTimeout(() => setExportStatus(''), 3000)
          }
        }
      } catch {}
    }
    let ws = window.__chat_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__chat_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  // Step 4: Infinite scroll with IntersectionObserver
  useEffect(() => {
    if (!loadMoreRef.current || !hasMore || !selectedSession) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasMore && !loadingMessages && selectedSession) {
          loadMessages(selectedSession.username, true)
        }
      },
      { root: msgContainerRef.current, threshold: 0.1 }
    )
    observer.observe(loadMoreRef.current)
    return () => observer.disconnect()
  }, [hasMore, loadingMessages, selectedSession, messages])

  async function loadSessions(keyword = '') {
    setLoading(true)
    try {
      const url = keyword
        ? `${API_BASE}/api/chat/sessions?keyword=${encodeURIComponent(keyword)}`
        : `${API_BASE}/api/chat/sessions`
      const res = await fetch(url)
      const data = await res.json()
      if (data.ok) {
        setSessions(data.data || [])
        setOaSessions(data.oaSessions || [])
        setFoldedSessions(data.foldedSessions || [])
        if (data.myWxid) setMyWxid(data.myWxid)
      }
    } catch {}
    setLoading(false)
  }

  async function loadMessages(talker, append = false) {
    if (!talker) return
    setLoadingMessages(true)
    const currentOffset = append ? offset : 0
    const limit = 100

    // Save scroll position before appending older messages at top
    const container = msgContainerRef.current
    const prevScrollHeight = append ? container?.scrollHeight : 0

    try {
      let url = `${API_BASE}/api/chat/messages?talker=${encodeURIComponent(talker)}&limit=${limit}&offset=${currentOffset}`
      const res = await fetch(url)
      const data = await res.json()
      if (data.ok) {
        const newMsgs = data.data || []
        // Older messages (higher offset) go at the top so timeline stays chronological
        if (append) { setMessages(prev => [...newMsgs, ...prev]) }
        else { setMessages(newMsgs) }
        setOffset(currentOffset + newMsgs.length)
        setHasMore(newMsgs.length >= limit)
      }
    } catch {}
    setLoadingMessages(false)

    if (!append) {
      // Initial load: scroll to bottom to show latest messages
      // Use rAF with retry to ensure DOM has finished rendering
      const scrollToEnd = (attempts = 0) => {
        requestAnimationFrame(() => {
          const el = msgContainerRef.current
          if (el) {
            el.scrollTop = el.scrollHeight
            // Retry if scroll didn't reach bottom (content still rendering)
            if (el.scrollTop + el.clientHeight < el.scrollHeight - 10 && attempts < 5) {
              scrollToEnd(attempts + 1)
            }
          }
        })
      }
      scrollToEnd()
      setAutoScroll(true)
    } else {
      // Append: restore scroll position so user stays at the same message
      // (content was inserted above, scrollHeight increased)
      requestAnimationFrame(() => {
        const el = msgContainerRef.current
        if (el) {
          const newScrollHeight = el.scrollHeight
          el.scrollTop = newScrollHeight - prevScrollHeight
        }
      })
    }
  }

  async function loadGroupMembers(chatroom, keyword = '') {
    if (!chatroom) return
    setLoadingMembers(true)
    try {
      let url = `${API_BASE}/api/chat/members?chatroom=${encodeURIComponent(chatroom)}`
      if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`
      const res = await fetch(url)
      const data = await res.json()
      if (data.ok) setGroupMembers(data.data || [])
    } catch {}
    setLoadingMembers(false)
  }

  async function loadCommonGroups(wxid) {
    if (!wxid) return
    setLoadingCommonGroups(true)
    try {
      const res = await fetch(`${API_BASE}/api/chat/common-groups?wxid=${encodeURIComponent(wxid)}`)
      const data = await res.json()
      if (data.ok) setCommonGroups(data.data || [])
    } catch {}
    setLoadingCommonGroups(false)
  }

  function toggleMembersPanel() {
    const next = !showMembers
    setShowMembers(next)
    if (next && selectedSession?.username && groupMembers.length === 0) {
      loadGroupMembers(selectedSession.username, memberSearch)
    }
  }

  function toggleCommonGroupsPanel() {
    const next = !showCommonGroups
    setShowCommonGroups(next)
    if (next && selectedSession?.username && commonGroups.length === 0) {
      loadCommonGroups(selectedSession.username)
    }
  }

  function handleMemberSearchChange(val) {
    setMemberSearch(val)
    clearTimeout(memberSearchTimerRef.current)
    memberSearchTimerRef.current = setTimeout(() => {
      if (selectedSession?.username?.includes('@chatroom')) {
        loadGroupMembers(selectedSession.username, val)
      }
    }, 300)
  }

  function selectSession(session) {
    // Clicking fold group entry switches to folded view
    if (session.isFoldGroup) {
      setFoldedViewType(session.foldType || 'oa')
      setFoldedView(true)
      return
    }
    setAiChatOpen(false)  // Close AI Drawer to avoid confusion
    setSelectedSession(session)
    setMessages([])
    setOffset(0)
    setHasMore(true)
    setTimePreset(0)
    setCustomStart('')
    setCustomEnd('')
    setCurrentPlayingVoice(null) // Stop any playing voice
    // Reset member/common groups state
    setGroupMembers([])
    setMemberSearch('')
    setShowMembers(false)
    setCommonGroups([])
    setShowCommonGroups(false)
    setTimeout(() => loadMessages(session.username), 0)
  }

  async function toggleAntiRevoke() {
    setTogglingAntiRevoke(true)
    try {
      const isInstalled = antiRevokeStatus?.installed
      const endpoint = isInstalled ? '/api/chat/anti-revoke/uninstall' : '/api/chat/anti-revoke/install'
      const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' })
      const data = await res.json()
      setAntiRevokeStatus(data)
    } catch {}
    setTogglingAntiRevoke(false)
  }

  function changeTimePreset(_idx) { /* deprecated — time filtering moved to export panel */ }

  function applyCustomRange() { /* DatePicker callback — no message reload needed, used by export panel */ }

  // Step 1: Track scroll position for "back to bottom" button
  function handleMsgScroll() {
    const el = msgContainerRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setAutoScroll(distFromBottom < 60)
  }

  function scrollToBottom() {
    const el = msgContainerRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setAutoScroll(true)
  }

  async function handleExport() {
    if (!selectedSession) return
    setExporting(true); setExportStatus('starting')
    let start_time = 0
    let end_time = 0
    if (customStart) start_time = Math.floor(new Date(customStart).getTime() / 1000)
    if (customEnd) end_time = Math.floor(new Date(customEnd + 'T23:59:59').getTime() / 1000)
    const mediaTypes = exportMediaTypes
    try {
      const dryRes = await fetch(`${API_BASE}/api/chat/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ talker: selectedSession.username, start_time, end_time, dry_run: true, media_types: mediaTypes }),
      })
      const dryData = await dryRes.json()
      if (!dryData.ok) { setExportStatus('error'); setExporting(false); return }
      const msg = dryData.truncated
        ? `消息数量较多，将导出最近 ${dryData.item_count} 条（已截断），约 ${dryData.image_count} 张图片、${dryData.voice_count} 条语音（预估 ~${dryData.estimated_mb} MB），确认继续？`
        : `即将导出 ${dryData.item_count} 条消息，约 ${dryData.image_count} 张图片、${dryData.voice_count} 条语音（预估 ~${dryData.estimated_mb} MB），确认继续？`
      if (dryData.size_warning || dryData.truncated) {
        const confirmed = window.confirm(msg)
        if (!confirmed) { setExporting(false); setExportStatus(''); return }
      }
      const res = await fetch(`${API_BASE}/api/chat/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ talker: selectedSession.username, start_time, end_time, media_types: mediaTypes }),
      })
      const data = await res.json()
      if (data.ok) {
        setExportStatus('completed'); setShowExportPanel(false)
        openExportFolder(); setTimeout(() => setExportStatus(''), 3000)
      }
      else { setExportStatus('error') }
    } catch { setExportStatus('error') }
    finally { setExporting(false) }
  }

  async function openExportFolder() {
    try { await fetch(`${API_BASE}/api/export/open-folder`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'chat' }) }) } catch {}
  }

  // Step 2: Debounce fix — use useRef instead of closure variable
  function handleSearchChange(val) {
    setSearch(val)
    clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => loadSessions(val), 300)
  }

  const isGroupChat = selectedSession?.username?.includes('@chatroom')

  // ── AI Chat (per-chat_id session) ──────────────────────────
  const activeAiData = activeAiTalker ? aiChatSessionsMap[activeAiTalker] : null

  function updateAiSession(updates) {
    setAiChatSessionsMap(prev => ({
      ...prev,
      [activeAiTalker]: { ...(prev[activeAiTalker] || {}), ...updates },
    }))
  }

  function openAIDrawer(talker) {
    setActiveAiTalker(talker)
    setAiChatOpen(true)
  }

  function closeAIDrawer() {
    setAiChatOpen(false)
  }

  function handleNewAIChat() {
    if (activeAiData?.session?.session_id) {
      fetch(`${API_BASE}/api/ai/chat/destroy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: activeAiData.session.session_id }),
      }).catch(() => {})
    }
    setAiChatSessionsMap(prev => {
      const next = { ...prev }
      delete next[activeAiTalker]
      return next
    })
  }

  async function startAIChat() {
    if (!activeAiTalker && !selectedSession?.username) return
    const talker = activeAiTalker || selectedSession.username
    const sourceType = talker.includes('@chatroom') ? 'group_chat' : 'private_chat'

    // Resolve time range from config
    let start_time = 0
    let end_time = 0
    if (aiChatTimePreset >= 0) {
      const preset = AI_CHAT_TIME_PRESETS[aiChatTimePreset]
      start_time = typeof preset.start === 'function' ? preset.start() : preset.start
      end_time = typeof preset.end === 'function' ? preset.end() : preset.end
    } else {
      // Custom range
      if (aiChatCustomStart) start_time = Math.floor(new Date(aiChatCustomStart).getTime() / 1000)
      if (aiChatCustomEnd) end_time = Math.floor(new Date(aiChatCustomEnd + 'T23:59:59').getTime() / 1000)
    }

    try {
      const res = await fetch(`${API_BASE}/api/ai/chat/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_type: sourceType,
          source_id: talker,
          start_time: start_time || undefined,
          end_time: end_time || undefined,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        setAiChatSessionsMap(prev => ({
          ...prev,
          [talker]: {
            session: data,
            messages: [],
            inputText: '',
            isStreaming: false,
            tokenUsage: data.token_usage || { used: 0, budget: 100000 },
            autoCompressed: false,
          },
        }))
      } else {
        alert(data.error || '启动 AI 对话失败')
      }
    } catch (e) {
      alert('无法连接服务器')
    }
  }

  // Folded view sessions
  const foldedViewSessions = foldedViewType === 'oa' ? oaSessions : foldedSessions

  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }}>
      <div className="flex flex-col lg:flex-row gap-4 h-[calc(100dvh-140px)]">
        {/* ── Left Panel ── */}
        <div className="w-full lg:w-72 lg:flex-shrink-0 flex flex-col border border-border-main rounded-xl bg-bg-card overflow-hidden max-h-[40vh] lg:max-h-none">
          <div className="p-3 border-b border-border-main">
            {foldedView ? (
              <div className="flex items-center gap-2">
                <button onClick={() => setFoldedView(false)}
                  className="p-1 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised/50 transition-colors cursor-pointer">
                  <X size={14} />
                </button>
                <span className="text-xs font-medium text-text-main">
                  {foldedViewType === 'oa' ? '公众号' : '折叠的聊天'}
                </span>
                <span className="text-xs text-text-muted ml-auto">
                  {foldedViewSessions.length} 个
                </span>
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <div className="relative flex-1">
                  <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                  <input
                    type="text" value={search} onChange={(e) => handleSearchChange(e.target.value)}
                    placeholder="搜索联系人..."
                    className="w-full bg-bg-raised border border-border-main rounded-full pl-8 pr-3 py-2 text-xs text-text-main
                      placeholder:text-text-muted focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15"
                  />
                </div>
                {restrictedEnabled && (
                <button
                  onClick={() => setShowAntiRevoke(!showAntiRevoke)}
                  className={`p-2 rounded-full transition-colors cursor-pointer shrink-0
                    ${antiRevokeStatus?.installed
                      ? 'text-brand-green hover:bg-brand-green-light/20'
                      : 'text-text-muted hover:bg-bg-raised'
                    }`}
                  title="撤回拦截设置"
                >
                  <ShieldCheck size={16} weight={antiRevokeStatus?.installed ? 'fill' : 'regular'} />
                </button>
                )}
              </div>
            )}
          </div>
          {/* Anti-revoke card (collapsible) */}
          <AnimatePresence>
            {restrictedEnabled && showAntiRevoke && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="px-3 py-2.5 border-b border-border-main">
                  <div className="p-3 rounded-xl border border-border-main bg-bg-raised/50">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2.5">
                        {antiRevokeStatus?.installed ? (
                          <ShieldCheck size={18} className="text-brand-green" weight="fill" />
                        ) : (
                          <ShieldWarning size={18} className="text-text-muted" />
                        )}
                        <div>
                          <p className="text-xs font-medium text-text-main">撤回拦截</p>
                          <p className="text-[10px] text-text-muted">
                            {antiRevokeStatus?.installed
                              ? '已启用 — 被撤回的消息将被拦截'
                              : '未启用 — 安装 trigger 拦截消息撤回'}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={toggleAntiRevoke}
                        disabled={togglingAntiRevoke}
                        className={`px-3 py-1.5 rounded-full text-[10px] font-semibold transition-all cursor-pointer
                          ${antiRevokeStatus?.installed
                            ? 'bg-status-error-soft text-status-error border border-status-error/20 hover:bg-status-error/20'
                            : 'bg-brand-green-light text-brand-green-hover dark:text-brand-green border border-brand-green/20 hover:bg-brand-green-light/60'
                          }`}
                      >
                        {togglingAntiRevoke ? '...' : antiRevokeStatus?.installed ? '卸载拦截' : '安装拦截'}
                      </button>
                    </div>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-5 h-5 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
              </div>
            ) : foldedView ? (
              foldedViewSessions.length === 0 ? (
                <div className="text-center py-12 text-text-muted text-xs">
                  <p>暂无{foldedViewType === 'oa' ? '公众号' : '折叠的聊天'}</p>
                </div>
              ) : (
                foldedViewSessions.map(s => (
                  <SessionItem
                    key={s.username}
                    session={s}
                    isActive={selectedSession?.username === s.username}
                    onSelect={(session) => { selectSession(session); }}
                  />
                ))
              )
            ) : sessions.length === 0 ? (
              <div className="text-center py-12 text-text-muted text-xs">
                <Chats size={28} className="mx-auto mb-2 opacity-30" />
                <p>暂无会话</p>
              </div>
            ) : (
              sessions.map(s => (
                <SessionItem
                  key={s.username}
                  session={s}
                  isActive={selectedSession?.username === s.username}
                  onSelect={selectSession}
                />
              ))
            )}
          </div>
        </div>

        {/* ── Right Panel ── */}
        <div className="flex-1 flex flex-col border border-border-main rounded-xl bg-bg-main overflow-hidden relative">
          {!selectedSession ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center text-text-muted">
                <Chats size={40} className="mx-auto mb-3 opacity-20" />
                <p className="text-sm">选择联系人查看会话记录</p>
                <p className="text-xs mt-1">支持搜索、时间筛选和导出</p>
              </div>
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="px-4 py-3 border-b border-border-main/30 bg-bg-card/80 backdrop-blur-sm flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <Avatar src={selectedSession.avatarUrl} name={selectedSession.username} size={32} />
                  <div>
                    <h3 className="text-sm font-semibold text-text-main">
                      {selectedSession.displayName || selectedSession.username}
                    </h3>
                    {isGroupChat && <span className="text-xs text-text-muted">群聊</span>}
                    {!isGroupChat && selectedSession?.username && !selectedSession.username.startsWith('gh_') && !selectedSession.username.startsWith('weixin') && selectedSession.username !== 'filehelper' && (
                      <span className="text-xs text-text-muted">好友</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {isGroupChat && (
                    <button onClick={toggleMembersPanel}
                      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors cursor-pointer
                        ${showMembers ? 'bg-brand-green-light/20 border-brand-green/30 text-brand-green' : 'border-border-main/50 text-text-muted hover:text-text-main'}`}>
                      <Users size={12} />群内好友
                    </button>
                  )}
                  <button onClick={() => openAIDrawer(selectedSession.username)}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border border-brand-green/30 bg-brand-green-light/20 text-brand-green hover:bg-brand-green/20 transition-colors cursor-pointer">
                    <ChatCircleDots size={12} />AI 对话
                    {activeAiTalker === selectedSession.username && activeAiData?.isStreaming && !aiChatOpen && (
                      <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-pulse" />
                    )}
                  </button>
                  {!isGroupChat && selectedSession?.username && !selectedSession.username.startsWith('gh_') && !selectedSession.username.startsWith('weixin') && selectedSession.username !== 'filehelper' && (
                    <button onClick={toggleCommonGroupsPanel}
                      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors cursor-pointer
                        ${showCommonGroups ? 'bg-brand-green-light/20 border-brand-green/30 text-brand-green' : 'border-border-main/50 text-text-muted hover:text-text-main'}`}>
                      <Chats size={12} />共同群聊
                    </button>
                  )}
                  {/* Revoked messages filter toggle (only when restricted features enabled) */}
                  {restrictedEnabled && (
                  <button onClick={() => setFilterRevoked(!filterRevoked)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors cursor-pointer
                      ${filterRevoked ? 'bg-orange-500/15 border-orange-500/30 text-orange-600' : 'border-border-main/50 text-text-muted hover:text-text-main'}`}
                    title="筛选已撤回消息">
                    <Eye size={12} />已撤回
                  </button>
                  )}
                  <button onClick={() => loadMessages(selectedSession.username)}
                    className="p-1.5 rounded-full text-text-muted hover:text-text-main hover:bg-bg-raised/50 transition-colors cursor-pointer" title="刷新">
                    <ArrowsClockwise size={14} />
                  </button>
                  <button onClick={() => setShowExportPanel(!showExportPanel)} disabled={exporting}
                    className={`hidden lg:flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-colors cursor-pointer
                      ${exporting ? 'bg-brand-green-light/20 text-brand-green cursor-wait' : showExportPanel ? 'bg-brand-green-light/20 border border-brand-green/30 text-brand-green' : 'bg-brand-green-hover text-white hover:bg-[#0d8c5c]'}`}>
                    <Download size={12} />{exporting ? '导出中...' : '导出'}
                  </button>
                </div>
              </div>

              {/* Export configuration panel */}
              <AnimatePresence>
                {showExportPanel && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <div className="px-4 py-3 border-b border-border-main/30 bg-bg-card/70 space-y-3">
                      {/* Time range */}
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-text-muted font-medium">时间范围</span>
                        <DatePicker
                          startDate={customStart}
                          endDate={customEnd}
                          onStartChange={setCustomStart}
                          onEndChange={setCustomEnd}
                          onRangeComplete={applyCustomRange}
                        />
                        {(!customStart && !customEnd) && <span className="text-xs text-text-muted">默认全部时间</span>}
                      </div>
                      {/* Media type selection */}
                      <div className="flex items-center gap-4">
                        <span className="text-xs text-text-muted font-medium">导出内容</span>
                        <label className="flex items-center gap-1.5 text-xs text-text-main cursor-pointer">
                          <input type="checkbox" checked={exportMediaTypes.images} onChange={e => setExportMediaTypes(prev => ({...prev, images: e.target.checked}))}
                            className="accent-brand-green" />
                          图片
                        </label>
                        <label className="flex items-center gap-1.5 text-xs text-text-main cursor-pointer">
                          <input type="checkbox" checked={exportMediaTypes.videos} onChange={e => setExportMediaTypes(prev => ({...prev, videos: e.target.checked}))}
                            className="accent-brand-green" />
                          视频
                        </label>
                        <label className="flex items-center gap-1.5 text-xs text-text-main cursor-pointer">
                          <input type="checkbox" checked={exportMediaTypes.voices} onChange={e => setExportMediaTypes(prev => ({...prev, voices: e.target.checked}))}
                            className="accent-brand-green" />
                          语音
                        </label>
                        <label className="flex items-center gap-1.5 text-xs text-text-main cursor-pointer">
                          <input type="checkbox" checked={exportMediaTypes.links} onChange={e => setExportMediaTypes(prev => ({...prev, links: e.target.checked}))}
                            className="accent-brand-green" />
                          链接/卡片
                        </label>
                      </div>
                      {/* Confirm button */}
                      <div className="flex items-center gap-3">
                        <button onClick={handleExport} disabled={exporting}
                          className={`hidden lg:inline-flex px-4 py-1.5 rounded-full text-xs font-semibold transition-colors cursor-pointer
                            ${exporting ? 'bg-brand-green-light/20 text-brand-green cursor-wait' : 'bg-brand-green-hover text-white hover:bg-[#0d8c5c]'}`}>
                          {exporting ? '导出中...' : '确认导出'}
                        </button>
                        <button onClick={() => setShowExportPanel(false)}
                          className="px-3 py-1.5 rounded-full text-xs border border-border-main/50 text-text-muted hover:text-text-main transition-colors cursor-pointer">
                          取消
                        </button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Group friends panel */}
              <AnimatePresence>
                {showMembers && isGroupChat && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <div className="px-4 py-3 border-b border-border-main/30 bg-bg-card/70">
                      <div className="relative mb-2">
                        <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                        <input
                          type="text"
                          value={memberSearch}
                          onChange={(e) => handleMemberSearchChange(e.target.value)}
                          placeholder="搜索好友..."
                          className="w-full bg-bg-raised border border-border-main rounded-full pl-8 pr-3 py-1.5 text-xs text-text-main placeholder:text-text-muted focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15"
                        />
                      </div>
                      <div className="flex items-center gap-2 mb-2">
                        <Users size={13} className="text-text-muted" />
                        <span className="text-xs text-text-muted">
                          {loadingMembers ? '加载中...' : `${groupFriends.length} 位好友`}
                        </span>
                      </div>
                      <div className="max-h-44 overflow-y-auto grid grid-cols-2 gap-1">
                        {groupFriends.map((member) => (
                          <div key={member.wxid} className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-bg-raised/40 hover:bg-bg-raised/70 transition-colors">
                            <Avatar name={member.display_name} size={22} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1">
                              <p className="text-sm text-text-main truncate">{member.display_name}</p>
                                {member.is_friend && (
                                  <span className="shrink-0 text-xs px-1 py-0.5 rounded-full bg-brand-green-light/20 text-brand-green">好友</span>
                                )}
                              </div>
                              {member.group_nickname && member.group_nickname !== member.display_name && (
                                <p className="text-xs text-text-muted truncate">群昵称：{member.group_nickname}</p>
                              )}
                            </div>
                          </div>
                        ))}
                        {!loadingMembers && groupFriends.length === 0 && (
                          <div className="col-span-2 text-center py-4 text-sm text-text-muted">该群暂无你的好友</div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              {/* Common groups panel */}
              <AnimatePresence>
                {showCommonGroups && !isGroupChat && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <div className="px-4 py-3 border-b border-border-main/30 bg-bg-card/70">
                      <div className="flex items-center gap-2 mb-2">
                        <Chats size={13} className="text-text-muted" />
                        <span className="text-sm font-medium text-text-main">共同群聊</span>
                        <span className="text-xs text-text-muted">
                          {loadingCommonGroups ? '加载中...' : `${commonGroups.length} 个`}
                        </span>
                      </div>
                      <div className="max-h-44 overflow-y-auto space-y-1">
                        {commonGroups.map((group) => (
                          <button
                            key={group.chatroom_id}
                            onClick={() => {
                              const groupSession = sessions.find(s => s.username === group.chatroom_id) || foldedSessions.find(s => s.username === group.chatroom_id)
                              if (groupSession) selectSession(groupSession)
                            }}
                            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg bg-bg-raised/40 hover:bg-bg-raised/70 transition-colors text-left cursor-pointer"
                          >
                            <Avatar name={group.group_name} size={24} className="group" />
                            <div className="min-w-0 flex-1">
                              <p className="text-sm text-text-main truncate">{group.group_name}</p>
                              <p className="text-xs text-text-muted">{group.member_count} 人</p>
                            </div>
                          </button>
                        ))}
                        {!loadingCommonGroups && commonGroups.length === 0 && (
                          <div className="text-center py-4 text-sm text-text-muted">暂无共同群聊</div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Messages */}
              <div ref={msgContainerRef} className="flex-1 overflow-y-auto px-4 py-3" onScroll={handleMsgScroll}>
                {(() => {
                  // Apply revoked filter: when active, only show revoked system messages
                  const displayMessages = filterRevoked
                    ? messages.filter(m => m.localType === 10000 && (m.content || '').includes('撤回'))
                    : messages
                  return displayMessages.length === 0 && !loadingMessages ? (
                  <div className="text-center py-12 text-text-muted text-xs">
                    <Eye size={24} className="mx-auto mb-2 opacity-30" />
                    {filterRevoked
                      ? <div>
                          <p>暂未捕获到撤回消息</p>
                          <p className="mt-1 text-[10px] opacity-60">该会话中的撤回消息可能已被清理或尚未出现</p>
                        </div>
                      : <p>暂无消息</p>
                    }
                  </div>
                ) : (
                  <>
                    {/* Step 4: IntersectionObserver sentinel replaces "load more" button */}
                    {hasMore && (
                      <div ref={loadMoreRef} className="py-2 text-center">
                        {loadingMessages && (
                          <div className="w-4 h-4 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin mx-auto" />
                        )}
                      </div>
                    )}
                    {displayMessages.map((msg, i) => {
                      const prevMsg = i > 0 ? displayMessages[i - 1] : null
                      const showDate = shouldShowDateDivider(msg, prevMsg)
                      const showTime = shouldShowTime(msg, prevMsg)
                      return (
                        <div key={msg.local_id || i}>
                          {showDate && (
                            <div className="flex justify-center py-2 mb-1">
                              <span className="text-xs text-text-muted bg-bg-raised/50 px-3 py-1 rounded-full">
                                {formatDateDivider(msg.create_time)}
                              </span>
                            </div>
                          )}
                          <ChatBubble
                            msg={msg}
                            talker={selectedSession.username}
                            myWxid={myWxid}
                            isGroupChat={isGroupChat}
                            onImageClick={(src) => {
                              setLightboxSrc(src)
                            }}
                            isPlaying={currentPlayingVoice === msg.local_id}
                            onPlayStart={() => setCurrentPlayingVoice(msg.local_id)}
                            onPlayStop={() => setCurrentPlayingVoice(null)}
                          />
                        </div>
                      )
                    })}
                  </>
                )
                })()}
              </div>

              {/* Step 1: "Back to bottom" floating button */}
              <AnimatePresence>
                {!autoScroll && messages.length > 0 && (
                  <motion.button
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 8 }}
                    onClick={scrollToBottom}
                    className="absolute bottom-4 right-4 px-3 py-2 rounded-full bg-bg-card border border-border-main shadow-lg
                      text-xs font-medium text-text-muted hover:text-text-main hover:border-brand-green/30 transition-all cursor-pointer
                      flex items-center gap-1.5 z-10"
                  >
                    <ArrowDown size={12} /> 回到最新
                  </motion.button>
                )}
              </AnimatePresence>

              {/* Export status */}
              {exporting && exportStatus && (
                <div className="px-4 py-2 border-t border-border-main bg-brand-green-light/10">
                  <div className="flex items-center gap-2 text-xs text-brand-green">
                    <div className="w-3 h-3 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
                    {exportStatus === 'starting' && '准备导出...'}
                    {exportStatus === 'downloading' && '下载媒体中...'}
                    {exportStatus === 'exporting' && '生成 HTML...'}
                    {exportStatus === 'completed' && '导出完成！'}
                    {exportStatus === 'error' && '导出失败'}
                  </div>
                </div>
              )}
              {exportStatus === 'completed' && !exporting && (
                <div className="px-4 py-2 border-t border-brand-green/20 bg-brand-green-light/10">
                  <p className="text-xs text-brand-green">导出完成，已打开文件夹</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Image Lightbox */}
      <AnimatePresence>
        {lightboxSrc && <ImageLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
      </AnimatePresence>

      {/* AI Chat Drawer */}
      <ChatDrawer
        open={aiChatOpen}
        onClose={closeAIDrawer}
        title={`AI 助手 — ${activeAiData?.session?.source_name || selectedSession?.displayName || ''}`}
      >
        {activeAiData?.session ? (
          <AIChatPanel
            sessionId={activeAiData.session.session_id}
            sourceName={activeAiData.session.source_name}
            contextSummary={activeAiData.session.context_summary}
            messages={activeAiData.messages}
            inputText={activeAiData.inputText}
            isStreaming={activeAiData.isStreaming}
            tokenUsage={activeAiData.tokenUsage}
            autoCompressed={activeAiData.autoCompressed}
            onMessagesChange={(m) => updateAiSession({ messages: m })}
            onInputTextChange={(t) => updateAiSession({ inputText: t })}
            onIsStreamingChange={(s) => updateAiSession({ isStreaming: s })}
            onTokenUsageChange={(u) => updateAiSession({ tokenUsage: u })}
            onAutoCompressedChange={(a) => updateAiSession({ autoCompressed: a })}
            onClose={closeAIDrawer}
            onNewChat={handleNewAIChat}
          />
        ) : (
          <AIChatConfig
            mode="chat"
            timePresets={AI_CHAT_TIME_PRESETS}
            selectedTimePreset={aiChatTimePreset}
            onTimePresetChange={setAiChatTimePreset}
            customStart={aiChatCustomStart}
            customEnd={aiChatCustomEnd}
            onCustomStartChange={setAiChatCustomStart}
            onCustomEndChange={setAiChatCustomEnd}
            onStart={startAIChat}
          />
        )}
      </ChatDrawer>
    </motion.div>
  )
}
