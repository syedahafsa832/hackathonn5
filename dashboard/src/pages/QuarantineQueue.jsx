import { useState, useEffect, useCallback } from 'react';
import client from '../api/client';

function formatAge(iso) {
  if (!iso) return '—';
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function pct(confidence) {
  if (confidence == null) return '—';
  return `${Math.round(confidence * 100)}%`;
}

const CLASSIFICATION_LABELS = {
  customer_support: 'Support',
  unknown: 'Unknown',
  promotion: 'Promo',
  newsletter: 'Newsletter',
  outreach: 'Outreach',
  spam: 'Spam',
  automation: 'Automation',
};

export default function QuarantineQueue() {
  const [items, setItems] = useState([]);
  const [pending, setPending] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [acting, setActing] = useState({});

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await client.get('/api/v1/quarantine?status=pending&limit=50');
      setItems(res.data.items || []);
      setPending(res.data.pending || 0);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load quarantine queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  const promote = async (id) => {
    setActing((a) => ({ ...a, [id]: 'promoting' }));
    try {
      await client.post(`/api/v1/quarantine/${id}/promote`);
      await fetchQueue();
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to promote email');
    } finally {
      setActing((a) => { const n = { ...a }; delete n[id]; return n; });
    }
  };

  const discard = async (id) => {
    if (!window.confirm('Discard this email? This cannot be undone.')) return;
    setActing((a) => ({ ...a, [id]: 'discarding' }));
    try {
      await client.post(`/api/v1/quarantine/${id}/discard`);
      await fetchQueue();
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to discard email');
    } finally {
      setActing((a) => { const n = { ...a }; delete n[id]; return n; });
    }
  };

  return (
    <div style={{ padding: '2rem', maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 700 }}>Quarantine Queue</h1>
        {pending > 0 && (
          <span style={{
            background: 'var(--warning, #f59e0b)',
            color: '#fff',
            borderRadius: 999,
            padding: '2px 10px',
            fontSize: '0.8rem',
            fontWeight: 700,
          }}>
            {pending} pending
          </span>
        )}
      </div>
      <p style={{ color: 'var(--text-muted, #888)', marginBottom: '1.5rem', marginTop: 0 }}>
        Emails the AI classified as <strong>customer_support</strong> but with confidence below your threshold.
        Review each email and either promote it to a ticket or discard it.
      </p>

      {loading && (
        <div style={{ color: 'var(--text-muted, #888)', padding: '3rem', textAlign: 'center' }}>
          Loading…
        </div>
      )}

      {error && (
        <div style={{
          background: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.3)',
          borderRadius: 8,
          padding: '1rem',
          color: 'var(--danger, #ef4444)',
          marginBottom: '1rem',
        }}>
          {error}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div style={{
          textAlign: 'center',
          padding: '4rem 2rem',
          color: 'var(--text-muted, #888)',
          border: '1px dashed var(--border, #333)',
          borderRadius: 12,
        }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>✓</div>
          <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>No emails in quarantine</div>
          <div style={{ fontSize: '0.875rem' }}>All inbound emails have been reviewed or cleared.</div>
        </div>
      )}

      {items.map((item) => {
        const busy = acting[item.id];
        return (
          <div
            key={item.id}
            style={{
              background: 'var(--surface, #1a1a2e)',
              border: '1px solid var(--border, #333)',
              borderRadius: 10,
              padding: '1.25rem 1.5rem',
              marginBottom: '1rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem' }}>
              <div>
                <div style={{ fontWeight: 600, marginBottom: '0.2rem' }}>
                  {item.subject || '(no subject)'}
                </div>
                <div style={{ fontSize: '0.85rem', color: 'var(--text-muted, #888)' }}>
                  From: <strong>{item.sender_email}</strong> &nbsp;·&nbsp; {formatAge(item.created_at)}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexShrink: 0 }}>
                <span style={{
                  background: 'rgba(245,158,11,0.15)',
                  color: 'var(--warning, #f59e0b)',
                  borderRadius: 6,
                  padding: '2px 8px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                }}>
                  {CLASSIFICATION_LABELS[item.ai_classification] || item.ai_classification || '—'} · {pct(item.ai_confidence)}
                </span>
              </div>
            </div>

            {item.body_preview && (
              <div style={{
                fontSize: '0.85rem',
                color: 'var(--text-secondary, #aaa)',
                background: 'rgba(255,255,255,0.03)',
                borderRadius: 6,
                padding: '0.5rem 0.75rem',
                maxHeight: 80,
                overflow: 'hidden',
                position: 'relative',
              }}>
                {item.body_preview}
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.25rem' }}>
              <button
                disabled={!!busy}
                onClick={() => promote(item.id)}
                style={{
                  background: busy === 'promoting' ? 'var(--text-muted, #888)' : 'var(--accent, #6366f1)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 6,
                  padding: '0.45rem 1rem',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  cursor: busy ? 'wait' : 'pointer',
                  opacity: busy ? 0.7 : 1,
                }}
              >
                {busy === 'promoting' ? 'Promoting…' : '✓ Promote to Ticket'}
              </button>
              <button
                disabled={!!busy}
                onClick={() => discard(item.id)}
                style={{
                  background: 'transparent',
                  color: 'var(--danger, #ef4444)',
                  border: '1px solid var(--danger, #ef4444)',
                  borderRadius: 6,
                  padding: '0.45rem 1rem',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  cursor: busy ? 'wait' : 'pointer',
                  opacity: busy ? 0.7 : 1,
                }}
              >
                {busy === 'discarding' ? 'Discarding…' : '✕ Discard'}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
