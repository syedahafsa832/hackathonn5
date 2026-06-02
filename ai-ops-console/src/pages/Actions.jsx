import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Badge from '../components/Badge';
import StatCard from '../components/StatCard';
import { useEscalations, useStats, useActions, useApproveAction, useRejectAction } from '../hooks/useApi';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const ACTION_LABELS = {
  refund: { label: 'Refund', color: 'var(--warning)' },
  cancel_order: { label: 'Cancel Order', color: 'var(--danger)' },
  change_address: { label: 'Address Change', color: 'var(--accent)' },
  REFUND: { label: 'Refund', color: 'var(--warning)' },
  CANCEL: { label: 'Cancel Order', color: 'var(--danger)' },
  ADDRESS_CHANGE: { label: 'Address Change', color: 'var(--accent)' },
  RESHIP: { label: 'Reship', color: 'var(--accent)' },
};

const EXECUTION_MESSAGES = {
  refund: (r) => `✓ $${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  cancel_order: (r) => `✓ Order #${r?.order_name ?? ''} cancelled. Stock restocked. Customer notified by Shopify.`,
  change_address: () => `✓ Shipping address updated on Shopify.`,
  REFUND: (r) => `✓ $${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  CANCEL: (r) => `✓ Order #${r?.order_name ?? ''} cancelled.`,
  ADDRESS_CHANGE: () => `✓ Shipping address updated on Shopify.`,
  RESHIP: (r) => `✓ Replacement order #${r?.new_order_number ?? ''} created. Ready to fulfil.`,
};

