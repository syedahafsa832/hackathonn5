'use client'

import { motion, type Variants } from 'framer-motion'
import type { OrbState } from './types'

const STATE_COLORS: Record<OrbState, string> = {
  idle:      '#6366F1',
  thinking:  '#3B82F6',
  acting:    '#8B5CF6',
  resolved:  '#10B981',
  escalated: '#F59E0B',
  error:     '#EF4444',
}

const innerVariants: Variants = {
  idle: {
    scale: [0.95, 1.05, 0.95],
    transition: { duration: 3, repeat: Infinity, ease: 'easeInOut' },
  },
  thinking: {
    scale: [1, 1.18, 1],
    transition: { duration: 0.8, repeat: Infinity, ease: 'easeInOut' },
  },
  acting: {
    scale: [1, 1.12, 1],
    transition: { duration: 1.2, repeat: Infinity, ease: 'easeInOut' },
  },
  resolved: {
    scale: [1, 1.45, 1],
    transition: { duration: 0.5, ease: 'easeOut' },
  },
  escalated: {
    scale: [1, 1.1, 1],
    transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
  },
  error: {
    x: [-2, 2, -2, 2, 0],
    scale: 1,
    transition: { duration: 0.35, ease: 'easeInOut' },
  },
}

const outerRingVariants: Variants = {
  idle:      { scale: 1, opacity: 0, transition: { duration: 0.4 } },
  thinking:  {
    scale: [1, 1.7, 1],
    opacity: [0.3, 0, 0.3],
    transition: { duration: 0.8, repeat: Infinity },
  },
  acting: {
    scale: [1, 1.9, 1],
    opacity: [0.2, 0, 0.2],
    transition: { duration: 1.2, repeat: Infinity },
  },
  resolved: {
    scale: [1, 2.2, 1.6],
    opacity: [0.45, 0, 0],
    transition: { duration: 0.6, ease: 'easeOut' },
  },
  escalated: {
    scale: [1, 1.5, 1],
    opacity: [0.2, 0, 0.2],
    transition: { duration: 2, repeat: Infinity },
  },
  error: { scale: 1, opacity: 0, transition: { duration: 0.2 } },
}

type Props = {
  state: OrbState
  size?: number
}

export function AiOrb({ state, size = 32 }: Props) {
  const color = STATE_COLORS[state]
  const midInset = Math.max(2, Math.round(size * 0.1))
  const innerInset = Math.max(4, Math.round(size * 0.2))

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      {/* Outer bloom ring */}
      <motion.div
        variants={outerRingVariants}
        animate={state}
        style={{
          position: 'absolute',
          inset: -8,
          borderRadius: '50%',
          border: `1px solid ${color}`,
          pointerEvents: 'none',
        }}
      />

      {/* Middle ring (always visible at 40% opacity) */}
      <div
        style={{
          position: 'absolute',
          inset: midInset,
          borderRadius: '50%',
          border: `1px solid ${color}`,
          opacity: 0.4,
          transition: 'border-color 0.5s',
        }}
      />

      {/* Animated inner fill */}
      <motion.div
        variants={innerVariants}
        animate={state}
        style={{
          position: 'absolute',
          inset: innerInset,
          borderRadius: '50%',
          background: `radial-gradient(circle at 35% 30%, ${color}ee, ${color}77)`,
          boxShadow: `0 0 ${Math.round(size * 0.5)}px ${color}55`,
          transition: 'background 0.5s, box-shadow 0.5s',
        }}
      />

      {/* Rotating dashed ring — only in thinking state */}
      {state === 'thinking' && (
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ duration: 1.5, repeat: Infinity, ease: 'linear' }}
          style={{
            position: 'absolute',
            inset: -3,
            borderRadius: '50%',
            border: `1.5px dashed ${color}66`,
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}
