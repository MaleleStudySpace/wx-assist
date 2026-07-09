import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Star, DownloadSimple, MagnifyingGlass, Clock, File, Image, Video, Link, FileText, Funnel, ArrowsDownUp, FolderOpen, Play, Pause, Microphone, ChatsCircle, CaretDown, ChatCircleDots, Tag } from '@phosphor-icons/react'
import { Toggle, SectionHeader, API_BASE, getWsUrl } from './SharedComponents'
import ChatDrawer from './ChatDrawer'
import AIChatPanel from './AIChatPanel'
import AIChatConfig from './AIChatConfig'

// Type mapping
const FAV_TYPE_LABELS = {
  1: '文字',
  2: '图片',
  3: '语音',
  4: '视频',
  5: '链接',
  8: '文件',
  14: '聊天',
  16: '位置',
  17: '联系人',
  33: '文章',
}

/* ── Voice Player (play/pause + progress bar + duration) ───────────── */
function VoicePlayer({ audioId, src, initialDuration, showDownload, compact }) {
  const audioRef = useRef(null)
  const barRef = useRef(null)
  const rafRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(initialDuration || 0)
  const [currentTime, setCurrentTime] = useState(0)

  function startProgress() {
    const audio = audioRef.current
    if (!audio || !audio.duration) return
    function update() {
      if (audio.paused) return
      const pct = (audio.currentTime / audio.duration) * 100
      if (barRef.current) barRef.current.style.width = `${pct}%`
      setCurrentTime(audio.currentTime)
      rafRef.current = requestAnimationFrame(update)
    }
    rafRef.current = requestAnimationFrame(update)
  }

  function stopProgress(reset) {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    if (reset && barRef.current) barRef.current.style.width = '0%'
    if (reset) setCurrentTime(0)
  }

  function toggle(e) {
    e.stopPropagation()
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      audio.play().catch(() => {})
    } else {
      audio.pause()
    }
  }

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onPlay = () => { setPlaying(true); startProgress() }
    const onPause = () => { setPlaying(false); stopProgress(false) }
    const onEnded = () => { setPlaying(false); stopProgress(true) }
    const onMeta = () => { if (audio.duration && isFinite(audio.duration)) setDuration(audio.duration) }
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnded)
    audio.addEventListener('loadedmetadata', onMeta)
    return () => {
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnded)
      audio.removeEventListener('loadedmetadata', onMeta)
      stopProgress(true)
    }
  }, [])

  const durationSec = duration > 0 && isFinite(duration) ? Math.round(duration) : (initialDuration || 0)

  return (
    <div className={`inline-flex items-center gap-2 ${compact ? 'py-0.5' : ''}`}>
      <button
        onClick={toggle}
        className={`flex items-center justify-center rounded-full transition-colors flex-shrink-0
          ${compact ? 'w-6 h-6' : 'w-7 h-7'}
          ${playing ? 'bg-brand-green text-white' : 'bg-brand-green/15 text-brand-green'}`}
        title={playing ? '暂停' : '播放'}
      >
        {playing ? <Pause size={compact ? 10 : 12} weight="fill" /> : <Play size={compact ? 10 : 12} weight="fill" className="ml-0.5" />}
      </button>

      {/* Progress bar + time */}
      <div className={`flex items-center gap-1.5 ${compact ? '' : 'flex-1 min-w-0'}`}>
        <div className={`bg-bg-card rounded-full overflow-hidden flex-shrink-0 ${compact ? 'w-16 h-1' : 'flex-1 h-1.5 max-w-[120px]'}`}>
          <div ref={barRef} className="h-full bg-brand-green rounded-full transition-none" style={{ width: '0%' }} />
        </div>
        {durationSec > 0 && (
          <span className={`text-text-muted/60 font-mono ${compact ? 'text-[10px]' : 'text-[11px]'}`}>
            {durationSec}s
          </span>
        )}
      </div>

      {showDownload && (
        <a
          href={showDownload}
          download
          onClick={e => e.stopPropagation()}
          className="p-1.5 rounded-lg bg-bg-raised border border-border-main hover:border-brand-green/40 text-text-muted hover:text-text-main transition-colors flex-shrink-0"
          title="下载"
        >
          <DownloadSimple size={12} />
        </a>
      )}

      <audio ref={audioRef} id={audioId} src={src} preload="metadata" style={{ display: 'none' }} />
    </div>
  )
}

function FavTypeIcon({ type }) {
  const iconMap = {
    1: FileText,
    2: Image,
    3: Microphone,
    4: Video,
    5: Link,
    8: File,
    14: ChatsCircle,
    16: FileText,
    17: FileText,
    33: FileText,
  }
  const Icon = iconMap[type] || File
  return <Icon size={14} />
}

