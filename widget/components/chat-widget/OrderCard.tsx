'use client'

import { motion } from 'framer-motion'
import type { OrderData } from './types'

const STATUS_COLORS: Record<string, string> = {
  fulfilled:   '#10B981',
  shipped:     '#10B981',
  processing:  '#F59E0B',
  unfulfilled: '#F59E0B',
  pending:     '#F59E0B',
  cancelled:   '#EF4444',
  refunded:    '#3B82F6',
  restocked:   '#6B7280',
}

function statusColor(s: string) {
  return STATUS_COLORS[s.toLowerCase()] ?? '#6B7280'
}

function statusLabel(s: string) {
  return s.replace(/_/g, ' ').toUpperCase()
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

export function OrderCard({ data }: { data: OrderData }) {
  const color = statusColor(data.status)

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      style={{
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderLeft: `3px solid ${color}`,
        borderRadius: '14px',
        padding: '14px 16px',
        marginBottom: '8px',
        maxWidth: '86%',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ fontSize: '13px' }}>🛍</span>
          <span style={{ fontSize: '12px', fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>
            Order #{data.orderNumber}
          </span>
        </div>
        <span style={{
          background: `${color}22`,
          border: `1px solid ${color}55`,
          color,
          fontSize: '10px',
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          borderRadius: '20px',
          padding: '2px 8px',
        }}>
          {statusLabel(data.status)}
        </span>
      </div>

      <div style={{ height: '1px', background: 'rgba(255,255,255,0.07)', marginBottom: '10px' }} />

      {/* Items */}
      {data.items.map((item, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            marginBottom: i < data.items.length - 1 ? '6px' : '0',
          }}
        >
          <div style={{ fontSize: '12px', fontWeight: 500, color: 'rgba(255,255,255,0.82)' }}>
            {item.name}
            {item.quantity > 1 && (
              <span style={{ color: 'rgba(255,255,255,0.38)', fontWeight: 400 }}> × {item.quantity}</span>
            )}
          </div>
          <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.5)', flexShrink: 0, marginLeft: '12px' }}>
            {item.price}
          </div>
        </div>
      ))}

      {/* Footer */}
      {(data.cancelledAt || data.tracking_url || (data.status === 'cancelled' && data.paymentStatus === 'paid')) && (
        <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
          {data.cancelledAt && (
            <div style={{ fontSize: '11px', color: '#EF4444', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span>✕</span>
              <span>Cancelled on {fmtDate(data.cancelledAt)}</span>
            </div>
          )}
          {data.status === 'cancelled' && data.paymentStatus === 'paid' && (
            <div style={{ fontSize: '11px', color: '#3B82F6', display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span>✓</span>
              <span>Refund in progress</span>
            </div>
          )}
          {data.tracking_url && (
            <a
              href={data.tracking_url}
              target="_blank"
              rel="noreferrer"
              style={{
                fontSize: '11px',
                color: '#10B981',
                textDecoration: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <span>📦</span>
              <span>Tracking available</span>
              <span style={{ fontSize: '10px' }}>↗</span>
            </a>
          )}
        </div>
      )}
    </motion.div>
  )
}
