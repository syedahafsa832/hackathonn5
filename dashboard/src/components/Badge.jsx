const STATUS_MAP = {
  // Channel badges
  chat:  { label: '💬 Chat', color: '#0891B2', bg: '#ECFEFF', border: '1px solid #A5F3FC', isChannel: true },
  email: { label: '✉ Email', color: '#64748B', bg: '#F8FAFC', border: '1px solid transparent', isChannel: true },
  
  // Status badges
  processing: { label: '⟳ Processing…', color: '#06B6D4', bg: '#ECFEFF' },
  auto_resolved: { label: 'Auto-resolved', color: '#10B981', bg: '#ECFDF5' },
  resolved: { label: 'Resolved', color: '#64748B', bg: '#F8FAFC' },
  escalated: { label: 'Escalated', color: '#F59E0B', bg: '#FFFBEB' },
  
  // Legacy / Other
  ai_suggested: { label: 'Draft ready', color: '#06B6D4', bg: '#ECFEFF' },
  human_managing: { label: 'Human managing', color: '#0891B2', bg: '#ECFEFF' },
  requires_human: { label: 'Needs human', color: '#F59E0B', bg: '#FFFBEB' },
  auto_resolved_review: { label: 'Review needed', color: '#F59E0B', bg: '#FFFBEB' },
  pending: { label: 'Pending', color: '#06B6D4', bg: '#ECFEFF' },
  open: { label: 'Open', color: '#06B6D4', bg: '#ECFEFF' },
  closed: { label: 'Closed', color: '#64748B', bg: '#F8FAFC' },
  
  // Action tags
  REFUND: { label: 'Refund', color: '#EF4444', bg: '#FEF2F2' },
  refund: { label: 'Refund', color: '#EF4444', bg: '#FEF2F2' },
  CANCEL: { label: 'Cancel', color: '#EF4444', bg: '#FEF2F2' },
  cancel_order: { label: 'Cancel', color: '#EF4444', bg: '#FEF2F2' },
  EXCHANGE: { label: 'Exchange', color: '#06B6D4', bg: '#ECFEFF' },
  exchange: { label: 'Exchange', color: '#06B6D4', bg: '#ECFEFF' },
  RESPOND: { label: 'Respond', color: '#10B981', bg: '#ECFDF5' },
  discount: { label: 'Discount', color: '#06B6D4', bg: '#ECFEFF' },
  change_address: { label: 'Address', color: '#64748B', bg: '#F8FAFC' },
};

export default function Badge({ status, size = 'sm' }) {
  const config = STATUS_MAP[status] || {
    label: status || 'Unknown',
    color: '#64748B',
    bg: '#F8FAFC',
  };

  const isChannel = config.isChannel;

  const baseStyle = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: '500',
    color: config.color,
    background: config.bg,
    whiteSpace: 'nowrap',
    borderRadius: '100px',
  };

  const specificStyle = isChannel ? {
    padding: '2px 8px',
    fontSize: '11px',
    border: config.border || '1px solid transparent',
  } : {
    padding: '3px 10px',
    fontSize: '12px',
    minWidth: '96px',
    textAlign: 'center',
    border: '1px solid transparent',
  };

  return (
    <span style={{ ...baseStyle, ...specificStyle }}>
      {config.label}
    </span>
  );
}
