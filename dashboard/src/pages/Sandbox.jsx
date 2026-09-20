import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import Alert from '../components/Alert';

// Luna Sandbox: a self-contained sample-store experience. Every request here
// goes to /api/v2/sandbox/* only - static sample fixtures on the server. It
// never sends a brand/tenant/store id and cannot touch a real Shopify store,
// inbox, or customer. Do not reuse ActionCard/production action endpoints
// here: approving in this page must only ever update sandbox state.

const money = (n, cur = 'USD') => (cur === 'USD' ? `$${Number(n).toFixed(2)}` : `${Number(n).toFixed(2)} ${cur}`);
const REVEAL_MS = 260;

const card = { background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px 20px' };

function SandboxBadge({ children }) {
  return (
    <span style={{ fontSize: '11px', fontWeight: 600, padding: '3px 8px', borderRadius: '10px', background: 'var(--warning-light, #FFFBEB)', color: 'var(--warning, #B45309)', letterSpacing: '0.02em' }}>
      {children}
    </span>
  );
}

function StepList({ steps, revealed }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }} aria-live="polite">
      {steps.slice(0, revealed).map((s) => {
        const approval = s.status === 'needs_approval';
        const icon = approval ? '⚠' : s.status === 'rejected' ? '✕' : '✓';
        const color = approval ? 'var(--warning, #B45309)' : s.status === 'rejected' ? 'var(--danger, #EF4444)' : 'var(--success, #16A34A)';
        return (
          <div key={s.key} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
            <span style={{ color, fontWeight: 700, lineHeight: '20px', flexShrink: 0 }}>{icon}</span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-primary)' }}>{s.label}</div>
              <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>{s.detail}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Bubble({ role, label, body, draft }) {
  const customer = role === 'customer';
  return (
    <div style={{ display: 'flex', justifyContent: customer ? 'flex-start' : 'flex-end' }}>
      <div style={{ maxWidth: '85%' }}>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '3px', textAlign: customer ? 'left' : 'right', fontWeight: 500 }}>{label}</div>
        <div style={{
          padding: '10px 14px', borderRadius: '8px', fontSize: '14px', lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          background: customer ? 'var(--bg-tertiary)' : draft ? 'transparent' : 'var(--accent)',
          color: customer ? 'var(--text-primary)' : draft ? 'var(--text-secondary)' : 'white',
          border: draft ? '1px dashed var(--border-strong)' : 'none',
        }}>{body}</div>
      </div>
    </div>
  );
}

function ApprovalCard({ action, busy, onDecide }) {
  return (
    <div style={{ ...card, border: '1px solid var(--warning, #F59E0B)', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ fontSize: '13px', fontWeight: 600 }}>⚠ Action requires your approval</div>
        <SandboxBadge>Sandbox action · sample store</SandboxBadge>
      </div>
      <div style={{ fontSize: '14px', fontWeight: 600 }}>{action.title}</div>
      <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{action.summary}</div>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        In production this waits for you before anything happens in Shopify. Here, approving only updates the sample store.
      </div>
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <button onClick={() => onDecide('approve')} disabled={busy} style={{ padding: '9px 20px', borderRadius: '4px', fontWeight: 600, fontSize: '13px', background: 'var(--success, #16A34A)', color: 'white', cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.6 : 1 }}>
          {busy ? 'Working…' : 'Approve'}
        </button>
        <button onClick={() => onDecide('reject')} disabled={busy} style={{ padding: '9px 20px', borderRadius: '4px', fontWeight: 600, fontSize: '13px', border: '1px solid var(--border-strong)', color: 'var(--text-secondary)', background: 'transparent', cursor: busy ? 'not-allowed' : 'pointer' }}>
          Reject
        </button>
      </div>
    </div>
  );
}

function Evidence({ evidence }) {
  const { order, product, source, policy } = evidence || {};
  if (!order && !product && !source && !policy) return null;
  return (
    <div style={{ ...card, fontSize: '12.5px', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Sample data Luna used</div>
      {order && (
        <div>
          <strong style={{ color: 'var(--text-primary)' }}>Order #{order.order_number}</strong> · {order.customer_name} · {money(order.total, order.currency)}<br />
          {order.financial_status} · {order.cancelled_at ? 'cancelled' : order.fulfillment_status}
          {order.line_items?.map((i, idx) => <div key={idx}>{i.quantity}× {i.title} ({i.variant})</div>)}
        </div>
      )}
      {product && (
        <div>
          <strong style={{ color: 'var(--text-primary)' }}>{product.title}</strong> · {money(product.price, product.currency)}
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '4px' }}>
            {product.variants.map(v => (
              <span key={v.size} style={{ padding: '2px 8px', borderRadius: '4px', background: v.inventory ? 'var(--success-light, #ECFDF5)' : 'var(--bg-tertiary)', color: v.inventory ? 'var(--success, #16A34A)' : 'var(--text-muted)' }}>
                {v.size}: {v.inventory ? v.inventory : 'sold out'}
              </span>
            ))}
          </div>
        </div>
      )}
      {(source || policy) && (
        <div>
          <strong style={{ color: 'var(--text-primary)' }}>{(source || policy).title}</strong>
          <div style={{ marginTop: '2px', fontStyle: 'italic' }}>“{(source || policy).excerpt}”</div>
        </div>
      )}
    </div>
  );
}

function AskLuna() {
  const [message, setMessage] = useState('');
  const [remaining, setRemaining] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    client.get('/api/v2/sandbox/ask/status').then(r => setRemaining(r.data?.remaining)).catch(() => {});
  }, []);

  const send = async () => {
    if (!message.trim() || busy) return;
    setBusy(true); setError(''); setResult(null);
    try {
      const res = await client.post('/api/v2/sandbox/ask', { message });
      setResult(res.data);
      if (typeof res.data?.remaining === 'number') setRemaining(res.data.remaining);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not reach the sandbox right now.'));
    } finally { setBusy(false); }
  };

  const limitCopy = {
    tenant_limit: "You've used today's free custom runs. The instant demos above are unlimited.",
    ip_limit: 'Too many sandbox runs from this network today. Try the instant demos above.',
    global_limit: "Custom runs are paused for today because the sample store is busy. The instant demos above still work.",
    busy: 'Luna is busy helping real customers right now. Try again shortly, or use the instant demos above.',
  };

  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ fontSize: '14px', fontWeight: 600 }}>Ask Luna your own question</div>
        {remaining !== null && <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{remaining} free run{remaining === 1 ? '' : 's'} left today</span>}
      </div>
      <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
        Type any customer message. Luna answers from the sample store only (live AI, limited free runs).
      </div>
      <textarea
        value={message} onChange={e => setMessage(e.target.value.slice(0, 500))} rows={3} maxLength={500}
        placeholder="e.g. Do you ship free over $75?"
        style={{ width: '100%', boxSizing: 'border-box', padding: '10px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px', resize: 'vertical', background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
      />
      <div>
        <button onClick={send} disabled={busy || !message.trim() || remaining === 0} style={{ padding: '9px 18px', borderRadius: '4px', fontWeight: 600, fontSize: '13px', background: 'var(--accent)', color: 'white', cursor: busy || !message.trim() || remaining === 0 ? 'not-allowed' : 'pointer', opacity: busy || !message.trim() || remaining === 0 ? 0.6 : 1 }}>
          {busy ? 'Luna is thinking…' : 'Ask Luna'}
        </button>
      </div>
      <Alert variant="error">{error}</Alert>
      {result && (result.reply ? (
        <Bubble role="ai" label={result.ok ? 'Luna · live AI · sample store' : 'Luna · sandbox'} body={result.reply} />
      ) : (
        <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{limitCopy[result.reason] || 'This run could not be completed.'}</div>
      ))}
    </div>
  );
}

