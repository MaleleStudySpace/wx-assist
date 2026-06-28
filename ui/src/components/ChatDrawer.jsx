import { motion, AnimatePresence } from 'framer-motion'
import { X } from '@phosphor-icons/react'

/**
 * Right-side slide-in drawer panel with backdrop blur.
 *
 * The panel is always mounted in the DOM — closing just slides it off-screen.
 * This preserves children's state (e.g. AIChatPanel messages, input, SSE stream)
 * across open/close cycles. Only the backdrop is conditionally rendered.
 *
 * Usage:
 *   <ChatDrawer open={true} onClose={() => {}} title="AI 对话">
 *     {children}
 *   </ChatDrawer>
 */
export default function ChatDrawer({ open, onClose, title, children }) {
  return (
    <>
      {/* Backdrop overlay — only when open */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={onClose}
          />
        )}
      </AnimatePresence>

      {/* Drawer panel — always in DOM, slides in/out */}
      <motion.div
        initial={{ x: '100%' }}
        animate={{ x: open ? 0 : '100%' }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="fixed right-0 top-0 h-full w-[420px] max-w-[calc(100vw-1rem)] bg-bg-main border-l border-border-main z-50 flex flex-col shadow-2xl"
        style={{ pointerEvents: open ? 'auto' : 'none' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-main shrink-0">
          <h3 className="text-sm font-semibold text-text-main truncate">{title}</h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-bg-raised transition-colors text-text-muted hover:text-text-main"
          >
            <X size={18} />
          </button>
        </div>
        {/* Content — always mounted so children state survives close/open */}
        <div className="flex-1 overflow-hidden">
          {children}
        </div>
      </motion.div>
    </>
  )
}
