import { ChatCircleDots, Sparkle } from '@phosphor-icons/react'

/**
 * AI Chat configuration panel — shown inside Drawer before starting a session.
 *
 * mode="favorites":
 *   - Tag chips + Type chips + "开始对话" button
 * mode="chat":
 *   - Time range chips + custom date inputs + "开始对话" button
 *
 * Common props:
 *   onStart: () => void   — call /api/ai/chat/start
 *   loading: boolean
 *
 * Favorites mode:
 *   tags, selectedTagId, onTagIdChange, selectedFavTypes, onFavTypesChange
 *
 * Chat mode:
 *   timePresets, selectedTimePreset, onTimePresetChange,
 *   customStart, customEnd, onCustomStartChange, onCustomEndChange
 */

const FAV_TYPE_OPTIONS = [
  { type: 1, label: '文字' },
  { type: 14, label: '聊天' },
  { type: 5, label: '链接' },
  { type: 33, label: '文章' },
  { type: 2, label: '图片' },
  { type: 4, label: '视频' },
  { type: 3, label: '语音' },
  { type: 8, label: '文件' },
]

export default function AIChatConfig({
  mode,
  onStart,
  loading = false,
  // Favorites mode
  tags = [],
  selectedTagId = '',
  onTagIdChange,
  selectedFavTypes = [],
  onFavTypesChange,
  // Chat mode
  timePresets = [],
  selectedTimePreset = 0,
  onTimePresetChange,
  customStart = '',
  customEnd = '',
  onCustomStartChange,
  onCustomEndChange,
}) {
  const canStart = mode === 'favorites'
    ? selectedFavTypes.length > 0
    : true  // chat mode always has a valid time range

  return (
    <div className="flex flex-col h-full">
      {/* Header illustration */}
      <div className="px-6 pt-8 pb-4">
        <div className="w-12 h-12 rounded-2xl bg-brand-green/10 flex items-center justify-center mb-4">
          <Sparkle size={24} className="text-brand-green" weight="fill" />
        </div>
        <h4 className="text-base font-semibold text-text-main mb-1">
          {mode === 'favorites' ? 'AI 收藏助手' : 'AI 对话助手'}
        </h4>
        <p className="text-xs text-text-muted leading-relaxed">
          {mode === 'favorites'
            ? '选择要分析的收藏内容范围，AI 将基于选定内容进行对话'
            : '选择要分析的聊天记录时间段，AI 将基于选定范围进行对话'}
        </p>
      </div>

      {/* Config options */}
      <div className="flex-1 overflow-y-auto px-6 pb-4">
        {mode === 'favorites' && (
          <>
            {/* Tag selection */}
            <div className="mb-5">
              <span className="text-xs font-medium text-text-muted block mb-2">标签范围</span>
              <div className="flex gap-1.5 flex-wrap">
                <button
                  onClick={() => onTagIdChange?.('')}
                  className={`px-2.5 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                    ${!selectedTagId
                      ? 'bg-brand-green-hover text-white border-brand-green'
                      : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
                >
                  全部标签
                </button>
                {tags.map(tag => (
                  <button
                    key={tag.id}
                    onClick={() => onTagIdChange?.(selectedTagId === tag.id ? '' : tag.id)}
                    className={`px-2.5 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                      ${selectedTagId === tag.id
                        ? 'bg-brand-green-hover text-white border-brand-green'
                        : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
                  >
                    {tag.name} ({tag.fav_count})
                  </button>
                ))}
              </div>
            </div>

            {/* Type selection */}
            <div className="mb-5">
              <span className="text-xs font-medium text-text-muted block mb-2">内容类型</span>
              <div className="flex gap-1.5 flex-wrap">
                {FAV_TYPE_OPTIONS.map(opt => {
                  const checked = selectedFavTypes.includes(opt.type)
                  return (
                    <button
                      key={opt.type}
                      onClick={() => onFavTypesChange?.(
                        checked
                          ? selectedFavTypes.filter(t => t !== opt.type)
                          : [...selectedFavTypes, opt.type]
                      )}
                      className={`px-2.5 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                        ${checked
                          ? 'bg-brand-green-light/20 border-brand-green/30 text-brand-green'
                          : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
                    >
                      {checked ? '✓ ' : ''}{opt.label}
                    </button>
                  )
                })}
              </div>
            </div>
          </>
        )}

        {mode === 'chat' && (
          <>
            {/* Time range presets */}
            <div className="mb-5">
              <span className="text-xs font-medium text-text-muted block mb-2">分析范围</span>
              <div className="flex gap-1.5 flex-wrap">
                {timePresets.map((preset, idx) => (
                  <button
                    key={idx}
                    onClick={() => onTimePresetChange?.(idx)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                      ${selectedTimePreset === idx
                        ? 'bg-brand-green-hover text-white border-brand-green'
                        : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
                  >
                    {preset.label}
                  </button>
                ))}
                <button
                  onClick={() => onTimePresetChange?.(-1)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer
                    ${selectedTimePreset === -1
                      ? 'bg-brand-green-hover text-white border-brand-green'
                      : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main'}`}
                >
                  自定义
                </button>
              </div>
            </div>

            {/* Custom date range */}
            {selectedTimePreset === -1 && (
              <div className="mb-5 p-3 rounded-xl bg-bg-raised border border-border-main">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-muted">从</span>
                  <input
                    type="date"
                    value={customStart}
                    onChange={(e) => onCustomStartChange?.(e.target.value)}
                    className="flex-1 bg-bg-card border border-border-main rounded-lg px-2 py-1.5 text-xs text-text-main focus:outline-none focus:border-brand-green"
                  />
                  <span className="text-xs text-text-muted">到</span>
                  <input
                    type="date"
                    value={customEnd}
                    onChange={(e) => onCustomEndChange?.(e.target.value)}
                    className="flex-1 bg-bg-card border border-border-main rounded-lg px-2 py-1.5 text-xs text-text-main focus:outline-none focus:border-brand-green"
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Start button */}
      <div className="px-6 py-4 border-t border-border-main shrink-0">
        <button
          onClick={onStart}
          disabled={!canStart || loading}
          className="w-full py-2.5 rounded-xl text-sm font-semibold text-white bg-brand-green-hover hover:bg-[#0d8c5c] transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              加载中…
            </>
          ) : (
            <>
              <ChatCircleDots size={16} />
              开始对话
            </>
          )}
        </button>
      </div>
    </div>
  )
}
