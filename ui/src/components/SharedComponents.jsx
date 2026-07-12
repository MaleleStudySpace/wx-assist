import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Warning, Eye, EyeSlash, X } from '@phosphor-icons/react'

// ── Shared constants ──────────────────────────────────────────────
export const API_BASE = ''

/** Build a WebSocket URL for the current origin (works in dev, desktop, and LAN). */
export function getWsUrl(path = '/ws') {
  return `ws://${window.location.host}${path}`
}

export const spring = { type: 'spring', stiffness: 200, damping: 25 }

export function Field({ label, hint, error, children }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <label className="block text-[14px] font-semibold text-text-main mb-1.5">{label}</label>
      {children}
      <AnimatePresence initial={false} mode="wait">
        {error ? (
          <motion.p
            key="error"
            initial={{ opacity: 0, height: 0, y: -4 }}
            animate={{ opacity: 1, height: 'auto', y: 0 }}
            exit={{ opacity: 0, height: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="text-xs text-status-error flex items-center gap-1 mt-1.5 overflow-hidden"
          >
            <Warning size={12} />{error}
          </motion.p>
        ) : hint ? (
          <motion.p
            key="hint"
            initial={{ opacity: 0, height: 0, y: -4 }}
            animate={{ opacity: 1, height: 'auto', y: 0 }}
            exit={{ opacity: 0, height: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="text-xs text-text-muted mt-1.5 overflow-hidden"
          >
            {hint}
          </motion.p>
        ) : null}
      </AnimatePresence>
    </div>
  )
}

export function Toggle({ enabled, onChange }) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onChange(!enabled) }}
      className={`relative w-11 h-6 rounded-full shrink-0 transition-colors duration-200 border cursor-pointer outline-none focus:ring-2 focus:ring-brand-green/20
        ${enabled ? 'bg-brand-green-light border-brand-green/30' : 'bg-bg-raised border-border-main'}`}
    >
      <motion.span
        layout
        transition={{ type: 'spring', stiffness: 500, damping: 28 }}
        className="absolute top-0.5 left-0.5 w-4.5 h-4.5 rounded-full shadow-sm"
        animate={{
          x: enabled ? 20 : 0,
          backgroundColor: enabled ? 'var(--brand-green)' : 'rgba(136, 136, 136, 0.6)',
        }}
      />
    </button>
  )
}

export function Select({ value, onChange, options }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function handleClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const selected = options.find(o => o.value === value)

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full bg-bg-raised border border-border-main rounded-full px-5 py-2.5 text-[14px] text-text-main
                   focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                   transition-all duration-200 cursor-pointer text-left
                   hover:border-text-muted/30 dark:hover:border-text-muted/40"
      >
        {selected ? `${selected.value}  ·  ${selected.desc}` : value}
      </button>
      <span
        className="absolute right-5 top-1/2 pointer-events-none select-none text-text-muted text-lg font-mono transition-all duration-200"
        style={{ transform: open ? 'translateY(-55%) rotate(90deg)' : 'translateY(-55%) rotate(0deg)' }}
      >&#8250;</span>

      {open && (
        <motion.div
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          className="absolute z-50 left-0 right-0 mt-1.5 bg-bg-card border border-border-main rounded-2xl shadow-xl overflow-hidden max-h-[280px] overflow-y-auto"
        >
          {options.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={`w-full text-left px-5 py-2.5 text-[14px] transition-colors flex items-center gap-3 font-mono cursor-pointer
                ${value === opt.value ? 'bg-brand-green-light text-brand-green-hover dark:text-brand-green font-semibold' : 'text-text-main hover:bg-bg-raised'}`}
            >
              <span className="w-[72px] shrink-0 font-semibold">{opt.value}</span>
              <span className="w-[4px] shrink-0 opacity-40 text-text-muted">·</span>
              <span className={opt.hint ? 'w-[80px] shrink-0' : ''}>{opt.desc}</span>
              {opt.hint && (<>
                <span className="w-[4px] shrink-0 opacity-40 text-text-muted">·</span>
                <span className="text-text-muted truncate">{opt.hint}</span>
              </>)}
            </button>
          ))}
        </motion.div>
      )}
    </div>
  )
}

