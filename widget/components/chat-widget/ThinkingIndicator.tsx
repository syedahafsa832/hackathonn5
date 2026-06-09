'use client'

import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

const STEPS_WITH_ORDER = [
  'Reading your message…',
  'Checking order details…',
  'Pulling Shopify data…',
  'Writing reply…',
]

const STEPS_DEFAULT = [
  'Reading your message…',
  'Writing reply…',
]

type Props = {
  userMessage?: string
}

export function ThinkingIndicator({ userMessage = '' }: Props) {
  const hasOrderNumber = /\b\d{3,6}\b/.test(userMessage)
  const steps = hasOrderNumber ? STEPS_WITH_ORDER : STEPS_DEFAULT

  const [stepIdx, setStepIdx] = useState(0)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const iv = setInterval(() => {
      setVisible(false)
      setTimeout(() => {
        setStepIdx((i) => (i + 1) % steps.length)
        setVisible(true)
      }, 300)
    }, 1400)
    return () => clearInterval(iv)
  }, [steps.length])

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '11px 14px',
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderRadius: '16px',
        borderBottomLeftRadius: '5px',
        width: 'fit-content',
      }}
    >
      {/* Dots */}
      <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
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

      {/* Step label */}
      <AnimatePresence mode="wait">
        {visible && (
          <motion.span
            key={stepIdx}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
            style={{
              fontSize: '11px',
              color: 'rgba(255,255,255,0.35)',
              fontStyle: 'italic',
              whiteSpace: 'nowrap',
            }}
          >
            {steps[stepIdx]}
          </motion.span>
        )}
      </AnimatePresence>
    </div>
  )
}
