import { useState } from 'react';
import {
  useEmailAutomations, useCreateEmailAutomation, useUpdateEmailAutomation,
  usePendingEmailSends, useSendPendingEmail, useDismissPendingEmail,
} from '../hooks/useApi';
import { extractErrorMessage } from '../api/client';
import Alert from './Alert';

const TRIGGERS = [
  { value: 'cancel_order', label: 'Cancellation confirmation', help: 'Sent after Luna successfully cancels an order for a customer.' },
  { value: 'refund', label: 'Refund confirmation', help: 'Sent after a refund has actually gone through, never before.' },
  { value: 'exchange', label: 'Exchange confirmation', help: 'Sent after an exchange is confirmed or ready.' },
  { value: 'change_address', label: 'Address change confirmation', help: "Sent after a customer's shipping address is updated." },
];

const VARIABLE_LABELS = {
  customer_name: 'Customer name',
  order_number: 'Order number',
  order_status: 'Order status',
  brand_name: 'Your store name',
  refund_amount: 'Refund amount',
};

const card = { background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '18px 20px' };
const label = { display: 'block', fontSize: '12.5px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '5px' };
const inputStyle = { width: '100%', padding: '9px 11px', border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13.5px', background: 'white', color: 'var(--text-primary)' };

function InfoDot({ text }) {
  return (
    <span
      title={text}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: '15px', height: '15px', borderRadius: '50%', border: '1px solid #CBD5E1',
        color: '#94A3B8', fontSize: '10.5px', fontWeight: '600', cursor: 'help', flexShrink: 0,
      }}
    >
      i
    </span>
  );
}

function statusMeta(automation) {
  if (!automation) return { label: 'Not set up', color: '#94A3B8', bg: '#F8FAFC', border: '#E4E4E7' };
  if (!automation.enabled) return { label: 'Draft (not sending)', color: '#94A3B8', bg: '#F8FAFC', border: '#E4E4E7' };
  if (automation.requires_approval) return { label: 'On, waits for your OK', color: '#B45309', bg: '#FFFBEB', border: '#FDE68A' };
  return { label: 'On, sends automatically', color: '#059669', bg: '#ECFDF5', border: '#A7F3D0' };
}

