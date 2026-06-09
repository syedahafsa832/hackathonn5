'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

type Props = {
  sessionId: string
  apiBaseUrl: string
}

export function SatisfactionRating({ sessionId, apiBaseUrl }: Props) {
  const [submitted, setSubmitted] = useState(false)
  const [showThanks, setShowThanks] = useState(false)

  const submit = async (rating: 'positive' | 'negative') => {
    setSubmitted(true)
    setShowThanks(true)
    setTimeout(() => setShowThanks(false), 2200)

    if (apiBaseUrl === 'demo') return
    try {
      await fetch(`${apiBaseUrl}/api/v2/widget/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, rating }),
      })
    } catch {
      // non-blocking
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '14px',
        padding: '10px 14px',
        maxWidth: '86%',
        marginTop: '4px',
      }}
    >
      <AnimatePresence mode="wait">
        {!submitted ? (
          <motion.div
            key="idle"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}
          >
            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)' }}>Was that helpful?</span>
            <button
              onClick={() => submit('positive')}
              style={{
                background: 'rgba(16,185,129,0.1)',
                border: '1px solid rgba(16,185,129,0.25)',
                borderRadius: '20px',
                padding: '3px 11px',
                cursor: 'pointer',
                fontSize: '12px',
                color: '#10B981',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              👍 Yes, thanks
            </button>
            <button
              onClick={() => submit('negative')}
              style={{
                background: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.2)',
                borderRadius: '20px',
                padding: '3px 11px',
                cursor: 'pointer',
                fontSize: '12px',
                color: '#EF4444',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              👎 No
            </button>
          </motion.div>
        ) : (
          <motion.div
            key="thanks"
            initial={{ opacity: 0 }}
            animate={{ opacity: showThanks ? 1 : 0 }}
            transition={{ duration: 0.3 }}
            style={{ fontSize: '12px', color: 'rgba(255,255,255,0.38)', fontStyle: 'italic' }}
          >
            Thanks for your feedback!
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
