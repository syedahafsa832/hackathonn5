import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Crown } from 'lucide-react';
import client from '../api/client';

const PAID_PLANS = ['starter', 'growth', 'enterprise'];
const LAST_SEEN_PLAN_KEY = 'resolv_last_seen_plan';

function Stat({ label, used, limit, danger }) {
  if (limit == null) return null;
  return (
    <span style={{ color: danger ? '#B91C1C' : '#64748B' }}>
      {label}: <strong style={{ color: danger ? '#B91C1C' : '#0F172A' }}>{used}/{limit}</strong>
    </span>
  );
}

// Plan/trial/usage summary — powers the dashboard's plan/usage widget.
// Fetches from GET /api/v1/settings/usage. Renders differently per plan:
// admin (unlimited badge), trial (days left + AI/email usage against the
// abuse-protection caps), free (ticket/AI-reply usage + upgrade), paid
// (current limits, no upgrade nag).
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
        background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '6px', fontSize: '13px',
      }}>
        <span style={{ fontWeight: '600', color: '#065F46' }}>Admin Account</span>
        <span style={{ color: '#065F46' }}>Unlimited Access</span>
      </div>
    );
  }

  const { plan, plan_label, trial_days_remaining, plan_days_remaining, was_previously_paid, usage_today, limits, upgrade_required } = usage;
  const isTrial = plan === 'trial';
  const isFree = plan === 'free' || plan === 'founding_free';
  const isPaid = PAID_PLANS.includes(plan);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {justActivated && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px', padding: '10px 16px',
          background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '6px', fontSize: '13px', color: '#065F46',
        }}>
          <Crown size={15} />
          <span>Your account is now active on the <strong>{justActivated}</strong> plan. Thanks for upgrading!</span>
        </div>
      )}

      {was_previously_paid && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px',
          padding: '10px 16px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', fontSize: '13px',
        }}>
          <span style={{ color: '#92400E' }}>Your paid plan expired 30 days after activation and your account has reverted to Free.</span>
          <Link to="/upgrade" style={{ padding: '5px 12px', borderRadius: '4px', background: '#F59E0B', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none', flexShrink: 0 }}>
            Resubscribe →
          </Link>
        </div>
      )}

      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px',
        padding: '12px 16px', background: upgrade_required ? '#FEF2F2' : '#F8FAFC',
        border: `1px solid ${upgrade_required ? '#FCA5A5' : '#E4E4E7'}`, borderRadius: '6px', fontSize: '13px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontWeight: '600', color: '#0F172A' }}>
            {isPaid && <Crown size={14} color="#D97706" />}
            {plan_label || 'Free'} plan
          </span>

          {isTrial && typeof trial_days_remaining === 'number' && (
            <span style={{ color: '#64748B' }}>
              Trial ends in {trial_days_remaining} day{trial_days_remaining === 1 ? '' : 's'}
            </span>
          )}

          {isPaid && typeof plan_days_remaining === 'number' && (
            <span style={{ color: '#64748B' }}>
              Renews in {plan_days_remaining} day{plan_days_remaining === 1 ? '' : 's'}
            </span>
          )}

          {isTrial && (
            <>
              <Stat label="AI replies" used={usage_today?.ai_replies ?? 0} limit={limits?.ai_replies_per_day} />
              <Stat label="Emails" used={usage_today?.emails ?? 0} limit={limits?.emails_per_day} />
            </>
          )}

          {isFree && (
            <>
              <Stat label="Tickets" used={usage_today?.tickets ?? 0} limit={limits?.tickets_per_day} danger={upgrade_required} />
              <Stat label="AI replies" used={usage_today?.ai_replies ?? 0} limit={limits?.ai_replies_per_day} />
            </>
          )}

          {!isTrial && !isFree && (
            <>
              <Stat label="Tickets" used={usage_today?.tickets ?? 0} limit={limits?.tickets_per_day} />
              <Stat label="AI replies" used={usage_today?.ai_replies ?? 0} limit={limits?.ai_replies_per_day} />
            </>
          )}
        </div>

        {!was_previously_paid && (
          <Link
            to="/upgrade"
            style={{ padding: '5px 12px', borderRadius: '4px', background: '#06B6D4', color: 'white', fontSize: '12px', fontWeight: '600', textDecoration: 'none', flexShrink: 0 }}
          >
            Upgrade →
          </Link>
        )}
      </div>
    </div>
  );
}
