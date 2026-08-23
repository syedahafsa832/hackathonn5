import { useState, useRef, useEffect } from 'react';
import { useBrand } from '../context/BrandContext';
import { useBrandAnalytics, useEnableCancellationAutopilot, useDisableCancellationAutopilot } from '../hooks/useApi';

// Every status here maps 1:1 to what the backend's category_readiness.status
// actually computed from real action history — never an invented label.
const STATUS_META = {
  not_ready: { emoji: '⚪', label: 'Building confidence', color: '#64748B', bg: '#F8FAFC', border: '#E4E4E7' },
  almost_there: { emoji: '🟡', label: 'Almost there', color: '#B45309', bg: '#FFFBEB', border: '#FDE68A' },
  ready_for_review: { emoji: '🟢', label: 'Ready for review', color: '#059669', bg: '#ECFDF5', border: '#A7F3D0' },
};

function readinessExplanation(readiness) {
  if (!readiness) return null;
  const { status, total_requests, failed_executions } = readiness;
  if (status === 'not_ready') {
    return `Luna has handled ${total_requests} cancellation request${total_requests === 1 ? '' : 's'}. We need more verified outcomes before recommending automation.`;
  }
  if (status === 'almost_there') {
    return `Luna has enough successful outcomes, but ${failed_executions} recent Shopify execution failure${failed_executions === 1 ? '' : 's'} need${failed_executions === 1 ? 's' : ''} to be reviewed.`;
  }
  return 'Luna has enough verified cancellation outcomes to review automation.';
}

function StatBlock({ label, value }) {
  return (
    <div>
      <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '22px', fontWeight: '700', color: '#0F172A' }}>{value}</div>
      <div style={{ fontSize: '12px', color: '#94A3B8', marginTop: '2px' }}>{label}</div>
    </div>
  );
}

