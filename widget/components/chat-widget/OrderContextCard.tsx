'use client'

import type { OrderData } from './types'

const STATUS_COLORS: Record<OrderData['status'], string> = {
  fulfilled:  '#10B981',
  pending:    '#F59E0B',
  cancelled:  '#EF4444',
  processing: '#3B82F6',
}

const PAYMENT_LABEL: Record<OrderData['paymentStatus'], string> = {
  paid:    'Paid ✓',
  pending: 'Pending',
  refunded: 'Refunded',
}

type Props = { orderData: OrderData }

export function OrderContextCard({ orderData }: Props) {
  const statusColor = STATUS_COLORS[orderData.status]

  return (
    <div
      style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.07)',
        borderLeft: `2px solid ${statusColor}`,
        borderRadius: '10px',
        padding: '11px 12px',
        fontSize: '12px',
        color: 'rgba(255,255,255,0.75)',
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '8px',
        }}
      >
        <span
          style={{
            color: 'rgba(255,255,255,0.95)',
            fontWeight: 600,
            fontSize: '11px',
            letterSpacing: '0.04em',
          }}
        >
          ORDER #{orderData.orderNumber}
        </span>
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '3px',
            fontSize: '9px',
            letterSpacing: '0.08em',
            color: '#10B981',
            textTransform: 'uppercase',
          }}
        >
          <span
            style={{
              width: '5px',
              height: '5px',
              borderRadius: '50%',
              background: '#10B981',
              animation: 'live-dot-pulse 2s ease-in-out infinite',
              display: 'inline-block',
            }}
          />
          LIVE
        </span>
      </div>

      {/* Divider */}
      <div
        style={{
          borderTop: '1px solid rgba(255,255,255,0.07)',
          paddingTop: '8px',
          marginBottom: '8px',
        }}
      >
        {orderData.items.map((item, i) => (
          <div key={i} style={{ marginBottom: i < orderData.items.length - 1 ? '6px' : 0 }}>
            <div style={{ fontSize: '12px', fontWeight: 500, color: 'rgba(255,255,255,0.85)' }}>
              {item.name}
            </div>
            <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.35)', marginTop: '1px' }}>
              Qty {item.quantity} · {item.price}
            </div>
          </div>
        ))}
      </div>

      {/* Status */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '5px',
          marginBottom: '6px',
        }}
      >
        <div
          style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            background: statusColor,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            color: statusColor,
            fontSize: '11px',
            fontWeight: 500,
            textTransform: 'capitalize',
          }}
        >
          {orderData.status}
          {orderData.cancelledAt ? ` · ${orderData.cancelledAt}` : ''}
        </span>
      </div>

      {/* Payment */}
      <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.35)' }}>
        Payment:{' '}
        <span
          style={{
            color:
              orderData.paymentStatus === 'paid'
                ? '#10B981'
                : orderData.paymentStatus === 'refunded'
                ? '#3B82F6'
                : 'rgba(255,255,255,0.5)',
          }}
        >
          {PAYMENT_LABEL[orderData.paymentStatus]}
        </span>
      </div>
    </div>
  )
}
