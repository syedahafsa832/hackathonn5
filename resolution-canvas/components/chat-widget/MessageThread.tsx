'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ConfidenceBadge } from './ConfidenceBadge'
import { OrderCard } from './OrderCard'
import { ActionResultCard } from './ActionResultCard'
import { ThinkingIndicator } from './ThinkingIndicator'
import { SatisfactionRating } from './SatisfactionRating'
import type { Message } from './types'

type Props = {
  messages: Message[]
  agentName: string
  accentColor: string
  sessionId: string
  apiBaseUrl: string
  hasSavedSession?: boolean
}

export function MessageThread({
  messages,
  agentName,
  accentColor,
  sessionId,
  apiBaseUrl,
  hasSavedSession = false,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [ratingShown, setRatingShown] = useState(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Find the last user message text (for ThinkingIndicator)
  const lastUserText = [...messages].reverse().find((m) => m.role === 'user' && !m.isTyping)?.text ?? ''

  // Find last assistant message with resolutionComplete flag
  const lastResolutionIdx = messages.reduce((acc, m, i) =>
    m.role === 'assistant' && m.resolutionComplete ? i : acc, -1)

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
      }}
    >
      {/* "— Earlier today —" divider for restored sessions */}
      {hasSavedSession && messages.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '2px 0' }}>
          <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.07)' }} />
          <span style={{ fontSize: '10px', color: 'rgba(255,255,255,0.25)', whiteSpace: 'nowrap' }}>
            — Earlier today —
          </span>
          <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.07)' }} />
        </div>
      )}

      <AnimatePresence initial={false}>
        {messages.map((msg, idx) => {
          const isUser = msg.role === 'user'

          return (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: isUser ? 'flex-end' : 'flex-start',
              }}
            >
              {/* Agent name label */}
              {!isUser && !msg.isTyping && (
                <span
                  style={{
                    fontSize: '10px',
                    color: 'rgba(255,255,255,0.28)',
                    paddingLeft: '2px',
                    marginBottom: '4px',
                    fontWeight: 300,
                    letterSpacing: '0.02em',
                  }}
                >
                  {agentName}
                </span>
              )}

              {/* Order card — renders above text bubble for assistant */}
              {!isUser && msg.orderData && (
                <OrderCard data={msg.orderData} />
              )}

              {/* Action result card — renders above text bubble */}
              {!isUser && msg.actionResult && (
                <ActionResultCard data={msg.actionResult} />
              )}

              {/* Bubble or thinking indicator */}
              {msg.isTyping ? (
                <ThinkingIndicator userMessage={lastUserText} />
              ) : (
                <div
                  style={{
                    maxWidth: '86%',
                    padding: '10px 14px',
                    borderRadius: '16px',
                    fontSize: '13px',
                    lineHeight: '1.55',
                    fontWeight: 400,
                    ...(isUser
                      ? {
                          background: `linear-gradient(135deg, ${accentColor}, #8B5CF6)`,
                          borderBottomRightRadius: '5px',
                          color: '#fff',
                          boxShadow: `0 4px 18px ${accentColor}44`,
                        }
                      : {
                          background: 'rgba(255,255,255,0.055)',
                          border: '1px solid rgba(255,255,255,0.08)',
                          borderBottomLeftRadius: '5px',
                          color: 'rgba(255,255,255,0.9)',
                        }),
                  }}
                >
                  {msg.text.split('\n').map((line, i, arr) => (
                    <span key={i}>
                      {line}
                      {i < arr.length - 1 && <br />}
                    </span>
                  ))}
                </div>
              )}

              {/* Confidence badge */}
              {!isUser && !msg.isTyping && (
                <ConfidenceBadge confidence={msg.confidence} />
              )}

              {/* Satisfaction rating — shows once after resolution_complete */}
              {!isUser && !msg.isTyping && msg.resolutionComplete && idx === lastResolutionIdx && !ratingShown && (
                <div onClick={() => setRatingShown(true)}>
                  <SatisfactionRating sessionId={sessionId} apiBaseUrl={apiBaseUrl} />
                </div>
              )}
            </motion.div>
          )
        })}
      </AnimatePresence>

      <div ref={bottomRef} />
    </div>
  )
}
