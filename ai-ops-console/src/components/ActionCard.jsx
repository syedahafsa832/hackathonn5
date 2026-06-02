import { useState } from 'react';
import Badge from './Badge';
import client from '../api/client';

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function ConfidencePill({ score }) {
  const pct = Math.round(score > 1 ? score : (score || 0) * 100);
  const color = pct >= 80 ? 'var(--success)' : pct >= 50 ? 'var(--warning)' : 'var(--danger)';
  const bg = pct >= 80 ? 'var(--success-light)' : pct >= 50 ? 'var(--warning-light)' : 'var(--danger-light)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', fontWeight: '500', color, background: bg, fontFamily: 'DM Mono, monospace' }}>
      {pct}% confidence
    </span>
  );
}

export default function ActionCard({ action, onApproved, onRejected, compact }) {
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { success, message, shopify_refund_id, amount, error }

  const handleApprove = async () => {
    setLoading(true);
    try {
      const res = await client.post(`/api/v2/actions/${action.id}/approve`);
      const data = res.data;
      if (data?.success) {
        setResult({
          success: true,
          message: data.message || 'Action executed successfully',
          shopify_refund_id: data.execution_result?.refund_id || data.execution_result?.id,
          amount: action.amount,
        });
        onApproved?.(action.id);
      } else {
        setResult({ success: false, error: data?.error || 'Execution failed' });
      }
    } catch (err) {
      const errMsg = err.response?.data?.detail || err.response?.data?.error || 'Failed to approve action';
      setResult({ success: false, error: errMsg });
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) return;
    setLoading(true);
    try {
      await client.post(`/api/v2/actions/${action.id}/reject`, { reason: rejectReason });
      onRejected?.(action.id);
    } catch {
      setResult({ success: false, error: 'Failed to reject action' });
    } finally {
      setLoading(false);
    }
  };

  const actionLabel = action.action_type === 'refund'
    ? `Issue $${(action.amount || 0).toFixed(2)} refund`
    : action.action_type === 'cancel_order'
    ? 'Cancel order'
    : action.action_type === 'exchange'
    ? 'Process exchange'
    : action.action_type === 'change_address'
    ? 'Change delivery address'
    : action.action_type === 'discount'
    ? `Apply $${(action.amount || 0).toFixed(2)} discount`
    : action.action_type || 'Action';

  const orderRef = action.order_number || action.order_id ? `for order #${action.order_number || action.order_id}` : '';

  return (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: '6px',
      background: 'var(--bg-primary)',
      padding: compact ? '16px' : '20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Badge status={action.action_type} />
          <ConfidencePill score={action.ai_confidence} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {(action.brand_name || action.shopify_shop_name) && (
            <span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '4px', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', fontWeight: '500' }}>
              {action.brand_name || action.shopify_shop_name}
            </span>
          )}
          <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'DM Mono, monospace' }}>
            {formatDate(action.created_at)}
          </span>
        </div>
      </div>

      <div style={{ marginBottom: '8px' }}>
        <span style={{ fontWeight: '600', color: 'var(--text-primary)', fontSize: '15px' }}>
          {actionLabel} {orderRef}
        </span>
      </div>

      <div style={{ marginBottom: '12px', display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
          <strong>Customer:</strong> {action.customer_email || action.customer_name || '—'}
        </span>
        {(action.order_id || action.order_number) && (
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            <strong>Order:</strong> #{action.order_number || action.order_id}
          </span>
        )}
      </div>

      {action.reason && (
        <div style={{ padding: '8px 12px', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px', borderLeft: '3px solid var(--border-strong)' }}>
          {action.reason}
        </div>
      )}

      {/* Execution result */}
      {result && (
        <div style={{
          padding: '12px 14px', borderRadius: '4px', marginBottom: '12px', fontSize: '13px',
          background: result.success ? 'var(--success-light)' : 'var(--danger-light)',
          color: result.success ? 'var(--success)' : 'var(--danger)',
          border: `1px solid ${result.success ? 'var(--success)' : 'var(--danger)'}`,
        }}>
          {result.success ? (
            <div>
              <strong>Executed</strong>
              {result.amount && <span> — ${Number(result.amount).toFixed(2)} refund issued</span>}
              {result.shopify_refund_id && <div style={{ fontSize: '11px', opacity: 0.8, marginTop: '3px', fontFamily: 'DM Mono, monospace' }}>Shopify ID: {result.shopify_refund_id}</div>}
            </div>
          ) : (
            <div><strong>Failed:</strong> {result.error}</div>
          )}
        </div>
      )}

      {!result && !rejecting && (
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={handleApprove}
            disabled={loading}
            style={{
              padding: '8px 16px', borderRadius: '4px',
              background: loading ? 'var(--bg-tertiary)' : 'var(--success)',
              color: loading ? 'var(--text-muted)' : 'white',
              fontWeight: '500', fontSize: '13px', border: 'none',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Approving...' : 'Approve'}
          </button>
          <button
            onClick={() => setRejecting(true)}
            disabled={loading}
            style={{
              padding: '8px 16px', borderRadius: '4px', background: 'transparent',
              color: 'var(--danger)', fontWeight: '500', fontSize: '13px',
              border: '1px solid var(--danger)', cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            Reject
          </button>
        </div>
      )}

      {!result && rejecting && (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input
            autoFocus
            placeholder="Reason for rejection..."
            value={rejectReason}
            onChange={e => setRejectReason(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleReject()}
            disabled={loading}
            style={{
              flex: 1, padding: '8px 10px', border: '1px solid var(--border-strong)',
              borderRadius: '4px', fontSize: '13px', background: 'var(--bg-primary)',
            }}
          />
          <button
            onClick={handleReject}
            disabled={!rejectReason.trim() || loading}
            style={{
              padding: '8px 14px', borderRadius: '4px',
              background: rejectReason.trim() && !loading ? 'var(--danger)' : 'var(--bg-tertiary)',
              color: rejectReason.trim() && !loading ? 'white' : 'var(--text-muted)',
              fontWeight: '500', fontSize: '13px', cursor: rejectReason.trim() && !loading ? 'pointer' : 'not-allowed',
            }}
          >
            {loading ? '...' : 'Confirm'}
          </button>
          <button
            onClick={() => setRejecting(false)}
            disabled={loading}
            style={{ padding: '8px 12px', borderRadius: '4px', background: 'transparent', color: 'var(--text-secondary)', fontSize: '13px', border: '1px solid var(--border)', cursor: 'pointer' }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
