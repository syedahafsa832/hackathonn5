import { useState, useEffect } from 'react';
import client from '../api/client';
import { useMe } from '../hooks/useApi';

const CALENDAR_URL = 'https://calendar.app.google/YkSqLTsYr18bUP2Z6';

function fmt(n) {
  return n === null || n === undefined ? 'Unlimited' : n.toLocaleString();
}

// Marketing copy matching tresolv.online's Pricing component verbatim — the
// landing page is the source of truth for wording. Numeric bullets (brand
// count, conversations/month) are interpolated from the live /api/v2/plans
// response so they can never silently drift from what plan_service.py
// actually enforces; the rest is pure descriptive copy with nothing behind
// it in PLAN_LIMITS to stay in sync with.
const FEATURES = {
  starter: (p) => [
    `${fmt(p.brands)} Shopify brand${p.brands === 1 ? '' : 's'}`,
    `${fmt(p.tickets_per_month)} conversations per month`,
    'Supervised AI support',
    'Gmail + Shopify integration',
    'Chat widget',
    'Email support',
  ],
  growth: (p) => [
    `Up to ${fmt(p.brands)} brands`,
    p.tickets_per_month == null ? 'Unlimited conversations' : `${fmt(p.tickets_per_month)} conversations per month`,
    'Autopilot mode',
    'Shopify actions (refunds, cancellations, updates)',
    'Chat widget',
    'AfterShip tracking',
    'Priority support',
  ],
  enterprise: () => [
    'Unlimited brands',
    'Unlimited conversations',
    'Dedicated account manager',
    'Chat widget (unlimited brands)',
    'Custom agent name and branding',
    'SLA guarantee',
    'Custom integrations',
  ],
};

const inputStyle = {
  padding: '9px 12px',
  border: '1px solid #E4E4E7',
  borderRadius: '6px',
  fontSize: '14px',
  color: '#0F172A',
  background: 'white',
  outline: 'none',
  transition: 'border-color 0.15s',
};

