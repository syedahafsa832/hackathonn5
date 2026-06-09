import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Badge from '../components/Badge';
import StatCard from '../components/StatCard';
import { useEscalations, useStats, useActions, useApproveAction, useRejectAction } from '../hooks/useApi';
import api from '../api/services';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function decodeHtml(str) {
  if (!str) return '';
  const el = document.createElement('textarea');
  el.innerHTML = str;
  return el.value;
}

const ACTION_LABELS = {
  refund: { label: 'Refund', color: 'var(--warning)' },
  cancel_order: { label: 'Cancel Order', color: 'var(--danger)' },
  change_address: { label: 'Address Change', color: 'var(--accent)' },
  reship: { label: 'Reship Order', color: 'var(--accent)' },
  restore_order: { label: 'Restore Order', color: 'var(--success)' },
  REFUND: { label: 'Refund', color: 'var(--warning)' },
  CANCEL: { label: 'Cancel Order', color: 'var(--danger)' },
  ADDRESS_CHANGE: { label: 'Address Change', color: 'var(--accent)' },
  RESHIP: { label: 'Reship Order', color: 'var(--accent)' },
  RESTORE_ORDER: { label: 'Restore Order', color: 'var(--success)' },
};

const ACTION_EXECUTE_LABELS = {
  cancel_order: 'Cancel Order',
  CANCEL: 'Cancel Order',
  refund: 'Issue Refund',
  REFUND: 'Issue Refund',
  change_address: 'Update Address',
  ADDRESS_CHANGE: 'Update Address',
  reship: 'Arrange Reship',
  RESHIP: 'Arrange Reship',
  restore_order: 'Restore in Shopify',
  RESTORE_ORDER: 'Restore in Shopify',
};

