import { useState, useEffect } from 'react';
import client from '../api/client';
import { useMe } from '../hooks/useApi';
import PricingCard from '../components/upgrade/PricingCard';
import PricingCardSkeleton from '../components/upgrade/PricingCardSkeleton';
import UpgradeModal from '../components/upgrade/UpgradeModal';
import PlanBadge from '../components/upgrade/PlanBadge';

const CALENDAR_URL = 'https://calendar.app.google/YkSqLTsYr18bUP2Z6';
const SKELETON_COUNT = 4; // matches free/starter/growth/enterprise — no layout shift on load

export default function Upgrade() {
  const { data: me } = useMe();
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [foundingActive, setFoundingActive] = useState(false);
  const [foundingSlotsRemaining, setFoundingSlotsRemaining] = useState(null);
  const [upgradePlan, setUpgradePlan] = useState(null); // plan object -> modal open, null -> closed

  useEffect(() => {
    document.title = 'Upgrade — tResolv';
  }, []);

  useEffect(() => {
    // Both requests fire in parallel (independent .then() chains, neither
    // awaits the other) — only /plans gates the page's loading state since
    // founding-status is a non-blocking pricing-badge enhancement, not
    // something the initial render needs to wait on.
    client.get('/api/v2/plans').then(res => setPlans(res.data.plans || [])).finally(() => setLoading(false));
    client.get('/api/v2/founding-status')
      .then(res => {
        setFoundingActive(!!res.data?.active);
        setFoundingSlotsRemaining(res.data?.slots_remaining ?? null);
      })
      .catch(() => {});
  }, []);

  return (
    <div style={{ padding: '40px 32px 60px', display: 'flex', flexDirection: 'column', gap: '32px', maxWidth: '1080px' }}>
      <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: '10px', maxWidth: '520px', margin: '0 auto' }}>
        {foundingActive && (
          <div>
            <PlanBadge tone="warning">🔥 Founding pricing — {foundingSlotsRemaining} of 20 spots left</PlanBadge>
          </div>
        )}
        <h1 style={{ margin: 0, fontSize: '28px', fontWeight: '700', color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          Simple, transparent pricing
        </h1>
        <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
          Upgrade anytime. Payments are verified manually — submit your transaction reference after sending payment
          and we'll activate your plan.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '18px' }}>
        {loading
          ? Array.from({ length: SKELETON_COUNT }, (_, i) => <PricingCardSkeleton key={i} />)
          : plans.map(p => (
            <PricingCard
              key={p.id}
              plan={p}
              isCurrent={p.id === me?.plan}
              isPopular={p.id === 'growth'}
              foundingActive={foundingActive}
              onUpgrade={setUpgradePlan}
              onContactSales={() => window.open(CALENDAR_URL, '_blank', 'noopener,noreferrer')}
            />
          ))}
      </div>

      {upgradePlan && (
        <UpgradeModal plan={upgradePlan} me={me} onClose={() => setUpgradePlan(null)} />
      )}
    </div>
  );
}