function ActionCard({ action, onApprove, onReject }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState('');

  const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: 'var(--text-muted)' };
  const execMsg = action.execution_result
    ? (EXECUTION_MESSAGES[action.action_type]?.(action.execution_result) || JSON.stringify(action.execution_result))
    : null;
  const failMsg = action.error_message
    ? `✗ Action failed: ${action.error_message}. Marked for manual review.`
    : null;

  const handleApprove = async () => {
    setApproving(true);
    setApproveError('');
    try {
      await onApprove(action.id);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const msg = (typeof detail === 'object' ? detail?.error : detail) || err?.message || 'Approval failed';
      setApproveError(msg);
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    if (!reason.trim()) return;
    setApproving(true);
    try { await onReject(action.id, reason); } finally { setApproving(false); }
  };

  return (
    <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', fontWeight: '700', padding: '2px 8px', borderRadius: '3px', background: meta.color + '22', color: meta.color }}>
            {meta.label}
          </span>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{action.order_number && `Order #${action.order_number}`}</span>
        </div>
        <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.created_at)}</span>
      </div>

      <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
        <strong>{action.customer_name || action.customer_email}</strong>
        {action.order_total && ` · $${action.order_total}`}
        {action.ai_reasoning && (
          <div style={{ marginTop: '4px', color: 'var(--text-muted)', fontSize: '12px' }}>{action.ai_reasoning}</div>
        )}
        {action.original_message && (
          <div style={{ marginTop: '6px', padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            "{action.original_message.slice(0, 200)}"
          </div>
        )}
      </div>

      {/* Execution result (after approval) */}
      {execMsg && (
        <div style={{ padding: '8px 12px', background: 'var(--success-light)', borderRadius: '4px', fontSize: '13px', color: 'var(--success)' }}>
          {execMsg}
        </div>
      )}
      {failMsg && (
        <div style={{ padding: '8px 12px', background: 'var(--danger-light)', borderRadius: '4px', fontSize: '13px', color: 'var(--danger)' }}>
          {failMsg}
        </div>
      )}

      {/* Approve error */}
      {approveError && (
        <div style={{ padding: '8px 12px', background: 'var(--danger-light)', borderRadius: '4px', fontSize: '12px', color: 'var(--danger)' }}>
          ✗ {approveError}
        </div>
      )}

      {/* Action buttons for pending */}
      {action.status === 'pending' && !execMsg && !failMsg && (
        rejecting ? (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <input
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="Reason for rejection..."
              style={{ flex: 1, padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border-strong)', fontSize: '13px', background: 'var(--bg-primary)' }}
            />
            <button
              onClick={handleReject}
              disabled={!reason.trim() || approving}
              style={{ padding: '7px 14px', borderRadius: '4px', background: 'var(--danger)', color: 'white', fontSize: '12px', fontWeight: '600', cursor: 'pointer' }}
            >
              Confirm
            </button>
            <button
              onClick={() => setRejecting(false)}
              style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', fontSize: '12px', cursor: 'pointer' }}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{ padding: '7px 16px', borderRadius: '4px', background: 'var(--success)', color: 'white', fontSize: '13px', fontWeight: '600', cursor: 'pointer' }}
            >
              {approving ? 'Approving...' : 'Approve'}
            </button>
            <button
              onClick={() => setRejecting(true)}
              style={{ padding: '7px 16px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--danger)', fontSize: '13px', fontWeight: '500', cursor: 'pointer' }}
            >
              Reject
            </button>
          </div>
        )
      )}
    </div>
  );
}

export default function Actions() {
  const navigate = useNavigate();
  const { data: escalations = [], isLoading: loadingEscalations } = useEscalations();
  const { data: actions = [], isLoading: loadingActions } = useActions('pending');
  const { data: stats } = useStats();
  const { mutateAsync: approveAction } = useApproveAction();
  const { mutateAsync: rejectAction } = useRejectAction();

  const loading = loadingEscalations || loadingActions;

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <StatCard label="Pending Approvals" value={loading ? null : actions.length} loading={loading} subtitle="Financial actions awaiting review" />
        <StatCard label="Escalated Tickets" value={loading ? null : escalations.length} loading={loading} subtitle="Need human response" />
      </div>

      {/* Pending Financial Actions */}
      {actions.length > 0 && (
        <section>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)', marginBottom: '12px' }}>Pending Approvals</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {actions.map(action => (
              <ActionCard
                key={action.id}
                action={action}
                onApprove={approveAction}
                onReject={(id, reason) => rejectAction({ id, reason })}
              />
            ))}
          </div>
        </section>
      )}

      {/* Escalated Conversations */}
      <section>
        <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)', marginBottom: '12px' }}>Escalated Conversations</h2>

        {loadingEscalations ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '80px', borderRadius: '6px' }} />)}
          </div>
        ) : escalations.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 24px', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)', gap: '12px' }}>
            <div style={{ fontSize: '36px' }}>✓</div>
            <div style={{ fontSize: '16px', fontWeight: '600', color: 'var(--text-primary)' }}>Queue Empty</div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', textAlign: 'center' }}>No conversations require human intervention.</div>
          </div>
        ) : (
          <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-secondary)' }}>
                  {['ID', 'Channel', 'Sender', 'Status', 'Updated'].map(h => (
                    <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {escalations.map((c, i) => (
                  <tr
                    key={c.id}
                    onClick={() => navigate(`/tickets/${c.id}`)}
                    style={{ cursor: 'pointer', background: i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-light)'}
                    onMouseLeave={e => e.currentTarget.style.background = i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)'}
                  >
                    <td style={{ padding: '12px 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: 'var(--text-muted)' }}>#{String(c.id).slice(0, 8)}</td>
                    <td style={{ padding: '12px 16px', textTransform: 'capitalize' }}>{c.channel || 'email'}</td>
                    <td style={{ padding: '12px 16px' }}>{c.customer_email || c.sender_id || '—'}</td>
                    <td style={{ padding: '12px 16px' }}><Badge status={c.status} /></td>
                    <td style={{ padding: '12px 16px', color: 'var(--text-muted)', fontSize: '12px', fontFamily: 'DM Mono, monospace' }}>{formatDate(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