export function Input({ type = 'text', value, onChange, placeholder }) {
  const [showPassword, setShowPassword] = useState(false)
  const isPassword = type === 'password'

  return (
    <div className="relative w-full flex items-center">
      <motion.input
        whileFocus={{ scale: 1.001 }}
        type={isPassword ? (showPassword ? 'text' : 'password') : type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className={`w-full bg-bg-raised border border-border-main rounded-full pl-5 ${isPassword ? 'pr-12' : 'pr-5'} py-2.5 text-[14px] text-text-main
                   placeholder:text-text-muted font-mono tabular-nums
                   focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                   transition-all duration-200
                   hover:border-text-muted/30 dark:hover:border-text-muted/40`}
      />
      {isPassword && (
        <button
          type="button"
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-4 text-text-muted hover:text-text-main focus:outline-none transition-colors cursor-pointer"
        >
          {showPassword ? <EyeSlash size={18} /> : <Eye size={18} />}
        </button>
      )}
    </div>
  )
}

// ── SectionHeader ─────────────────────────────────────────────────
// Unified section header used across AssistantPanel, FavoritesTab, etc.
export function SectionHeader({ title, accent = 'var(--brand-green)', icon: Icon, subtitle, action }) {
  return (
    <div className="mb-5">
      <div className="flex items-center gap-2.5 mb-1">
        <div className="w-1.5 h-5 rounded-full shadow-sm" style={{ backgroundColor: accent }} />
        <h3 className="text-base font-semibold tracking-tight text-text-main">{title}</h3>
        {Icon && <Icon size={18} className="text-text-muted" />}
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {subtitle && <p className="text-xs text-text-muted leading-relaxed pl-4">{subtitle}</p>}
    </div>
  )
}

// ── ImageLightbox ─────────────────────────────────────────────────
// Full-screen image viewer with Escape-to-close and backdrop click.
export function ImageLightbox({ src, onClose }) {
  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg-main/90 dark:bg-black/80 backdrop-blur-sm cursor-zoom-out"
      onClick={onClose}
    >
      <motion.img
        initial={{ scale: 0.86, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.86, opacity: 0 }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
        src={src}
        alt="放大图片"
        className="max-w-[92vw] max-h-[92vh] object-contain rounded-xl shadow-2xl cursor-zoom-out"
        onClick={onClose}
      />
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 rounded-full bg-bg-raised/70 hover:bg-bg-raised text-text-muted hover:text-text-main transition-colors cursor-pointer"
      >
        <X size={20} />
      </button>
    </motion.div>
  )
}

// ── Avatar ─────────────────────────────────────────────────────────
// Resuable avatar with image fallback to initial letter.
// Uses CSS-variable-based colors: --avatar-group, --avatar-private.
export function Avatar({ src, name, size = 36, className = '' }) {
  const [error, setError] = useState(false)
  const initial = (name || '?')[0]?.toUpperCase() || '?'

  // Detect group vs private from naming convention
  const isGroup = name?.includes('@chatroom') || className?.includes('group')
  const fallbackClass = isGroup
    ? 'bg-[var(--avatar-group-bg,var(--status-info-soft))] text-[var(--avatar-group-fg,var(--status-info))]'
    : 'bg-[var(--avatar-private-bg,#8B735520)] text-[var(--avatar-private-fg,#8B7355)]'

  if (src && !error) {
    return (
      <img
        src={src}
        alt={name || ''}
        className={`rounded-full object-cover flex-shrink-0 ${className}`}
        style={{ width: size, height: size }}
        onError={() => setError(true)}
        referrerPolicy="no-referrer"
        loading="lazy"
      />
    )
  }

  return (
    <div
      className={`rounded-full flex items-center justify-center text-xs font-semibold flex-shrink-0 ${fallbackClass} ${className}`}
      style={{ width: size, height: size }}
    >
      {initial}
    </div>
  )
}

// ── TagInput ───────────────────────────────────────────────────────
// Chips-style tag input: type + Enter to add, click x to remove.
export function TagInput({ tags = [], onChange, placeholder = '输入后按回车或逗号添加' }) {
  const [input, setInput] = useState('')
  const ref = useRef(null)

  function addTag(value) {
    const trimmed = value.trim()
    if (!trimmed || tags.includes(trimmed)) return
    onChange([...tags, trimmed])
    setInput('')
  }

  function removeTag(tag) {
    onChange(tags.filter(t => t !== tag))
  }

  function handleKey(e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(input)
    } else if (e.key === 'Backspace' && !input && tags.length) {
      removeTag(tags[tags.length - 1])
    }
  }

  return (
    <div
      className="flex flex-wrap gap-1.5 bg-bg-raised border border-border-main rounded-lg px-3 py-2.5 min-h-[46px] cursor-text focus-within:border-brand-green focus-within:ring-1 focus-within:ring-brand-green/15 transition-all"
      onClick={() => ref.current?.focus()}
    >
      {tags.map(tag => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-brand-green/10 text-brand-green-hover dark:text-brand-green text-xs font-medium"
        >
          {tag}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); removeTag(tag) }}
            className="text-brand-green/50 hover:text-status-error transition-colors cursor-pointer"
          >
            <X size={10} weight="bold" />
          </button>
        </span>
      ))}
      <input
        ref={ref}
        type="text"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={handleKey}
        onBlur={() => addTag(input)}
        placeholder={tags.length ? '' : placeholder}
        className="flex-1 min-w-[80px] bg-transparent text-sm text-text-main placeholder:text-text-muted focus:outline-none"
      />
    </div>
  )
}