// Turn-on confirmation — exact copy provided by the product spec. Never
// hides that this is an automatic action; "Cancel" is the safe default.
function ConfirmEnableDialog({ onCancel, onConfirm, submitting, error }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    dialogRef.current?.focus();
    const onKeyDown = (e) => { if (e.key === 'Escape') onCancel(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onCancel]);

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(15,23,42,0.5)', padding: '20px',
      }}
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="enable-autopilot-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: '10px', width: '100%', maxWidth: '420px',
          padding: '24px', boxShadow: '0 24px 60px rgba(15,23,42,0.25)', outline: 'none',
        }}
      >
        <h3 id="enable-autopilot-title" style={{ margin: '0 0 10px', fontSize: '16px', fontWeight: '700', color: '#0F172A' }}>
          Turn on Cancellation Autopilot?
        </h3>
        <p style={{ margin: '0 0 8px', fontSize: '13.5px', color: '#334155', lineHeight: 1.55 }}>
          Luna will automatically cancel eligible orders when all of your store's cancellation rules are satisfied.
        </p>
        <p style={{ margin: '0 0 20px', fontSize: '13.5px', color: '#334155', lineHeight: 1.55 }}>
          Anything uncertain will still be sent to your team.
        </p>
        {error && (
          <p style={{ margin: '0 0 16px', fontSize: '12.5px', color: '#B91C1C', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '6px', padding: '8px 10px' }}>
            {error}
          </p>
        )}
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={submitting}
            style={{ padding: '9px 16px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: '#475569', fontSize: '13px', fontWeight: '600', cursor: submitting ? 'default' : 'pointer' }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={submitting}
            style={{ padding: '9px 16px', borderRadius: '6px', border: '1px solid #059669', background: '#059669', color: 'white', fontSize: '13px', fontWeight: '600', cursor: submitting ? 'default' : 'pointer', opacity: submitting ? 0.7 : 1 }}
          >
            {submitting ? 'Turning on…' : 'Turn on Autopilot'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ON-state card — real backend data only. An honest zero/empty state when
// Autopilot has handled nothing yet, never a fabricated number.
function AutopilotOnCard({ readiness, brandId }) {
  const disableMutation = useDisableCancellationAutopilot();
  const stats = readiness?.autopilot || {};
  const handled = stats.handled_automatically ?? 0;

  return (
    <div style={{ padding: '22px 24px', border: '1px solid #A7F3D0', borderRadius: '8px', background: '#ECFDF5' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{ fontSize: '15px' }}>🟢</span>
        <span style={{ fontSize: '14px', fontWeight: '700', color: '#059669' }}>ON</span>
      </div>
      <h3 style={{ margin: '2px 0 8px', fontSize: '15px', fontWeight: '700', color: '#0F172A' }}>Cancellation Autopilot</h3>
      <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#334155', lineHeight: 1.55 }}>
        Luna can automatically cancel eligible orders that meet your store's rules.
      </p>

      {handled === 0 ? (
        <p style={{ margin: '0 0 18px', fontSize: '13px', color: '#475569' }}>
          No cancellation requests have come in since Autopilot was turned on yet.
        </p>
      ) : (
        <div style={{ display: 'flex', gap: '28px', flexWrap: 'wrap', marginBottom: '18px' }}>
          <StatBlock label="handled automatically" value={handled} />
          {stats.success_rate != null && <StatBlock label="successful" value={`${stats.success_rate}%`} />}
          <StatBlock label="escalated for review" value={stats.escalated_for_review ?? 0} />
        </div>
      )}

      {stats.recent_escalations?.length > 0 && (
        <div style={{ marginBottom: '18px' }}>
          <p style={{ margin: '0 0 8px', fontSize: '12px', fontWeight: '700', color: '#0F172A' }}>Recent escalations</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {stats.recent_escalations.map((esc, i) => (
              <div key={i} style={{ padding: '10px 12px', borderRadius: '6px', background: 'white', border: '1px solid #E4E4E7' }}>
                <div style={{ fontSize: '12.5px', fontWeight: '600', color: '#0F172A' }}>
                  Order #{esc.order_id} was not cancelled automatically.
                </div>
                <div style={{ fontSize: '12px', color: '#64748B', marginTop: '2px' }}>Reason: {esc.reason}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {disableMutation.isError && (
        <p style={{ margin: '0 0 12px', fontSize: '12.5px', color: '#B91C1C' }}>
          Couldn't turn off Autopilot — please try again.
        </p>
      )}
      <button
        onClick={() => disableMutation.mutate(brandId)}
        disabled={disableMutation.isPending}
        style={{
          padding: '9px 16px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white',
          color: '#0F172A', fontSize: '13px', fontWeight: '600', cursor: disableMutation.isPending ? 'default' : 'pointer',
        }}
      >
        {disableMutation.isPending ? 'Turning off…' : 'Turn off Autopilot'}
      </button>
    </div>
  );
}

// OFF-state detail — readiness + the real activation control (Not
// ready / Ready for review sub-states).
function ReadinessDetail({ readiness, brandId }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const enableMutation = useEnableCancellationAutopilot();

  if (!readiness) return null;
  const meta = STATUS_META[readiness.status] || STATUS_META.not_ready;
  const canEnable = readiness.status === 'ready_for_review';

  const handleConfirm = () => {
    enableMutation.mutate(brandId, {
      onSuccess: () => setConfirmOpen(false),
    });
  };

  const enableError = enableMutation.isError
    ? (enableMutation.error?.response?.data?.detail || 'Cancellation Autopilot could not be enabled — please try again.')
    : null;

  return (
    <div style={{ padding: '22px 24px', border: `1px solid ${meta.border}`, borderRadius: '8px', background: meta.bg }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{ fontSize: '15px' }}>🔴</span>
        <span style={{ fontSize: '14px', fontWeight: '700', color: '#64748B' }}>OFF</span>
      </div>
      <h3 style={{ margin: '2px 0 14px', fontSize: '15px', fontWeight: '700', color: '#0F172A' }}>Cancellation automation</h3>

      <div style={{ display: 'flex', gap: '28px', flexWrap: 'wrap', marginBottom: '14px' }}>
        <StatBlock label="requests handled" value={readiness.total_requests} />
        <StatBlock label="successful" value={readiness.successful} />
        <StatBlock label="escalated" value={readiness.escalated} />
        {readiness.approval_rate != null && <StatBlock label="successful %" value={`${readiness.approval_rate}%`} />}
      </div>

      <p style={{ margin: '0 0 6px', fontSize: '13px', fontWeight: '600', color: '#0F172A' }}>Current mode: Copilot</p>
      <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#475569', lineHeight: 1.55 }}>
        Luna currently asks your team for approval before cancelling an order.
      </p>

      {/* Not ready / Ready for review — plain language, no internals */}
      <div style={{ padding: '12px 14px', borderRadius: '6px', background: 'white', border: `1px solid ${meta.border}`, marginBottom: '16px' }}>
        <div style={{ fontSize: '12.5px', fontWeight: '700', color: meta.color, marginBottom: '2px' }}>{meta.label}</div>
        <p style={{ margin: 0, fontSize: '13px', color: '#475569', lineHeight: 1.5 }}>{readinessExplanation(readiness)}</p>
      </div>

      {enableError && (
        <p style={{ margin: '0 0 12px', fontSize: '12.5px', color: '#B91C1C' }}>{enableError}</p>
      )}

      <button
        onClick={() => setConfirmOpen(true)}
        disabled={!canEnable}
        title={canEnable ? undefined : 'Not ready yet'}
        style={{
          padding: '9px 18px', borderRadius: '6px', border: '1px solid #E4E4E7',
          background: canEnable ? 'white' : '#F1F5F9',
          color: canEnable ? '#0F172A' : '#94A3B8',
          fontSize: '13px', fontWeight: '600', cursor: canEnable ? 'pointer' : 'not-allowed',
        }}
      >
        Enable Cancellation Autopilot
      </button>
      {!canEnable && (
        <p style={{ margin: '8px 0 0', fontSize: '11.5px', color: '#94A3B8' }}>
          Not ready yet — Luna needs more verified cancellation outcomes first. Every cancellation still requires your team's approval today.
        </p>
      )}

      {confirmOpen && (
        <ConfirmEnableDialog
          onCancel={() => setConfirmOpen(false)}
          onConfirm={handleConfirm}
          submitting={enableMutation.isPending}
          error={enableError}
        />
      )}
    </div>
  );
}

function CategoryRow({ name, description, badge, action }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px',
      padding: '16px 18px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <span style={{ fontSize: '14px', fontWeight: '700', color: '#0F172A' }}>{name}</span>
          {badge}
        </div>
        <p style={{ margin: 0, fontSize: '12.5px', color: '#94A3B8' }}>{description}</p>
      </div>
      <div style={{ flexShrink: 0 }}>{action}</div>
    </div>
  );
}

const copilotBadge = (
  <span style={{
    fontSize: '10.5px', fontWeight: '600', color: '#475569',
    background: '#F1F5F9', border: '1px solid #E4E4E7', borderRadius: '999px', padding: '2px 8px',
  }}>
    Copilot
  </span>
);

const btnBase = {
  padding: '7px 14px', borderRadius: '6px', fontSize: '12.5px', fontWeight: '600',
};

export default function Automation() {
  const { brand } = useBrand();
  const { data: analytics, isLoading } = useBrandAnalytics(brand?.id);
  const [reviewOpen, setReviewOpen] = useState(false);

  const cancellation = analytics?.category_readiness?.cancellation;
  const isOn = !!cancellation?.enabled;

  const cancellationBadge = isOn ? (
    <span style={{
      fontSize: '10.5px', fontWeight: '700', color: '#059669',
      background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '999px', padding: '2px 8px',
    }}>
      🟢 Autopilot ON
    </span>
  ) : copilotBadge;

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '32px' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Automation</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94A3B8' }}>
          From Copilot to Autopilot — decide when Luna is trusted to act on its own.
        </p>
      </div>

      {/* Value proposition, concise */}
      <section style={{ padding: '18px 20px', border: '1px solid #E4E4E7', borderRadius: '8px', background: '#F8FAFC' }}>
        <p style={{ margin: '0 0 6px', fontSize: '13px', color: '#334155', lineHeight: 1.6 }}>
          Start with human approval. As Luna proves it can handle your store's cancellation workflow correctly, you can
          choose to let it handle eligible requests automatically.
        </p>
        <p style={{ margin: 0, fontSize: '12.5px', fontWeight: '600', color: '#0E7490', letterSpacing: '0.2px' }}>
          Train → Verify → Approve → Automate
        </p>
      </section>

      {/* Category controls */}
      <section>
        <h2 style={{ margin: '0 0 12px', fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Automation Categories</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <CategoryRow
            name="Cancellation"
            badge={cancellationBadge}
            description={isOn
              ? 'Luna automatically cancels eligible orders — anything uncertain still goes to your team.'
              : "Luna prepares eligible cancellations and asks your team for approval."}
            action={
              <button
                onClick={() => setReviewOpen(v => !v)}
                style={{ ...btnBase, border: '1px solid #06B6D4', background: 'white', color: '#0E7490', cursor: 'pointer' }}
              >
                {reviewOpen ? 'Hide details' : (isOn ? 'View Autopilot' : 'Review readiness')}
              </button>
            }
          />
          <CategoryRow
            name="Refunds"
            badge={copilotBadge}
            description="Luna prepares refunds for approval."
            action={<button disabled style={{ ...btnBase, border: '1px solid #E4E4E7', background: '#F8FAFC', color: '#CBD5E1', cursor: 'not-allowed' }}>Coming soon</button>}
          />
          <CategoryRow
            name="Exchanges"
            badge={copilotBadge}
            description="Luna prepares exchange actions for approval."
            action={<button disabled style={{ ...btnBase, border: '1px solid #E4E4E7', background: '#F8FAFC', color: '#CBD5E1', cursor: 'not-allowed' }}>Coming soon</button>}
          />
        </div>
      </section>

      {/* Cancellation detail — OFF (Not ready / Ready for review) or ON */}
      {reviewOpen && (
        <section>
          {isLoading ? (
            <div className="skeleton" style={{ height: '220px', borderRadius: '8px' }} />
          ) : isOn ? (
            <AutopilotOnCard readiness={cancellation} brandId={brand?.id} />
          ) : (
            <ReadinessDetail readiness={cancellation} brandId={brand?.id} />
          )}
        </section>
      )}
    </div>
  );
}
