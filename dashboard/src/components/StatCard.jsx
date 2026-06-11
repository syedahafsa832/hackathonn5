export default function StatCard({ label, value, subtitle, loading, isAi, labelColor, valueSize }) {
  return (
    <div style={{
      border: '1px solid #E4E4E7',
      borderLeft: isAi ? '3px solid #06B6D4' : '1px solid #E4E4E7',
      borderRadius: '8px',
      background: 'white',
      padding: '20px 24px',
      flex: '1',
      minWidth: '160px',
    }}>
      <div style={{ fontSize: '12px', color: labelColor || '#64748B', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px', fontWeight: '500' }}>
        {label}
      </div>
      {loading ? (
        <>
          <div className="skeleton" style={{ height: valueSize || '36px', width: '80px', marginBottom: '6px' }} />
          <div className="skeleton" style={{ height: '14px', width: '60px' }} />
        </>
      ) : (
        <>
          <div style={{
            fontFamily: 'DM Mono, monospace',
            fontSize: valueSize || '36px',
            fontWeight: '600',
            color: '#0F172A',
            lineHeight: '1',
            marginBottom: '4px',
          }}>
            {value ?? '—'}
          </div>
          {subtitle && (
            <div style={{ fontSize: '12px', color: '#94A3B8' }}>{subtitle}</div>
          )}
        </>
      )}
    </div>
  );
}
