import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import client from '../api/client';

const REASON_LABELS = {
  gmail_category:        'Gmail Category',
  blocked_sender_pattern:'Blocked Prefix',
  blocked_domain:        'Blocked Domain',
  auto_reply_header:     'Auto-Reply Header',
  promotional_content:   'Promotional Content',
  loop_risk:             'Loop Prevention',
  self_reply:            'Self-Reply',
  // Guardian reasons (feature 006)
  ai_classification:     'AI Classification',
  low_confidence:        'Low Confidence',
};

export default function FilteredEmailsWidget() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    client.get('/api/v1/filter-logs', { params: { summary: true, days: 7 }, signal: controller.signal })
      .then(res => { setData(res.data); setError(''); })
      .catch(err => {
        if (!controller.signal.aborted) {
          // 404 = no brand yet; timeout/network = backend still warming up → show empty state, not an error
          if (!err.response || err.response?.status === 404) {
            setData({ total_blocked: 0, total_allowed: 0, total_quarantined: 0, by_reason: {}, prevented_loops: 0 });
          } else {
            setError('Could not load filter data.');
          }
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  const cardStyle = {
    background: 'var(--bg-primary)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
    padding: '20px',
    minWidth: '260px',
    flex: '1',
  };

  if (loading) {
    return (
      <div style={cardStyle}>
        <div className="skeleton" style={{ height: '16px', width: '140px', marginBottom: '16px' }} />
        {[1,2,3].map(i => (
          <div key={i} className="skeleton" style={{ height: '12px', width: '80%', marginBottom: '8px' }} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div style={cardStyle}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          Filtered Emails
        </div>
        <div style={{ fontSize: '13px', color: 'var(--danger)' }}>{error}</div>
      </div>
    );
  }

  if (!data) return null;

  const { total_blocked = 0, total_allowed = 0, total_quarantined = 0, by_reason = {}, prevented_loops = 0 } = data;
  const total = total_blocked + total_allowed;

  return (
    <div style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Filtered Emails
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Last 7 days</div>
      </div>

      {total === 0 && total_quarantined === 0 ? (
        <div style={{ fontSize: '13px', color: 'var(--text-muted)', padding: '16px 0', textAlign: 'center' }}>
          No emails processed yet
        </div>
      ) : (
        <>
          {/* Summary counts */}
          <div style={{ display: 'flex', gap: '16px', marginBottom: '16px', flexWrap: 'wrap' }}>
            <div style={{ textAlign: 'center', flex: 1, padding: '10px', background: 'var(--bg-secondary)', borderRadius: '6px', minWidth: '60px' }}>
              <div style={{ fontSize: '22px', fontWeight: '700', color: 'var(--danger)' }}>{total_blocked}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Blocked</div>
            </div>
            <div style={{ textAlign: 'center', flex: 1, padding: '10px', background: 'var(--bg-secondary)', borderRadius: '6px', minWidth: '60px' }}>
              <div style={{ fontSize: '22px', fontWeight: '700', color: 'var(--success)' }}>{total_allowed}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Passed</div>
            </div>
            <div style={{ textAlign: 'center', flex: 1, padding: '10px', background: 'var(--bg-secondary)', borderRadius: '6px', minWidth: '60px' }}>
              <div style={{ fontSize: '22px', fontWeight: '700', color: 'var(--accent)' }}>{prevented_loops}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Loops Stopped</div>
            </div>
            <div style={{ textAlign: 'center', flex: 1, padding: '10px', background: total_quarantined > 0 ? 'rgba(245,158,11,0.1)' : 'var(--bg-secondary)', borderRadius: '6px', minWidth: '60px', border: total_quarantined > 0 ? '1px solid rgba(245,158,11,0.3)' : '1px solid transparent' }}>
              <div style={{ fontSize: '22px', fontWeight: '700', color: 'var(--warning, #f59e0b)' }}>{total_quarantined}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Quarantined</div>
            </div>
          </div>

          {total_quarantined > 0 && (
            <div style={{ marginBottom: '12px' }}>
              <Link
                to="/quarantine"
                style={{
                  fontSize: '13px',
                  color: 'var(--warning, #f59e0b)',
                  textDecoration: 'none',
                  fontWeight: '600',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '4px',
                  padding: '6px 12px',
                  background: 'rgba(245,158,11,0.1)',
                  borderRadius: '6px',
                  border: '1px solid rgba(245,158,11,0.3)',
                }}
              >
                Review Quarantine →
              </Link>
            </div>
          )}

          {/* Breakdown by reason */}
          {Object.keys(by_reason).length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {Object.entries(by_reason)
                .sort(([, a], [, b]) => b - a)
                .map(([reason, count]) => (
                  <div key={reason} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {REASON_LABELS[reason] || reason}
                    </span>
                    <span style={{
                      fontWeight: '600',
                      color: reason === 'loop_risk' ? 'var(--accent)' : 'var(--danger)',
                      background: reason === 'loop_risk' ? 'var(--accent-light)' : 'var(--danger-light)',
                      padding: '1px 7px',
                      borderRadius: '10px',
                      fontSize: '11px',
                    }}>
                      {count}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
