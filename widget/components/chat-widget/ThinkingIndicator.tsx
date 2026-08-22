'use client'

import { motion, AnimatePresence } from 'framer-motion'
import type { ActivityStep } from './types'

type Props = {
  /** Real resolution steps reported by the backend so far, in order — never
   * a fabricated sequence. Empty until the first real status event arrives. */
  steps: ActivityStep[]
  /** True once this turn has been running noticeably longer than usual — shows a generic reassurance, never a fake specific action. */
  slow?: boolean
}

/** Small teal checkmark for a completed step. */
function CompleteDot() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" style={{ flexShrink: 0 }}>
      <circle cx="8" cy="8" r="8" fill="#10B981" />
      <path d="M4.5 8.2L6.8 10.5L11.5 5.5" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** Pulsing indigo ring for the current, in-progress step. */
function ActiveDot() {
  return (
    <span style={{ position: 'relative', width: '11px', height: '11px', flexShrink: 0, display: 'inline-block' }}>
      <span
        style={{
          position: 'absolute', inset: 0, borderRadius: '50%',
          border: '1.5px solid var(--accent-color, #6366F1)', background: 'rgba(99,102,241,0.15)',
        }}
      />
      <motion.span
        animate={{ scale: [1, 1.9, 1], opacity: [0.6, 0, 0.6] }}
        transition={{ duration: 1.3, repeat: Infinity, ease: 'easeInOut' }}
        style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '1.5px solid var(--accent-color, #6366F1)' }}
      />
    </span>
  )
}

export function ThinkingIndicator({ steps, slow = false }: Props) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '7px',
        padding: '11px 14px',
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: '16px',
        borderBottomLeftRadius: '5px',
        width: 'fit-content',
        minWidth: '180px',
        maxWidth: '86%',
      }}
    >
      {steps.length === 0 ? (
        // Nothing real to report yet — bouncing dots only, never a guessed
        // first stage.
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              animate={{ y: [0, -5, 0] }}
              transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.18, ease: 'easeInOut' }}
              style={{ width: '5px', height: '5px', borderRadius: '50%', background: 'rgba(255,255,255,0.35)', display: 'block' }}
            />
          ))}
        </div>
      ) : (
        <AnimatePresence initial={false}>
          {steps.map((step, i) => {
            const isLast = i === steps.length - 1
            return (
              <motion.div
                key={step.stage}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
              >
                {isLast ? <ActiveDot /> : <CompleteDot />}
                <span
                  style={{
                    fontSize: '11px',
                    color: isLast ? 'rgba(255,255,255,0.75)' : 'rgba(255,255,255,0.38)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {step.label}
                </span>
              </motion.div>
            )
          })}
        </AnimatePresence>
      )}

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
