import { useNavigate } from 'react-router-dom';
import { useBrand } from '../context/BrandContext';
import { useTrainingReadiness } from '../hooks/useApi';

const LIFECYCLE = ['Teach Luna', 'Copilot', 'Review', 'Correct', 'Verify', 'Approve', 'Autopilot'];

const STATUS_META = {
  not_ready: { emoji: '⚪', label: 'Not ready', color: '#64748B', bg: '#F8FAFC', border: '#E4E4E7' },
  almost_there: { emoji: '🟡', label: 'Almost there', color: '#B45309', bg: '#FFFBEB', border: '#FDE68A' },
  ready_for_review: { emoji: '🟢', label: 'Ready for review', color: '#059669', bg: '#ECFDF5', border: '#A7F3D0' },
};

// Same real-numbers-only explanation pattern already used on the Automation
// page (readinessExplanation/refundReadinessExplanation) — never a bare
// "Not ready", and never a new threshold beyond total_requests/failed_executions.
function readinessReason(readiness, categoryLabel) {
  if (!readiness) return '';
  const { status, total_requests, failed_executions } = readiness;
  if (status === 'not_ready') {
    if (!total_requests) return `No verified ${categoryLabel.toLowerCase()} outcomes yet. Luna is still handling these with your team's approval.`;
    return `Luna has handled ${total_requests} ${categoryLabel.toLowerCase()} request${total_requests === 1 ? '' : 's'}. More verified outcomes are needed before this is ready.`;
  }
  if (status === 'almost_there') {
    return `Luna has enough outcomes, but ${failed_executions} recent execution failure${failed_executions === 1 ? '' : 's'} need${failed_executions === 1 ? 's' : ''} review.`;
  }
  return `Luna has enough verified ${categoryLabel.toLowerCase()} outcomes for your team to review automation.`;
}

const card = { background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px 22px' };
const sectionLabel = { fontSize: '12px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#94A3B8', marginBottom: '4px' };
const bigTitle = { fontSize: '15px', fontWeight: '700', color: '#0F172A' };

function CheckRow({ ok, label, detail }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', padding: '10px 0', borderBottom: '1px solid #F1F5F9' }}>
      <span style={{ fontSize: '14px', color: ok ? '#059669' : '#CBD5E1', flexShrink: 0, marginTop: '1px' }}>{ok ? '✓' : '○'}</span>
      <div>
        <div style={{ fontSize: '13px', fontWeight: '600', color: '#0F172A' }}>{label}</div>
        {detail && <div style={{ fontSize: '12px', color: '#94A3B8', marginTop: '1px' }}>{detail}</div>}
      </div>
    </div>
  );
}

function StatBlock({ label, value }) {
  return (
    <div>
      <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>{value ?? '—'}</div>
      <div style={{ fontSize: '11.5px', color: '#94A3B8', marginTop: '2px' }}>{label}</div>
    </div>
  );
}