export default function Sandbox() {
  const { scenarioId } = useParams();
  const navigate = useNavigate();
  const [meta, setMeta] = useState(null);
  const [scenario, setScenario] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [revealed, setRevealed] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef(null);

  useEffect(() => {
    document.title = 'Test Luna: tResolv';
    client.get('/api/v2/sandbox/scenarios')
      .then(r => setMeta(r.data))
      .catch(err => setError(extractErrorMessage(err, 'Could not load the sandbox. Please try again.')));
  }, []);

  // The selected scenario is driven by the URL so refresh/deep links work.
  useEffect(() => {
    clearTimeout(timer.current);
    setOutcome(null); setScenario(null); setRevealed(0);
    if (!scenarioId) return;
    let cancelled = false;
    setLoading(true); setError('');
    client.get(`/api/v2/sandbox/scenarios/${encodeURIComponent(scenarioId)}`)
      .then(r => { if (!cancelled) setScenario(r.data); })
      .catch(err => { if (!cancelled) setError(err.response?.status === 404 ? "That sandbox scenario doesn't exist." : extractErrorMessage(err, 'Could not load this scenario.')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scenarioId]);

  // Presentation-only: steps are already computed server-side; this just
  // reveals them one by one so the investigation is easy to follow.
  useEffect(() => {
    if (!scenario) return;
    const all = scenario.steps.length + (outcome ? outcome.steps.length : 0);
    if (revealed >= all) return;
    timer.current = setTimeout(() => setRevealed(r => r + 1), REVEAL_MS);
    return () => clearTimeout(timer.current);
  }, [scenario, outcome, revealed]);

  const decide = async (decision) => {
    setBusy(true); setError('');
    try {
      const res = await client.post(`/api/v2/sandbox/scenarios/${encodeURIComponent(scenarioId)}/resolve`, { decision });
      setOutcome(res.data);
      if (decision === 'approve') localStorage.setItem('resolv_test_reply_done', 'true');
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not complete that sandbox action.'));
    } finally { setBusy(false); }
  };

  const allSteps = scenario ? [...scenario.steps, ...(outcome?.steps || [])] : [];
  const stepsDone = scenario && revealed >= allSteps.length;
  const store = meta?.store || scenario?.store;

  return (
    <div style={{ padding: '24px', maxWidth: '980px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '18px' }}>
      <div>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '6px' }}>
          <h1 style={{ fontSize: '24px', fontWeight: 700, margin: 0 }}>Test Luna</h1>
          <SandboxBadge>Sandbox mode</SandboxBadge>
        </div>
        <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          See how Luna handles real support requests using a sample Shopify store. No Shopify or Gmail connection required.
        </div>
        {store && (
          <div style={{ fontSize: '12.5px', color: 'var(--text-muted)', marginTop: '6px' }}>
            {store.label} · {store.name}. {meta?.notice || scenario?.notice}
          </div>
        )}
      </div>

      <Alert variant="error">{error}</Alert>

      <div>
        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-secondary)' }}>Pick a support request</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {!meta && !error && <div className="skeleton" style={{ height: '38px', width: '100%', borderRadius: '6px' }} />}
          {meta?.scenarios.map(s => {
            const active = scenarioId === s.id;
            return (
              <button key={s.id} onClick={() => navigate(`/sandbox/${s.id}`)} title={s.summary}
                style={{ padding: '9px 16px', borderRadius: '6px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                  border: `1px solid ${active ? 'var(--accent)' : 'var(--border-strong)'}`,
                  background: active ? 'var(--accent-light, #ECFEFF)' : 'var(--bg-primary)', color: active ? 'var(--accent)' : 'var(--text-primary)' }}>
                {s.cta}{s.default ? ' ★' : ''}
              </button>
            );
          })}
        </div>
      </div>

      {!scenarioId && meta && (
        <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '10px', alignItems: 'flex-start' }}>
          <div style={{ fontSize: '15px', fontWeight: 600 }}>Start with the cancellation demo</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            Luna reads the order, checks your policy, and asks for your approval before acting. About 60 seconds.
          </div>
          <button onClick={() => navigate(`/sandbox/${meta.scenarios.find(s => s.default)?.id || 'cancel'}`)} style={{ padding: '10px 22px', borderRadius: '4px', fontWeight: 600, fontSize: '14px', background: 'var(--accent)', color: 'white', cursor: 'pointer' }}>
            Test Luna
          </button>
        </div>
      )}

      {loading && <div className="skeleton" style={{ height: '260px', borderRadius: '8px' }} />}

      {scenario && (
        <div className="split-panel" style={{ display: 'flex', gap: '18px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 380px', display: 'flex', flexDirection: 'column', gap: '14px', minWidth: 0 }}>
            <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontSize: '14px', fontWeight: 600 }}>{scenario.ticket.customer.name}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{scenario.ticket.customer.email} · sample customer</div>
                </div>
                <SandboxBadge>{outcome ? (outcome.ticket_status === 'resolved' ? 'Resolved' : 'Needs a human') : 'Open'} · #{scenario.ticket.id}</SandboxBadge>
              </div>
              <Bubble role="customer" label="Customer" body={scenario.ticket.messages[0].body} />
              {stepsDone && !outcome && <Bubble role="ai" label="Luna · draft (not sent)" body={scenario.draft_reply} draft />}
              {outcome?.final_reply && <Bubble role="ai" label="Luna · reply (simulated, nothing was emailed)" body={outcome.final_reply} />}
            </div>
            {stepsDone && !outcome && scenario.pending_action && <ApprovalCard action={scenario.pending_action} busy={busy} onDecide={decide} />}
            {outcome && (
              <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div style={{ fontSize: '14px', fontWeight: 600, color: outcome.ticket_status === 'resolved' ? 'var(--success, #16A34A)' : 'var(--text-primary)' }}>
                  {outcome.ticket_status === 'resolved' ? '✓ Resolved' : 'Left open for a human'}
                </div>
                <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>{outcome.action_label}</div>
                {outcome.order_after && (
                  <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                    Sample order #{outcome.order_after.order_number}: {outcome.order_after.cancelled_at ? 'cancelled' : outcome.order_after.fulfillment_status}, {outcome.order_after.financial_status}
                  </div>
                )}
              </div>
            )}
            {stepsDone && (outcome || !scenario.pending_action) && (
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                <button onClick={() => navigate('/sandbox')} style={{ padding: '10px 18px', borderRadius: '4px', fontWeight: 600, fontSize: '13px', border: '1px solid var(--border-strong)', background: 'transparent', color: 'var(--text-primary)', cursor: 'pointer' }}>
                  Try another request
                </button>
                <Link to="/brands" style={{ padding: '10px 18px', borderRadius: '4px', fontWeight: 600, fontSize: '13px', background: 'var(--accent)', color: 'white', textDecoration: 'none' }}>
                  Connect your store
                </Link>
              </div>
            )}
          </div>

          <div style={{ flex: '1 1 300px', display: 'flex', flexDirection: 'column', gap: '14px', minWidth: 0 }}>
            <div style={card}>
              <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '12px' }}>What Luna did</div>
              <StepList steps={allSteps} revealed={revealed} />
            </div>
            {stepsDone && <Evidence evidence={scenario.evidence} />}
          </div>
        </div>
      )}

      <AskLuna />
    </div>
  );
}
