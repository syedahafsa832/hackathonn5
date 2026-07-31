import { useState, useEffect } from 'react';
import client from '../api/client';
import { useMe } from '../hooks/useApi';

function fmt(n) {
  return n === null || n === undefined ? 'Unlimited' : n.toLocaleString();
}

export default function Upgrade() {
  const { data: me } = useMe();
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ name: '', email: '', brand: '', plan: '' });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    document.title = 'Upgrade — tResolv';
  }, []);

  useEffect(() => {
    client.get('/api/v2/plans').then(res => setPlans(res.data.plans || [])).finally(() => setLoading(false));
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
    <div style={{ padding: '24px', maxWidth: '920px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '18px', fontWeight: '700', color: '#0F172A', marginBottom: '4px' }}>Plans</h2>
        <p style={{ fontSize: '13px', color: '#64748B' }}>
          Your current plan: <strong>{me?.plan || '—'}</strong>. Payment is confirmed manually (bank transfer) —
          submit a request below and we'll activate it once payment is received.
        </p>
      </div>

      {loading ? (
        <div className="skeleton" style={{ height: '200px', borderRadius: '8px' }} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px', marginBottom: '32px' }}>
          {plans.map(p => (
            <div
              key={p.id}
              onClick={() => pickPlan(p.id)}
              style={{
                border: '1px solid ' + (form.plan === p.id ? '#06B6D4' : '#E4E4E7'),
                boxShadow: form.plan === p.id ? '0 0 0 2px #ECFEFF' : 'none',
                borderRadius: '10px',
                padding: '18px',
                cursor: 'pointer',
                background: 'white',
              }}
            >
              <div style={{ fontSize: '14px', fontWeight: '700', color: '#0F172A', marginBottom: '10px' }}>{p.label}</div>
              <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '12.5px', color: '#475569' }}>
                <li>{fmt(p.tickets_per_day)} tickets/day</li>
                <li>{fmt(p.ai_replies_per_day)} AI replies/day</li>
                <li>{fmt(p.brands)} brand(s)</li>
                <li>{fmt(p.users)} user(s)</li>
              </ul>
              <div style={{
                marginTop: '14px', textAlign: 'center', padding: '7px', borderRadius: '6px',
                fontSize: '12.5px', fontWeight: '600',
                background: form.plan === p.id ? '#06B6D4' : '#F8FAFC',
                color: form.plan === p.id ? 'white' : '#64748B',
              }}>
                {form.plan === p.id ? 'Selected' : 'Select'}
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ maxWidth: '440px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '700', color: '#0F172A', marginBottom: '4px' }}>Request Upgrade</h3>
        <p style={{ fontSize: '12px', color: '#94A3B8', marginBottom: '16px' }}>
          No automatic checkout — we'll follow up with bank transfer details.
        </p>

        {submitted ? (
          <div style={{ padding: '14px', background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '6px', color: '#16A34A', fontSize: '13px', fontWeight: '500' }}>
            Request submitted. We'll be in touch to confirm payment and activate your plan.
          </div>
        ) : (
          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <input
              required placeholder="Your name" value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              style={{ padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '14px' }}
            />
            <input
              required type="email" placeholder="Email" value={form.email}
              onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
              style={{ padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '14px' }}
            />
            <input
              placeholder="Brand / store name (optional)" value={form.brand}
              onChange={e => setForm(f => ({ ...f, brand: e.target.value }))}
              style={{ padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '14px' }}
            />
            <select
              required value={form.plan}
              onChange={e => setForm(f => ({ ...f, plan: e.target.value }))}
              style={{ padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px', fontSize: '14px' }}
            >
              <option value="">Select a plan…</option>
              {plans.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>

            {error && <div style={{ fontSize: '12.5px', color: '#DC2626' }}>{error}</div>}

            <button
              type="submit"
              disabled={submitting}
              style={{
                padding: '10px', borderRadius: '6px', border: 'none', background: '#06B6D4',
                color: 'white', fontSize: '13px', fontWeight: '600', cursor: 'pointer',
                opacity: submitting ? 0.6 : 1,
              }}
            >
              {submitting ? 'Submitting…' : 'Request Upgrade'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