function CategoryReadinessRow({ label, readiness, financial }) {
  const navigate = useNavigate();
  if (!readiness) return null;
  const badge = readiness.enabled
    ? { emoji: '🟢', label: 'Autopilot ON', color: '#059669', bg: '#ECFDF5', border: '#A7F3D0' }
    : readiness.autopilot_capable
      ? (STATUS_META[readiness.status] || STATUS_META.not_ready)
      : { emoji: '🔵', label: 'Copilot only', color: '#0E7490', bg: '#ECFEFF', border: '#A5F3FC' };

  return (
    <div style={{ padding: '14px 16px', border: '1px solid #E4E4E7', borderRadius: '8px', background: '#FAFAFA' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '13.5px', fontWeight: '700', color: '#0F172A' }}>{label}</span>
          {financial && (
            <span style={{ fontSize: '10px', fontWeight: '600', color: '#9A3412', background: '#FFF7ED', border: '1px solid #FED7AA', borderRadius: '999px', padding: '1px 7px' }}>
              💳 Financial
            </span>
          )}
        </div>
        <span style={{ fontSize: '10.5px', fontWeight: '600', color: badge.color, background: badge.bg, border: `1px solid ${badge.border}`, borderRadius: '999px', padding: '2px 8px' }}>
          {badge.emoji} {badge.label}
        </span>
      </div>
      <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', marginBottom: '8px' }}>
        <StatBlock label="requests handled" value={readiness.total_requests} />
        <StatBlock label="successful" value={readiness.successful} />
        <StatBlock label="rejected by team" value={readiness.escalated} />
        <StatBlock label="execution failures" value={readiness.failed_executions} />
      </div>
      <p style={{ margin: '0 0 8px', fontSize: '12.5px', color: '#475569', lineHeight: 1.5 }}>
        {readinessReason(readiness, label)}
      </p>
      {readiness.autopilot_capable && (
        <button
          onClick={() => navigate('/automation')}
          style={{ fontSize: '12px', fontWeight: '600', color: '#0E7490', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
        >
          Review {label} Automation →
        </button>
      )}
    </div>
  );
}

export default function Training() {
  const { brand } = useBrand();
  const navigate = useNavigate();
  const { data, isLoading, isError } = useTrainingReadiness(brand?.id);

  if (isLoading) {
    return (
      <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '160px', borderRadius: '8px' }} />)}
      </div>
    );
  }

  if (isError || !data) {
    return <div style={{ padding: '28px 32px', fontSize: '13px', color: '#94A3B8' }}>Training data isn't available yet.</div>;
  }

  const { train, verify, automate } = data;

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '24px', maxWidth: '920px' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Train Luna</h1>
        <p style={{ margin: 0, fontSize: '13px', color: '#94A3B8' }}>
          You're training and supervising an AI employee, not just configuring a chatbot.
        </p>
      </div>

      {/* Lifecycle */}
      <div style={{ ...card, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px', padding: '14px 18px' }}>
        {LIFECYCLE.map((step, i) => (
          <span key={step} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '12.5px', fontWeight: '600', color: '#0E7490' }}>{step}</span>
            {i < LIFECYCLE.length - 1 && <span style={{ color: '#CBD5E1' }}>→</span>}
          </span>
        ))}
      </div>

      {/* TRAIN */}
      <section style={card}>
        <div style={sectionLabel}>Train</div>
        <div style={{ ...bigTitle, marginBottom: '10px' }}>What has Luna learned?</div>
        <CheckRow ok={train.knowledge.has_any} label="Brand knowledge" detail={`${train.knowledge.completed_count} of ${train.knowledge.sources_count} sources indexed`} />
        <CheckRow ok={train.policies.has_any} label="Store policies" detail={train.policies.has_any ? 'Configured' : 'Not set yet'} />
        <CheckRow ok={train.examples.count > 0} label="Uploaded examples" detail={`${train.examples.count} added`} />
        <CheckRow
          ok={train.reply_style.learned}
          label="Learned Reply Style"
          detail={train.reply_style.learned ? 'Learned ✓' : `${train.reply_style.approved_reply_count} / ${train.reply_style.min_replies_required} human-approved replies`}
        />
        <div style={{ marginTop: '10px' }}>
          <button onClick={() => navigate('/settings')} style={{ fontSize: '12px', fontWeight: '600', color: '#0E7490', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}>
            Manage Reply Style & Knowledge →
          </button>
        </div>
      </section>

      {/* VERIFY */}
      <section style={card}>
        <div style={sectionLabel}>Verify</div>
        <div style={{ ...bigTitle, marginBottom: '10px' }}>How is Luna performing?</div>
        <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <StatBlock label="AI conversations" value={verify.total_ai_conversations} />
          <StatBlock label="reviewed by your team" value={verify.conversations_reviewed} />
          <StatBlock label="needing review" value={verify.conversations_needing_review} />
          {verify.approval_rate != null && <StatBlock label="approval rate" value={`${verify.approval_rate}%`} />}
          {verify.edit_rate != null && <StatBlock label="edit rate" value={`${verify.edit_rate}%`} />}
          {verify.rejection_rate != null && <StatBlock label="rejection rate" value={`${verify.rejection_rate}%`} />}
          {verify.csat && <StatBlock label="CSAT" value={`${verify.csat.average} / 5`} />}
        </div>
        <button
          onClick={() => navigate('/review')}
          style={{ padding: '9px 16px', borderRadius: '6px', border: '1px solid #06B6D4', background: 'white', color: '#0E7490', fontSize: '13px', fontWeight: '600', cursor: 'pointer' }}
        >
          {verify.conversations_needing_review > 0
            ? `Review Luna's Work (${verify.conversations_needing_review} waiting)`
            : "Review Luna's Work"}
        </button>
      </section>

      {/* AUTOMATE */}
      <section style={card}>
        <div style={sectionLabel}>Automate</div>
        <div style={{ ...bigTitle, marginBottom: '10px' }}>Which automation category is ready?</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <CategoryReadinessRow label="Cancellation" readiness={automate.cancellation} />
          <CategoryReadinessRow label="Refunds" readiness={automate.refund} financial />
          <CategoryReadinessRow label="Exchanges" readiness={automate.exchange} />
          <CategoryReadinessRow label="Address changes" readiness={automate.address_change} />
        </div>
      </section>
    </div>
  );
}
