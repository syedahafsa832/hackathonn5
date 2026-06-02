const STATUS_MAP = {
  processing: { label: '⟳ Processing…', color: 'var(--text-secondary)', bg: 'var(--bg-tertiary)' },
  auto_resolved: { label: 'Auto-resolved', color: 'var(--success)', bg: 'var(--success-light)' },
  resolved: { label: 'Resolved', color: 'var(--success)', bg: 'var(--success-light)' },
  ai_suggested: { label: 'Draft ready', color: 'var(--accent)', bg: 'var(--accent-light)' },
  human_managing: { label: 'Human managing', color: '#7C3AED', bg: '#F5F3FF' },
  requires_human: { label: 'Needs human', color: 'var(--warning)', bg: 'var(--warning-light)' },
  escalated: { label: 'Escalated', color: 'var(--warning)', bg: 'var(--warning-light)' },
  auto_resolved_review: { label: 'Review needed', color: '#2563EB', bg: '#EFF6FF' },
  pending: { label: 'Pending', color: 'var(--accent)', bg: 'var(--accent-light)' },
  open: { label: 'Open', color: 'var(--accent)', bg: 'var(--accent-light)' },
  closed: { label: 'Closed', color: 'var(--text-muted)', bg: 'var(--bg-tertiary)' },
  REFUND: { label: 'Refund', color: 'var(--danger)', bg: 'var(--danger-light)' },
  refund: { label: 'Refund', color: 'var(--danger)', bg: 'var(--danger-light)' },
  CANCEL: { label: 'Cancel', color: 'var(--warning)', bg: 'var(--warning-light)' },
  cancel_order: { label: 'Cancel', color: 'var(--warning)', bg: 'var(--warning-light)' },
  EXCHANGE: { label: 'Exchange', color: '#2563EB', bg: '#EFF6FF' },
  exchange: { label: 'Exchange', color: '#2563EB', bg: '#EFF6FF' },
  RESPOND: { label: 'Respond', color: 'var(--success)', bg: 'var(--success-light)' },
  discount: { label: 'Discount', color: '#7C3AED', bg: '#F5F3FF' },
  change_address: { label: 'Address', color: 'var(--text-secondary)', bg: 'var(--bg-tertiary)' },
};

export default function Badge({ status, size = 'sm' }) {
  const config = STATUS_MAP[status] || {
    label: status || 'Unknown',
    color: 'var(--text-muted)',
    bg: 'var(--bg-tertiary)',
  };

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: size === 'sm' ? '2px 8px' : '4px 10px',
      borderRadius: '4px',
      fontSize: size === 'sm' ? '12px' : '13px',
      fontWeight: '500',
      color: config.color,
      background: config.bg,
      whiteSpace: 'nowrap',
    }}>
      {config.label}
    </span>
  );
}