export default function Upgrade() {
  const { data: me } = useMe();
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [foundingActive, setFoundingActive] = useState(false);
  const [foundingSlotsRemaining, setFoundingSlotsRemaining] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', brand: '', plan: '', transaction_reference: '' });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    document.title = 'Upgrade — tResolv';
  }, []);

  useEffect(() => {
    client.get('/api/v2/plans').then(res => setPlans(res.data.plans || [])).finally(() => setLoading(false));
    // Founding pricing only shows while slots remain — same "first 20
    // brands" framing as the landing page. Fails closed (no founding
    // pricing shown) if this request fails, so we never show a
    // discount we can't actually honor.
    client.get('/api/v2/founding-status')
      .then(res => {
        setFoundingActive(!!res.data?.active);
        setFoundingSlotsRemaining(res.data?.slots_remaining ?? null);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (me) setForm(f => ({ ...f, name: f.name || me.company_name || '', email: f.email || me.email || '' }));
  }, [me]);

  const pickPlan = (planId) => {
    setForm(f => ({ ...f, plan: planId }));
    setSubmitted(false);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!form.plan) { setError('Select a plan first.'); return; }
    setSubmitting(true);
    setError('');
    try {
      await client.post('/api/v2/upgrade-requests', form);
      setSubmitted(true);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to submit request.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: '28px', maxWidth: '920px' }}>
      <div>
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: '#0F172A', marginBottom: '6px' }}>Plans</h2>
        <p style={{ fontSize: '13px', color: '#64748B', margin: 0 }}>
          Your current plan: <strong style={{ color: '#0F172A' }}>{plans.find(p => p.id === me?.plan)?.label || me?.plan || '—'}</strong>. Payment is confirmed manually (bank transfer) —
          submit a request below and we'll activate it once payment is received.
        </p>
      </div>

      {loading ? (
        <div className="skeleton" style={{ height: '200px', borderRadius: '8px' }} />
      ) : (
        <>
          {foundingActive && (
            <div style={{ fontSize: '12px', color: '#B45309', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', padding: '8px 12px', fontWeight: '600' }}>
              🔥 Founding pricing — {foundingSlotsRemaining} of 20 spots left
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
            {plans.map(p => {
              if (p.id === 'free') {
                // Not on the landing page pricing table — exists for
                // trial/demo purposes, kept in its original simple form.
                return (
                  <div
                    key={p.id}
                    onClick={() => pickPlan(p.id)}
                    style={{
                      border: '1px solid ' + (form.plan === p.id ? '#06B6D4' : '#E4E4E7'),
                      boxShadow: form.plan === p.id ? '0 0 0 2px #ECFEFF' : 'none',
                      borderRadius: '8px', padding: '20px', cursor: 'pointer', background: 'white',
                      transition: 'border-color 0.15s, box-shadow 0.15s',
                    }}
                  >
                    <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>{p.label}</div>
                    <div style={{ fontSize: '20px', fontWeight: '700', color: '#0F172A', marginBottom: '12px', fontFamily: 'DM Mono, monospace' }}>Free</div>
                    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '13px', color: '#64748B' }}>
                      <li>{fmt(p.tickets_per_day)} tickets/day</li>
                      <li>{fmt(p.brands)} brand</li>
                      <li>{fmt(p.users)} user</li>
                    </ul>
                    <div style={{
                      marginTop: '14px', textAlign: 'center', padding: '7px', borderRadius: '6px',
                      fontSize: '13px', fontWeight: '600',
                      background: form.plan === p.id ? '#06B6D4' : '#F8FAFC',
                      color: form.plan === p.id ? 'white' : '#64748B',
                    }}>
                      {form.plan === p.id ? 'Selected' : 'Select'}
                    </div>
                  </div>
                );
              }

              const isScale = p.id === 'enterprise';
              const isGrowth = p.id === 'growth';
              const isPopular = isGrowth;
              const features = FEATURES[p.id] ? FEATURES[p.id](p) : [];
              const showFounding = foundingActive && p.price_monthly_original != null;
              const displayPrice = showFounding ? p.price_monthly : (p.price_monthly_original ?? p.price_monthly);

              return (
                <div
                  key={p.id}
                  onClick={() => !isScale && pickPlan(p.id)}
                  style={{
                    position: 'relative',
                    border: '1px solid ' + (isPopular ? '#06B6D4' : (form.plan === p.id ? '#06B6D4' : '#E4E4E7')),
                    boxShadow: form.plan === p.id ? '0 0 0 2px #ECFEFF' : 'none',
                    borderRadius: '8px',
                    padding: '20px',
                    cursor: isScale ? 'default' : 'pointer',
                    background: 'white',
                    transition: 'border-color 0.15s, box-shadow 0.15s',
                  }}
                >
                  {isPopular && (
                    <div style={{ position: 'absolute', top: '-11px', left: '16px', background: '#06B6D4', color: 'white', fontSize: '11px', fontWeight: '700', padding: '3px 10px', borderRadius: '10px' }}>
                      Most popular
                    </div>
                  )}
                  <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>{p.label}</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '12px' }}>
                    {isScale ? (
                      <span style={{ fontSize: '20px', fontWeight: '700', color: '#0F172A' }}>Contact us</span>
                    ) : (
                      <>
                        {showFounding && (
                          <span style={{ fontSize: '14px', color: '#94A3B8', textDecoration: 'line-through', fontFamily: 'DM Mono, monospace' }}>
                            ${p.price_monthly_original}
                          </span>
                        )}
                        <span style={{ fontSize: '20px', fontWeight: '700', color: '#0F172A', fontFamily: 'DM Mono, monospace' }}>${displayPrice}</span>
                        <span style={{ fontSize: '12px', color: '#94A3B8' }}>/month</span>
                      </>
                    )}
                  </div>
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '13px', color: '#64748B' }}>
                    {features.map(f => (
                      <li key={f} style={{ display: 'flex', gap: '6px' }}>
                        <span style={{ color: '#06B6D4', flexShrink: 0 }}>✓</span>
                        {f}
                      </li>
                    ))}
                  </ul>

                  {isGrowth && (
                    <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '12px', marginBottom: 0, lineHeight: '1.5' }}>
                      Multi-brand management is provisioned manually during onboarding for Growth customers — reach out after claiming your spot.
                    </p>
                  )}

                  {isScale ? (
                    <a
                      href={CALENDAR_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={e => e.stopPropagation()}
                      style={{
                        display: 'block', marginTop: '14px', textAlign: 'center', padding: '7px', borderRadius: '6px',
                        fontSize: '13px', fontWeight: '600', background: '#F8FAFC', color: '#06B6D4', textDecoration: 'none',
                      }}
                    >
                      Talk to us →
                    </a>
                  ) : (
                    <div style={{
                      marginTop: '14px', textAlign: 'center', padding: '7px', borderRadius: '6px',
                      fontSize: '13px', fontWeight: '600',
                      background: form.plan === p.id ? '#06B6D4' : '#F8FAFC',
                      color: form.plan === p.id ? 'white' : '#64748B',
                    }}>
                      {form.plan === p.id ? 'Selected' : 'Select'}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <section style={{ flex: '1', minWidth: '300px', maxWidth: '440px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px' }}>
          <h3 style={{ margin: 0, fontSize: '15px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>Bank Transfer Details</h3>
          <p style={{ fontSize: '12px', color: '#94A3B8', marginBottom: '16px', marginTop: 0 }}>
            Send payment to the account below, then paste your transaction reference in the form — this helps us confirm and activate your account faster.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '13px' }}>
            {[
              ['Bank Name', 'United Bank Limited (UBL)'],
              ['Account Title', 'Bushra Zohaib'],
              ['Account Number', '1052342011278'],
              ['IBAN', 'PK09UNIL0109000342011278'],
              ['SWIFT Code (BIC)', 'UNILPKKA'],
              ['Country', 'Pakistan'],
              ['City', 'Karachi, Sindh'],
              ['Postcode', '75800'],
              ['Street/Region', 'Block A, North Nazimabad'],
            ].map(([label, value]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', padding: '6px 0', borderBottom: '1px solid #F1F5F9' }}>
                <span style={{ color: '#64748B' }}>{label}</span>
                <span style={{ color: '#0F172A', fontWeight: '500', fontFamily: 'DM Mono, monospace', textAlign: 'right' }}>{value}</span>
              </div>
            ))}
          </div>
        </section>

        <section style={{ flex: '1', minWidth: '300px', maxWidth: '440px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px' }}>
        <h3 style={{ margin: 0, fontSize: '15px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>Request Upgrade</h3>
        <p style={{ fontSize: '12px', color: '#94A3B8', marginBottom: '16px', marginTop: 0 }}>
          No automatic checkout — send payment using the bank details on the left, then submit this form.
        </p>

        {submitted ? (
          <div style={{ padding: '14px 16px', background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '6px', color: '#16A34A', fontSize: '13px', fontWeight: '500' }}>
            Request submitted. We'll be in touch to confirm payment and activate your plan.
            {form.transaction_reference ? '' : ' Forgot to add a transaction reference? Just reply to any of our emails with it.'}
          </div>
        ) : (
          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <input
              required placeholder="Your name" value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
            <input
              required type="email" placeholder="Email" value={form.email}
              onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
            <input
              placeholder="Brand / store name (optional)" value={form.brand}
              onChange={e => setForm(f => ({ ...f, brand: e.target.value }))}
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
            <select
              required value={form.plan}
              onChange={e => setForm(f => ({ ...f, plan: e.target.value }))}
              style={{ ...inputStyle, cursor: 'pointer' }}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            >
              <option value="">Select a plan…</option>
              {plans.filter(p => p.id !== 'enterprise').map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
            <input
              placeholder="Transaction reference / proof of payment (optional)" value={form.transaction_reference}
              onChange={e => setForm(f => ({ ...f, transaction_reference: e.target.value }))}
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />

            {error && <div style={{ fontSize: '12.5px', color: '#DC2626' }}>{error}</div>}

            <button
              type="submit"
              disabled={submitting}
              style={{
                padding: '10px', borderRadius: '6px', border: 'none', background: '#06B6D4',
                color: 'white', fontSize: '13px', fontWeight: '600', cursor: submitting ? 'not-allowed' : 'pointer',
                opacity: submitting ? 0.6 : 1,
              }}
            >
              {submitting ? 'Submitting…' : 'Request Upgrade'}
            </button>
          </form>
        )}
        </section>
      </div>
    </div>
  );
}
