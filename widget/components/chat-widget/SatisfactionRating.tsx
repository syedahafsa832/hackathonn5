'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { SatisfactionRatingValue } from './types'

type Props = {
  sessionId: string
  apiBaseUrl: string
}

const pillButton = {
  borderRadius: '20px',
  padding: '3px 11px',
  cursor: 'pointer',
  fontSize: '12px',
  fontWeight: 500,
  display: 'flex',
  alignItems: 'center',
  gap: '4px',
} as const

export function SatisfactionRating({ sessionId, apiBaseUrl }: Props) {
  // idle -> a rating was picked, showing the optional comment step -> sent.
  // Nothing is posted to the backend until 'sent' (either "Skip" or
  // "Send" is pressed) so a single request always carries the rating and
  // any comment together — never two partial inserts for one visit.
  const [step, setStep] = useState<'idle' | 'picked' | 'sent'>('idle')
  const [rating, setRating] = useState<SatisfactionRatingValue | null>(null)
  const [comment, setComment] = useState('')

  const pick = (value: SatisfactionRatingValue) => {
    setRating(value)
    setStep('picked')
  }

  const send = async () => {
    setStep('sent')
    if (apiBaseUrl === 'demo' || !rating) return
    try {
      await fetch(`${apiBaseUrl}/api/v2/widget/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          rating,
          feedback_text: comment.trim() || undefined,
        }),
      })
    } catch {
      // non-blocking — the customer already sees their feedback as sent
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      style={{
        background: 'rgba(255,255,255,0.035)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '14px',
        padding: '12px 14px',
        maxWidth: '86%',
        marginTop: '4px',
      }}
    >
      <AnimatePresence mode="wait">
        {step === 'idle' && (
          <motion.div
            key="idle"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}
          >
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)' }}>Was that helpful?</span>
            <button
              onClick={() => pick('positive')}
              style={{ ...pillButton, background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.22)', color: '#10B981' }}
            >
              👍 Yes, thanks
            </button>
            <button
              onClick={() => pick('negative')}
              style={{ ...pillButton, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.18)', color: '#EF4444' }}
            >
              👎 No
            </button>
          </motion.div>
        )}

        {step === 'picked' && (
          <motion.div
            key="picked"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}
          >
            <span style={{ fontSize: '11.5px', color: 'rgba(255,255,255,0.45)' }}>
              Anything else you'd like us to know? (optional)
            </span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Tell us more…"
              maxLength={1000}
              rows={2}
              style={{
                resize: 'none',
                fontFamily: 'inherit',
                fontSize: '12.5px',
                lineHeight: 1.5,
                color: 'rgba(255,255,255,0.9)',
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '10px',
                padding: '8px 10px',
                outline: 'none',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button
                onClick={send}
                style={{
                  fontSize: '11.5px',
                  color: 'rgba(255,255,255,0.45)',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  padding: '4px 6px',
                }}
              >
                Skip
              </button>
              <button
                onClick={send}
                style={{
                  fontSize: '12px',
                  fontWeight: 600,
                  color: '#fff',
                  background: 'var(--accent-color, #6366F1)',
                  border: 'none',
                  borderRadius: '8px',
                  padding: '6px 14px',
                  cursor: 'pointer',
                }}
              >
                Send
              </button>
            </div>
          </motion.div>
        )}

        {step === 'sent' && (
          <motion.div
            key="sent"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3 }}
            style={{ fontSize: '12px', color: 'rgba(255,255,255,0.4)', fontStyle: 'italic' }}
          >
            Thanks for your feedback!
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
