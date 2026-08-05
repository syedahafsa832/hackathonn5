// Mirrors PricingCard's exact outer dimensions/padding so swapping the
// skeleton for the real card causes zero layout shift.
export default function PricingCardSkeleton() {
  return (
    <div style={{
      border: '1px solid var(--border-default)', borderRadius: '16px', padding: '28px',
      background: 'var(--bg-surface)', display: 'flex', flexDirection: 'column', gap: '14px',
      minHeight: '380px',
    }}>
      <div className="skeleton" style={{ width: '70px', height: '14px', borderRadius: '4px' }} />
      <div className="skeleton" style={{ width: '100px', height: '30px', borderRadius: '4px' }} />
      <div className="skeleton" style={{ width: '160px', height: '12px', borderRadius: '4px' }} />
      <div style={{ height: '1px', background: 'var(--border-subtle)', margin: '4px 0' }} />
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 8}%`, height: '11px', borderRadius: '4px' }} />
      ))}
      <div className="skeleton" style={{ marginTop: 'auto', height: '40px', borderRadius: '9px' }} />
    </div>
  );
}
