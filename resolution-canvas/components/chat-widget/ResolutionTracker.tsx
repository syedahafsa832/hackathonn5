'use client'

import { motion, type Variants } from 'framer-motion'
import type { ResolutionStep } from './types'

const containerVariants: Variants = {
  hidden: {},
  visible: {
    transition: { staggerChildren: 0.15 },
  },
}

const itemVariants: Variants = {
  hidden: { opacity: 0, x: -10 },
  visible: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.3, ease: 'easeOut' },
  },
}

type Props = { steps: ResolutionStep[] }

export function ResolutionTracker({ steps }: Props) {
  return (
    <div>
      <div
        style={{
          fontSize: '9px',
          fontWeight: 600,
          letterSpacing: '0.09em',
          color: 'rgba(255,255,255,0.28)',
          textTransform: 'uppercase',
          marginBottom: '14px',
        }}
      >
        Resolution
      </div>

      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        style={{ display: 'flex', flexDirection: 'column' }}
      >
        {steps.map((step, i) => {
          const isComplete = step.status === 'complete'
          const isActive   = step.status === 'active'
          const dotColor   = isComplete ? '#10B981' : isActive ? '#6366F1' : 'transparent'
          const dotBorder  = isComplete ? '#10B981' : isActive ? '#6366F1' : 'rgba(255,255,255,0.2)'
          const lineColor  = isComplete ? '#10B981' : 'rgba(255,255,255,0.08)'

          return (
            <motion.div
              key={step.id}
              variants={itemVariants}
              style={{ display: 'flex', gap: '9px', position: 'relative' }}
            >
              {/* Connector line to next step */}
              {i < steps.length - 1 && (
                <div
                  style={{
                    position: 'absolute',
                    left: '5.5px',
                    top: '14px',
                    width: '1px',
                    height: 'calc(100% - 4px)',
                    background: lineColor,
                    minHeight: '24px',
                    transition: 'background 0.4s',
                  }}
                />
              )}

              {/* Dot */}
              <div style={{ position: 'relative', flexShrink: 0, marginTop: '1px' }}>
                <div
                  style={{
                    width: '12px',
                    height: '12px',
                    borderRadius: '50%',
                    background: dotColor,
                    border: `1.5px solid ${dotBorder}`,
                    transition: 'background 0.4s, border-color 0.4s',
                    position: 'relative',
                    zIndex: 1,
                  }}
                />
                {/* Pulsing ring for active step */}
                {isActive && (
                  <motion.div
                    animate={{
                      scale: [1, 2, 1],
                      opacity: [0.5, 0, 0.5],
                    }}
                    transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
                    style={{
                      position: 'absolute',
                      inset: '-3px',
                      borderRadius: '50%',
                      border: '1px solid #6366F1',
                      pointerEvents: 'none',
                    }}
                  />
                )}
              </div>

              {/* Text */}
              <div style={{ paddingBottom: i < steps.length - 1 ? '22px' : 0 }}>
                <div
                  style={{
                    fontSize: '11px',
                    fontWeight: isActive ? 600 : 400,
                    color: isComplete
                      ? 'rgba(255,255,255,0.3)'
                      : isActive
                      ? 'rgba(255,255,255,0.92)'
                      : 'rgba(255,255,255,0.28)',
                    textDecoration: isComplete ? 'line-through' : 'none',
                    lineHeight: 1.35,
                    transition: 'color 0.3s',
                  }}
                >
                  {step.label}
                  {isComplete && ' ✓'}
                </div>
                {step.detail && !isComplete && (
                  <div
                    style={{
                      fontSize: '10px',
                      color: 'rgba(255,255,255,0.22)',
                      marginTop: '2px',
                      lineHeight: 1.3,
                    }}
                  >
                    {step.detail}
                  </div>
                )}
              </div>
            </motion.div>
          )
        })}
      </motion.div>
    </div>
  )
}