function NestedChatCard({ record, itemId }) {
  const [expanded, setExpanded] = useState(false)
  const [zoomImg, setZoomImg] = useState(null)
  const subRecords = record.sub_records || []
  const title = record.title || record.datatitle || '聊天记录'

  // Build preview: show first 2 messages as compact preview text
  const previewLines = subRecords.slice(0, 2).map((sub, i) => {
    const subType = parseInt(sub.type || '0')
    const name = sub.src_name || '未知'
    if (subType === 2) return `${name}: [图片]`
    if (subType === 3) return `${name}: [语音]`
    if (subType === 8) return `${name}: [文件]`
    if (subType === 17) return `${name}: [聊天记录]`
    return sub.desc ? `${name}: ${sub.desc}` : `${name}: ...`
  })
  const hasMore = subRecords.length > 2

  return (
    <div className="mt-1 rounded-lg border border-border-main/50 overflow-hidden max-w-[90%]">
      {/* Clickable header — always visible */}
      <div
        className="flex items-center gap-1.5 px-2.5 py-1.5 bg-bg-raised cursor-pointer hover:bg-bg-card transition-colors"
        onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
      >
        <ChatsCircle size={10} className="text-brand-green" weight="fill" />
        <span className="text-xs text-brand-green font-medium truncate flex-1">{title}</span>
        <span className="text-xs text-text-muted">{subRecords.length}条</span>
        <CaretDown size={10} className={`text-text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </div>

      {/* Collapsed: preview content (like WeChat favorites style) */}
      {!expanded && previewLines.length > 0 && (
        <div className="px-2.5 py-1.5 bg-bg-card/50 space-y-0.5">
          {previewLines.map((line, i) => (
            <p key={i} className="text-xs text-text-muted leading-relaxed truncate">{line}</p>
          ))}
          {hasMore && <p className="text-xs text-text-muted">...</p>}
        </div>
      )}

      {/* Expanded: full chat bubble style like main chat records */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-2 py-2 space-y-2 max-h-60 overflow-y-auto bg-bg-card">
              {subRecords.map((sub, si) => {
                const subType = parseInt(sub.type || '0')
                return (
                  <div key={si} className="flex items-start gap-2">
                    {/* Avatar */}
                    <div className="w-6 h-6 rounded-sm overflow-hidden shrink-0 bg-brand-green-light/30 flex items-center justify-center text-xs text-brand-green">
                      {sub.head_url ? (
                        <img src={sub.head_url} className="w-full h-full object-cover" onError={(e) => { e.target.style.display = 'none'; e.target.parentElement.textContent = (sub.src_name || '?')[0] }} />
                      ) : (sub.src_name || '?')[0]}
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-xs text-brand-green font-medium">{sub.src_name || '未知'}</span>
                      {sub.time && <span className="text-xs text-text-muted ml-1">{sub.time}</span>}

                      {/* Text */}
                      {sub.desc && subType === 1 && (
                        <div className="mt-0.5 px-2 py-1 rounded-md bg-bg-card text-xs text-text-main leading-relaxed break-words max-w-[90%]">
                          {sub.desc}
                        </div>
                      )}
                      {/* Image - actually load it, click to zoom */}
                      {subType === 2 && (
                        <div className="mt-0.5 max-w-[60%]">
                          {sub.fullmd5 ? (
                            <img
                              src={`${API_BASE}/api/fav/image?id=${itemId}&fullmd5=${sub.fullmd5}&fullsize=${sub.fullsize || ''}&size=original`}
                              alt="聊天图片"
                              className={`rounded-md object-contain border border-border-main/50 cursor-pointer hover:opacity-90 transition-all ${zoomImg === si ? 'max-h-80' : 'max-h-28'}`}
                              loading="lazy"
                              onClick={(e) => { e.stopPropagation(); setZoomImg(zoomImg === si ? null : si) }}
                              onError={(e) => {
                                e.target.onerror = null
                                e.target.style.display = 'none'
                                const p = e.target.parentElement
                                if (p && !p.querySelector('.img-fb')) {
                                  const fb = document.createElement('div')
                                  fb.className = 'img-fb flex items-center gap-1 px-2 py-1 rounded-md bg-bg-card text-xs text-text-muted'
                                  fb.innerHTML = '[图片]'
                                  p.appendChild(fb)
                                }
                              }}
                            />
                          ) : (
                            <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-bg-card text-xs text-text-muted">
                              <Image size={10} className="opacity-40" /> [图片]
                            </div>
                          )}
                        </div>
                      )}
                      {/* Voice */}
                      {subType === 3 && (
                        <div className="mt-0.5">
                          {sub.dataid ? (
                            <VoicePlayer
                              audioId={`nestedvoice-${itemId}-${si}`}
                              src={`${API_BASE}/api/fav/voice/record?fav_id=${itemId}&dataid=${sub.dataid}`}
                              initialDuration={sub.duration ? Math.round(sub.duration / 1000) : 0}
                              compact
                            />
                          ) : (
                            <span className="text-xs text-text-muted">[语音]</span>
                          )}
                        </div>
                      )}
                      {/* File */}
                      {subType === 8 && (
                        <div className="mt-0.5 flex items-center gap-1 px-2 py-1 rounded-md bg-bg-card text-xs text-text-muted max-w-[90%]">
                          <File size={10} className="opacity-40" />
                          {sub.file_name || '文件'}
                          {sub.file_type && <span className="text-text-muted">.{sub.file_type}</span>}
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
    </div>
  )
}

function FavCard({ item }) {
  const [expanded, setExpanded] = useState(false)
  const [viewImage, setViewImage] = useState(null)  // For standalone image items
  const [chatZoom, setChatZoom] = useState(null)    // For chat record images: record index or 'nested-${ri}-${si}'
  const typeLabel = FAV_TYPE_LABELS[item.type] || '未知'
  // Defensive: ensure create_time is a valid number before calling toLocaleString
  const createTime = item.create_time != null ? Number(item.create_time) : 0
  const timeStr = createTime > 0 ? new Date(createTime * 1000).toLocaleString('zh-CN') : ''

  // Check if it's a link type and has link data
  const isLink = item.type === 5
  const isVoice = item.type === 3
  const isImage = item.type === 2
  const isVideo = item.type === 4
  const isChatRecord = item.type === 14 && item.chat_records?.length > 0

  // Build image URL — use local cache decryption for protobuf URLs, CDN proxy for moments
  const buildImageUrl = (img, itemId, thumb = false) => {
    const sizeParam = thumb ? 'thumb' : 'original'
    // Case 1: img object provided with url
    if (img?.url) {
      // Protobuf-encoded CDN URLs (start with 305/306/307) — use local V2 cache decryption
      if (img.url.startsWith('305') || img.url.startsWith('306') || img.url.startsWith('307')) {
        return `${API_BASE}/api/fav/image?id=${itemId}&size=${sizeParam}`
      }
      // Explicit v2_cache marker (from our API fix)
      if (img.key === 'v2_cache') {
        return `${API_BASE}/api/fav/image?id=${itemId}&size=${sizeParam}`
      }
      const key = img.key || 0
      if (key && String(key) !== '0') {
        // Standard CDN URL needs ISAAC-64 decryption via proxy
        return `${API_BASE}/api/image/proxy?url=${encodeURIComponent(img.url)}&key=${key}`
      }
      // Direct URL (thumbUrl fallback)
      return img.url
    }
    // Case 2: No img provided but this is a type 2 image item — try local V2 cache
    return `${API_BASE}/api/fav/image?id=${itemId}&size=${sizeParam}`
  }

  // Parse voice duration from chat_records (API returns duration in ms)
  const voiceDuration = (() => {
    if (!isVoice) return 0
    if (item.chat_records && item.chat_records.length > 0) {
      const total = item.chat_records.reduce((sum, r) => sum + (Number(r.duration) || 0), 0)
      return Math.round(total / 1000)
    }
    return 0
  })()

  return (
    <div
      className="border border-border-main rounded-xl overflow-hidden bg-bg-card hover:border-text-muted/20 transition-colors"
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-center gap-3 p-4 cursor-pointer">
        {(isImage || isVideo) ? (
          <div className="w-8 h-8 rounded-lg overflow-hidden shrink-0 bg-bg-raised border border-border-main relative">
            <img
              src={buildImageUrl(item.images?.[0] || null, item.id, true)}
              alt=""
              className="w-full h-full object-cover"
              onError={(e) => { e.target.style.display = 'none'; e.target.parentElement.innerHTML = '<div class="w-full h-full flex items-center justify-center text-text-muted"><svg width="16" height="16" viewBox="0 0 256 256" fill="currentColor" opacity="0.5"><path d="M216 40H40a16 16 0 0 0-16 16v144a16 16 0 0 0 16 16h176a16 16 0 0 0 16-16V56a16 16 0 0 0-16-16Z"/></svg></div>' }}
            />
            {isVideo && (
              <div className="absolute bottom-0.5 right-0.5 w-3 h-3 rounded-full bg-black/60 flex items-center justify-center">
                <span className="text-[6px] text-white">▶</span>
              </div>
            )}
          </div>
        ) : (
          <div className="w-8 h-8 rounded-lg bg-bg-raised flex items-center justify-center text-text-muted">
            <FavTypeIcon type={item.type} />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full bg-brand-green-light/30 text-brand-green font-medium">
              {typeLabel}
            </span>
            {item.tags?.length > 0 && item.tags.map(t => (
              <span key={t.id} className="text-xs px-1.5 py-0.5 rounded-full bg-brand-green-light/20 text-brand-green/70 font-medium flex items-center gap-0.5">
                <Tag size={8} />
                {t.name}
              </span>
            ))}
            {item.title && (
              <span className="text-sm text-text-main font-medium truncate">
                {item.title}
              </span>
            )}
          </div>
          {isVoice ? (
            <div className="flex items-center gap-2 mt-1">
              <VoicePlayer
                audioId={`voice-${item.id}`}
                src={`${API_BASE}/api/fav/voice?id=${item.id}`}
                initialDuration={voiceDuration}
                showDownload={expanded ? `${API_BASE}/api/fav/voice/download?id=${item.id}&format=wav` : ''}
              />
            </div>
          ) : item.content && !isImage ? (
            <p className="text-xs text-text-muted truncate mt-0.5">
              {item.content.slice(0, 60)}...
            </p>
          ) : null}
        </div>
        <div className="text-xs text-text-muted font-mono">
          {timeStr}
        </div>
      </div>

      {/* Link preview for link type */}
      {expanded && isLink && item.link && (
        <div className="px-4 pb-2">
          <a
            href={item.link}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="flex items-center gap-2 p-2 rounded-lg bg-bg-raised border border-border-main hover:border-brand-green/30 transition-colors"
          >
            <Link size={14} className="text-brand-green" />
            <span className="text-xs text-brand-green truncate flex-1">{item.link}</span>
          </a>
        </div>
      )}

      {/* Image preview for image type */}
      {expanded && isImage && (
        <div className="px-4 pb-2" onClick={(e) => e.stopPropagation()}>
          <div className="grid gap-2" style={{ gridTemplateColumns: (item.images?.length || 1) === 1 ? '1fr' : 'repeat(3, 1fr)' }}>
            {(item.images?.length > 0 ? item.images : [{ url: null }]).map((img, idx) => (
              <div key={idx} className="rounded-lg overflow-hidden border border-border-main bg-bg-raised">
                <img
                  src={buildImageUrl(img, item.id)}
                  alt="收藏图片"
                  className="w-full object-cover cursor-pointer hover:opacity-90 transition-opacity"
                  style={{ maxHeight: viewImage === buildImageUrl(img, item.id) ? 'none' : ((item.images?.length || 1) === 1 ? '320px' : '180px'), objectFit: viewImage === buildImageUrl(img, item.id) ? 'contain' : ((item.images?.length || 1) === 1 ? 'contain' : 'cover') }}
                  onClick={() => setViewImage(viewImage === buildImageUrl(img, item.id) ? null : buildImageUrl(img, item.id))}
                  onError={(e) => {
                    e.target.onerror = null
                    e.target.style.display = 'none'
                    const parent = e.target.parentElement
                    if (parent && !parent.querySelector('.img-fallback')) {
                      const fallback = document.createElement('div')
                      fallback.className = 'img-fallback flex flex-col items-center justify-center p-6 text-text-muted'
                      fallback.innerHTML = '<span class="text-xs opacity-70">图片加载失败</span>'
                      parent.appendChild(fallback)
                    }
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Video preview for video type */}
      {expanded && isVideo && (
        <div className="px-4 pb-2" onClick={(e) => e.stopPropagation()}>
          <div className="rounded-lg overflow-hidden border border-border-main bg-bg-raised relative group">
            <video
              src={buildImageUrl(item.images?.[0] || null, item.id)}
              poster={item.images?.[0] ? buildImageUrl(item.images[0], item.id, true) : undefined}
              className="w-full max-h-80 object-contain"
              controls
              preload="metadata"
              onError={(e) => {
                e.target.style.display = 'none'
                const parent = e.target.parentElement
                if (parent && !parent.querySelector('.vid-fallback')) {
                  const fallback = document.createElement('div')
                  fallback.className = 'vid-fallback flex flex-col items-center justify-center p-8 text-text-muted'
                  fallback.innerHTML = '<span class="text-xs opacity-70">视频加载失败</span>'
                  parent.appendChild(fallback)
                }
              }}
            />
          </div>
        </div>
      )}

      {/* Chat record card for type 14 (笔记/聊天记录) */}
      {expanded && isChatRecord && (
        <div className="px-4 pb-4">
          <div className="rounded-xl border border-border-main bg-bg-raised overflow-hidden">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-border-main bg-bg-card">
              <ChatsCircle size={14} className="text-brand-green" weight="fill" />
              <span className="text-xs font-medium text-text-main">聊天记录</span>
              <span className="text-xs text-text-muted">{item.chat_records.length}条消息</span>
            </div>
            <div className="px-3 py-3 space-y-3 max-h-80 overflow-y-auto">
              {item.chat_records.map((record, idx) => {
                const recordType = parseInt(record.type || '0')
                return (
                  <div key={idx} className="flex items-start gap-2.5">
                    {/* Avatar */}
                    <div className="w-8 h-8 rounded-sm overflow-hidden shrink-0 bg-brand-green-light/30 flex items-center justify-center text-xs text-brand-green">
                      {record.head_url ? (
                        <img src={record.head_url} className="w-full h-full object-cover" onError={(e) => { e.target.style.display = 'none'; e.target.parentElement.textContent = (record.src_name || '?')[0] }} />
                      ) : (record.src_name || '?')[0]}
                    </div>
                    {/* Message bubble */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-xs text-brand-green font-medium">{record.src_name || '未知'}</span>
                        {record.time && (
                          <span className="text-xs text-text-muted">{record.time}</span>
                        )}
                      </div>
                      {/* Text message */}
                      {record.desc && recordType === 1 && (
                        <div className="mt-1 px-2.5 py-1.5 rounded-lg bg-bg-card text-xs text-text-main leading-relaxed break-words max-w-[85%] shadow-sm">
                          {record.desc}
                        </div>
                      )}
                      {/* Image message */}
                      {recordType === 2 && (
                        <div className="mt-1 max-w-[65%]">
                          {record.cdn_dataurl ? (
                            <img
                              src={`${API_BASE}/api/fav/image?id=${item.id}&fullmd5=${record.fullmd5 || ''}&fullsize=${record.fullsize || ''}&size=original`}
                              alt="聊天图片"
                              className={`rounded-lg object-contain cursor-pointer hover:opacity-90 transition-all border border-border-main/50 ${chatZoom === idx ? 'max-h-80' : 'max-h-48'}`}
                              onClick={(e) => { e.stopPropagation(); setChatZoom(chatZoom === idx ? null : idx) }}
                              onError={(e) => {
                                e.target.onerror = null
                                e.target.style.display = 'none'
                                const parent = e.target.parentElement
                                if (parent && !parent.querySelector('.img-fallback')) {
                                  const fb = document.createElement('div')
                                  fb.className = 'img-fallback flex items-center gap-1 px-2 py-1.5 rounded-lg bg-bg-card text-xs text-text-muted'
                                  fb.innerHTML = '[图片]'
                                  parent.appendChild(fb)
                                }
                              }}
                            />
                          ) : (
                            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-bg-card text-xs text-text-muted">
                              <Image size={12} className="opacity-50" />
                              [图片]
                            </div>
                          )}
                        </div>
                      )}
                      {/* Voice message */}
                      {recordType === 3 && (
                        <div className="mt-1">
                          {record.dataid ? (
                            <VoicePlayer
                              audioId={`chatvoice-${item.id}-${idx}`}
                              src={`${API_BASE}/api/fav/voice/record?fav_id=${item.id}&dataid=${record.dataid}`}
                              initialDuration={record.duration ? Math.round(record.duration / 1000) : 0}
                              compact
                            />
                          ) : (
                            <span className="text-xs text-text-muted">[语音]</span>
                          )}
                        </div>
                      )}
                      {/* File message */}
                      {recordType === 8 && (
                        <div className="mt-1 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-bg-card text-xs text-text-muted max-w-[85%]">
                          <File size={12} className="opacity-50" />
                          <span>{record.file_name || record.desc || '文件'}</span>
                          {record.file_type && <span className="text-text-muted">.{record.file_type}</span>}
                        </div>
                      )}
                      {/* Nested chat record (type 17) - clickable expandable chat card */}
                      {recordType === 17 && (record.desc || record.sub_records) && (
                        <NestedChatCard record={record} itemId={item.id} />
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      <AnimatePresence>
        {expanded && item.content && !isLink && !isImage && !isVideo && !isVoice && !isChatRecord && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="px-4 pb-4 overflow-hidden"
          >
            <div className="pt-3 border-t border-border-main">
              <p className="text-xs text-text-muted whitespace-pre-wrap leading-relaxed">
                {item.content}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Hidden audio element for voice playback */}
      {isVoice && expanded && (
        <audio
          id={`voice-${item.id}`}
          src={`${API_BASE}/api/fav/voice?id=${item.id}`}
          preload="metadata"
          style={{ display: 'none' }}
        />
      )}
    </div>
  )
}

export default function FavoritesTab() {
  const [favorites, setFavorites] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [sortBy, setSortBy] = useState('time_desc')
  const [dateRange, setDateRange] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportStatus, setExportStatus] = useState('')
  const [favOffset, setFavOffset] = useState(0)          // Step 7: pagination
  const [hasMoreFav, setHasMoreFav] = useState(true)    // Step 7: pagination
  const [loadingMoreFav, setLoadingMoreFav] = useState(false) // Step 7: pagination
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [aiChatSession, setAiChatSession] = useState(null)
  // Hoisted AI chat state (owned by parent so it survives Drawer close)
  const [aiChatMessages, setAiChatMessages] = useState([])
  const [aiChatInputText, setAiChatInputText] = useState('')
  const [aiChatIsStreaming, setAiChatIsStreaming] = useState(false)
  const [aiChatTokenUsage, setAiChatTokenUsage] = useState({ used: 0, budget: 100000 })
  const [aiChatAutoCompressed, setAiChatAutoCompressed] = useState(false)
  const [aiChatTagId, setAiChatTagId] = useState('')     // selected tag id for AI context
  const [aiChatFavTypes, setAiChatFavTypes] = useState([1, 14, 5, 33])  // default: text, chat, link, article
  const [tags, setTags] = useState([])            // [{id, name, fav_count}]
  const [tagFilter, setTagFilter] = useState('')   // 选中的 tag id
  const [typeDropdownOpen, setTypeDropdownOpen] = useState(false)
  const [dateDropdownOpen, setDateDropdownOpen] = useState(false)
  const typeDropdownRef = useRef(null)
  const dateDropdownRef = useRef(null)

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClick(e) {
      if (typeDropdownRef.current && !typeDropdownRef.current.contains(e.target)) setTypeDropdownOpen(false)
      if (dateDropdownRef.current && !dateDropdownRef.current.contains(e.target)) setDateDropdownOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // Subscribe to WebSocket events (addEventListener to avoid listener accumulation)
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'fav_export_progress') {
          setExportStatus(data.status)
          if (data.status === 'completed' || data.status === 'error') {
            setExporting(false)
          }
        }
      } catch {}
    }

    let ws = window.__fav_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__fav_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  useEffect(() => {
    loadFavorites()
  }, [])

  // Restore AI chat session from backend on tab remount (safety net)
  useEffect(() => {
    if (aiChatSession?.session_id && aiChatMessages.length === 0) {
      fetch(`${API_BASE}/api/ai/chat/history?session_id=${aiChatSession.session_id}`)
        .then(r => r.json())
        .then(data => {
          if (data.ok && data.history?.length > 0) {
            setAiChatMessages(data.history.map(m => ({ role: m.role, content: m.content })))
            if (data.token_usage) setAiChatTokenUsage(data.token_usage)
          }
        })
        .catch(() => {})
    }
  }, [])

  async function loadFavorites(append = false) {
    if (append) {
      setLoadingMoreFav(true)
    } else {
      setLoading(true)
    }
    setError('')
    try {
      const currentOffset = append ? favOffset : 0
      const limit = 50
      const [favRes, tagsRes] = await Promise.all([
        fetch(`${API_BASE}/api/fav/list?limit=${limit}&offset=${currentOffset}`),
        append ? Promise.resolve(null) : fetch(`${API_BASE}/api/fav/tags`),
      ])
      const data = await favRes.json()
      if (data.ok) {
        const items = data.data || []
        if (append) {
          setFavorites(prev => [...prev, ...items])
        } else {
          setFavorites(items)
        }
        setFavOffset(currentOffset + items.length)
        setHasMoreFav(items.length >= limit)
      } else {
        setError(data.error || '加载失败')
      }
      // Load tags on first load
      if (tagsRes) {
        const tagsData = await tagsRes.json()
        if (tagsData.ok) {
          setTags(tagsData.data || [])
        }
      }
    } catch (e) {
      setError('无法连接到服务器')
    } finally {
      setLoading(false)
      setLoadingMoreFav(false)
    }
  }

  async function handleExport() {
    setExporting(true)
    setExportStatus('starting')
    try {
      // Build query params from current filters
      const queryParams = new URLSearchParams({ format: 'json,image,html' })
      if (typeFilter) queryParams.set('type_filter', typeFilter)
      if (tagFilter) queryParams.set('tag_id', tagFilter)
      if (search) queryParams.set('search', search)
      if (dateRange) queryParams.set('date_range', dateRange)
      const qs = queryParams.toString()

      // Step 1: dry_run to estimate size
      const dryRes = await fetch(`${API_BASE}/api/fav/export?${qs}&dry_run=true`, {
        method: 'POST',
      })
      const dryData = await dryRes.json()
      if (!dryData.ok) {
        setExportStatus('error')
        setError(dryData.error || '预估失败')
        setExporting(false)
        return
      }

      // Step 2: if size_warning, ask user to confirm
      const itemCount = dryData.filtered_count ?? dryData.item_count ?? 0
      if (dryData.size_warning) {
        const confirmed = window.confirm(
          `即将导出 ${itemCount} 条收藏，约 ${dryData.image_count} 张图片（预估 ~${dryData.estimated_mb} MB），确认继续？`
        )
        if (!confirmed) {
          setExporting(false)
          setExportStatus('')
          return
        }
      }

      // Step 3: real export
      const res = await fetch(`${API_BASE}/api/fav/export?${qs}`, {
        method: 'POST',
      })
      const data = await res.json()
      if (data.ok) {
        setExportStatus('completed')
        // Auto open folder
        openExportFolder()
        setTimeout(() => setExportStatus(''), 3000)
      } else {
        setExportStatus('error')
        setError(data.error)
      }
    } catch (e) {
      setExportStatus('error')
      setError('导出失败')
    } finally {
      setExporting(false)
    }
  }

  async function openExportFolder() {
    try {
      await fetch(`${API_BASE}/api/export/open-folder?type=fav`, { method: 'POST' })
    } catch (e) {
      console.warn('Open folder failed:', e)
    }
  }

  async function startAIChat() {
    try {
      const res = await fetch(`${API_BASE}/api/ai/chat/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_type: 'favorites',
          tag_id: aiChatTagId || undefined,
          fav_types: aiChatFavTypes.length > 0 ? aiChatFavTypes : undefined,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        setAiChatSession(data)
        setAiChatMessages([])
        setAiChatInputText('')
        setAiChatIsStreaming(false)
        setAiChatTokenUsage(data.token_usage || { used: 0, budget: 100000 })
        setAiChatAutoCompressed(false)
      } else {
        alert(data.error || '启动 AI 对话失败')
      }
    } catch (e) {
      alert('无法连接服务器')
    }
  }

  // Close Drawer — only hide, do NOT destroy session
  function closeAIDrawer() {
    setAiChatOpen(false)
  }

  // "开启新对话" — destroy session + reset state → back to config
  function handleNewAIChat() {
    if (aiChatSession?.session_id) {
      fetch(`${API_BASE}/api/ai/chat/destroy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: aiChatSession.session_id }),
      }).catch(() => {})
    }
    setAiChatSession(null)
    setAiChatMessages([])
    setAiChatInputText('')
    setAiChatIsStreaming(false)
    setAiChatTokenUsage({ used: 0, budget: 100000 })
    setAiChatAutoCompressed(false)
  }

  // Open Drawer — directly, no inline config
  function openAIDrawer() {
    setAiChatOpen(true)
  }

  // Date range filter options
  const dateRangeOptions = [
    { value: '', label: '全部时间' },
    { value: 'today', label: '今天' },
    { value: 'week', label: '最近7天' },
    { value: 'month', label: '最近30天' },
    { value: 'older', label: '更早' },
  ]

  const filteredFavorites = useMemo(() => {
    let result = [...favorites]

    // Search filter
    if (search) {
      const kw = search.toLowerCase()
      result = result.filter(item =>
        (item.title || '').toLowerCase().includes(kw) ||
        (item.content || '').toLowerCase().includes(kw) ||
        (item.from_user || '').toLowerCase().includes(kw)
      )
    }

    // Type filter
    if (typeFilter) {
      result = result.filter(item => item.type === parseInt(typeFilter))
    }

    // Tag filter
    if (tagFilter) {
      result = result.filter(item =>
        item.tags?.some(t => t.id === tagFilter)
      )
    }

    // Date range filter
    if (dateRange) {
      const now = Date.now()
      const todayStart = new Date().setHours(0, 0, 0, 0) / 1000
      const weekStart = (now - 7 * 24 * 60 * 60 * 1000) / 1000
      const monthStart = (now - 30 * 24 * 60 * 60 * 1000) / 1000

      result = result.filter(item => {
        const ts = item.create_time || 0
        if (dateRange === 'today') return ts >= todayStart
        if (dateRange === 'week') return ts >= weekStart
        if (dateRange === 'month') return ts >= monthStart
        if (dateRange === 'older') return ts < monthStart
        return true
      })
    }

    // Sort
    result.sort((a, b) => {
      const ta = a.create_time || 0
      const tb = b.create_time || 0
      if (sortBy === 'time_desc') return tb - ta
      if (sortBy === 'time_asc') return ta - tb
      if (sortBy === 'type') return (a.type || 0) - (b.type || 0)
      return 0
    })

    return result
  }, [favorites, search, typeFilter, tagFilter, dateRange, sortBy])

  const typeOptions = [
    { value: '', label: '全部类型' },
    { value: '1', label: '文字' },
    { value: '2', label: '图片' },
    { value: '3', label: '语音' },
    { value: '4', label: '视频' },
    { value: '5', label: '链接' },
    { value: '8', label: '文件' },
    { value: '14', label: '聊天' },
    { value: '33', label: '文章' },
  ]

  return (
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }}>
      <SectionHeader
        title="收藏管理"
        icon={Star}
        subtitle="浏览和导出微信收藏内容"
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={openAIDrawer}
              className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium bg-brand-green/10 text-brand-green border border-brand-green/20 hover:bg-brand-green/20 transition-all cursor-pointer"
              title="AI 对话"
            >
              <ChatCircleDots size={14} />
              AI 对话
              {aiChatIsStreaming && !aiChatOpen && (
                <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-pulse" />
              )}
            </button>
            <button
              onClick={openExportFolder}
              className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium bg-bg-raised border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-all cursor-pointer"
              title="打开导出文件夹"
            >
              <FolderOpen size={14} />
              文件夹
            </button>
            <button
              onClick={handleExport}
              disabled={exporting}
              className={`flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold transition-all cursor-pointer
                ${exporting
                  ? 'bg-bg-raised text-text-muted cursor-wait'
                  : 'bg-brand-green-hover text-white hover:bg-brand-green-hover'
                }`}
            >
              <DownloadSimple size={14} />
              {exporting ? '导出中...' : (typeFilter || tagFilter || search) ? '导出筛选结果' : '导出收藏'}
            </button>
          </div>
        }
      />

      {exportStatus && exportStatus !== 'starting' && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className={`mb-4 p-3 rounded-xl border text-xs font-medium flex items-center gap-2
            ${exportStatus === 'completed'
              ? 'bg-brand-green-light/20 border-brand-green/30 text-brand-green'
              : exportStatus === 'error'
                ? 'bg-status-error-soft border-status-error/30 text-status-error'
                : 'bg-bg-raised border-border-main text-text-muted'
            }`}
        >
          {exportStatus === 'completed' && <><Star weight="fill" /> 导出完成</>}
          {exportStatus === 'error' && <><Star /> 导出失败</>}
          {exportStatus === 'started' && <><Clock /> 开始导出...</>}
          {exportStatus === 'exporting' && <><Clock /> 正在导出...</>}
        </motion.div>
      )}

      {/* Search and Filter */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[180px]">
          <MagnifyingGlass size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索收藏内容..."
            className="w-full bg-bg-raised border border-border-main rounded-full pl-10 pr-4 py-2.5 text-sm text-text-main
              placeholder:text-text-muted font-mono
              focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
              transition-all"
          />
        </div>

        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-2 px-4 py-2.5 rounded-full text-xs font-medium border transition-all
            ${showFilters ? 'bg-brand-green-light border-brand-green/30 text-brand-green' : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}
          `}
        >
          <Funnel size={14} />
          筛选
        </button>
      </div>

      {/* Tag filter chips */}
      {tags.length > 0 && (
        <div className="flex gap-2 mb-4 flex-wrap items-center">
          <Tag size={14} className="text-text-muted shrink-0" />
          <button
            onClick={() => setTagFilter('')}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
              ${!tagFilter ? 'bg-brand-green-hover text-white border-brand-green' : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
          >
            全部
          </button>
          {tags.map(tag => (
            <button
              key={tag.id}
              onClick={() => setTagFilter(tag.id === tagFilter ? '' : tag.id)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                ${tagFilter === tag.id ? 'bg-brand-green-hover text-white border-brand-green' : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
            >
              {tag.name} ({tag.fav_count})
            </button>
          ))}
        </div>
      )}

      {/* Expanded filters */}
      <AnimatePresence>
        {showFilters && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mb-4"
          >
            <div className="flex gap-2 flex-wrap items-center p-3 rounded-xl bg-bg-raised border border-border-main">
              {/* Type dropdown */}
              <div className="relative" ref={typeDropdownRef}>
                <button onClick={() => { setTypeDropdownOpen(!typeDropdownOpen); setDateDropdownOpen(false) }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs border border-border-main bg-bg-card text-text-main hover:border-text-muted/30 transition-all cursor-pointer">
                  <span>{typeOptions.find(o => o.value === typeFilter)?.label || '全部类型'}</span>
                  <CaretDown size={10} className={`text-text-muted transition-transform ${typeDropdownOpen ? 'rotate-180' : ''}`} />
                </button>
                <AnimatePresence>
                  {typeDropdownOpen && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }}
                      className="absolute z-50 mt-1 left-0 w-36 bg-bg-card border border-border-main rounded-xl shadow-lg overflow-hidden"
                    >
                      {typeOptions.map(opt => (
                        <button key={opt.value} onClick={() => { setTypeFilter(opt.value); setTypeDropdownOpen(false) }}
                          className={`w-full text-left px-3 py-2 text-xs transition-colors cursor-pointer
                            ${typeFilter === opt.value ? 'bg-brand-green-light/15 text-brand-green font-medium' : 'text-text-main hover:bg-bg-raised/60'}`}>
                          {opt.label}
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Date range dropdown */}
              <div className="relative" ref={dateDropdownRef}>
                <button onClick={() => { setDateDropdownOpen(!dateDropdownOpen); setTypeDropdownOpen(false) }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs border border-border-main bg-bg-card text-text-main hover:border-text-muted/30 transition-all cursor-pointer">
                  <span>{dateRangeOptions.find(o => o.value === dateRange)?.label || '全部时间'}</span>
                  <CaretDown size={10} className={`text-text-muted transition-transform ${dateDropdownOpen ? 'rotate-180' : ''}`} />
                </button>
                <AnimatePresence>
                  {dateDropdownOpen && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }}
                      className="absolute z-50 mt-1 left-0 w-36 bg-bg-card border border-border-main rounded-xl shadow-lg overflow-hidden"
                    >
                      {dateRangeOptions.map(opt => (
                        <button key={opt.value} onClick={() => { setDateRange(opt.value); setDateDropdownOpen(false) }}
                          className={`w-full text-left px-3 py-2 text-xs transition-colors cursor-pointer
                            ${dateRange === opt.value ? 'bg-brand-green-light/15 text-brand-green font-medium' : 'text-text-main hover:bg-bg-raised/60'}`}>
                          {opt.label}
                        </button>
                      ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Sort toggle — simple two-way switch */}
              <button onClick={() => setSortBy(sortBy === 'time_desc' ? 'time_asc' : sortBy === 'time_asc' ? 'type' : 'time_desc')}
                className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs border border-border-main bg-bg-card text-text-main hover:border-text-muted/30 transition-all cursor-pointer">
                <ArrowsDownUp size={12} className="text-text-muted" />
                <span>{sortBy === 'time_desc' ? '最新' : sortBy === 'time_asc' ? '最旧' : '按类型'}</span>
              </button>

              {(typeFilter || dateRange || sortBy !== 'time_desc' || tagFilter) && (
                <button
                  onClick={() => { setTypeFilter(''); setDateRange(''); setSortBy('time_desc'); setTagFilter('') }}
                  className="px-3 py-2 text-xs text-status-error hover:bg-status-error-soft rounded-full transition-colors cursor-pointer"
                >
                  清除筛选
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Results count */}
      <div className="text-xs text-text-muted mb-4 font-mono">
        共 {filteredFavorites.length} 条收藏
      </div>

      {/* Favorites list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-6 h-6 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
        </div>
      ) : error ? (
        <div className="text-center py-12 text-status-error text-sm">
          {error}
        </div>
      ) : filteredFavorites.length === 0 ? (
        <div className="text-center py-12 text-text-muted text-sm">
          {search || typeFilter ? '没有匹配的收藏' : '暂无收藏内容'}
        </div>
      ) : (
        <div className="space-y-3">
          {filteredFavorites.map(item => (
            <FavCard key={item.id} item={item} />
          ))}
          {/* Step 7: Load more button */}
          {hasMoreFav && (
            <div className="flex justify-center py-4">
              <button
                onClick={() => loadFavorites(true)}
                disabled={loadingMoreFav}
                className="px-6 py-2.5 rounded-full text-xs font-medium bg-bg-raised border border-border-main
                  text-text-muted hover:text-text-main hover:border-text-muted/30 transition-all cursor-pointer
                  disabled:cursor-wait"
              >
                {loadingMoreFav ? (
                  <span className="flex items-center gap-2">
                    <div className="w-3 h-3 border border-text-muted/30 border-t-text-muted rounded-full animate-spin" />
                    加载中...
                  </span>
                ) : '加载更多'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* AI Chat Drawer */}
      <ChatDrawer open={aiChatOpen} onClose={closeAIDrawer} title="AI 收藏助手">
        {aiChatSession ? (
          <AIChatPanel
            sessionId={aiChatSession.session_id}
            sourceName={aiChatSession.source_name}
            contextSummary={aiChatSession.context_summary}
            messages={aiChatMessages}
            inputText={aiChatInputText}
            isStreaming={aiChatIsStreaming}
            tokenUsage={aiChatTokenUsage}
            autoCompressed={aiChatAutoCompressed}
            onMessagesChange={setAiChatMessages}
            onInputTextChange={setAiChatInputText}
            onIsStreamingChange={setAiChatIsStreaming}
            onTokenUsageChange={setAiChatTokenUsage}
            onAutoCompressedChange={setAiChatAutoCompressed}
            onClose={closeAIDrawer}
            onNewChat={handleNewAIChat}
          />
        ) : (
          <AIChatConfig
            mode="favorites"
            tags={tags}
            selectedTagId={aiChatTagId}
            onTagIdChange={setAiChatTagId}
            selectedFavTypes={aiChatFavTypes}
            onFavTypesChange={setAiChatFavTypes}
            onStart={startAIChat}
          />
        )}
      </ChatDrawer>
    </motion.div>
  )
}