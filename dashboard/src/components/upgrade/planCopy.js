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
  starter: 'For founders who are tired of answering the same customer questions',
  growth: 'For Shopify brands ready to stop managing their inbox themselves',
  enterprise: 'For brands where support is becoming a real operational bottleneck',
};

// Qualitative capabilities only — numeric caps live in limitsSummary() so
// the two don't get visually jumbled into one long checklist. tResolv is
// single-store-per-account, so brand/store count is never a plan dimension
// here — usage limits and feature tier are what differ. Growth's list is
// what it adds ON TOP of Starter (matches the landing page's "Includes
// everything in Starter, plus:" framing) — the value difference between
// Starter and Growth isn't extra conversations, it's autopilot taking
// responsibility for routine tickets instead of just assisting.
export const FEATURES = {
  free: () => [
    'Supervised AI support',
    'Gmail + Shopify integration',
  ],
  starter: () => [
    'Gmail + Shopify',
    'AI email support',
    'Live chat widget',
    'Live Shopify order lookup',
    'Shipping & tracking questions',
    'Product/order questions',
    'Returns & policy questions',
    'AI-drafted replies',
    'Human approval before replies/actions',
    'Confidence scoring',
    'Email quarantine / loop protection',
  ],
  growth: () => [
    'Autopilot for high-confidence routine tickets',
    'Shopify action workflows',
    'Refund approval workflows',
    'Cancellation approval workflows',
    'Address-change workflows',
    'AfterShip live tracking',
    'Live storefront chat',
    'Customizable Luna identity',
    'Priority support',
  ],
  enterprise: () => [
    'Higher/custom conversation volume',
    'Full autopilot where supported',
    'Shopify action execution with existing approval safeguards',
    'Custom integrations',
    'Custom agent identity/branding',
    'AfterShip',
    'Priority support',
    'Dedicated onboarding',
    'Custom SLA options',
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
      return 'Higher/custom conversation volume';
    default:
      return '';
  }
}
