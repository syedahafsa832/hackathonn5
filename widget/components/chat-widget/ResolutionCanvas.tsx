'use client'

import { useState, useRef } from 'react'
import { AiOrb } from './AiOrb'
import { MessageThread } from './MessageThread'
import { ResolutionTracker } from './ResolutionTracker'
import { OrderContextCard } from './OrderContextCard'
import type { Message, OrbState, ResolutionStep, OrderData } from './types'

const ORB_STATUS: Record<OrbState, string> = {
  idle:      'Online · AI Support',
  thinking:  'Thinking…',
  acting:    'Taking action…',
  resolved:  'Resolved ✓',
  escalated: 'Escalating to human…',
  error:     'Connection issue',
}

type Props = {
  messages: Message[]
  orbState: OrbState
  resolutionSteps: ResolutionStep[]
  orderData: OrderData | null
  agentName: string
  accentColor: string
  sessionId: string
  apiBaseUrl: string
  hasSavedSession?: boolean
  onClose: () => void
  onSend: (message: string) => void
}

export function ResolutionCanvas({
  messages,
  orbState,
  resolutionSteps,
  orderData,
  agentName,
  accentColor,
  sessionId,
  apiBaseUrl,
  hasSavedSession = false,
  onClose,
  onSend,
}: Props) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const isLoading = messages.some((m) => m.isTyping)

  const handleSend = () => {
    const text = input.trim()
    if (!text || isLoading) return
    setInput('')
    // Reset textarea height
    if (inputRef.current) inputRef.current.style.height = '38px'
    onSend(text)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Compute a thin progress bar value for mobile
  const completedCount = resolutionSteps.filter((s) => s.status === 'complete').length
  const progressPct = Math.round((completedCount / resolutionSteps.length) * 100)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ── Header ── */}
      <div
        style={{
          padding: '13px 18px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          flexShrink: 0,
          background: 'linear-gradient(180deg, rgba(99,102,241,0.07) 0%, transparent 100%)',
        }}
      >
        <AiOrb state={orbState} size={28} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: '14px',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.95)',
              letterSpacing: '-0.01em',
            }}
          >
            {agentName}
          </div>
          <div
            style={{
              fontSize: '11px',
              color:
                orbState === 'resolved'
                  ? '#10B981'
                  : orbState === 'error'
                  ? '#EF4444'
                  : 'rgba(255,255,255,0.38)',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              marginTop: '2px',
              transition: 'color 0.3s',
            }}
          >
            {orbState === 'idle' && (
              <span
                style={{
                  width: '5px',
                  height: '5px',
                  borderRadius: '50%',
                  background: '#10B981',
                  display: 'inline-block',
                  flexShrink: 0,
                }}
              />
            )}
            {ORB_STATUS[orbState]}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.45)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '13px',
            transition: 'background 0.15s, color 0.15s',
            flexShrink: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'rgba(255,255,255,0.12)'
            e.currentTarget.style.color = '#fff'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'rgba(255,255,255,0.06)'
            e.currentTarget.style.color = 'rgba(255,255,255,0.45)'
          }}
          aria-label="Close"
        >
          ✕
        </button>
      </div>

      {/* ── Mobile progress bar (hidden on desktop via CSS) ── */}
      <div
        className="rc-mobile-progress"
        style={{
          display: 'none',
          height: '3px',
          background: 'rgba(255,255,255,0.07)',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${progressPct}%`,
            background: progressPct === 100 ? '#10B981' : '#6366F1',
            transition: 'width 0.5s ease, background 0.3s',
          }}
        />
      </div>

      {/* ── Body ── */}
      <div
        className="rc-body"
        style={{
          flex: 1,
          display: 'flex',
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        {/* Left: message thread */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <MessageThread
            messages={messages}
            agentName={agentName}
            accentColor={accentColor}
            sessionId={sessionId}
            apiBaseUrl={apiBaseUrl}
            hasSavedSession={hasSavedSession}
          />
        </div>

        {/* Right: resolution tracker + order card */}
        <div
          className="rc-right-col"
          style={{
            width: '150px',
            flexShrink: 0,
            borderLeft: '1px solid rgba(255,255,255,0.06)',
            padding: '16px 12px',
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
          }}
        >
          <ResolutionTracker steps={resolutionSteps} />
          {orderData && <OrderContextCard orderData={orderData} />}
        </div>
      </div>

      {/* ── Input bar ── */}
      <div
        style={{
          padding: '10px 14px 14px',
          borderTop: '1px solid rgba(255,255,255,0.07)',
          display: 'flex',
          gap: '8px',
          alignItems: 'flex-end',
          flexShrink: 0,
          background: 'linear-gradient(0deg, rgba(0,0,0,0.18) 0%, transparent 100%)',
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 90) + 'px'
          }}
          onKeyDown={handleKeyDown}
          placeholder={`Message ${agentName}…`}
          disabled={isLoading}
          maxLength={1000}
          rows={1}
          style={{
            flex: 1,
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '12px',
            padding: '9px 12px',
            fontSize: '13px',
            color: 'rgba(255,255,255,0.9)',
            fontFamily: 'inherit',
            resize: 'none',
            outline: 'none',
            lineHeight: '1.4',
            height: '38px',
            maxHeight: '90px',
            minHeight: '38px',
            transition: 'border-color 0.15s',
            scrollbarWidth: 'none',
          }}
          onFocus={(e) => {
            e.target.style.borderColor = 'rgba(99,102,241,0.55)'
          }}
          onBlur={(e) => {
            e.target.style.borderColor = 'rgba(255,255,255,0.1)'
          }}
        />
        <button
          onClick={handleSend}
          disabled={isLoading || !input.trim()}
          style={{
            width: '36px',
            height: '36px',
            flexShrink: 0,
            borderRadius: '10px',
            background: `linear-gradient(135deg, ${accentColor}, #8B5CF6)`,
            border: 'none',
            cursor: isLoading || !input.trim() ? 'default' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'opacity 0.15s, transform 0.1s',
            opacity: isLoading || !input.trim() ? 0.35 : 1,
            transform: 'scale(1)',
            boxShadow: `0 4px 14px ${accentColor}44`,
          }}
          onMouseEnter={(e) => {
            if (!isLoading && input.trim()) e.currentTarget.style.transform = 'scale(1.06)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'scale(1)'
          }}
          aria-label="Send"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
            <path
              d="M22 2L11 13"
              stroke="white"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d="M22 2L15 22L11 13L2 9L22 2Z"
              stroke="white"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </div>
  )
}
