import { useState } from 'react';
import { Upload } from 'lucide-react';
import client from '../../api/client';
import Alert from '../Alert';

const inputStyle = {
  padding: '10px 12px',
  border: '1px solid var(--border-default)',
  borderRadius: '8px',
  fontSize: '13.5px',
  color: 'var(--text-primary)',
  background: 'var(--bg-surface)',
  outline: 'none',
  transition: 'border-color 0.15s',
  width: '100%',
};

function Field({ label, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-secondary)' }}>{label}</span>
      {children}
    </label>
  );
}

export default function UpgradeForm({ plan, initialName, initialEmail, onSuccess }) {
  const [form, setForm] = useState({
    name: initialName || '', email: initialEmail || '', brand: '', transaction_reference: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await client.post('/api/v2/upgrade-requests', { ...form, plan: plan.id });
      onSuccess();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to submit request.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <Field label="Name">
        <input
          required placeholder="Your name" value={form.name}
          onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
          style={inputStyle}
          onFocus={e => e.target.style.borderColor = 'var(--cyan-500)'}
          onBlur={e => e.target.style.borderColor = 'var(--border-default)'}
        />
      </Field>
      <Field label="Email">
        <input
          required type="email" placeholder="you@company.com" value={form.email}
          onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
          style={inputStyle}
          onFocus={e => e.target.style.borderColor = 'var(--cyan-500)'}
          onBlur={e => e.target.style.borderColor = 'var(--border-default)'}
        />
      </Field>
      <Field label="Brand / store name (optional)">
        <input
          placeholder="Your store name" value={form.brand}
          onChange={e => setForm(f => ({ ...f, brand: e.target.value }))}
          style={inputStyle}
          onFocus={e => e.target.style.borderColor = 'var(--cyan-500)'}
          onBlur={e => e.target.style.borderColor = 'var(--border-default)'}
        />
      </Field>
      <Field label="Plan">
        <div style={{ ...inputStyle, background: 'var(--bg-elevated)', color: 'var(--text-primary)', fontWeight: '600' }}>
          {plan.label}
        </div>
      </Field>
      <Field label="Transaction reference (optional)">
        <input
          placeholder="Reference / proof of payment" value={form.transaction_reference}
          onChange={e => setForm(f => ({ ...f, transaction_reference: e.target.value }))}
          style={inputStyle}
          onFocus={e => e.target.style.borderColor = 'var(--cyan-500)'}
          onBlur={e => e.target.style.borderColor = 'var(--border-default)'}
        />
      </Field>
      <Field label="Payment screenshot">
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px', padding: '12px',
          border: '1.5px dashed var(--border-default)', borderRadius: '8px',
          color: 'var(--text-tertiary)', fontSize: '12.5px', cursor: 'not-allowed',
        }}>
          <Upload size={14} />
          Coming soon. For now, just include your transaction reference above
        </div>
      </Field>

      <Alert variant="error">{error}</Alert>

      <button
        type="submit"
        disabled={submitting}
        style={{
          padding: '11px', borderRadius: '9px', border: 'none', background: 'var(--cyan-500)',
          color: 'white', fontSize: '13.5px', fontWeight: '700', cursor: submitting ? 'not-allowed' : 'pointer',
          opacity: submitting ? 0.6 : 1, marginTop: '4px',
        }}
      >
        {submitting ? 'Submitting…' : 'Submit request'}
      </button>
    </form>
  );
}
