import { useState, useEffect, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Eye, DownloadSimple, MagnifyingGlass, Clock, ShieldCheck, ShieldWarning, Heart, ChatCircle, CaretDown, Funnel, X, MapPin, FolderOpen, Sparkle } from '@phosphor-icons/react'
import { ImageLightbox, API_BASE, getWsUrl } from './SharedComponents'
import SnsAiDrawer from './SnsAiDrawer'

function SnsPostCard({ post, avatarCache }) {
  const [expanded, setExpanded] = useState(false)
  const [lightboxSrc, setLightboxSrc] = useState(null)
  // Defensive: ensure create_time is valid before calling toLocaleString
  const createTime = post.create_time != null ? Number(post.create_time) : 0
  const timeStr = createTime > 0 ? new Date(createTime * 1000).toLocaleString('zh-CN') : ''
  // Step 12: Use avatar cache to avoid duplicate requests
  const cachedHeadUrl = avatarCache?.get(post.username)
  const effectiveHeadUrl = cachedHeadUrl || post.user_head_url
  if (post.user_head_url && avatarCache && !cachedHeadUrl) {
    avatarCache.set(post.username, post.user_head_url)
  }
  const headUrl = effectiveHeadUrl
  const contentPreview = (post.content || '').slice(0, 100)
  const hasMedia = post.media_list && post.media_list.length > 0
  const hasLocation = !!post.location

  return (
    <div className="border border-border-main rounded-xl overflow-hidden bg-bg-card hover:border-text-muted/20 transition-colors">
      <div
        className="p-4 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-full overflow-hidden shrink-0 bg-brand-green-light/30 flex items-center justify-center">
            {headUrl ? (
              <img
                src={headUrl}
                alt=""
                className="w-full h-full object-cover"
                onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex' }}
              />
            ) : null}
            <span className={`text-brand-green text-sm font-bold ${headUrl ? 'hidden' : 'flex'}`}>
              {(post.nickname || post.username || '?')[0]}
            </span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-text-main">
                {post.nickname || post.username || '未知用户'}
              </span>
              <span className="text-xs text-text-muted font-mono">{timeStr}</span>
            </div>
            <p className="text-xs text-text-muted mt-1 leading-relaxed whitespace-pre-wrap">
              {expanded ? post.content : contentPreview}
              {!expanded && (post.content || '').length > 100 && '...'}
            </p>
            {/* Thumbnail preview when collapsed */}
            {!expanded && hasMedia && (
              <div className="flex gap-1 mt-2 overflow-hidden">
                {post.media_list.slice(0, 3).map((media, i) => {
                  const isVideo = media.type === 'video' || (media.url && (
                    media.url.toLowerCase().includes('snsvideodownload') ||
                    (media.url.toLowerCase().includes('video') && !media.url.toLowerCase().includes('vweixinthumb'))
                  ))
                  return (
                    <div key={i} className="w-12 h-12 rounded overflow-hidden shrink-0 bg-bg-raised border border-border-main relative">
                      {isVideo && media.thumb_url ? (
                        <div className="w-full h-full relative">
                          <img
                            src={media.key && media.key !== '0'
                              ? `${API_BASE}/api/image/proxy?url=${encodeURIComponent(media.thumb_url)}&key=${media.key}&token=${media.token || ''}`
                              : media.thumb_url}
                            alt=""
                            className="w-full h-full object-cover"
                            loading="lazy"
                            onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex' }}
                          />
                          <div className="absolute inset-0 flex items-center justify-center bg-black/30" style={{ display: 'none' }}>
                            <span className="text-[14px]">🎬</span>
                          </div>
                          <div className="absolute bottom-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-black/60 flex items-center justify-center">
                            <span className="text-[7px] text-white">▶</span>
                          </div>
                        </div>
                      ) : isVideo ? (
                        <div className="w-full h-full flex items-center justify-center bg-black/20">
                          <span className="text-[14px]">🎬</span>
                        </div>
                      ) : media.thumb_url ? (
                        <img
                          src={media.key && media.key !== '0'
                            ? `${API_BASE}/api/image/proxy?url=${encodeURIComponent(media.url || media.thumb_url)}&key=${media.key}&token=${media.token || ''}`
                            : media.thumb_url}
                          alt=""
                          className="w-full h-full object-cover"
                          loading="lazy"
                          onError={(e) => { e.target.style.display = 'none' }}
                        />
                      ) : null}
                    </div>
                  )
                })}
                {post.media_list.length > 3 && (
                  <div className="w-12 h-12 rounded flex items-center justify-center bg-bg-raised border border-border-main text-xs text-text-muted">
                    +{post.media_list.length - 3}
                  </div>
                )}
              </div>
            )}
            <div className="flex items-center gap-3 mt-2">
              {post.like_count > 0 && (
                <span className="text-xs text-text-muted flex items-center gap-1">
                  <Heart size={10} weight="fill" className="text-red-400" /> {post.like_count}
                </span>
              )}
              {post.comment_count > 0 && (
                <span className="text-xs text-text-muted flex items-center gap-1">
                  <ChatCircle size={10} /> {post.comment_count}
                </span>
              )}
              {hasMedia && (
                <span className="text-xs text-text-muted flex items-center gap-1">
                  <Eye size={10} /> {post.media_list.length}张
                </span>
              )}
              {hasLocation && (
                <span className="text-xs text-text-muted flex items-center gap-1">
                  <MapPin size={10} /> {post.location}
                </span>
              )}
            </div>
          </div>
          <CaretDown
            size={14}
            className={`text-text-muted transition-transform shrink-0 ${expanded ? 'rotate-180' : ''}`}
          />
        </div>
      </div>

      {/* Media grid */}
      <AnimatePresence>
        {expanded && hasMedia && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="px-4 pb-4 overflow-hidden"
          >
            <div className="pt-3 border-t border-border-main">
              <div className={`grid gap-2 ${post.media_list.length === 1 ? 'grid-cols-1' : 'grid-cols-3'}`}>
                {post.media_list.slice(0, 9).map((media, i) => {
                  const isVideo = media.type === 'video' || (media.url && (
                    media.url.toLowerCase().includes('snsvideodownload') ||
                    media.url.toLowerCase().includes('.mp4') ||
                    (media.url.toLowerCase().includes('video') && !media.url.toLowerCase().includes('vweixinthumb'))
                  ))
                  const mediaUrl = media.key && media.key !== '0'
                    ? `${API_BASE}/api/image/proxy?url=${encodeURIComponent(media.url || media.thumb_url)}&key=${media.key || ''}&token=${media.token || ''}`
                    : media.url || media.thumb_url
                  const thumbUrl = media.thumb_url
                  const isSingleMedia = post.media_list.length === 1
                  return (
                    <div
                      key={i}
                      className={`rounded-lg bg-bg-raised border border-border-main overflow-hidden ${isVideo ? '' : 'cursor-pointer'}`}
                      style={isSingleMedia ? { maxHeight: '320px' } : {}}
                      onClick={(e) => { if (!isVideo) { e.stopPropagation(); setLightboxSrc(mediaUrl) } }}
                    >
                      {isVideo ? (
                        <div className="relative group">
                          <video
                            src={mediaUrl}
                            className="w-full max-h-80 object-contain bg-black/10"
                            controls
                            preload="metadata"
                            poster={thumbUrl ? `${API_BASE}/api/image/proxy?url=${encodeURIComponent(thumbUrl)}&key=${media.key || ''}&token=${media.token || ''}` : undefined}
                            onClick={(e) => e.stopPropagation()}
                          />
                          <a
                            href={`${API_BASE}/api/sns/video/download?post_id=${post.id}&idx=${i}`}
                            download
                            onClick={(e) => e.stopPropagation()}
                            className="absolute top-2 right-2 p-1 rounded bg-black/50 hover:bg-black/70 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                            title="下载视频"
                          >
                            <DownloadSimple size={14} />
                          </a>
                        </div>
                      ) : thumbUrl ? (
                        <img
                          src={mediaUrl}
                          alt=""
                          className={`w-full ${isSingleMedia ? 'max-h-80 object-contain' : 'aspect-square object-cover'}`}
                          loading="lazy"
                          onError={(e) => {
                            e.target.style.display = 'none'
                            e.target.nextSibling && (e.target.nextSibling.style.display = 'flex')
                          }}
                        />
                      ) : null}
                      {!isVideo && (
                        <div
                          className="w-full h-full items-center justify-center text-text-muted text-xs"
                          style={{ display: thumbUrl ? 'none' : 'flex' }}
                        >
                          媒体
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Comments and likes when expanded */}
      {expanded && (post.comments?.length > 0 || post.likes?.length > 0) && (
        <div className="px-4 pb-4">
          <div className="pt-3 border-t border-border-main">
            <div className="rounded-lg bg-bg-raised/60 p-2.5 space-y-1.5 text-xs">
              {/* Likes */}
              {post.likes?.length > 0 && (
                <div className="flex items-start gap-1.5">
                  <Heart size={12} className="text-red-400 shrink-0 mt-0.5" weight="fill" />
                  <span className="text-brand-green font-medium leading-relaxed">
                    {post.likes.map(l => typeof l === 'string' ? l : l.nickname || '').filter(Boolean).join('、')}
                  </span>
                </div>
              )}
              {/* Comments */}
              {post.comments?.map((c, ci) => (
                <div key={ci} className="leading-relaxed">
                  <span className="text-brand-green font-medium">{c.nickname || '未知'}</span>
                  {c.refNickname ? (
                    <>
                      <span className="text-text-muted mx-0.5">回复</span>
                      <span className="text-brand-green font-medium">{c.refNickname}</span>
                    </>
                  ) : null}
                  <span className="text-text-muted">：{c.content || ''}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Location detail when expanded */}
      {expanded && hasLocation && !hasMedia && (
        <div className="px-4 pb-4">
          <div className="pt-3 border-t border-border-main flex items-center gap-1 text-xs text-text-muted">
            <MapPin size={10} /> {post.location}
          </div>
        </div>
      )}

      {/* Image Lightbox */}
      <AnimatePresence>
        {lightboxSrc && <ImageLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
      </AnimatePresence>
    </div>
  )
}

export default function MomentsTab() {
  const [posts, setPosts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [searchMode, setSearchMode] = useState(false)
  const [usernameFilter, setUsernameFilter] = useState('')
  const [usernames, setUsernames] = useState([])
  const [nicknameMap, setNicknameMap] = useState({})  // username -> nickname
  const [contactSearch, setContactSearch] = useState('')
  const [contactDropdownOpen, setContactDropdownOpen] = useState(false)
  const contactDropdownRef = useRef(null)

  // Close dropdowns on outside click
  const timeDropdownRef = useRef(null)
  useEffect(() => {
    function handleClick(e) {
      if (contactDropdownRef.current && !contactDropdownRef.current.contains(e.target)) setContactDropdownOpen(false)
      if (timeDropdownRef.current && !timeDropdownRef.current.contains(e.target)) setTimeDropdownOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])
  const [protectionStatus, setProtectionStatus] = useState(null)
  const [restrictedEnabled, setRestrictedEnabled] = useState(false) // Whether restricted features are enabled
  const [exporting, setExporting] = useState(false)
  const [exportStatus, setExportStatus] = useState('')
  const [togglingProtection, setTogglingProtection] = useState(false)
  const [showProtection, setShowProtection] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const avatarCacheRef = useRef(new Map())  // Step 12: avatar cache
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false)
  const [timePreset, setTimePreset] = useState(0)  // 0=全部, 1=今天, 2=7天, 3=30天, -1=自定义
  const [customStart, setCustomStart] = useState('')
  const [customEnd, setCustomEnd] = useState('')
  const [timeDropdownOpen, setTimeDropdownOpen] = useState(false)

  useEffect(() => {
    loadTimeline()
    loadProtectionStatus()
  }, [])

  // Extract usernames and nicknames from loaded posts
  useEffect(() => {
    if (posts.length > 0) {
      const unique = [...new Set(posts.map(p => p.username).filter(Boolean))]
      setUsernames(prev => {
        const merged = [...new Set([...prev, ...unique])]
        return merged
      })
      // Build nickname map
      setNicknameMap(prev => {
        const updated = { ...prev }
        for (const p of posts) {
          if (p.username && p.nickname && p.nickname !== p.username) {
            updated[p.username] = p.nickname
          }
        }
        return updated
      })
    }
  }, [posts])

  // WebSocket events (addEventListener to avoid listener accumulation)
  useEffect(() => {
    const handleMessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'sns_export_progress') {
          setExportStatus(data.status)
          if (data.status === 'completed' || data.status === 'error') {
            setExporting(false)
          }
        }
      } catch {}
    }
    let ws = window.__moments_ws
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      ws = new WebSocket(getWsUrl())
      window.__moments_ws = ws
    }
    ws.addEventListener('message', handleMessage)
    return () => { ws.removeEventListener('message', handleMessage) }
  }, [])

  async function loadTimeline() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/api/sns/timeline?limit=30`)
      const data = await res.json()
      if (data.ok) {
        setPosts(data.data || [])
        setOffset(data.data?.length || 0)
        setHasMore((data.data || []).length >= 30)
      } else {
        setError(data.error || '加载失败')
      }
    } catch {
      setError('无法连接到服务器')
    } finally {
      setLoading(false)
    }
  }

  async function loadMore() {
    if (loadingMore) return
    setLoadingMore(true)
    try {
      const res = await fetch(`${API_BASE}/api/sns/timeline?limit=30&offset=${offset}`)
      const data = await res.json()
      if (data.ok && data.data?.length > 0) {
        setPosts(prev => [...prev, ...(data.data || [])])
        setOffset(prev => prev + (data.data?.length || 0))
        setHasMore(data.data.length >= 30)
      } else {
        setHasMore(false)
      }
    } catch {
      setHasMore(false)
    } finally {
      setLoadingMore(false)
    }
  }

  async function loadProtectionStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/sns/protect/status`)
      const data = await res.json()
      if (data.disabled) { setRestrictedEnabled(false); return }
      setRestrictedEnabled(true)
      setProtectionStatus(data)
    } catch {}
  }

  async function handleSearch() {
    if (!search.trim()) {
      setSearchMode(false)
      loadTimeline()
      return
    }
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/sns/search?q=${encodeURIComponent(search)}`)
      const data = await res.json()
      if (data.ok) {
        setPosts(data.data || [])
        setSearchMode(true)
        setHasMore(false)
      } else {
        setError(data.error)
      }
    } catch {
      setError('搜索失败')
    } finally {
      setLoading(false)
    }
  }

  function clearSearch() {
    setSearch('')
    setSearchMode(false)
    loadTimeline()
  }

  async function toggleProtection() {
    setTogglingProtection(true)
    try {
      const isInstalled = protectionStatus?.installed
      const endpoint = isInstalled ? '/api/sns/protect/uninstall' : '/api/sns/protect/install'
      const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' })
      const data = await res.json()
      setProtectionStatus(data)
    } catch {
      // ignore
    } finally {
      setTogglingProtection(false)
    }
  }

  function _getTimeRange() {
    let start_time = 0, end_time = 0
    if (timePreset === 1) { // 今天
      const d = new Date(); d.setHours(0,0,0,0)
      start_time = Math.floor(d.getTime() / 1000)
    } else if (timePreset === 2) { // 7天
      start_time = Math.floor((Date.now() - 7 * 86400000) / 1000)
    } else if (timePreset === 3) { // 30天
      start_time = Math.floor((Date.now() - 30 * 86400000) / 1000)
    } else if (timePreset === -1) { // 自定义
      if (customStart) start_time = Math.floor(new Date(customStart).getTime() / 1000)
      if (customEnd) end_time = Math.floor(new Date(customEnd + 'T23:59:59').getTime() / 1000)
    }
    return { start_time, end_time }
  }

  async function handleExport() {
    setExporting(true)
    setExportStatus('starting')
    const { start_time, end_time } = _getTimeRange()
    const exportBody = {
      username: usernameFilter || undefined,
      start_time: start_time || undefined,
      end_time: end_time || undefined,
    }
    try {
      // Step 1: dry_run to estimate size
      const dryRes = await fetch(`${API_BASE}/api/sns/export?dry_run=true`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exportBody),
      })
      const dryData = await dryRes.json()
      if (!dryData.ok) {
        setExportStatus('error')
        setExporting(false)
        return
      }

      // Step 2: if size_warning or truncated, ask user to confirm
      let confirmMsg = `即将导出 ${dryData.item_count} 条朋友圈，约 ${dryData.image_count} 张图片（预估 ~${dryData.estimated_mb} MB）`
      if (dryData.truncated) {
        confirmMsg = `结果已达上限 ${dryData.limit} 条，建议缩小筛选范围。\n` + confirmMsg
      }
      if (dryData.size_warning || dryData.truncated) {
        const confirmed = window.confirm(confirmMsg + '，确认继续？')
        if (!confirmed) {
          setExporting(false)
          setExportStatus('')
          return
        }
      }

      // Step 3: real export
      const res = await fetch(`${API_BASE}/api/sns/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exportBody),
      })
      const data = await res.json()
      if (data.ok) {
        setExportStatus('completed')
        // Auto open folder
        openExportFolder()
        setTimeout(() => setExportStatus(''), 3000)
      } else {
        setExportStatus('error')
      }
    } catch {
      setExportStatus('error')
    } finally {
      setExporting(false)
    }
  }

  async function openExportFolder() {
    try {
      await fetch(`${API_BASE}/api/export/open-folder?type=sns`, { method: 'POST' })
    } catch (e) {
      console.warn('Open folder failed:', e)
    }
  }

  const filteredPosts = useMemo(() => {
    if (!usernameFilter) return posts
    return posts.filter(p => p.username === usernameFilter)
  }, [posts, usernameFilter])

  // Build contact list: username -> display name (nickname or username)
  const contactOptions = useMemo(() => {
    const options = usernames.map(u => ({
      username: u,
      displayName: nicknameMap[u] || u
    }))
    // Filter by search if provided
    if (contactSearch.trim()) {
      const kw = contactSearch.toLowerCase()
      return options.filter(c => c.displayName.toLowerCase().includes(kw))
    }
    return options
  }, [usernames, nicknameMap, contactSearch])

  // Sort contacts by display name
  const sortedContacts = useMemo(() => {
    return [...contactOptions].sort((a, b) =>
      a.displayName.localeCompare(b.displayName, 'zh-CN')
    )
  }, [contactOptions])

  return (
    <>
    <motion.div initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }}>
      {/* Timeline */}
      <div className="mb-5">
        <div className="flex items-center gap-2.5 mb-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm" style={{ backgroundColor: '#8B5CF6' }} />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">朋友圈</h3>
          <Eye size={16} className="text-text-muted" />
          <div className="ml-auto flex items-center gap-2">
            {restrictedEnabled && (
            <button
              onClick={() => setShowProtection(!showProtection)}
              className={`p-2 rounded-full transition-colors cursor-pointer
                ${protectionStatus?.installed
                  ? 'text-brand-green hover:bg-brand-green-light/20'
                  : 'text-text-muted hover:bg-bg-raised'
                }`}
              title="内容归档设置"
            >
              <ShieldCheck size={16} weight={protectionStatus?.installed ? 'fill' : 'regular'} />
            </button>
            )}
            <button
              onClick={() => setAiDrawerOpen(true)}
              className="flex items-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium bg-brand-green/10 text-brand-green-hover dark:text-brand-green border border-brand-green/20 hover:bg-brand-green/20 transition-all cursor-pointer"
              title="AI 对话"
            >
              <Sparkle size={14} />
              AI 对话
            </button>
            <button
              onClick={openExportFolder}
              className="hidden lg:flex items-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium bg-bg-raised border border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30 transition-all cursor-pointer"
              title="打开导出文件夹"
            >
              <FolderOpen size={14} />
              文件夹
            </button>
            <button
              onClick={handleExport}
              disabled={exporting}
              className={`hidden lg:flex items-center gap-2 px-4 py-2 rounded-full text-xs font-semibold transition-all cursor-pointer
                ${exporting
                  ? 'bg-bg-raised text-text-muted cursor-wait'
                  : 'bg-brand-green-hover text-white hover:bg-brand-green-hover'
                }`}
            >
              <DownloadSimple size={14} />
              {exporting ? '导出中...' : '导出HTML'}
            </button>
          </div>
        </div>
        <p className="text-xs text-text-muted leading-relaxed pl-4">浏览微信朋友圈动态</p>
      </div>

      {/* Protection card (collapsible) */}
      <AnimatePresence>
        {restrictedEnabled && showProtection && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mb-4 overflow-hidden"
          >
            <div className="p-4 rounded-xl border border-border-main bg-bg-card">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {protectionStatus?.installed ? (
                    <ShieldCheck size={20} className="text-brand-green" weight="fill" />
                  ) : (
                    <ShieldWarning size={20} className="text-text-muted" />
                  )}
                  <div>
                    <p className="text-sm font-medium text-text-main">内容归档</p>
                    <p className="text-xs text-text-muted">
                      {protectionStatus?.installed
                        ? '已启用 — 被删除的朋友圈将被拦截'
                        : '未启用 — 安装 trigger 防止朋友圈被删除'}
                    </p>
                  </div>
                </div>
                <button
                  onClick={toggleProtection}
                  disabled={togglingProtection}
                  className={`px-4 py-2 rounded-full text-xs font-semibold transition-all cursor-pointer
                    ${protectionStatus?.installed
                      ? 'bg-status-error-soft text-status-error border border-status-error/20 hover:bg-status-error/20'
                      : 'bg-brand-green-light text-brand-green-hover dark:text-brand-green border border-brand-green/20 hover:bg-brand-green-light/60'
                    }`}
                >
                  {togglingProtection ? '...' : protectionStatus?.installed ? '卸载保护' : '安装保护'}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Search and Filter */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[180px]">
          <MagnifyingGlass size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="搜索朋友圈内容..."
            className="w-full bg-bg-raised border border-border-main rounded-full pl-10 pr-10 py-2.5 text-sm text-text-main
              placeholder:text-text-muted font-mono
              focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
              transition-all"
          />
          {search && (
            <button
              onClick={clearSearch}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main cursor-pointer"
            >
              <X size={14} />
            </button>
          )}
        </div>
        {/* Contact filter dropdown — search is inside the dropdown */}
        <div className="relative" ref={contactDropdownRef}>
          <button
            onClick={() => setContactDropdownOpen(!contactDropdownOpen)}
            className="bg-bg-raised border border-border-main rounded-full px-3 py-2.5 text-xs text-text-main font-mono
              focus:outline-none focus:border-brand-green cursor-pointer flex items-center gap-1.5"
          >
            {usernameFilter
              ? (nicknameMap[usernameFilter] || usernameFilter)
              : '全部联系人'}
            <CaretDown size={10} className={`text-text-muted transition-transform ${contactDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          <AnimatePresence>
            {contactDropdownOpen && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                className="absolute z-50 mt-1.5 right-0 w-56 bg-bg-card border border-border-main rounded-xl shadow-lg overflow-hidden"
              >
                <div className="relative border-b border-border-main bg-bg-raised">
                  <MagnifyingGlass size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
                  <input
                    type="text"
                    value={contactSearch}
                    onChange={(e) => setContactSearch(e.target.value)}
                    placeholder="搜索联系人..."
                    className="w-full bg-transparent pl-8 pr-3 py-2 text-xs text-text-main
                      placeholder:text-text-muted focus:outline-none"
                    autoFocus
                  />
                </div>
                <div className="max-h-48 overflow-y-auto">
                  <button
                    onClick={() => { setUsernameFilter(''); setContactDropdownOpen(false); setContactSearch('') }}
                    className={`w-full text-left px-3 py-2 text-xs transition-colors cursor-pointer
                      ${!usernameFilter ? 'bg-brand-green-light/15 text-brand-green' : 'text-text-main hover:bg-bg-raised/60'}`}
                  >
                    全部联系人
                  </button>
                  {sortedContacts
                    .filter(c => !contactSearch || c.displayName.toLowerCase().includes(contactSearch.toLowerCase()))
                    .map(c => (
                      <button
                        key={c.username}
                        onClick={() => { setUsernameFilter(c.username); setContactDropdownOpen(false); setContactSearch('') }}
                        className={`w-full text-left px-3 py-2 text-xs transition-colors cursor-pointer truncate
                          ${usernameFilter === c.username ? 'bg-brand-green-light/15 text-brand-green' : 'text-text-main hover:bg-bg-raised/60'}`}
                      >
                        {c.displayName}
                      </button>
                    ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        {/* Time filter dropdown */}
        <div className="relative" ref={timeDropdownRef}>
          <button
            onClick={() => setTimeDropdownOpen(!timeDropdownOpen)}
            className="bg-bg-raised border border-border-main rounded-full px-3 py-2.5 text-xs text-text-main font-mono
              focus:outline-none focus:border-brand-green cursor-pointer flex items-center gap-1.5"
          >
            <Clock size={12} className="text-text-muted" />
            {timePreset === 0 ? '全部时间' : timePreset === 1 ? '今天' : timePreset === 2 ? '7天' : timePreset === 3 ? '30天' : '自定义'}
            <CaretDown size={10} className={`text-text-muted transition-transform ${timeDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          <AnimatePresence>
            {timeDropdownOpen && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                className="absolute z-50 mt-1.5 right-0 w-44 bg-bg-card border border-border-main rounded-xl shadow-lg overflow-hidden"
              >
                {[
                  { value: 0, label: '全部时间' },
                  { value: 1, label: '今天' },
                  { value: 2, label: '最近7天' },
                  { value: 3, label: '最近30天' },
                  { value: -1, label: '自定义' },
                ].map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => { setTimePreset(opt.value); if (opt.value !== -1) setTimeDropdownOpen(false) }}
                    className={`w-full text-left px-3 py-2 text-xs transition-colors cursor-pointer
                      ${timePreset === opt.value ? 'bg-brand-green-light/15 text-brand-green' : 'text-text-main hover:bg-bg-raised/60'}`}
                  >
                    {opt.label}
                  </button>
                ))}
                {timePreset === -1 && (
                  <div className="border-t border-border-main p-2 space-y-1.5">
                    <input
                      type="date"
                      value={customStart}
                      onChange={(e) => setCustomStart(e.target.value)}
                      className="w-full bg-bg-raised border border-border-main rounded px-2 py-1 text-xs text-text-main focus:outline-none focus:border-brand-green"
                      placeholder="开始日期"
                    />
                    <input
                      type="date"
                      value={customEnd}
                      onChange={(e) => setCustomEnd(e.target.value)}
                      className="w-full bg-bg-raised border border-border-main rounded px-2 py-1 text-xs text-text-main focus:outline-none focus:border-brand-green"
                      placeholder="结束日期"
                    />
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Search mode indicator */}
      {searchMode && (
        <div className="mb-4 flex items-center gap-2 text-xs text-text-muted">
          <span>搜索 &ldquo;{search}&rdquo; 的结果</span>
          <button onClick={clearSearch} className="text-brand-green hover:underline cursor-pointer">返回时间线</button>
        </div>
      )}

      {/* Export status */}
      {exportStatus && (
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
          <Clock size={14} />
          {exportStatus === 'completed' && '导出完成'}
          {exportStatus === 'error' && '导出失败'}
          {(exportStatus === 'starting' || exportStatus === 'started') && '正在导出...'}
        </motion.div>
      )}

      {/* Results info */}
      <div className="text-xs text-text-muted mb-4 font-mono">
        共 {filteredPosts.length} 条动态
      </div>

      {/* Posts list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-6 h-6 border-2 border-brand-green/30 border-t-brand-green rounded-full animate-spin" />
        </div>
      ) : error ? (
        <div className="text-center py-12 text-status-error text-sm">{error}</div>
      ) : filteredPosts.length === 0 ? (
        <div className="text-center py-12 text-text-muted text-sm">
          {searchMode ? '没有匹配的动态' : '暂无朋友圈动态，请在微信中打开朋友圈同步数据'}
        </div>
      ) : (
        <div className="space-y-3">
          {filteredPosts.map(post => (
            <SnsPostCard key={post.id || post.create_time} post={post} avatarCache={avatarCacheRef.current} />
          ))}

          {/* Load more */}
          {hasMore && !searchMode && (
            <div className="flex justify-center py-4">
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="px-6 py-2.5 rounded-full text-xs font-medium bg-bg-raised border border-border-main
                  text-text-muted hover:text-text-main hover:border-text-muted/30 transition-all cursor-pointer
                  disabled:cursor-wait"
              >
                {loadingMore ? (
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
    </motion.div>

      {/* SNS AI Drawer */}
      <SnsAiDrawer
        open={aiDrawerOpen}
        onClose={() => setAiDrawerOpen(false)}
        contacts={usernames.map(u => ({ username: u, nickname: nicknameMap[u] || u }))}
      />
    </>
  )
}
