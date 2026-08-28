import { useState, useEffect, useCallback } from 'react';
import client from '../api/client';
import Alert from '../components/Alert';

function formatAge(iso) {
  if (!iso) return '-';
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function pct(confidence) {
  if (confidence == null) return '-';
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
  const [actionError, setActionError] = useState('');
  // Set only after the real API call has already succeeded - never before,
  // never speculative. The item stays on screen showing this confirmation
  // for a moment purely so the merchant can read it before it leaves the
  // list; that display window is not a fake loading state (the operation
  // is already done by the time this is set).
  const [succeeded, setSucceeded] = useState({}); // { [id]: 'promoted' | 'discarded' }
  const SUCCESS_DISPLAY_MS = 1400;

  const removeAfterSuccess = (id) => {
    setTimeout(() => {
      setItems((prev) => prev.filter((it) => it.id !== id));
      setSucceeded((s) => { const n = { ...s }; delete n[id]; return n; });
    }, SUCCESS_DISPLAY_MS);
  };

  useEffect(() => {
    document.title = "Quarantine: tResolv";
  }, []);

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

  // Both actions used to re-fetch the entire queue after succeeding, which
  // (a) made the button sit on "Promoting…"/"Discarding…" for two sequential
  // round trips instead of one, and (b) meant a transient failure on that
  // second fetch set the page-level error state, which hid the whole list
  // (including the item that had just been successfully acted on) behind
  // "Failed to load quarantine queue". The action's own response already
  // tells us the item is gone from the queue — remove it locally instead of
  // re-fetching to find that out.
  const promote = async (id) => {
    setActing((a) => ({ ...a, [id]: 'promoting' }));
    setActionError('');
    try {
      await client.post(`/api/v1/quarantine/${id}/promote`);
      setSucceeded((s) => ({ ...s, [id]: 'promoted' }));
      setPending((p) => Math.max(0, p - 1));
      removeAfterSuccess(id);
    } catch (e) {
      setActionError(e.response?.data?.detail || 'Failed to promote email');
    } finally {
      setActing((a) => { const n = { ...a }; delete n[id]; return n; });
    }
  };

  const discard = async (id) => {
    if (!window.confirm('Discard this email? This cannot be undone.')) return;
    setActing((a) => ({ ...a, [id]: 'discarding' }));
    setActionError('');
    try {
      await client.post(`/api/v1/quarantine/${id}/discard`);
      setSucceeded((s) => ({ ...s, [id]: 'discarded' }));
      setPending((p) => Math.max(0, p - 1));
      removeAfterSuccess(id);
    } catch (e) {
      setActionError(e.response?.data?.detail || 'Failed to discard email');
    } finally {
      setActing((a) => { const n = { ...a }; delete n[id]; return n; });
    }
  };

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: '#0F172A' }}>Quarantine Queue</h2>
        {pending > 0 && (
          <span style={{
            background: '#F59E0B',
            color: '#fff',
            borderRadius: '10px',
            padding: '2px 10px',
            fontSize: '12px',
            fontWeight: '700',
            fontFamily: 'DM Mono, monospace',
          }}>
            {pending} pending
          </span>
        )}
      </div>
      <p style={{ color: '#64748B', fontSize: '13px', margin: 0, maxWidth: '640px' }}>
        Emails the AI classified as <strong>customer_support</strong> but with confidence below your threshold.
        Review each email and either promote it to a ticket or discard it.
      </p>

      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '110px', borderRadius: '8px' }} />)}
        </div>
      )}

      <Alert variant="error">{error}</Alert>
      <Alert variant="error" onDismiss={() => setActionError('')} autoDismissMs={5000}>{actionError}</Alert>

      {!loading && !error && items.length === 0 && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          padding: '60px 24px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', gap: '12px',
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '50%', background: '#ECFEFF', color: '#06B6D4', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px' }}>✓</div>
          <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B' }}>No emails in quarantine</div>
          <div style={{ fontSize: '13px', color: '#94A3B8', textAlign: 'center' }}>All inbound emails have been reviewed or cleared.</div>
        </div>
      )}

      {!loading && !error && items.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {items.map((item) => {
            const busy = acting[item.id];
            const done = succeeded[item.id]; // set only after the API call already succeeded
            return (
              <div
                key={item.id}
                style={{
                  background: 'white',
                  border: '1px solid #E4E4E7',
                  borderRadius: '8px',
                  padding: '20px 24px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '10px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A', marginBottom: '2px' }}>
                      {item.subject || '(no subject)'}
                    </div>
                    <div style={{ fontSize: '12px', color: '#64748B' }}>
                      From: <strong style={{ color: '#475569' }}>{item.sender_email}</strong>
                      <span style={{ color: '#CBD5E1' }}> · </span>
                      <span style={{ fontFamily: 'DM Mono, monospace' }}>{formatAge(item.created_at)}</span>
                    </div>
                  </div>
                  <span style={{
                    background: '#FFFBEB',
                    border: '1px solid #FDE68A',
                    color: '#B45309',
                    borderRadius: '10px',
                    padding: '2px 9px',
                    fontSize: '11px',
                    fontWeight: '600',
                    flexShrink: 0,
                  }}>
                    {CLASSIFICATION_LABELS[item.ai_classification] || item.ai_classification || '-'} · {pct(item.ai_confidence)}
                  </span>
                </div>

                {item.body_preview && (
                  <div style={{
                    fontSize: '13px',
                    color: '#475569',
                    background: '#F8FAFC',
                    borderRadius: '6px',
                    padding: '10px 12px',
                    lineHeight: '1.5',
                  }}>
                    {item.body_preview}
                  </div>
                )}

                {done ? (
                  <div style={{
                    fontSize: '13px', fontWeight: '600', color: '#059669',
                    background: '#ECFDF5', border: '1px solid #A7F3D0',
                    borderRadius: '6px', padding: '8px 12px', marginTop: '2px',
                  }}>
                    {done === 'promoted' ? '✓ Promoted to Conversations' : '✓ Discarded'}
                  </div>
                ) : (
                  <div style={{ display: 'flex', gap: '8px', marginTop: '2px' }}>
                    <button
                      disabled={!!busy}
                      onClick={() => promote(item.id)}
                      style={{
                        padding: '7px 16px',
                        borderRadius: '4px',
                        border: '1px solid transparent',
                        background: '#06B6D4',
                        color: 'white',
                        fontSize: '13px',
                        fontWeight: '600',
                        cursor: busy ? 'not-allowed' : 'pointer',
                        opacity: busy ? 0.6 : 1,
                      }}
                    >
                      {busy === 'promoting' ? 'Promoting…' : '✓ Promote to Ticket'}
                    </button>
                    <button
                      disabled={!!busy}
                      onClick={() => discard(item.id)}
                      style={{
                        padding: '7px 16px',
                        borderRadius: '4px',
                        background: 'white',
                        color: '#EF4444',
                        border: '1px solid #FECACA',
                        fontSize: '13px',
                        fontWeight: '600',
                        cursor: busy ? 'not-allowed' : 'pointer',
                        opacity: busy ? 0.6 : 1,
                      }}
                    >
                      {busy === 'discarding' ? 'Discarding…' : '✕ Discard'}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
