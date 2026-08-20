'use client'

import { motion, AnimatePresence } from 'framer-motion'

type Props = {
  /** Current real activity label from the backend (e.g. "Looking up your order…"). */
  label: string
  /** True once this turn has been running noticeably longer than usual — shows a generic reassurance, never a fake specific action. */
  slow?: boolean
}

export function ThinkingIndicator({ label, slow = false }: Props) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        padding: '11px 14px',
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: '16px',
        borderBottomLeftRadius: '5px',
        width: 'fit-content',
        maxWidth: '86%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {/* Dots */}
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center', flexShrink: 0 }}>
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              animate={{ y: [0, -5, 0] }}
              transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.18, ease: 'easeInOut' }}
              style={{
                width: '5px',
                height: '5px',
                borderRadius: '50%',
                background: 'rgba(255,255,255,0.35)',
                display: 'block',
              }}
            />
          ))}
        </div>

        {/* Live activity label — reflects what the backend is actually doing */}
        <AnimatePresence mode="wait">
          <motion.span
            key={label}
            initial={{ opacity: 0, y: 2 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            style={{
              fontSize: '11px',
              color: 'rgba(255,255,255,0.4)',
              fontStyle: 'italic',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </motion.span>
        </AnimatePresence>
      </div>

      {slow && (
        <motion.span
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
          style={{
            fontSize: '10px',
            color: 'rgba(255,255,255,0.25)',
            paddingLeft: '19px',
          }}
        >
          Still working on it — thanks for your patience
        </motion.span>
      )}
    </div>
  )
}
