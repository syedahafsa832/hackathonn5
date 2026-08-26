import { useState } from 'react';
import PlanBadge from './PlanBadge';
import { SHORT_DESCRIPTIONS, FEATURES, limitsSummary, fmt } from './planCopy';

export default function PricingCard({ plan, isCurrent, isPopular, foundingActive, onUpgrade, onContactSales }) {
  const [hover, setHover] = useState(false);
  const isScale = plan.id === 'enterprise';
  const features = FEATURES[plan.id] ? FEATURES[plan.id](plan) : [];
  // "Founding price" is a plain limited-spots label, not a struck-through
  // discount — Scale never carries it (no founding price for that plan).
  const showFounding = foundingActive && (plan.id === 'starter' || plan.id === 'growth');

  const cta = isCurrent
    ? { label: 'Current Plan', disabled: true }
    : isScale
      ? { label: 'Contact Sales', action: onContactSales }
      : { label: 'Upgrade', action: () => onUpgrade(plan) };

  const accentBorder = isCurrent ? 'var(--success)' : isPopular ? 'var(--cyan-500)' : 'var(--border-default)';

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative',
        border: `1.5px solid ${accentBorder}`,
        borderRadius: '16px',
        padding: '28px',
        background: 'var(--bg-surface)',
        display: 'flex',
        flexDirection: 'column',
        boxShadow: hover && !isCurrent
          ? '0 12px 28px rgba(15,23,42,0.10)'
          : isPopular ? '0 4px 16px rgba(6,182,212,0.12)' : '0 1px 2px rgba(15,23,42,0.04)',
        transform: hover && !isCurrent ? 'translateY(-3px)' : 'none',
        transition: 'transform 0.2s ease, box-shadow 0.2s ease',
      }}
    >
      <div style={{ position: 'absolute', top: '-11px', left: '24px', display: 'flex', gap: '6px' }}>
        {isPopular && <PlanBadge tone="cyan">Most popular</PlanBadge>}
        {isCurrent && <PlanBadge tone="success">Current plan</PlanBadge>}
      </div>

      <div style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)', marginTop: '6px' }}>
        {plan.label}
      </div>
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px', minHeight: '18px' }}>
        {SHORT_DESCRIPTIONS[plan.id]}
      </div>

      {showFounding && (
        <div style={{
          alignSelf: 'flex-start', marginTop: '14px', fontSize: '11px', fontWeight: '700',
          color: 'var(--cyan-700, #0E7490)', background: 'var(--cyan-50, #ECFEFF)',
          border: '1px solid var(--cyan-200, #A5F3FC)', borderRadius: '999px', padding: '3px 10px',
        }}>
          {plan.id === 'starter' ? 'Founding price, limited founding spots' : 'Founding price'}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', margin: '18px 0 4px' }}>
        {plan.price_monthly == null ? (
          <span style={{ fontSize: '28px', fontWeight: '700', color: 'var(--text-primary)' }}>Custom</span>
        ) : (
          <>
            <span style={{ fontSize: '32px', fontWeight: '700', color: 'var(--text-primary)', fontFamily: 'DM Mono, monospace', letterSpacing: '-0.02em' }}>
              {plan.price_monthly === 0 ? 'Free' : `$${plan.price_monthly}`}
            </span>
            {plan.price_monthly !== 0 && (
              <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
                {isScale ? '/month+' : '/month'}
              </span>
            )}
          </>
        )}
      </div>

      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '18px' }}>
        {limitsSummary(plan.id, plan)}
      </div>

      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '9px', fontSize: '13px', color: 'var(--text-secondary)', flex: 1 }}>
        {features.map(f => (
          <li key={f} style={{ display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
            <span style={{ color: 'var(--cyan-500)', flexShrink: 0, fontWeight: '700', lineHeight: '1.4' }}>✓</span>
            {f}
          </li>
        ))}
      </ul>

      <button
        onClick={cta.action}
        disabled={cta.disabled}
        style={{
          marginTop: '22px', width: '100%', padding: '11px', borderRadius: '9px', border: 'none',
          fontSize: '13.5px', fontWeight: '700', cursor: cta.disabled ? 'default' : 'pointer',
          background: cta.disabled ? 'var(--slate-100)' : (isPopular ? 'var(--cyan-500)' : 'var(--text-primary)'),
          color: cta.disabled ? 'var(--text-tertiary)' : 'white',
          transition: 'opacity 0.15s',
          opacity: hover && !cta.disabled ? 0.88 : 1,
        }}
      >
        {cta.label}
      </button>
    </div>
  );
}
