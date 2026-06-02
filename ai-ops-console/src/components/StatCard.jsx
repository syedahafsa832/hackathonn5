export default function StatCard({ label, value, subtitle, loading }) {
  return (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: '6px',
      background: 'var(--bg-primary)',
      padding: '20px 24px',
      flex: '1',
      minWidth: '160px',
    }}>
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px', fontWeight: '500' }}>
        {label}
      </div>
      {loading ? (
        <>
          <div className="skeleton" style={{ height: '28px', width: '80px', marginBottom: '6px' }} />
          <div className="skeleton" style={{ height: '14px', width: '60px' }} />
        </>
      ) : (
        <>
          <div style={{
            fontFamily: 'DM Mono, monospace',
            fontSize: '28px',
            fontWeight: '500',
            color: 'var(--text-primary)',
            lineHeight: '1',
            marginBottom: '4px',
          }}>
            {value ?? '—'}
          </div>
          {subtitle && (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{subtitle}</div>
          )}
        </>
      )}
    </div>
  );
}