const EXECUTION_MESSAGES = {
  refund: (r) => `✓ $${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  cancel_order: (r) => `✓ Order ${r?.order_name ?? ''} cancelled. Stock restocked. Customer notified by Shopify.`,
  change_address: (r) => r?.manual_action_required
    ? `✓ Queued — update address manually in Shopify admin.${r?.new_address_text ? ' New address: ' + r.new_address_text : ''}`
    : `✓ Shipping address updated automatically in Shopify.`,
  reship: () => `✓ Queued — please create a replacement shipment in Shopify admin.`,
  restore_order: (r) => `✓ Order ${r?.order_name ?? ''} has been restored and is active again. Customer has been notified.`,
  REFUND: (r) => `✓ $${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  CANCEL: (r) => `✓ Order ${r?.order_name ?? ''} cancelled.`,
  ADDRESS_CHANGE: (r) => r?.manual_action_required
    ? `✓ Queued — update address manually in Shopify admin.${r?.new_address_text ? ' New address: ' + r.new_address_text : ''}`
    : `✓ Shipping address updated automatically in Shopify.`,
  RESHIP: () => `✓ Queued — please create a replacement shipment in Shopify admin.`,
  RESTORE_ORDER: (r) => `✓ Order ${r?.order_name ?? ''} has been restored and is active again. Customer has been notified.`,
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
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{(action.order_id || action.order_number) && `Order #${action.order_id || action.order_number}`}</span>
        </div>
        <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.created_at)}</span>
      </div>

      <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
        <strong>{action.customer_name || action.customer_email}</strong>
        {action.order_total && ` · $${action.order_total}`}
        {action.ai_reasoning && (
          <div style={{ marginTop: '4px', color: 'var(--text-muted)', fontSize: '12px' }}>{decodeHtml(action.ai_reasoning)}</div>
        )}
        {action.original_message && (
          <div style={{ marginTop: '6px', padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            "{decodeHtml(action.original_message).slice(0, 200)}"
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
              {approving ? 'Processing...' : (ACTION_EXECUTE_LABELS[action.action_type] || 'Approve')}
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
  const { data: escalations = [], isLoading: loadingEscalations, refetch: refetchEscalations } = useEscalations();
  const { data: actions = [], isLoading: loadingActions, refetch: refetchActions } = useActions('pending');
  const { data: stats } = useStats();
  const { mutateAsync: approveAction } = useApproveAction();
  const { mutateAsync: rejectAction } = useRejectAction();
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkWorking, setBulkWorking] = useState(false);
  const [selectedEscalationIds, setSelectedEscalationIds] = useState(new Set());
  const [bulkEscalWorking, setBulkEscalWorking] = useState(false);

  const loading = loadingEscalations || loadingActions;

  const toggleSelect = (id) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelectedIds(next);
  };

  const toggleAll = () => {
    if (selectedIds.size === actions.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(actions.map(a => a.id)));
  };

  const handleBulkReject = async () => {
    if (selectedIds.size === 0 || !window.confirm(`Reject ${selectedIds.size} action(s)?`)) return;
    setBulkWorking(true);
    try {
      await api.bulkRejectActions({ action_ids: Array.from(selectedIds) });
      setSelectedIds(new Set());
      await refetchActions();
    } finally { setBulkWorking(false); }
  };

  const handleClearAll = async () => {
    if (!window.confirm('Reject ALL pending approvals? This cannot be undone.')) return;
    setBulkWorking(true);
    try {
      await api.bulkRejectActions({ clear_all: true });
      setSelectedIds(new Set());
      await refetchActions();
    } finally { setBulkWorking(false); }
  };

  const toggleEscalation = (id) => {
    const next = new Set(selectedEscalationIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelectedEscalationIds(next);
  };

  const toggleAllEscalations = () => {
    if (selectedEscalationIds.size === escalations.length) setSelectedEscalationIds(new Set());
    else setSelectedEscalationIds(new Set(escalations.map(e => e.id)));
  };

  const handleBulkCloseEscalations = async () => {
    if (selectedEscalationIds.size === 0 || !window.confirm(`Mark ${selectedEscalationIds.size} escalation(s) as resolved?`)) return;
    setBulkEscalWorking(true);
    try {
      await api.bulkCloseEscalations({ ticket_ids: Array.from(selectedEscalationIds) });
      setSelectedEscalationIds(new Set());
      await refetchEscalations();
    } catch { window.alert('Failed to close escalations'); }
    finally { setBulkEscalWorking(false); }
  };

  const handleCloseAllEscalations = async () => {
    if (!window.confirm(`Mark all ${escalations.length} escalated tickets as resolved? This cannot be undone.`)) return;
    setBulkEscalWorking(true);
    try {
      await api.bulkCloseEscalations({ close_all: true });
      setSelectedEscalationIds(new Set());
      await refetchEscalations();
    } catch { window.alert('Failed to close escalations'); }
    finally { setBulkEscalWorking(false); }
  };

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
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
            <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>Pending Approvals</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12px', color: 'var(--text-muted)', cursor: 'pointer' }}>
                <input type="checkbox" checked={selectedIds.size === actions.length && actions.length > 0} onChange={toggleAll} />
                Select all
              </label>
              {selectedIds.size > 0 && (
                <button
                  onClick={handleBulkReject}
                  disabled={bulkWorking}
                  style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: 'var(--danger-light)', color: 'var(--danger)', border: '1px solid var(--danger)', cursor: 'pointer' }}
                >
                  Reject {selectedIds.size} selected
                </button>
              )}
              <button
                onClick={handleClearAll}
                disabled={bulkWorking}
                style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: 'transparent', color: 'var(--text-muted)', border: '1px solid var(--border)', cursor: 'pointer' }}
              >
                Clear all
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {actions.map(action => (
              <div key={action.id} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(action.id)}
                  onChange={() => toggleSelect(action.id)}
                  style={{ marginTop: '20px', flexShrink: 0 }}
                />
                <div style={{ flex: 1 }}>
                  <ActionCard
                    action={action}
                    onApprove={approveAction}
                    onReject={(id, reason) => rejectAction({ id, reason })}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Escalated Conversations */}
      <section>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>Escalated Conversations</h2>
          {escalations.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12px', color: 'var(--text-muted)', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedEscalationIds.size === escalations.length && escalations.length > 0}
                  onChange={toggleAllEscalations}
                />
                Select all
              </label>
              {selectedEscalationIds.size > 0 && (
                <button
                  onClick={handleBulkCloseEscalations}
                  disabled={bulkEscalWorking}
                  style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: 'var(--success-light, #e6f7e6)', color: 'var(--success)', border: '1px solid var(--success)', cursor: 'pointer' }}
                >
                  {bulkEscalWorking ? 'Closing...' : `Mark ${selectedEscalationIds.size} resolved`}
                </button>
              )}
              <button
                onClick={handleCloseAllEscalations}
                disabled={bulkEscalWorking}
                style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: 'transparent', color: 'var(--text-muted)', border: '1px solid var(--border)', cursor: 'pointer' }}
              >
                Clear all
              </button>
            </div>
          )}
        </div>

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
                  <th style={{ padding: '10px 8px 10px 16px', width: '36px', borderBottom: '1px solid var(--border)' }} />
                  {['ID', 'Channel', 'Sender', 'Status', 'Updated'].map(h => (
                    <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '12px', fontWeight: '600', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {escalations.map((c, i) => (
                  <tr
                    key={c.id}
                    style={{ background: selectedEscalationIds.has(c.id) ? 'var(--accent-light)' : (i % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)') }}
                  >
                    <td style={{ padding: '12px 8px 12px 16px' }}>
                      <input
                        type="checkbox"
                        checked={selectedEscalationIds.has(c.id)}
                        onChange={() => toggleEscalation(c.id)}
                      />
                    </td>
                    <td style={{ padding: '12px 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
                      #{String(c.id).slice(0, 8)}
                      <button
                        onClick={() => navigate(`/tickets/${c.id}`)}
                        style={{ marginLeft: '8px', fontSize: '11px', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: '0', fontFamily: 'inherit' }}
                      >
                        View →
                      </button>
                    </td>
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
