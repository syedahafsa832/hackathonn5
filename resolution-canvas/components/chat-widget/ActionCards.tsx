'use client'

import { motion } from 'framer-motion'
import { AiOrb } from './AiOrb'
import type { OrbState } from './types'

const ACTIONS = [
  { icon: '📦', label: 'Track Order',    message: 'Track my order' },
  { icon: '↩',  label: 'Return Item',   message: 'I want to return an item' },
  { icon: '💳', label: 'Refund Status', message: 'What is my refund status?' },
  { icon: '🚚', label: 'Shipping Issue', message: 'I have a shipping issue' },
] as const

type Props = {
  agentName: string
  accentColor: string
  orbState: OrbState
  onClose: () => void
  onSend: (message: string) => void
}

function ActionCard({
  icon,
  label,
  onClick,
}: {
  icon: string
  label: string
  onClick: () => void
}) {
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.97 }}
      style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '14px',
        padding: '16px 14px',
        cursor: 'pointer',
        textAlign: 'left',
        display: 'flex',
        flexDirection: 'column',
        gap: '7px',
        transition: 'border-color 0.15s, background 0.15s',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'rgba(99,102,241,0.5)'
        e.currentTarget.style.background = 'rgba(99,102,241,0.08)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'
        e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
      }}
    >
      <span style={{ fontSize: '20px', lineHeight: 1 }}>{icon}</span>
      <span
        style={{
          fontSize: '13px',
          fontWeight: 500,
          color: 'rgba(255,255,255,0.85)',
          lineHeight: 1.3,
        }}
      >
        {label}
      </span>
    </motion.button>
  )
}

export function ActionCards({ agentName, accentColor, orbState, onClose, onSend }: Props) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div
        style={{
          padding: '16px 20px 14px',
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
              color: 'rgba(255,255,255,0.38)',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              marginTop: '2px',
            }}
          >
            <span
              style={{
                width: '5px',
                height: '5px',
                borderRadius: '50%',
                background: '#10B981',
                display: 'inline-block',
              }}
            />
            Online · AI Support
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

      {/* Body */}
      <div style={{ padding: '24px 20px 22px' }}>
        <div style={{ marginBottom: '22px' }}>
          <div
            style={{
              fontSize: '18px',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.95)',
              letterSpacing: '-0.02em',
              marginBottom: '5px',
            }}
          >
            Hi there 👋
          </div>
          <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.45)', lineHeight: 1.5 }}>
            What can {agentName} help you with?
          </div>
        </div>

        {/* 2 × 2 grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '8px',
            marginBottom: '8px',
          }}
        >
          {ACTIONS.map(({ icon, label, message }) => (
            <ActionCard key={label} icon={icon} label={label} onClick={() => onSend(message)} />
          ))}
        </div>

        {/* Full-width fallback */}
        <motion.button
          onClick={() => onSend('I need help with something else')}
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.98 }}
          style={{
            width: '100%',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '14px',
            padding: '14px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'border-color 0.15s, background 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = 'rgba(99,102,241,0.5)'
            e.currentTarget.style.background = 'rgba(99,102,241,0.08)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'
            e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
          }}
        >
          <span style={{ fontSize: '18px', lineHeight: 1 }}>💬</span>
          <span style={{ fontSize: '13px', fontWeight: 500, color: 'rgba(255,255,255,0.85)' }}>
            Something Else
          </span>
        </motion.button>
      </div>
    </div>
  )
}
