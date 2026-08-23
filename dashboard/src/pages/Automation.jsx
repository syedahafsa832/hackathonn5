import { useState } from 'react';
import { useBrand } from '../context/BrandContext';
import { useBrandAnalytics } from '../hooks/useApi';

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

function ReadinessDetail({ readiness }) {
  if (!readiness) return null;
  const meta = STATUS_META[readiness.status] || STATUS_META.not_ready;
  const canEnable = readiness.status === 'ready_for_review';

  return (
    <div style={{ padding: '22px 24px', border: `1px solid ${meta.border}`, borderRadius: '8px', background: meta.bg }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{ fontSize: '15px' }}>{meta.emoji}</span>
        <span style={{ fontSize: '14px', fontWeight: '700', color: meta.color }}>{meta.label}</span>
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

      {/* "Why isn't Autopilot ready" — Phase 4, plain language, no internals */}
      <div style={{ padding: '12px 14px', borderRadius: '6px', background: 'white', border: `1px solid ${meta.border}`, marginBottom: '16px' }}>
        <div style={{ fontSize: '12.5px', fontWeight: '700', color: meta.color, marginBottom: '2px' }}>{meta.label}</div>
        <p style={{ margin: 0, fontSize: '13px', color: '#475569', lineHeight: 1.5 }}>{readinessExplanation(readiness)}</p>
      </div>

      {/* Phase 3 — the future activation control, never wired to real execution here */}
      <button
        disabled={!canEnable}
        title={canEnable ? 'Enabling Autopilot execution is not available yet' : 'Not ready yet'}
        style={{
          padding: '9px 18px', borderRadius: '6px', border: '1px solid #E4E4E7',
          background: canEnable ? 'white' : '#F1F5F9',
          color: canEnable ? '#0F172A' : '#94A3B8',
          fontSize: '13px', fontWeight: '600', cursor: 'not-allowed',
        }}
      >
        Enable Cancellation Autopilot
      </button>
      <p style={{ margin: '8px 0 0', fontSize: '11.5px', color: '#94A3B8' }}>
        Not available yet — Autopilot execution hasn't launched. Every cancellation still requires your team's approval today.
      </p>
    </div>
  );
}

function CategoryRow({ name, description, action }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px',
      padding: '16px 18px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <span style={{ fontSize: '14px', fontWeight: '700', color: '#0F172A' }}>{name}</span>
          <span style={{
            fontSize: '10.5px', fontWeight: '600', color: '#475569',
            background: '#F1F5F9', border: '1px solid #E4E4E7', borderRadius: '999px', padding: '2px 8px',
          }}>
            Copilot
          </span>
        </div>
        <p style={{ margin: 0, fontSize: '12.5px', color: '#94A3B8' }}>{description}</p>
      </div>
      <div style={{ flexShrink: 0 }}>{action}</div>
    </div>
  );
}

const btnBase = {
  padding: '7px 14px', borderRadius: '6px', fontSize: '12.5px', fontWeight: '600',
};

export default function Automation() {
  const { brand } = useBrand();
  const { data: analytics, isLoading } = useBrandAnalytics(brand?.id);
  const [reviewOpen, setReviewOpen] = useState(false);

  const cancellation = analytics?.category_readiness?.cancellation;

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '32px' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Automation</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94A3B8' }}>
          From Copilot to Autopilot — decide when Luna is trusted to act on its own.
        </p>
      </div>

      {/* Phase 5 — value proposition, concise */}
      <section style={{ padding: '18px 20px', border: '1px solid #E4E4E7', borderRadius: '8px', background: '#F8FAFC' }}>
        <p style={{ margin: '0 0 6px', fontSize: '13px', color: '#334155', lineHeight: 1.6 }}>
          Start with human approval. As Luna proves it can handle your store's cancellation workflow correctly, you can
          choose to let it handle eligible requests automatically.
        </p>
        <p style={{ margin: 0, fontSize: '12.5px', fontWeight: '600', color: '#0E7490', letterSpacing: '0.2px' }}>
          Train → Verify → Approve → Automate
        </p>
      </section>

      {/* Phase 2 — category controls */}
      <section>
        <h2 style={{ margin: '0 0 12px', fontSize: '15px', fontWeight: '600', color: '#0F172A' }}>Automation Categories</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <CategoryRow
            name="Cancellation"
            description="Luna prepares eligible cancellations and asks your team for approval."
            action={
              <button
                onClick={() => setReviewOpen(v => !v)}
                style={{ ...btnBase, border: '1px solid #06B6D4', background: 'white', color: '#0E7490', cursor: 'pointer' }}
              >
                {reviewOpen ? 'Hide readiness' : 'Review readiness'}
              </button>
            }
          />
          <CategoryRow
            name="Refunds"
            description="Luna prepares refunds for approval."
            action={<button disabled style={{ ...btnBase, border: '1px solid #E4E4E7', background: '#F8FAFC', color: '#CBD5E1', cursor: 'not-allowed' }}>Coming soon</button>}
          />
          <CategoryRow
            name="Exchanges"
            description="Luna prepares exchange actions for approval."
            action={<button disabled style={{ ...btnBase, border: '1px solid #E4E4E7', background: '#F8FAFC', color: '#CBD5E1', cursor: 'not-allowed' }}>Coming soon</button>}
          />
        </div>
      </section>

      {/* Phase 1/3/4 — cancellation readiness detail */}
      {reviewOpen && (
        <section>
          {isLoading ? (
            <div className="skeleton" style={{ height: '220px', borderRadius: '8px' }} />
          ) : (
            <ReadinessDetail readiness={cancellation} />
          )}
        </section>
      )}
    </div>
  );
}
