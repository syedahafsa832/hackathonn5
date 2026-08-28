import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Badge from '../components/Badge';
import StatCard from '../components/StatCard';
import Alert from '../components/Alert';
import { useEscalations, useStats, useActions, useApproveAction, useRejectAction, useCompleteManualAction } from '../hooks/useApi';
import api from '../api/services';

function formatDate(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function decodeHtml(str) {
  if (!str) return '';
  const el = document.createElement('textarea');
  el.innerHTML = str;
  return el.value;
}

const ACTION_LABELS = {
  refund: { label: 'Refund', color: '#EF4444', isDestructive: true },
  cancel_order: { label: 'Cancel Order', color: '#EF4444', isDestructive: true },
  change_address: { label: 'Address Change', color: '#0E7490', isDestructive: false },
  reship: { label: 'Reship Order', color: '#0E7490', isDestructive: false },
  restore_order: { label: 'Restore Order', color: '#0E7490', isDestructive: false },
  REFUND: { label: 'Refund', color: '#EF4444', isDestructive: true },
  CANCEL: { label: 'Cancel Order', color: '#EF4444', isDestructive: true },
  ADDRESS_CHANGE: { label: 'Address Change', color: '#0E7490', isDestructive: false },
  RESHIP: { label: 'Reship Order', color: '#0E7490', isDestructive: false },
  RESTORE_ORDER: { label: 'Restore Order', color: '#0E7490', isDestructive: false },
};

const ACTION_EXECUTE_LABELS = {
  cancel_order: 'Cancel in Shopify',
  CANCEL: 'Cancel in Shopify',
  refund: 'Issue Refund',
  REFUND: 'Issue Refund',
  change_address: 'Update Address',
  ADDRESS_CHANGE: 'Update Address',
  reship: 'Arrange Reship',
  RESHIP: 'Arrange Reship',
  restore_order: 'Restore in Shopify',
  RESTORE_ORDER: 'Restore in Shopify',
};

// Text only — Alert supplies its own ✓/✗ icon, so these no longer carry one.
function formatExecutedAddress(addr) {
  return addr ? [addr.address1, addr.city, addr.country].filter(Boolean).join(', ') : null;
}

const EXECUTION_MESSAGES = {
  refund: (r) => `$${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  cancel_order: (r) => `Order ${r?.order_name ?? ''} cancelled. Stock restocked. Customer notified by Shopify.`,
  change_address: (r) => r?.manual_action_required
    ? `Queued. Update address manually in Shopify admin.${r?.new_address_text ? ' New address: ' + r.new_address_text : ''}`
    : `✓ Address updated in Shopify.${formatExecutedAddress(r?.new_address) ? ' Now: ' + formatExecutedAddress(r.new_address) : ''}`,
  // Only reached once status='executed', which for reship only happens via
  // the merchant's explicit "Mark Reship Complete" confirmation (see
  // AWAITING_MANUAL_STEP / complete-manual) — never a claim of automation.
  reship: () => `✓ Reship Completed`,
  restore_order: (r) => `Order ${r?.order_name ?? ''} has been restored and is active again. Customer has been notified.`,
  REFUND: (r) => `$${r?.amount ?? ''} refunded via Shopify. Customer will receive Shopify's confirmation email.`,
  CANCEL: (r) => `Order ${r?.order_name ?? ''} cancelled.`,
  ADDRESS_CHANGE: (r) => r?.manual_action_required
    ? `Queued. Update address manually in Shopify admin.${r?.new_address_text ? ' New address: ' + r.new_address_text : ''}`
    : `✓ Address updated in Shopify.${formatExecutedAddress(r?.new_address) ? ' Now: ' + formatExecutedAddress(r.new_address) : ''}`,
  RESHIP: () => `✓ Reship Completed`,
  RESTORE_ORDER: (r) => `Order ${r?.order_name ?? ''} has been restored and is active again. Customer has been notified.`,
};

function ActionCard({ action, onApprove, onReject }) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState('');
  const [hoverReject, setHoverReject] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  // Bounded excerpt (<=800 chars, see return_actions_integration.py's
  // _policy_evidence_excerpt) - never the full multi-page RAG dump. Real
  // data only: no fabricated "recommendation" text, this is either the
  // actual retrieved excerpt or, if none was safely identifiable, the
  // honest fallback line below.
  const policyEvidence = action.extracted_data?.policy_evidence;
  const requiresPolicyCheck = !!policyEvidence ||
    !!(action.extracted_data?.eligibility && !action.extracted_data.eligibility.eligible &&
       (action.extracted_data.eligibility.staging_required || action.extracted_data.eligibility.requires_manual_review));
  // Human-entered partial refund override — never AI-suggested, blank means
  // "full refund" (unchanged existing behavior). Only shown for refunds.
  const [amountOverride, setAmountOverride] = useState('');

  // Reship escalations enrich extracted_data with a live Shopify snapshot
  // (order_snapshot — see return_actions_integration.py's reship staging)
  // so the reviewer sees what's actually being requested, not just a bare
  // order number. Absent for older/manually-entered actions — every field
  // below is rendered only "where available", never invented.
  const orderSnapshot = action.extracted_data?.order_snapshot;
  const shippingAddr = orderSnapshot?.shipping_address;
  const formatAddr = (a) => a
    ? [a.address1, a.address2, a.city, a.province, a.zip, a.country].filter(Boolean).join(', ')
    : null;
  const shippingAddrLine = formatAddr(shippingAddr);

  // Change Address escalations: shows what's being changed FROM (the live
  // order's address at staging time - current_shipping_address) and what's
  // being requested (new_address / new_address_text). Absent for a
  // manually-staged action created before this field existed - rendered
  // only where available, never invented.
  const isAddressChange = action.action_type === 'change_address';
  const currentAddrLine = isAddressChange ? formatAddr(action.extracted_data?.current_shipping_address) : null;
  const requestedAddrLine = isAddressChange
    ? (formatAddr(action.extracted_data?.new_address) || action.extracted_data?.new_address_text)
    : null;
  const currentFulfillmentStatus = isAddressChange ? action.extracted_data?.current_fulfillment_status : null;
  // identity_verified is only ever set by the customer-initiated staging
  // path (return_actions_integration.py) - explicitly `false` means the
  // order's Shopify email didn't match (or had none to compare against)
  // the conversation's sender email. Missing entirely (undefined) means
  // either a merchant-initiated action (Order Context - always trusted,
  // no customer identity involved) or an older action staged before this
  // check existed - neither should show a warning.
  const identityUnverified = isAddressChange && action.extracted_data?.identity_verified === false;

  const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: '#0E7490', isDestructive: false };
  const execMsg = action.execution_result
    ? (EXECUTION_MESSAGES[action.action_type]?.(action.execution_result) || JSON.stringify(action.execution_result))
    : null;
  const failMsg = action.error_message
    ? `Action failed: ${action.error_message}. Marked for manual review.`
    : null;
  const isRefund = action.action_type === 'refund' || action.action_type === 'REFUND';

  const handleApprove = async () => {
    setApproveError('');
    let amount;
    if (isRefund && amountOverride.trim() !== '') {
      const parsed = Number(amountOverride);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setApproveError('Enter a positive amount, or leave blank for a full refund.');
        return;
      }
      amount = parsed;
    }
    setApproving(true);
    try {
      await onApprove({ id: action.id, amount });
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
    // No local error state here — onReject optimistically removes this card
    // from the pending list the instant the mutation starts (see
    // useRejectAction), so by the time a failure comes back this component
    // is usually already unmounted and a local error state would never be
    // seen. onReject surfaces failures itself (see Actions() below).
    try { await onReject(action.id, reason); } finally { setApproving(false); }
  };

  return (
    <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '14px', fontWeight: '600', color: meta.color }}>
            {meta.label}
          </span>
          {action.order_id || action.order_number ? (
            <span style={{ fontSize: '12px', background: '#F8FAFC', color: '#475569', padding: '2px 8px', borderRadius: '4px' }}>
              Order #{action.order_id || action.order_number}
            </span>
          ) : null}
        </div>
        <span style={{ fontSize: '11px', color: '#94A3B8', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.created_at)}</span>
      </div>

      <div style={{ fontSize: '13px', color: '#64748B', display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div>
          <strong style={{ fontSize: '14px', fontWeight: '500', color: '#0F172A' }}>{action.customer_name || action.customer_email}</strong>
          {action.order_total && ` · $${action.order_total}`}
        </div>

        {action.original_message && (
          <div>
            <div style={{ fontSize: '10.5px', fontWeight: '600', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '3px' }}>
              Customer request
            </div>
            <div style={{ padding: '8px 12px', background: '#F8FAFC', borderRadius: '0 4px 4px 0', fontSize: '13px', color: '#475569', fontStyle: 'italic', borderLeft: '3px solid #E2E8F0' }}>
              "{decodeHtml(action.original_message).slice(0, 200)}"
            </div>
          </div>
        )}

        {action.ai_reasoning && (
          <div>
            <div style={{ fontSize: '10.5px', fontWeight: '600', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '3px' }}>
              Why approval is needed
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{ color: '#334155', fontSize: '13px', flex: 1, minWidth: '160px' }}>{decodeHtml(action.ai_reasoning)}</span>
              {requiresPolicyCheck && (
                <span style={{ flexShrink: 0, fontSize: '10.5px', fontWeight: '600', color: '#0E7490', background: '#ECFEFF', border: '1px solid #A5F3FC', borderRadius: '999px', padding: '2px 9px' }}>
                  Policy check required
                </span>
              )}
              {identityUnverified && (
                <span style={{ flexShrink: 0, fontSize: '10.5px', fontWeight: '600', color: '#B91C1C', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '999px', padding: '2px 9px' }}>
                  ⚠ Identity not verified
                </span>
              )}
            </div>
          </div>
        )}

        {identityUnverified && (
          <div style={{ padding: '8px 12px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '4px', fontSize: '12.5px', color: '#991B1B' }}>
            <strong>Could not confirm this order belongs to the requester.</strong>{' '}
            {action.extracted_data?.identity_verification_reason || 'Verify the customer\'s identity manually before approving.'}
          </div>
        )}

        {orderSnapshot && (
          <div>
            <div style={{ fontSize: '10.5px', fontWeight: '600', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '3px' }}>
              Order details
            </div>
            <div style={{ padding: '8px 12px', background: '#F8FAFC', borderRadius: '4px', fontSize: '12.5px', color: '#475569', lineHeight: '1.6' }}>
              {orderSnapshot.items?.length > 0 && (
                <div>
                  {orderSnapshot.items.map((it, i) => (
                    <div key={i}>{it.quantity}× {it.title}{it.variant_title ? ` (${it.variant_title})` : ''}</div>
                  ))}
                </div>
              )}
              {orderSnapshot.fulfillment_status && (
                <div><strong>Fulfillment:</strong> {orderSnapshot.fulfillment_status}</div>
              )}
              {orderSnapshot.tracking_number && (
                <div>
                  <strong>Tracking:</strong> {orderSnapshot.tracking_company ? `${orderSnapshot.tracking_company} ` : ''}{orderSnapshot.tracking_number}
                  {orderSnapshot.tracking_url && <a href={orderSnapshot.tracking_url} target="_blank" rel="noopener noreferrer" style={{ marginLeft: '6px', color: '#0E7490' }}>Track →</a>}
                </div>
              )}
              {shippingAddrLine && (
                <div><strong>Shipping to:</strong> {shippingAddrLine}</div>
              )}
            </div>
          </div>
        )}

        {isAddressChange && (currentAddrLine || requestedAddrLine) && (
          <div>
            <div style={{ fontSize: '10.5px', fontWeight: '600', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '3px' }}>
              Address change
            </div>
            <div style={{ padding: '8px 12px', background: '#F8FAFC', borderRadius: '4px', fontSize: '12.5px', color: '#475569', lineHeight: '1.6' }}>
              {currentAddrLine && <div><strong>Current:</strong> {currentAddrLine}</div>}
              {requestedAddrLine && <div><strong>Requested:</strong> {requestedAddrLine}</div>}
              {currentFulfillmentStatus && <div><strong>Fulfillment:</strong> {currentFulfillmentStatus}{currentFulfillmentStatus === 'fulfilled' ? ' — Shopify will reject this update' : ''}</div>}
            </div>
          </div>
        )}

        {requiresPolicyCheck && (
          <div>
            <button
              onClick={() => setEvidenceOpen(o => !o)}
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '12.5px', fontWeight: '600', color: '#0E7490', display: 'flex', alignItems: 'center', gap: '4px' }}
            >
              {evidenceOpen ? '▾' : '▸'} {evidenceOpen ? 'Hide' : 'View'} policy evidence
            </button>
            {evidenceOpen && (
              <div style={{ marginTop: '6px', padding: '10px 12px', background: '#F8FAFC', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '12.5px', color: '#475569', lineHeight: '1.55', whiteSpace: 'pre-wrap' }}>
                {policyEvidence
                  ? decodeHtml(policyEvidence)
                  : 'Merchant policy information was found and requires human review.'}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Execution result (after approval) */}
      <Alert variant="success">{execMsg}</Alert>
      <Alert variant="error">{failMsg}</Alert>
      <Alert variant="error">{approveError}</Alert>

      {/* Action buttons for pending */}
      {action.status === 'pending' && !execMsg && !failMsg && (
        rejecting ? (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <input
              value={reason}
              onChange={e => setReason(e.target.value)}
              placeholder="Reason for rejection..."
              style={{ flex: 1, padding: '7px 10px', borderRadius: '4px', border: '1px solid #E4E4E7', fontSize: '13px', background: 'white', outline: 'none' }}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
            <button
              onClick={handleReject}
              disabled={!reason.trim() || approving}
              style={{ padding: '7px 14px', borderRadius: '4px', background: '#EF4444', color: 'white', fontSize: '12px', fontWeight: '600', cursor: approving ? 'not-allowed' : 'pointer', border: 'none', opacity: approving ? 0.6 : 1 }}
            >
              {approving ? 'Rejecting…' : 'Confirm'}
            </button>
            <button
              onClick={() => setRejecting(false)}
              style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid #E4E4E7', background: 'transparent', fontSize: '12px', cursor: 'pointer', color: '#475569' }}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div style={{ marginTop: '4px' }}>
            {isRefund && (
              <div style={{ marginBottom: '8px' }}>
                <label style={{ display: 'block', fontSize: '12px', color: '#64748B', marginBottom: '4px' }}>
                  Partial refund amount (optional, leave blank for the full ${(action.amount || 0).toFixed(2)})
                </label>
                <input
                  type="number" min="0.01" step="0.01"
                  placeholder={`Full refund: $${(action.amount || 0).toFixed(2)}`}
                  value={amountOverride}
                  onChange={e => setAmountOverride(e.target.value)}
                  disabled={approving}
                  style={{ padding: '6px 10px', borderRadius: '4px', border: '1px solid #E4E4E7', fontSize: '13px', width: '180px' }}
                />
              </div>
            )}
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{
                padding: '7px 16px',
                borderRadius: '4px',
                background: meta.isDestructive ? 'white' : '#06B6D4',
                color: meta.isDestructive ? '#EF4444' : 'white',
                border: meta.isDestructive ? '1px solid #FECACA' : '1px solid transparent',
                fontSize: '13px',
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              {approving ? 'Processing...' : (ACTION_EXECUTE_LABELS[action.action_type] || 'Approve')}
            </button>
            <button
              onClick={() => setRejecting(true)}
              onMouseEnter={() => setHoverReject(true)}
              onMouseLeave={() => setHoverReject(false)}
              style={{
                padding: '7px 16px',
                borderRadius: '4px',
                border: '1px solid transparent',
                background: 'transparent',
                color: hoverReject ? '#64748B' : '#94A3B8',
                fontSize: '13px',
                fontWeight: '500',
                cursor: 'pointer',
                transition: 'color 0.15s'
              }}
            >
              Reject
            </button>
          </div>
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
  const { data: history = [], isLoading: loadingHistory } = useActions('history');
  const rejectedActions = history.filter(a => a.status === 'rejected');
  // Persistent failure surface (was: a toast that vanished on refresh).
  // get_action_history() already includes status='failed' rows — the data
  // was always there, it just had nowhere to render. actions_service._mark_failed()
  // already persists action_type/order_id/created_at (all already columns
  // on the row) plus a human-readable error_message, so no backend change
  // was needed to have something to show here.
  const failedActions = history.filter(a => a.status === 'failed');
  const completedActions = history.filter(a => a.status === 'executed');
  // Approved, but a human still has to do the actual Shopify work by hand
  // (reship, today) - deliberately never shown under Completed until the
  // merchant explicitly confirms it (see completeManualAction below).
  const awaitingManualActions = history.filter(a => a.status === 'awaiting_manual_step');
  const { data: stats } = useStats();
  const { mutateAsync: approveAction } = useApproveAction();
  const { mutateAsync: rejectAction } = useRejectAction();
  const { mutateAsync: completeManualAction } = useCompleteManualAction();
  const [completingId, setCompletingId] = useState(null);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkWorking, setBulkWorking] = useState(false);
  const [selectedEscalationIds, setSelectedEscalationIds] = useState(new Set());
  const [bulkEscalWorking, setBulkEscalWorking] = useState(false);
  const [showRejected, setShowRejected] = useState(false);
  const [showFailed, setShowFailed] = useState(true);
  const [showCompleted, setShowCompleted] = useState(false);
  const [retryingId, setRetryingId] = useState(null);
  const [pageError, setPageError] = useState('');

  // ActionCard's own onReject already awaits this and shows its own working
  // state — but the card optimistically disappears from the pending list the
  // instant the mutation starts (see useRejectAction), so by the time a
  // failure comes back the card is already unmounted and any error state on
  // it would never render. Surface failures here instead, at the level that
  // survives the optimistic remove/rollback cycle.
  const handleReject = async (id, reason) => {
    try {
      await rejectAction({ id, reason });
    } catch (err) {
      setPageError(err?.response?.data?.detail || 'Failed to reject action. It has been restored to the list.');
    }
  };

  // Retry reuses the exact same approve call — the backend now accepts a
  // re-claim from status='failed' (see actions_service.approve_action).
  // useApproveAction's onSettled invalidates ['actions'] regardless of
  // outcome, so this card moves itself out of Failed (into pending/executed,
  // or right back into Failed with the new error) on the next refetch.
  const handleRetry = async (id) => {
    setRetryingId(id);
    setPageError('');
    try {
      await approveAction({ id });
    } catch (err) {
      setPageError(err?.response?.data?.detail || 'Retry failed. See the error below for details.');
    } finally {
      setRetryingId(null);
    }
  };

  // Merchant is explicitly confirming they did the manual Shopify work by
  // hand (see complete-manual endpoint) - never inferred, never automatic.
  const handleCompleteManual = async (id) => {
    setCompletingId(id);
    setPageError('');
    try {
      await completeManualAction(id);
    } catch (err) {
      setPageError(err?.response?.data?.detail || 'Could not mark this complete. Please try again.');
    } finally {
      setCompletingId(null);
    }
  };

  useEffect(() => {
    document.title = "Escalations: tResolv";
  }, []);

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
    } catch { setPageError('Failed to close escalations'); }
    finally { setBulkEscalWorking(false); }
  };

  const handleCloseAllEscalations = async () => {
    if (!window.confirm(`Mark all ${escalations.length} escalated tickets as resolved? This cannot be undone.`)) return;
    setBulkEscalWorking(true);
    try {
      await api.bulkCloseEscalations({ close_all: true });
      setSelectedEscalationIds(new Set());
      await refetchEscalations();
    } catch { setPageError('Failed to close escalations'); }
    finally { setBulkEscalWorking(false); }
  };

  // Determine colors based on count dynamically
  const pendingColor = (!loading && actions.length > 0) ? '#06B6D4' : '#64748B';
  const escalColor = (!loading && escalations.length > 0) ? '#F59E0B' : '#64748B';

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>

      <Alert variant="error" onDismiss={() => setPageError('')} autoDismissMs={5000}>{pageError}</Alert>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <StatCard label="Pending Approvals" value={loading ? null : actions.length} loading={loading} subtitle="Financial actions awaiting review" labelColor={pendingColor} valueSize="48px" />
        <StatCard label="Escalated Tickets" value={loading ? null : escalations.length} loading={loading} subtitle="Need human response" labelColor={escalColor} valueSize="48px" />
      </div>

      {/* Pending Financial Actions */}
      {loadingActions ? (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <h2 style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A' }}>Pending Approvals</h2>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '140px', borderRadius: '8px' }} />)}
          </div>
        </section>
      ) : actions.length > 0 && (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <h2 style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A' }}>Pending Approvals</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '13px', color: '#64748B', cursor: 'pointer' }}
                     onMouseEnter={e => e.target.style.color = '#0F172A'}
                     onMouseLeave={e => e.target.style.color = '#64748B'}>
                <input type="checkbox" checked={selectedIds.size === actions.length && actions.length > 0} onChange={toggleAll} />
                Select all
              </label>
              {selectedIds.size > 0 && (
                <button
                  onClick={handleBulkReject}
                  disabled={bulkWorking}
                  style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: '#FEF2F2', color: '#EF4444', border: '1px solid #EF4444', cursor: 'pointer' }}
                >
                  Reject {selectedIds.size} selected
                </button>
              )}
              <button
                onClick={handleClearAll}
                disabled={bulkWorking}
                style={{ padding: '4px 10px', fontSize: '13px', borderRadius: '4px', background: 'transparent', color: '#64748B', border: '1px solid transparent', cursor: 'pointer', transition: 'color 0.15s' }}
                onMouseEnter={e => e.target.style.color = '#0F172A'}
                onMouseLeave={e => e.target.style.color = '#64748B'}
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
                    onReject={handleReject}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Rejected Approvals */}
      {loadingHistory ? (
        <section>
          <div className="skeleton" style={{ height: '20px', width: '140px', borderRadius: '4px' }} />
        </section>
      ) : rejectedActions.length > 0 && (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <button
              onClick={() => setShowRejected(s => !s)}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '14px', fontWeight: '600', color: '#0F172A' }}
            >
              <span style={{ display: 'inline-block', transition: 'transform 0.15s', transform: showRejected ? 'rotate(90deg)' : 'none' }}>▸</span>
              Rejected ({rejectedActions.length})
            </button>
          </div>
          {showRejected && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {rejectedActions.map(action => {
                const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: '#0E7490' };
                return (
                  <div key={action.id} style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '8px', opacity: 0.75 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '14px', fontWeight: '600', color: meta.color }}>{meta.label}</span>
                        {(action.order_id || action.order_number) && (
                          <span style={{ fontSize: '12px', background: '#F8FAFC', color: '#475569', padding: '2px 8px', borderRadius: '4px' }}>
                            Order #{action.order_id || action.order_number}
                          </span>
                        )}
                        <span style={{ fontSize: '11px', background: '#F1F5F9', color: '#64748B', padding: '2px 8px', borderRadius: '10px', fontWeight: '600' }}>Rejected</span>
                      </div>
                      <span style={{ fontSize: '11px', color: '#94A3B8', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.approved_at || action.updated_at)}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: '#64748B' }}>
                      <strong style={{ fontSize: '14px', fontWeight: '500', color: '#0F172A' }}>{action.customer_name || action.customer_email}</strong>
                    </div>
                    {action.rejection_reason && (
                      <div style={{ fontSize: '13px', color: '#64748B' }}>
                        <strong style={{ color: '#475569' }}>Reason:</strong> {action.rejection_reason}
                        {action.approved_by && <span style={{ color: '#94A3B8' }}>, by {action.approved_by}</span>}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Failed Actions — persists after refresh, unlike a toast. A merchant
          clicked Cancel/Refund in Shopify and execution failed; the row
          stays here (fetched from the same history endpoint as Rejected,
          just filtered to status='failed') until it's retried. */}
      {loadingHistory ? null : failedActions.length > 0 && (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <button
              onClick={() => setShowFailed(s => !s)}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '14px', fontWeight: '600', color: '#B91C1C' }}
            >
              <span style={{ display: 'inline-block', transition: 'transform 0.15s', transform: showFailed ? 'rotate(90deg)' : 'none' }}>▸</span>
              Failed ({failedActions.length})
            </button>
          </div>
          {showFailed && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {failedActions.map(action => {
                const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: '#0E7490' };
                return (
                  <div key={action.id} style={{ background: 'white', border: '1px solid #FECACA', borderRadius: '8px', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '14px', fontWeight: '600', color: meta.color }}>{meta.label}</span>
                        {(action.order_id || action.order_number) && (
                          <span style={{ fontSize: '12px', background: '#F8FAFC', color: '#475569', padding: '2px 8px', borderRadius: '4px' }}>
                            Order #{action.order_id || action.order_number}
                          </span>
                        )}
                        <span style={{ fontSize: '11px', background: '#FEF2F2', color: '#B91C1C', padding: '2px 8px', borderRadius: '10px', fontWeight: '600' }}>Failed</span>
                      </div>
                      <span style={{ fontSize: '11px', color: '#94A3B8', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.updated_at || action.created_at)}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: '#64748B' }}>
                      <strong style={{ fontSize: '14px', fontWeight: '500', color: '#0F172A' }}>{action.customer_name || action.customer_email}</strong>
                    </div>
                    {/* Human-readable reason only — never a raw stack trace or
                        internal error detail. actions_service._mark_failed()
                        already stores a ShopifyError's own merchant-facing
                        .message (e.g. "Cannot cancel a fulfilled order"),
                        never a Python exception string, for every known
                        failure path except a genuinely unexpected exception,
                        which still only ever reaches this field as str(e) —
                        no traceback, no internal file paths. */}
                    {action.error_message && (
                      <div style={{ fontSize: '13px', color: '#991B1B', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '4px', padding: '8px 10px' }}>
                        {decodeHtml(action.error_message)}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <button
                        onClick={() => handleRetry(action.id)}
                        disabled={retryingId === action.id}
                        style={{ padding: '7px 16px', borderRadius: '4px', background: '#06B6D4', color: 'white', border: 'none', fontSize: '13px', fontWeight: '600', cursor: retryingId === action.id ? 'not-allowed' : 'pointer', opacity: retryingId === action.id ? 0.6 : 1 }}
                      >
                        {retryingId === action.id ? 'Retrying…' : 'Retry'}
                      </button>
                      <span style={{ fontSize: '12px', color: '#94A3B8' }}>Retries the same action against Shopify.</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Awaiting Manual Action — status='awaiting_manual_step' (reship,
          today). Approved, but nothing was actually done in Shopify - never
          shown as Completed until the merchant explicitly confirms it. */}
      {loadingHistory ? null : awaitingManualActions.length > 0 && (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <span style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A' }}>
              Awaiting Manual Action ({awaitingManualActions.length})
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {awaitingManualActions.map(action => {
              const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: '#0E7490' };
              const orderRef = action.order_id || action.order_number;
              return (
                <div key={action.id} style={{ background: 'white', border: '1px solid #FDE68A', borderRadius: '8px', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <span style={{ fontSize: '14px', fontWeight: '600', color: meta.color }}>{meta.label}</span>
                    {orderRef && (
                      <span style={{ fontSize: '12px', background: '#F8FAFC', color: '#475569', padding: '2px 8px', borderRadius: '4px' }}>
                        Order #{orderRef}
                      </span>
                    )}
                    <span style={{ fontSize: '11px', background: '#FEF9C3', color: '#854D0E', padding: '2px 8px', borderRadius: '10px', fontWeight: '600' }}>
                      Approved — manual Shopify step required
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: '#475569' }}>
                    Create the replacement shipment manually in Shopify.
                  </div>
                  <div>
                    <button
                      onClick={() => handleCompleteManual(action.id)}
                      disabled={completingId === action.id}
                      style={{ padding: '6px 14px', fontSize: '12px', fontWeight: '600', borderRadius: '4px', border: 'none', background: '#0EA5B7', color: 'white', cursor: completingId === action.id ? 'not-allowed' : 'pointer', opacity: completingId === action.id ? 0.6 : 1 }}
                    >
                      {completingId === action.id ? 'Marking complete…' : 'Mark Reship Complete'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Completed Actions — status='executed'. execution_result.manual_action_required
          (reship/some address changes) means Shopify wasn't actually touched
          automatically - a team member still has to finish it by hand, so
          that's called out here rather than implying full automation. */}
      {loadingHistory ? null : completedActions.length > 0 && (
        <section>
          <div className="header-row" style={{ marginBottom: '10px' }}>
            <button
              onClick={() => setShowCompleted(s => !s)}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '14px', fontWeight: '600', color: '#0F172A' }}
            >
              <span style={{ display: 'inline-block', transition: 'transform 0.15s', transform: showCompleted ? 'rotate(90deg)' : 'none' }}>▸</span>
              Completed ({completedActions.length})
            </button>
          </div>
          {showCompleted && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {completedActions.map(action => {
                const meta = ACTION_LABELS[action.action_type] || { label: action.action_type, color: '#0E7490' };
                const manualStepRemains = !!action.execution_result?.manual_action_required;
                const msg = EXECUTION_MESSAGES[action.action_type]?.(action.execution_result) || 'Completed.';
                return (
                  <div key={action.id} style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '14px', fontWeight: '600', color: meta.color }}>{meta.label}</span>
                        {(action.order_id || action.order_number) && (
                          <span style={{ fontSize: '12px', background: '#F8FAFC', color: '#475569', padding: '2px 8px', borderRadius: '4px' }}>
                            Order #{action.order_id || action.order_number}
                          </span>
                        )}
                        <span style={{ fontSize: '11px', background: manualStepRemains ? '#FEF9C3' : '#ECFDF5', color: manualStepRemains ? '#854D0E' : '#10B981', padding: '2px 8px', borderRadius: '10px', fontWeight: '600' }}>
                          {manualStepRemains ? 'Approved — manual step remaining' : 'Completed'}
                        </span>
                      </div>
                      <span style={{ fontSize: '11px', color: '#94A3B8', fontFamily: 'DM Mono, monospace' }}>{formatDate(action.executed_at || action.updated_at)}</span>
                    </div>
                    <div style={{ fontSize: '13px', color: '#64748B' }}>
                      <strong style={{ fontSize: '14px', fontWeight: '500', color: '#0F172A' }}>{action.customer_name || action.customer_email}</strong>
                    </div>
                    <div style={{ fontSize: '13px', color: manualStepRemains ? '#854D0E' : '#166534', background: manualStepRemains ? '#FEFCE8' : '#F0FDF4', border: `1px solid ${manualStepRemains ? '#FDE68A' : '#BBF7D0'}`, borderRadius: '4px', padding: '8px 10px' }}>
                      {decodeHtml(msg)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Escalated Conversations */}
      <section>
        <div className="header-row" style={{ marginBottom: '12px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A' }}>Escalated Conversations</h2>
          {escalations.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '13px', color: '#64748B', cursor: 'pointer' }}
                     onMouseEnter={e => e.target.style.color = '#0F172A'}
                     onMouseLeave={e => e.target.style.color = '#64748B'}>
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
                  style={{ padding: '4px 10px', fontSize: '12px', borderRadius: '4px', background: '#ECFDF5', color: '#10B981', border: '1px solid #10B981', cursor: 'pointer' }}
                >
                  {bulkEscalWorking ? 'Closing...' : `Mark ${selectedEscalationIds.size} resolved`}
                </button>
              )}
              <button
                onClick={handleCloseAllEscalations}
                disabled={bulkEscalWorking}
                style={{ padding: '4px 10px', fontSize: '13px', borderRadius: '4px', background: 'transparent', color: '#64748B', border: '1px solid transparent', cursor: 'pointer', transition: 'color 0.15s' }}
                onMouseEnter={e => e.target.style.color = '#0F172A'}
                onMouseLeave={e => e.target.style.color = '#64748B'}
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
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 24px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', gap: '12px' }}>
            <div style={{ width: '48px', height: '48px', borderRadius: '50%', background: '#ECFEFF', color: '#06B6D4', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '24px' }}>✓</div>
            <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B' }}>Queue Empty</div>
            <div style={{ fontSize: '13px', color: '#94A3B8', textAlign: 'center' }}>No conversations require human intervention.</div>
          </div>
        ) : (
          <>
            {/* Mobile card list */}
            <div className="table-mobile-cards">
              {escalations.map(c => (
                <div key={c.id} className="mobile-card" onClick={() => navigate(`/tickets/${c.id}`)}>
                  <div className="mobile-card-row">
                    <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '12px', color: '#64748B' }}>#{String(c.id).slice(0, 8)}</span>
                    <span style={{ color: '#64748B' }}>{formatDate(c.updated_at)}</span>
                  </div>
                  <div style={{ fontWeight: '500', color: '#0F172A' }}>{c.customer_email || c.sender_id || '-'}</div>
                  <div className="mobile-card-row">
                    <span style={{ textTransform: 'capitalize' }}>{c.channel || 'email'}</span>
                    <Badge status={c.status} />
                  </div>
                </div>
              ))}
            </div>

          <div className="table-desktop-wrap" style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#F8FAFC' }}>
                  <th style={{ padding: '10px 8px 10px 16px', width: '36px', borderBottom: '1px solid #E4E4E7' }} />
                  {['ID', 'Channel', 'Sender', 'Status', 'Updated'].map(h => (
                    <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '11px', fontWeight: '600', color: '#64748B', borderBottom: '1px solid #E4E4E7', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {escalations.map((c, i) => (
                  <tr
                    key={c.id}
                    style={{ background: selectedEscalationIds.has(c.id) ? '#F0FAFE' : 'transparent', borderBottom: '1px solid #F1F5F9', height: '48px' }}
                    onMouseEnter={e => { if (!selectedEscalationIds.has(c.id)) e.currentTarget.style.background = '#F8FAFC'; }}
                    onMouseLeave={e => { if (!selectedEscalationIds.has(c.id)) e.currentTarget.style.background = 'transparent'; }}
                  >
                    <td style={{ padding: '0 8px 0 16px' }}>
                      <input
                        type="checkbox"
                        checked={selectedEscalationIds.has(c.id)}
                        onChange={() => toggleEscalation(c.id)}
                      />
                    </td>
                    <td style={{ padding: '0 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: '#64748B' }}>
                      #{String(c.id).slice(0, 8)}
                      <button
                        onClick={() => navigate(`/tickets/${c.id}`)}
                        style={{ marginLeft: '8px', fontSize: '11px', color: '#06B6D4', background: 'none', border: 'none', cursor: 'pointer', padding: '0', fontFamily: 'inherit' }}
                      >
                        View →
                      </button>
                    </td>
                    <td style={{ padding: '0 16px', textTransform: 'capitalize', color: '#1E293B' }}>{c.channel || 'email'}</td>
                    <td style={{ padding: '0 16px', color: '#1E293B' }}>{c.customer_email || c.sender_id || '-'}</td>
                    <td style={{ padding: '0 16px' }}><Badge status={c.status} /></td>
                    <td style={{ padding: '0 16px', color: '#64748B', fontSize: '12px', fontFamily: 'DM Mono, monospace' }}>{formatDate(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>
    </div>
  );
}
