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
    border: '1px solid rgba(16,185,129,0.2)',
    badgeColor: '#10B981',
  },
  cancel_staged: {
    icon: '✕',
    title: 'Cancellation Requested',
    badge: 'STAGED',
    bg: 'rgba(239,68,68,0.06)',
    border: '1px solid rgba(239,68,68,0.2)',
    badgeColor: '#EF4444',
  },
  address_updated: {
    icon: '📍',
    title: 'Address Updated',
    badge: 'DONE',
    bg: 'rgba(99,102,241,0.06)',
    border: '1px solid rgba(99,102,241,0.2)',
    badgeColor: '#6366F1',
  },
  restore_staged: {
    icon: '↩',
    title: 'Reship Requested',
    badge: 'STAGED',
    bg: 'rgba(245,158,11,0.06)',
    border: '1px solid rgba(245,158,11,0.2)',
    badgeColor: '#F59E0B',
  },
}

export function ActionResultCard({ data }: { data: ActionResult }) {
  const cfg = CONFIGS[data.type]
  if (!cfg) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      style={{
        background: cfg.bg,
        border: cfg.border,
        borderRadius: '14px',
        padding: '14px 16px',
        marginBottom: '8px',
        maxWidth: '86%',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ fontSize: '13px', color: cfg.badgeColor }}>{cfg.icon}</span>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>
            {cfg.title}
          </span>
        </div>
        <span style={{
          background: `${cfg.badgeColor}22`,
          border: `1px solid ${cfg.badgeColor}44`,
          color: cfg.badgeColor,
          fontSize: '10px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          borderRadius: '20px',
          padding: '2px 8px',
        }}>
          {cfg.badge}
        </span>
      </div>

      <div style={{ height: '1px', background: 'rgba(255,255,255,0.07)', marginBottom: '10px' }} />

      {data.type === 'refund_staged' && (
        <>
          {data.amount && (
            <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.75)', marginBottom: '4px' }}>
              {data.amount} → back to original method
            </div>
          )}
          <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)' }}>Awaiting merchant approval</div>
        </>
      )}

      {data.type === 'cancel_staged' && (
        <>
          {data.order_number && (
            <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.75)', marginBottom: '4px' }}>
              Order #{data.order_number}
            </div>
          )}
          <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)' }}>Awaiting merchant approval</div>
        </>
      )}

      {data.type === 'address_updated' && data.new_address && (
        <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.75)', lineHeight: 1.5 }}>
          {data.new_address}
        </div>
      )}

      {data.type === 'restore_staged' && (
        <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)' }}>Reship request sent for review</div>
      )}
    </motion.div>
  )
}