function EditorForm({ trigger, existing, brandId, onDone }) {
  const [name, setName] = useState(existing?.name || TRIGGERS.find(t => t.value === trigger)?.label || '');
  const [subject, setSubject] = useState(existing?.subject || '');
  const [body, setBody] = useState(existing?.body || '');
  const [enabled, setEnabled] = useState(existing?.enabled ?? false);
  const [requiresApproval, setRequiresApproval] = useState(existing?.requires_approval ?? true);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');

  const createMutation = useCreateEmailAutomation(brandId);
  const updateMutation = useUpdateEmailAutomation(brandId);
  const saving = createMutation.isPending || updateMutation.isPending;

  const availableVars = ['customer_name', 'order_number', 'order_status', 'brand_name', ...(trigger === 'refund' ? ['refund_amount'] : [])];

  const insertVariable = (setter) => (varName) => {
    setter(v => `${v}{{${varName}}}`);
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const payload = { name, subject, body, enabled, requires_approval: requiresApproval };
      if (existing) {
        await updateMutation.mutateAsync({ automationId: existing.id, payload });
      } else {
        await createMutation.mutateAsync({ trigger, ...payload });
      }
      onDone();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not save this email.'));
    }
  };

  // Renders the CURRENT unsaved form fields with realistic sample data —
  // matches the backend's own render_template's literal {{var}} rules, so
  // this always shows exactly what the saved automation will produce, even
  // before the first Save.
  const handlePreview = () => {
    let rendered = { subject, body };
    const sample = { customer_name: 'Alex', order_number: '#1234', order_status: 'Cancelled', brand_name: 'Your Store', refund_amount: 'PKR 49.00' };
    for (const [k, v] of Object.entries(sample)) {
      rendered = {
        subject: rendered.subject.replaceAll(`{{${k}}}`, v),
        body: rendered.body.replaceAll(`{{${k}}}`, v),
      };
    }
    setPreview(rendered);
  };

  return (
    <form onSubmit={handleSave} style={{ ...card, marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div>
        <label style={label}>Email name</label>
        <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} required maxLength={120} placeholder="e.g. Cancellation confirmation" />
      </div>

      <div>
        <label style={label}>Subject line</label>
        <input style={inputStyle} value={subject} onChange={e => setSubject(e.target.value)} required maxLength={255} placeholder="Your order {{order_number}} has been cancelled" />
      </div>

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '5px' }}>
          <label style={{ ...label, marginBottom: 0 }}>Email body</label>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {availableVars.map(v => (
              <button
                key={v}
                type="button"
                onClick={() => insertVariable(setBody)(v)}
                style={{ fontSize: '11px', padding: '3px 8px', borderRadius: '999px', border: '1px solid #E4E4E7', background: '#F8FAFC', color: '#0E7490', cursor: 'pointer' }}
              >
                + {VARIABLE_LABELS[v] || v}
              </button>
            ))}
          </div>
        </div>
        <textarea
          style={{ ...inputStyle, minHeight: '140px', resize: 'vertical', fontFamily: 'inherit' }}
          value={body}
          onChange={e => setBody(e.target.value)}
          required
          placeholder={`Hi {{customer_name}},\n\nYour order {{order_number}} is now {{order_status}}.\n\nThanks,\n{{brand_name}}`}
        />
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Turn this email on
      </label>

      <div>
        <div style={{ ...label, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
          When it's ready to send
          <InfoDot text="This only controls the EMAIL. The underlying action (cancelling, refunding, etc.) still always follows your existing approval rules. This email only ever goes out after that action has actually succeeded." />
        </div>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '13px', color: 'var(--text-primary)', marginBottom: '8px', cursor: 'pointer' }}>
          <input type="radio" checked={requiresApproval} onChange={() => setRequiresApproval(true)} style={{ marginTop: '3px' }} />
          <span>Let me approve each one first <span style={{ color: '#94A3B8' }}>, it'll wait in a queue for you to send</span></span>
        </label>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '13px', color: 'var(--text-primary)', cursor: 'pointer' }}>
          <input type="radio" checked={!requiresApproval} onChange={() => setRequiresApproval(false)} style={{ marginTop: '3px' }} />
          <span>Luna sends it automatically <span style={{ color: '#94A3B8' }}>, as soon as the action succeeds</span></span>
        </label>
      </div>

      <Alert variant="error">{error}</Alert>

      {preview && (
        <div style={{ padding: '14px 16px', border: '1px solid #E4E4E7', borderRadius: '6px', background: '#F8FAFC' }}>
          <div style={{ fontSize: '11px', fontWeight: '700', color: '#94A3B8', textTransform: 'uppercase', marginBottom: '6px' }}>Preview (sample data)</div>
          <div style={{ fontSize: '13px', fontWeight: '600', marginBottom: '6px' }}>{preview.subject}</div>
          <div style={{ fontSize: '13px', whiteSpace: 'pre-wrap', color: 'var(--text-secondary)' }}>{preview.body}</div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '10px' }}>
        <button type="button" onClick={handlePreview} style={{ padding: '9px 16px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: 'var(--text-primary)', fontSize: '13px', fontWeight: '600', cursor: 'pointer' }}>
          Preview
        </button>
        <button type="submit" disabled={saving} style={{ padding: '9px 16px', borderRadius: '6px', border: 'none', background: '#06B6D4', color: 'white', fontSize: '13px', fontWeight: '600', cursor: saving ? 'not-allowed' : 'pointer' }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onDone} style={{ padding: '9px 16px', borderRadius: '6px', border: 'none', background: 'transparent', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function PendingOutbox({ brandId }) {
  const { data } = usePendingEmailSends(brandId);
  const sendMutation = useSendPendingEmail(brandId);
  const dismissMutation = useDismissPendingEmail(brandId);
  const pending = data?.pending || [];

  if (pending.length === 0) return null;

  return (
    <section>
      <h3 style={{ margin: '0 0 4px', fontSize: '13.5px', fontWeight: '700', color: '#0F172A' }}>Waiting for your OK</h3>
      <p style={{ margin: '0 0 10px', fontSize: '12px', color: '#94A3B8' }}>These emails are ready. Nothing is sent until you approve.</p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {pending.map(p => (
          <div key={p.id} style={{ padding: '12px 14px', border: '1px solid #FDE68A', background: '#FFFBEB', borderRadius: '6px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', marginBottom: '6px', flexWrap: 'wrap' }}>
              <div style={{ fontSize: '13px', fontWeight: '600' }}>{p.subject}</div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button onClick={() => sendMutation.mutate(p.id)} disabled={sendMutation.isPending} style={{ fontSize: '12px', fontWeight: '600', padding: '4px 10px', borderRadius: '6px', border: 'none', background: '#06B6D4', color: 'white', cursor: 'pointer' }}>Send</button>
                <button onClick={() => dismissMutation.mutate(p.id)} disabled={dismissMutation.isPending} style={{ fontSize: '12px', fontWeight: '600', padding: '4px 10px', borderRadius: '6px', border: '1px solid #E4E4E7', background: 'white', color: 'var(--text-secondary)', cursor: 'pointer' }}>Dismiss</button>
              </div>
            </div>
            <div style={{ fontSize: '12px', color: '#64748B' }}>To: {p.to_email}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function EmailAutomations({ brandId }) {
  const { data, isLoading } = useEmailAutomations(brandId);
  const [editingTrigger, setEditingTrigger] = useState(null);

  const automations = data?.automations || [];
  const byTrigger = Object.fromEntries(automations.map(a => [a.trigger, a]));

  if (isLoading) {
    return <div className="skeleton" style={{ height: '160px', borderRadius: '8px' }} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <p style={{ margin: 0, fontSize: '12.5px', color: '#94A3B8' }}>
        Write the confirmation email customers get after Luna finishes one of these. Luna only ever sends it once the action has actually succeeded.
      </p>

      <PendingOutbox brandId={brandId} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {TRIGGERS.map(t => {
          const existing = byTrigger[t.value];
          const meta = statusMeta(existing);
          const isEditing = editingTrigger === t.value;
          return (
            <div key={t.value} style={card}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ fontSize: '13.5px', fontWeight: '700', color: '#0F172A' }}>{t.label}</span>
                  <InfoDot text={t.help} />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '10.5px', fontWeight: '600', color: meta.color, background: meta.bg, border: `1px solid ${meta.border}`, borderRadius: '999px', padding: '2px 8px' }}>
                    {meta.label}
                  </span>
                  <button
                    onClick={() => setEditingTrigger(isEditing ? null : t.value)}
                    style={{ fontSize: '12px', fontWeight: '600', color: '#0E7490', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
                  >
                    {isEditing ? 'Close' : existing ? 'Edit' : 'Set up'}
                  </button>
                </div>
              </div>
              {isEditing && (
                <EditorForm
                  trigger={t.value}
                  existing={existing}
                  brandId={brandId}
                  onDone={() => setEditingTrigger(null)}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
