import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Crown, Sparkles, PartyPopper, Clock } from 'lucide-react';
import client from '../api/client';

const PAID_PLANS = ['starter', 'growth', 'enterprise'];
const LAST_SEEN_PLAN_KEY = 'resolv_last_seen_plan';
const CHANNEL_LABELS = { gmail: 'Gmail', chat_widget: 'Chat Widget', web_form: 'Web Form' };

const card = {
  padding: '26px 30px',
  borderRadius: '16px',
  background: 'var(--bg-primary, #FFFFFF)',
  border: '1px solid var(--border, #E4E4E7)',
  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.03), 0 8px 24px rgba(15, 23, 42, 0.04)',
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
};

const upgradeLink = {
  alignSelf: 'flex-start',
  padding: '10px 22px',
  borderRadius: '10px',
  background: '#06B6D4',
  color: 'white',
  fontSize: '13px',
  fontWeight: '600',
  letterSpacing: '0.01em',
  textDecoration: 'none',
  transition: 'opacity 0.15s ease',
};

function progressColor(pct) {
  if (pct >= 90) return '#EF4444'; // red — 90%+
  if (pct >= 60) return '#F59E0B'; // amber — 60-90%
  return '#10B981'; // green — 0-60%
}

function ProgressBar({ used, limit }) {
  if (limit == null) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return (
    <div style={{ height: '8px', borderRadius: '999px', background: 'var(--bg-tertiary, #E4E4E7)', overflow: 'hidden' }}>
      <div
        style={{
          height: '100%',
          width: `${pct}%`,
          borderRadius: '999px',
          background: progressColor(pct),
          transition: 'width 0.6s cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      />
    </div>
  );
}

function ChannelBreakdown({ breakdown }) {
  const entries = Object.entries(breakdown || {}).filter(([, count]) => count > 0);
  if (entries.length === 0) return null;
  return (
    <div style={{ fontSize: '12px', color: '#94A3B8', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
      {entries.map(([ch, count]) => (
        <span key={ch}>
          {CHANNEL_LABELS[ch] || ch}: <strong style={{ color: '#64748B' }}>{count}</strong>
        </span>
      ))}
    </div>
  );
}

// In-progress trial — premium card: progress bar, days remaining, channel
// breakdown (the "Analytics" line), Upgrade CTA. Replaces the old debug-panel
// style label/number row entirely for the trial state.
function TrialCard({ usage }) {
  const { trial_days_remaining, ai_replies_used_trial, ai_replies_trial_limit, ai_replies_trial_remaining, ai_replies_breakdown } = usage;
  const used = ai_replies_used_trial ?? 0;
  const limit = ai_replies_trial_limit ?? 25;
  const remaining = ai_replies_trial_remaining ?? Math.max(0, limit - used);

  // Urgency ramps up as the trial nears its end — same card, just a louder
  // countdown badge, so the days-remaining number is unmissable without a
  // second component or a redesign.
  const isUrgent = typeof trial_days_remaining === 'number' && trial_days_remaining <= 3;
  const daysBadge = isUrgent
    ? { background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#DC2626' }
    : { background: 'var(--bg-tertiary, #F1F5F9)', border: '1px solid transparent', color: '#64748B' };

  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Sparkles size={16} color="#06B6D4" />
          <span style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary, #0F172A)' }}>Free Trial</span>
        </div>
        {typeof trial_days_remaining === 'number' && (
          <span style={{
            fontSize: '12px', fontWeight: isUrgent ? '700' : '500', padding: '4px 10px', borderRadius: '999px',
            ...daysBadge,
          }}>
            {trial_days_remaining === 0
              ? 'Ends today'
              : `${trial_days_remaining} day${trial_days_remaining === 1 ? '' : 's'} remaining`}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: '13px' }}>
          <span style={{ color: '#64748B' }}>AI Replies</span>
          <span style={{ fontWeight: '700', color: 'var(--text-primary, #0F172A)' }}>{used} / {limit} used</span>
        </div>
        <ProgressBar used={used} limit={limit} />
        <span style={{ fontSize: '12px', color: '#94A3B8' }}>
          {remaining} AI repl{remaining === 1 ? 'y' : 'ies'} left
        </span>
      </div>

      <ChannelBreakdown breakdown={ai_replies_breakdown} />

      <p style={{ fontSize: '12px', color: '#94A3B8', margin: 0, lineHeight: '1.5' }}>
        Let Luna handle more customer conversations before your trial ends.
      </p>

      <Link to="/upgrade" style={upgradeLink}>Upgrade Plan →</Link>
    </div>
  );
}

// Trial ended — two variants sharing one layout. Deliberately NOT a blocking
// modal: rendered in the same dashboard slot as every other state, so the
// sidebar/Conversations/Settings/Shopify/Gmail stay reachable throughout.
function TrialEndedCard({ variant }) {
  const isLimitReached = variant === 'limit_reached';
  return (
    <div style={{
      ...card,
      background: 'linear-gradient(135deg, #0F172A 0%, #1E293B 100%)',
      border: '1px solid #1E293B',
      color: 'white',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        {isLimitReached ? <PartyPopper size={20} color="#FBBF24" /> : <Clock size={20} color="#94A3B8" />}
        <h3 style={{ fontSize: '16px', fontWeight: '700', margin: 0 }}>
          {isLimitReached ? "You've experienced Luna" : 'Your 14-day trial has ended'}
        </h3>
      </div>

      <p style={{ fontSize: '13px', color: '#CBD5E1', margin: 0, lineHeight: '1.6' }}>
        {isLimitReached
          ? "You've used all 25 AI replies included in your free trial. Your workspace is still available, but Luna needs an upgraded plan to continue handling customer conversations."
          : 'Upgrade to continue using Luna and automate your customer support.'}
      </p>

      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 12px',
        background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.25)', borderRadius: '8px',
      }}>
        <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#F87171', flexShrink: 0 }} />
        <span style={{ fontSize: '12px', color: '#FCA5A5', fontWeight: '600' }}>
          AI replies are paused. New customer emails are still received, but Luna won't respond until you upgrade.
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', fontSize: '12px', color: '#94A3B8' }}>
        <span>✓ Shopify remains connected</span>
        <span>✓ Gmail remains connected</span>
        <span>✓ Knowledge Base remains available</span>
        <span>✓ Your previous conversations remain available</span>
      </div>

      <Link to="/upgrade" style={{ ...upgradeLink, marginTop: '4px' }}>Upgrade Now →</Link>
    </div>
  );
}

function Stat({ label, used, limit, danger }) {
  if (limit == null) return null;
  return (
    <span style={{ color: danger ? '#B91C1C' : '#64748B' }}>
      {label}: <strong style={{ color: danger ? '#B91C1C' : 'var(--text-primary, #0F172A)' }}>{used}/{limit}</strong>
    </span>
  );
}

// Plan/trial/usage summary — powers the dashboard's plan/usage widget.
// Fetches from GET /api/v1/settings/usage. Renders differently per state:
// admin (unlimited badge), trial-in-progress (premium card above), trial
// ended by quota or by time (TrialEndedCard), free (ticket/AI-reply usage +
// upgrade), paid (current limits, no upgrade nag).
export default function PlanUsageWidget() {
  const [usage, setUsage] = useState(null);
  const [justActivated, setJustActivated] = useState(null); // plan_label string, or null

  useEffect(() => {
    client.get('/api/v1/settings/usage')
      .then(res => {
        const data = res.data;
        setUsage(data);

        // One-time "you're now on plan X" confirmation: compare against the
        // last plan we saw for this browser. Only fires on an actual move
        // INTO a paid plan (not on every load, not on trial/free states),
        // and only once — updating localStorage immediately means a refresh
        // won't show it again.
        const lastSeen = localStorage.getItem(LAST_SEEN_PLAN_KEY);
        if (data?.plan && PAID_PLANS.includes(data.plan) && lastSeen !== data.plan && !data.is_super_admin) {
          setJustActivated(data.plan_label || data.plan);
        }
        if (data?.plan) {
          localStorage.setItem(LAST_SEEN_PLAN_KEY, data.plan);
        }
      })
      .catch(() => {}); // non-critical widget — fail silently, don't block the dashboard
  }, []);

  if (!usage) return null;

  if (usage.is_super_admin) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 16px',
        background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '8px', fontSize: '13px',
      }}>
        <span style={{ fontWeight: '600', color: '#065F46' }}>Admin Account</span>
        <span style={{ color: '#065F46' }}>Unlimited Access</span>
      </div>
    );
  }

  const {
    plan, plan_label, trial_days_remaining, plan_days_remaining, was_previously_paid,
    usage_today, limits, upgrade_required, tickets_used_this_period, tickets_period,
    trial_expired, ai_replies_trial_remaining,
  } = usage;
  const isTrial = plan === 'trial';
  const isFree = plan === 'free' || plan === 'founding_free';
  const isPaid = PAID_PLANS.includes(plan);
  const ticketsLabel = tickets_period === 'month' ? 'Conversations' : 'Tickets';
  const ticketsUsed = tickets_used_this_period ?? usage_today?.tickets ?? 0;
  const ticketsLimit = limits?.tickets_per_month ?? limits?.tickets_per_day;

  const justActivatedBanner = justActivated && (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px',
      background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '8px', fontSize: '13px', color: '#065F46',
    }}>
      <Crown size={15} />
      <span>Your account is now active on the <strong>{justActivated}</strong> plan. Thanks for upgrading!</span>
    </div>
  );

  // Paid plan lapsed — most specific/recent status, takes priority over the
  // (older) trial-expired signal a tenant might also carry.
  if (was_previously_paid) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {justActivatedBanner}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px',
          padding: '12px 16px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '8px', fontSize: '13px',
        }}>
          <span style={{ color: '#92400E' }}>Your paid plan expired 30 days after activation and your account has reverted to Free.</span>
          <Link to="/upgrade" style={{ padding: '5px 12px', borderRadius: '6px', background: '#F59E0B', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none', flexShrink: 0 }}>
            Resubscribe →
          </Link>
        </div>
      </div>
    );
  }

  // Trial, quota exhausted (still within the 14 days).
  if (isTrial && typeof ai_replies_trial_remaining === 'number' && ai_replies_trial_remaining <= 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {justActivatedBanner}
        <TrialEndedCard variant="limit_reached" />
      </div>
    );
  }

  // Trial, still active — the premium in-progress card.
  if (isTrial) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {justActivatedBanner}
        <TrialCard usage={usage} />
      </div>
    );
  }

  // Trial window ran out before 25 replies were used (auto-downgraded to free).
  if (isFree && trial_expired) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {justActivatedBanner}
        <TrialEndedCard variant="time_expired" />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {justActivatedBanner}

      <div style={{
        ...card,
        flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap',
        padding: '14px 20px',
        background: upgrade_required ? '#FEF2F2' : 'var(--bg-primary, #F8FAFC)',
        borderColor: upgrade_required ? '#FCA5A5' : 'var(--border, #E4E4E7)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontWeight: '600', color: 'var(--text-primary, #0F172A)' }}>
            {isPaid && <Crown size={14} color="#D97706" />}
            {plan_label || 'Free'} plan
          </span>

          {isPaid && typeof plan_days_remaining === 'number' && (
            <span style={{ color: '#64748B', fontSize: '13px' }}>
              Renews in {plan_days_remaining} day{plan_days_remaining === 1 ? '' : 's'}
            </span>
          )}

          {isFree && (
            <>
              <Stat label="Tickets" used={usage_today?.tickets ?? 0} limit={limits?.tickets_per_day} danger={upgrade_required} />
              <Stat label="AI replies" used={usage_today?.ai_replies ?? 0} limit={limits?.ai_replies_per_day} />
            </>
          )}

          {isPaid && (
            <Stat label={ticketsLabel} used={ticketsUsed} limit={ticketsLimit} danger={upgrade_required} />
          )}
        </div>

        <Link to="/upgrade" style={{ padding: '6px 14px', borderRadius: '6px', background: '#06B6D4', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none', flexShrink: 0 }}>
          Upgrade →
        </Link>
      </div>
    </div>
  );
}
