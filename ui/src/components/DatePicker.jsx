import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Calendar, CaretLeft, CaretRight, X } from '@phosphor-icons/react'

const MONTH_NAMES = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月']
const WEEKDAY_NAMES = ['日', '一', '二', '三', '四', '五', '六']

const QUICK_OPTIONS = [
  { label: '最近7天', days: 7 },
  { label: '最近30天', days: 30 },
  { label: '最近90天', days: 90 },
  { label: '全部时间', days: 0 },
]

/**
 * DateRangePicker — custom calendar component for WebView2 compatibility.
 * Replaces <input type="date"> which has poor UX in WebView2.
 *
 * Props:
 *   startDate: string (YYYY-MM-DD)
 *   endDate: string (YYYY-MM-DD)
 *   onStartChange: (date: string) => void
 *   onEndChange: (date: string) => void
 *   onRangeComplete?: () => void  — called when both dates are selected
 *   className?: string
 */
export default function DatePicker({ startDate, endDate, onStartChange, onEndChange, onRangeComplete, className }) {
  const [isOpen, setIsOpen] = useState(false)
  const [currentMonth, setCurrentMonth] = useState(new Date())
  const [selectingStart, setSelectingStart] = useState(true)
  const [internalStart, setInternalStart] = useState(startDate)
  const [internalEnd, setInternalEnd] = useState(endDate)
  const [popupPos, setPopupPos] = useState({ top: 0, left: 0 })
  const containerRef = useRef(null)
  const buttonRef = useRef(null)

  useEffect(() => { setInternalStart(startDate); setInternalEnd(endDate) }, [startDate, endDate])
  useEffect(() => { if (isOpen) setSelectingStart(true) }, [isOpen])

  // Calculate popup position when opening
  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    setPopupPos({
      top: rect.bottom + 4,
      left: Math.min(rect.left, window.innerWidth - 280),  // keep in viewport
    })
  }, [])

  useEffect(() => {
    if (isOpen) {
      updatePosition()
      const onScroll = () => updatePosition()
      window.addEventListener('scroll', onScroll, true)
      window.addEventListener('resize', onScroll)
      return () => {
        window.removeEventListener('scroll', onScroll, true)
        window.removeEventListener('resize', onScroll)
      }
    }
  }, [isOpen, updatePosition])

  // Click outside to close (works with Portal)
  useEffect(() => {
    if (!isOpen) return
    const handler = (e) => {
      // Check if click is inside the button or the portal popup
      if (buttonRef.current?.contains(e.target)) return
      if (containerRef.current?.contains(e.target)) return
      setIsOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [isOpen])

  const formatDisplay = (dateStr) => {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
  }

  const getDisplayText = () => {
    if (!startDate && !endDate) return '选择时间范围'
    if (startDate && endDate) return `${formatDisplay(startDate)} - ${formatDisplay(endDate)}`
    if (startDate) return `${formatDisplay(startDate)} - ?`
    return `? - ${formatDisplay(endDate)}`
  }

  const handleQuickOption = (days) => {
    if (days === 0) {
      onStartChange('')
      onEndChange('')
    } else {
      const end = new Date()
      const start = new Date()
      start.setDate(start.getDate() - days)
      onStartChange(fmt(start))
      onEndChange(fmt(end))
    }
    setIsOpen(false)
    setTimeout(() => onRangeComplete?.(), 0)
  }

  const handleClear = (e) => {
    e.stopPropagation()
    onStartChange('')
    onEndChange('')
  }

  const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

  const getDaysInMonth = (date) => new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate()
  const getFirstDayOfMonth = (date) => new Date(date.getFullYear(), date.getMonth(), 1).getDay()

  const handleDateClick = (day) => {
    const dateStr = `${currentMonth.getFullYear()}-${String(currentMonth.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`

    if (selectingStart) {
      setInternalStart(dateStr)
      if (internalEnd && dateStr > internalEnd) setInternalEnd('')
      setSelectingStart(false)
    } else {
      let finalStart = internalStart
      let finalEnd = dateStr
      if (dateStr < internalStart) { finalStart = dateStr; finalEnd = internalStart }
      setInternalStart(finalStart)
      setInternalEnd(finalEnd)
      setSelectingStart(true)
      setIsOpen(false)
      onStartChange(finalStart)
      onEndChange(finalEnd)
      setTimeout(() => onRangeComplete?.(), 0)
    }
  }

  const isInRange = (day) => {
    if (!internalStart || !internalEnd) return false
    const dateStr = `${currentMonth.getFullYear()}-${String(currentMonth.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    return dateStr >= internalStart && dateStr <= internalEnd
  }

  const isStartDate = (day) => {
    const dateStr = `${currentMonth.getFullYear()}-${String(currentMonth.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    return dateStr === internalStart
  }

  const isEndDate = (day) => {
    const dateStr = `${currentMonth.getFullYear()}-${String(currentMonth.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    return dateStr === internalEnd
  }

  const isToday = (day) => {
    const today = new Date()
    return currentMonth.getFullYear() === today.getFullYear() && currentMonth.getMonth() === today.getMonth() && day === today.getDate()
  }

  const renderCalendar = () => {
    const daysInMonth = getDaysInMonth(currentMonth)
    const firstDay = getFirstDayOfMonth(currentMonth)
    const days = []
    for (let i = 0; i < firstDay; i++) days.push(null)
    for (let i = 1; i <= daysInMonth; i++) days.push(i)

    return (
      <div className="grid grid-cols-7 gap-0.5">
        {WEEKDAY_NAMES.map(name => (
          <div key={name} className="text-center text-xs text-text-muted py-1">{name}</div>
        ))}
        {days.map((day, index) => (
          <div
            key={index}
            className={`text-center text-xs py-1.5 rounded-md cursor-pointer transition-colors
              ${!day ? '' : 'hover:bg-bg-raised'}
              ${day && isInRange(day) ? 'bg-brand-green-light/30' : ''}
              ${day && isStartDate(day) ? 'bg-brand-green text-white hover:bg-brand-green' : ''}
              ${day && isEndDate(day) ? 'bg-brand-green text-white hover:bg-brand-green' : ''}
              ${day && isToday(day) && !isStartDate(day) && !isEndDate(day) ? 'font-bold text-brand-green' : ''}
              ${day && !isInRange(day) && !isStartDate(day) && !isEndDate(day) ? 'text-text-main' : ''}
            `}
            onClick={() => day && handleDateClick(day)}
          >
            {day}
          </div>
        ))}
      </div>
    )
  }

  const popupContent = isOpen && (
    <div ref={containerRef}
      className="bg-bg-card border border-border-main rounded-lg shadow-lg overflow-hidden min-w-[260px]"
      style={{ position: 'fixed', top: popupPos.top, left: popupPos.left, zIndex: 9999 }}>
      {/* Quick options */}
      <div className="flex gap-1 px-2 py-2 border-b border-border-main/30">
        {QUICK_OPTIONS.map(opt => (
          <button key={opt.label}
            onClick={() => handleQuickOption(opt.days)}
            className="text-xs px-2 py-1 rounded-md bg-bg-raised hover:bg-brand-green-light/30 text-text-muted hover:text-brand-green transition-colors cursor-pointer whitespace-nowrap"
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Calendar */}
      <div className="px-3 py-2">
        <div className="flex items-center justify-between mb-2">
          <button onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1))}
            className="p-1 rounded-md hover:bg-bg-raised cursor-pointer">
            <CaretLeft size={14} className="text-text-muted" />
          </button>
          <span className="text-xs font-medium text-text-main">
            {currentMonth.getFullYear()}年 {MONTH_NAMES[currentMonth.getMonth()]}
          </span>
          <button onClick={() => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1))}
            className="p-1 rounded-md hover:bg-bg-raised cursor-pointer">
            <CaretRight size={14} className="text-text-muted" />
          </button>
        </div>
        {renderCalendar()}
        <div className="text-center text-xs text-text-muted mt-2">
          {selectingStart ? '请选择开始日期' : '请选择结束日期'}
        </div>
      </div>
    </div>
  )

  return (
    <div className={`${className || ''}`}>
      <button
        ref={buttonRef}
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 bg-bg-raised border border-border-main rounded-lg px-2.5 py-1.5 text-xs text-text-main hover:border-brand-green transition-colors cursor-pointer"
      >
        <Calendar size={14} className="text-text-muted" />
        <span>{getDisplayText()}</span>
        {(startDate || endDate) && (
          <span onClick={handleClear} className="ml-1 text-text-muted hover:text-text-main cursor-pointer">
            <X size={12} />
          </span>
        )}
      </button>
      {createPortal(popupContent, document.body)}
    </div>
  )
}
