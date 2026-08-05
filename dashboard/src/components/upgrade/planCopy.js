// Marketing copy matching tresolv.online's Pricing component — the landing
// page is the source of truth for wording. Numeric limits are always
// interpolated from the live /api/v2/plans response so they can never
// silently drift from what plan_service.py actually enforces; only the
// qualitative feature bullets and short descriptions are static copy with
// nothing behind them in PLAN_LIMITS to stay in sync with.

export function fmt(n) {
  return n === null || n === undefined ? 'Unlimited' : n.toLocaleString();
}

export const SHORT_DESCRIPTIONS = {
  free: 'For testing tResolv before you commit',
  starter: 'For a growing Shopify store',
  growth: 'Best for a store using AI every day',
  enterprise: 'For high-volume stores needing custom infrastructure',
};

// Qualitative capabilities only — numeric caps live in limitsSummary() so
// the two don't get visually jumbled into one long checklist. tResolv is
// single-store-per-account, so brand/store count is never a plan dimension
// here — usage limits and feature tier are what differ.
export const FEATURES = {
  free: () => [
    'Supervised AI support',
    'Gmail + Shopify integration',
  ],
  starter: () => [
    'Supervised AI support',
    'Gmail + Shopify integration',
    'Chat widget',
    'Email support',
  ],
  growth: () => [
    'Autopilot mode',
    'Shopify actions (refunds, cancellations, updates)',
    'Chat widget',
    'AfterShip tracking',
    'Priority support',
  ],
  enterprise: () => [
    'Dedicated account manager',
    'Chat widget',
    'Custom agent name and branding',
    'SLA guarantee',
    'Custom integrations',
  ],
};

export function limitsSummary(planId, p) {
  switch (planId) {
    case 'free':
      return `${fmt(p.tickets_per_day)} tickets/day · ${fmt(p.users)} user`;
    case 'starter':
      return `${fmt(p.tickets_per_month)} conversations/mo`;
    case 'growth':
      return p.tickets_per_month == null ? 'Unlimited conversations' : `${fmt(p.tickets_per_month)} conversations/mo`;
    case 'enterprise':
      return 'Unlimited conversations';
    default:
      return '';
  }
}
