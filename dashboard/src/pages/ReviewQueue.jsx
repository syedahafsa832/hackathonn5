import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useReviewQueue, useSubmitTicketReview } from '../hooks/useApi';

const TABS = [
  { key: '', label: 'All' },
  { key: 'needs_review', label: 'Needs Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'edited', label: 'Edited' },
  { key: 'rejected', label: 'Rejected' },
];

const STATUS_BADGE = {
  needs_review: { label: 'Needs Review', color: '#B45309', bg: '#FFFBEB', border: '#FDE68A' },
  approved: { label: 'Approved', color: '#059669', bg: '#ECFDF5', border: '#A7F3D0' },
  edited: { label: 'Edited', color: '#0E7490', bg: '#ECFEFF', border: '#A5F3FC' },
  rejected: { label: 'Rejected', color: '#B91C1C', bg: '#FEF2F2', border: '#FECACA' },
};

// Deterministic, fixed vocabulary offered to the merchant — never classified
// by an LLM. Mirrors backend REJECTION_REASONS in tickets.py.
const REJECTION_REASONS = ['Wrong tone', 'Wrong information', 'Missing information', 'Policy issue', 'Too verbose', 'Other'];

function excerpt(text, max = 160) {
  if (!text) return '—';
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function ReviewItem({ item, onOpen }) {
  const navigate = useNavigate();
  const submitReview = useSubmitTicketReview();
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState(item.luna_reply || '');
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');

  const badge = STATUS_BADGE[item.review_status] || STATUS_BADGE.needs_review;
  const busy = submitReview.isPending;

  const decide = (decision, extra = {}) => {
    setError('');
    submitReview.mutate({ ticketId: item.ticket_id, decision, ...extra }, {
      onSuccess: () => { setEditing(false); setRejecting(false); },
      onError: (err) => setError(err.response?.data?.detail || 'Failed to record review.'),
    });
  };

  return (
    <div style={{ border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', padding: '16px 18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', marginBottom: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '10.5px', fontWeight: '600', color: badge.color, background: badge.bg, border: `1px solid ${badge.border}`, borderRadius: '999px', padding: '2px 8px' }}>
            {badge.label}
          </span>
          <span style={{ fontSize: '11px', color: '#94A3B8', textTransform: 'capitalize' }}>{item.channel}</span>
          <span style={{ fontSize: '11px', color: '#94A3B8' }}>{formatDate(item.created_at)}</span>
          {item.order_id && <span style={{ fontSize: '11px', color: '#94A3B8' }}>Order #{item.order_id}</span>}
          {item.actions?.map((a, i) => (
            <span key={i} style={{ fontSize: '10px', fontWeight: '600', color: '#475569', background: '#F1F5F9', border: '1px solid #E4E4E7', borderRadius: '999px', padding: '1px 7px' }}>
              {a.action_type} · {a.status}
            </span>
          ))}
        </div>
        <button
          onClick={() => navigate(`/tickets/${item.ticket_id}`)}
          style={{ fontSize: '11.5px', fontWeight: '600', color: '#0E7490', background: 'none', border: 'none', padding: 0, cursor: 'pointer', flexShrink: 0 }}
        >
          Open conversation →
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
        <div style={{ fontSize: '12.5px', color: '#334155', lineHeight: 1.5 }}>
          <span style={{ color: '#94A3B8', fontWeight: '600' }}>Customer: </span>{excerpt(item.customer_message)}
        </div>
        <div style={{ fontSize: '12.5px', color: '#334155', lineHeight: 1.5, background: '#F8FAFC', borderRadius: '6px', padding: '8px 10px' }}>
          <span style={{ color: '#94A3B8', fontWeight: '600' }}>Luna: </span>{excerpt(item.luna_reply)}
        </div>
      </div>

      {item.review_status === 'rejected' && item.human_outcome?.rejection_reason && (
        <div style={{ fontSize: '11.5px', color: '#B91C1C', marginBottom: '10px' }}>
          Rejected: {item.human_outcome.rejection_reason}
        </div>
      )}

      {error && <div style={{ fontSize: '11.5px', color: '#B91C1C', marginBottom: '8px' }}>{error}</div>}

      {editing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <textarea
            value={draftText}
            onChange={e => setDraftText(e.target.value)}
            rows={3}
            style={{ fontSize: '13px', padding: '8px 10px', borderRadius: '6px', border: '1px solid #E4E4E7', resize: 'vertical', fontFamily: 'inherit' }}
          />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              disabled={busy || !draftText.trim()}
              onClick={() => decide('edit_approve', { edited_response: draftText.trim() })}
              style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #059669', background: '#059669', color: 'white', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}
            >
              Save & Approve
            </button>
            <button onClick={() => setEditing(false)} style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: '#475569', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}>
              Cancel
            </button>
          </div>
        </div>
      ) : rejecting ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {REJECTION_REASONS.map(r => (
              <button
                key={r}
                onClick={() => setReason(r)}
                style={{
                  padding: '5px 10px', borderRadius: '999px', fontSize: '11.5px', fontWeight: '600', cursor: 'pointer',
                  border: reason === r ? '1px solid #B91C1C' : '1px solid #E4E4E7',
                  background: reason === r ? '#FEF2F2' : 'white',
                  color: reason === r ? '#B91C1C' : '#475569',
                }}
              >
                {r}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              disabled={busy}
              onClick={() => decide('reject', { rejection_reason: reason || undefined })}
              style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #B91C1C', background: '#B91C1C', color: 'white', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}
            >
              Confirm Reject
            </button>
            <button onClick={() => setRejecting(false)} style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: '#475569', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            disabled={busy}
            onClick={() => decide('approve')}
            style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #059669', background: 'white', color: '#059669', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}
          >
            Approve
          </button>
          <button
            disabled={busy}
            onClick={() => { setDraftText(item.luna_reply || ''); setEditing(true); }}
            style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #06B6D4', background: 'white', color: '#0E7490', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}
          >
            Edit & Approve
          </button>
          <button
            disabled={busy}
            onClick={() => { setReason(''); setRejecting(true); }}
            style={{ padding: '7px 14px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: '#B91C1C', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

export default function ReviewQueue() {
  const [tab, setTab] = useState('needs_review');
  const { data, isLoading } = useReviewQueue(tab || undefined);
  const items = data?.items || [];

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '18px', maxWidth: '860px' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Review Luna's Work</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94A3B8' }}>
          See what Luna actually replied, then approve, correct, or reject it. Every decision here trains Reply Style.
        </p>
      </div>

      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '6px 14px', borderRadius: '999px', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer',
              border: tab === t.key ? '1px solid #06B6D4' : '1px solid #E4E4E7',
              background: tab === t.key ? '#ECFEFF' : 'white',
              color: tab === t.key ? '#0E7490' : '#475569',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '140px', borderRadius: '8px' }} />)}
        </div>
      ) : items.length === 0 ? (
        <div style={{ padding: '32px', textAlign: 'center', fontSize: '13px', color: '#94A3B8' }}>
          Nothing here yet.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {items.map(item => <ReviewItem key={item.ticket_id} item={item} />)}
        </div>
      )}
    </div>
  );
}
