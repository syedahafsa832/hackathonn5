'use client'

import { motion } from 'framer-motion'
import type { ActionResult } from './types'

type Config = {
  icon: string
  title: string
  badge: string
  bg: string
  border: string
  badgeColor: string
}

const CONFIGS: Record<ActionResult['type'], Config> = {
  refund_staged: {
    icon: '✓',
    title: 'Refund Requested',
    badge: 'STAGED',
    bg: 'rgba(16,185,129,0.06)',
    border: '1px solid rgba(16,185,129,0.18)',
    badgeColor: '#10B981',
  },
  cancel_staged: {
    icon: '✕',
    title: 'Cancellation Requested',
    badge: 'STAGED',
    bg: 'rgba(239,68,68,0.06)',
    border: '1px solid rgba(239,68,68,0.18)',
    badgeColor: '#EF4444',
  },
  address_updated: {
    icon: '📍',
    title: 'Address Updated',
    badge: 'DONE',
    bg: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    badgeColor: 'var(--accent-color, #6366F1)',
  },
  restore_staged: {
    icon: '↩',
    title: 'Reship Requested',
    badge: 'STAGED',
    bg: 'rgba(245,158,11,0.06)',
    border: '1px solid rgba(245,158,11,0.18)',
    badgeColor: '#F59E0B',
  },
  exchange_staged: {
    icon: '⇄',
    title: 'Exchange Requested',
    badge: 'STAGED',
    bg: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    badgeColor: 'var(--accent-color, #6366F1)',
  },
}

// Cancellation only, once the real merchant-approved outcome is known
// (learned by polling, never shown optimistically — see ChatWidget.tsx).
const CANCEL_RESOLVED_CONFIG: Record<'executed' | 'failed', Config> = {
  executed: {
    icon: '✓',
    title: 'Order Cancelled',
    badge: 'CONFIRMED',
    bg: 'rgba(16,185,129,0.06)',
    border: '1px solid rgba(16,185,129,0.18)',
    badgeColor: '#10B981',
  },
  failed: {
    icon: '!',
    title: "Couldn't Complete Automatically",
    badge: 'NEEDS FOLLOW-UP',
    bg: 'rgba(239,68,68,0.06)',
    border: '1px solid rgba(239,68,68,0.18)',
    badgeColor: '#EF4444',
  },
}

const cardBase = {
  borderRadius: '14px',
  padding: '14px 16px',
  marginBottom: '8px',
  maxWidth: '86%',
}

const rowLabel = { fontSize: '12px', color: 'rgba(255,255,255,0.72)', marginBottom: '4px' }
const rowMuted = { fontSize: '11px', color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }

function Header({ cfg }: { cfg: Config }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ fontSize: '13px', color: cfg.badgeColor }}>{cfg.icon}</span>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>{cfg.title}</span>
        </div>
        <span
          style={{
            background: `${cfg.badgeColor}1f`,
            border: `1px solid ${cfg.badgeColor}40`,
            color: cfg.badgeColor,
            fontSize: '10px',
            fontWeight: 700,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            borderRadius: '20px',
            padding: '2px 8px',
          }}
        >
          {cfg.badge}
        </span>
      </div>
      <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', marginBottom: '10px' }} />
    </>
  )
}

export function ActionResultCard({ data }: { data: ActionResult }) {
  // Cancellation with a confirmed outcome takes over the whole card —
  // everything else falls through to the original staged-state rendering.
  if (data.type === 'cancel_staged' && (data.status === 'executed' || data.status === 'failed')) {
    const cfg = CANCEL_RESOLVED_CONFIG[data.status]
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: 'easeOut' }}
        style={{ ...cardBase, background: cfg.bg, border: cfg.border }}
      >
        <Header cfg={cfg} />
        {data.order_number && <div style={rowLabel}>Order #{data.order_number}</div>}
        <div style={rowMuted}>
          {data.status === 'executed'
            ? 'Your order has been cancelled. If you paid by card, your refund will appear in your account within 3–5 business days.'
            : "We couldn't complete this automatically — our team has been notified and will follow up with you by email shortly."}
        </div>
      </motion.div>
    )
  }

  const cfg = CONFIGS[data.type]
  if (!cfg) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      style={{ ...cardBase, background: cfg.bg, border: cfg.border }}
    >
      <Header cfg={cfg} />

      {data.type === 'refund_staged' && (
        <>
          {data.amount && <div style={rowLabel}>{data.amount} → back to original method</div>}
          <div style={rowMuted}>Awaiting merchant approval</div>
        </>
      )}

      {data.type === 'cancel_staged' && (
        <>
          {data.order_number && <div style={rowLabel}>Order #{data.order_number}</div>}
          <div style={rowMuted}>Awaiting merchant approval</div>
        </>
      )}

      {data.type === 'address_updated' && data.new_address && (
        <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.72)', lineHeight: 1.5 }}>{data.new_address}</div>
      )}

      {data.type === 'restore_staged' && <div style={rowMuted}>Reship request sent for review</div>}

      {data.type === 'exchange_staged' && (
        <>
          {data.order_number && <div style={rowLabel}>Order #{data.order_number}</div>}
          <div style={rowMuted}>Awaiting merchant approval</div>
        </>
      )}
    </motion.div>
  )
}
