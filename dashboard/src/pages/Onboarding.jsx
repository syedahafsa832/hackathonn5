import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import Alert from '../components/Alert';
import GmailUnverifiedNotice from '../components/GmailUnverifiedNotice';
import { gmailOAuthErrorMessage } from '../components/gmailOAuthErrors';
import HelpContactLink from '../components/HelpContactLink';

const STEP_LABELS = ['Connect Shopify', 'Import your store', 'Connect inbox', 'Customize Luna', 'Test AI', 'Go Live'];

const inputStyle = {
  width: '100%', padding: '10px 12px', border: '1px solid var(--border-strong)',
  borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)',
  color: 'var(--text-primary)', boxSizing: 'border-box',
};

const primaryBtn = (disabled) => ({
  padding: '11px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600',
  background: disabled ? 'var(--bg-tertiary)' : 'var(--accent)',
  color: disabled ? 'var(--text-muted)' : 'white',
  cursor: disabled ? 'not-allowed' : 'pointer',
  alignSelf: 'flex-start',
});

const skipBtn = {
  padding: '11px 20px', borderRadius: '4px', fontSize: '14px', color: 'var(--text-secondary)',
  background: 'transparent', border: '1px solid var(--border)', cursor: 'pointer',
};

function ProgressBar({ step }) {
  const total = STEP_LABELS.length;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
        {STEP_LABELS.map((_, i) => {
          const s = i + 1;
          return (
            <div key={s} style={{ display: 'flex', alignItems: 'center', flex: s < total ? '1' : 'none' }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '50%', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontWeight: '600', fontSize: '13px',
                background: s < step ? 'var(--success)' : s === step ? 'var(--accent)' : 'var(--bg-tertiary)',
                color: s <= step ? 'white' : 'var(--text-muted)',
              }}>
                {s < step ? '✓' : s}
              </div>
              {s < total && (
                <div style={{ flex: 1, height: '2px', background: s < step ? 'var(--success)' : 'var(--border)', margin: '0 6px', minWidth: '10px' }} />
              )}
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--accent)' }}>
        Step {step} of {total}: {STEP_LABELS[step - 1]}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────── Step 1: Shopify ──

const SHOPIFY_ERROR_MESSAGES = {
  denied: 'Shopify connection was cancelled.',
  invalid_state: 'That connection link expired or was invalid. Please try again.',
  missing_params: 'Shopify did not return the expected information. Please try again.',
  token_exchange_failed: 'Could not complete the connection with Shopify. Please try again.',
  domain_taken: 'This Shopify store is already connected to a different tResolv account.',
  connection_failed: 'Could not connect to Shopify. Please check your store URL and try again.',
};

function StepShopify({ brandId, onNext, onConnected }) {
  const [shopifyDomain, setShopifyDomain] = useState('');
  const [loading, setLoading] = useState(false);
  const [slowConnect, setSlowConnect] = useState(false);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(null); // { shopName, shopDomain, products, orders } once OAuth returns
  const slowTimerRef = useRef(null);

  // Picks up the redirect back from the OAuth callback (backend/src/api/routes/shopify_auth.py).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('shopify_connected') === '1') {
      setConnected({
        shopName: params.get('shop_name') || params.get('shop_domain') || 'your store',
        products: params.get('products'),
        orders: params.get('orders'),
      });
      onConnected();
      window.history.replaceState({}, '', window.location.pathname);
    } else if (params.get('shopify_error')) {
      setError(SHOPIFY_ERROR_MESSAGES[params.get('shopify_error')] || 'Could not connect to Shopify. Please try again.');
      window.history.replaceState({}, '', window.location.pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleConnect = async () => {
    if (!shopifyDomain.trim()) {
      setError('Store URL is required.');
      return;
    }
    setLoading(true);
    setSlowConnect(false);
    setError('');
    // The backend can take a while on a cold start — without this, "Connecting..."
    // sits there for up to 35s with zero feedback and looks hung.
    slowTimerRef.current = setTimeout(() => setSlowConnect(true), 6000);
    try {
      const shop = shopifyDomain.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');
      const res = await client.get(`/api/v2/brands/${brandId}/shopify/oauth/start`, { params: { shop } });
      window.location.href = res.data.auth_url;
    } catch (err) {
      setError(err.response
        ? extractErrorMessage(err, 'Could not start the Shopify connection. Check your store URL.')
        : "Couldn't reach the server right now. This can happen briefly while it wakes up — try again in a few seconds.");
      clearTimeout(slowTimerRef.current);
      setSlowConnect(false);
      setLoading(false);
    }
  };

  if (connected) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Shopify Connected ✅</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
            Store: {connected.shopName}
            {connected.products != null && <><br />Products: {connected.products}</>}
            {connected.orders != null && <><br />Orders: {connected.orders}</>}
            <br />Ready for Luna 🚀
          </p>
        </div>
        <button onClick={onNext} style={primaryBtn(false)}>Continue →</button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Connect Shopify</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          We'll import your products, policies, and pages so Luna understands your store automatically — no manual setup.
        </p>
      </div>

      <div>
        <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Store URL</label>
        <input value={shopifyDomain} onChange={e => setShopifyDomain(e.target.value)} placeholder="yourstore.myshopify.com" style={inputStyle} />
      </div>

      <Alert variant="error">{error}</Alert>

      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={handleConnect} disabled={loading} style={primaryBtn(loading)}>
          {loading ? 'Connecting...' : 'Connect Shopify Store →'}
        </button>
        <button onClick={onNext} style={skipBtn}>Skip for now</button>
        {slowConnect && (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Still working — this can take longer than usual if our server just woke up.
          </span>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────── Step 2: Import store ──

const SCOPE_LABELS = { read_products: 'Products', read_content: 'Online Store content (Pages, Blogs)' };

function StepImport({ brandId, shopifyConnected, onNext }) {
  const [status, setStatus] = useState('not_started');
  const [sources, setSources] = useState([]);
  const [missingScopes, setMissingScopes] = useState([]);
  const [report, setReport] = useState([]);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!shopifyConnected || !brandId) return;
    client.post(`/api/v2/brands/${brandId}/shopify/import`).catch(() => {});

    const poll = () => {
      client.get(`/api/v2/brands/${brandId}/shopify/import-status`).then(res => {
        setStatus(res.data?.status || 'not_started');
        setSources(res.data?.sources || []);
        setMissingScopes(res.data?.missing_scopes || []);
        setReport(res.data?.report || []);
        if (res.data?.status === 'running') {
          pollRef.current = setTimeout(poll, 2000);
        }
      }).catch(() => {
        pollRef.current = setTimeout(poll, 3000);
      });
    };
    poll();
    return () => clearTimeout(pollRef.current);
  }, [brandId, shopifyConnected]);

  if (!shopifyConnected) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Import your store</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
            You skipped Shopify, so there's nothing to import yet. You can connect it later in Settings.
          </p>
        </div>
        <button onClick={onNext} style={primaryBtn(false)}>Continue →</button>
      </div>
    );
  }

  const stillGoing = status === 'running';
  const blocked = status === 'blocked_missing_scopes';

  if (blocked) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Almost there</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
            Your Shopify connection works, but additional permissions are required to import products and store content.
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px 20px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
          <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text-secondary)' }}>Missing permissions:</div>
          {missingScopes.map(s => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '14px' }}>
              <span style={{ color: 'var(--warning, #b58900)', fontWeight: '700', flexShrink: 0 }}>⚠</span>
              <span style={{ color: 'var(--text-secondary)' }}>{SCOPE_LABELS[s] || s} ({s})</span>
            </div>
          ))}
        </div>

        <Alert variant="error">
          These permissions allow tResolv to understand your products, policies, and store information so Luna can answer customers accurately.
          In Shopify Admin, go to Settings → Apps and sales channels → Develop apps → your app → Configuration, add the missing
          permission(s), save, then reinstall the app and reconnect Shopify here with the updated access token.
        </Alert>

        <button onClick={onNext} style={primaryBtn(false)}>Continue for now →</button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>
          {stillGoing ? 'Learning your store…' : 'Store imported'}
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Pulling your products, return policy, shipping info, and pages so Luna can answer real questions.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px 20px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
        {report.length === 0 && (
          <div style={{ fontSize: '14px', color: 'var(--text-muted)' }}>
            {stillGoing ? 'Starting import…' : 'Checking your store…'}
          </div>
        )}
        {report.map(r => (
          <div key={r.resource} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', fontSize: '14px' }}>
            <span style={{
              color: r.status === 'imported' ? 'var(--success)' : r.status === 'skipped' ? 'var(--warning, #b58900)' : 'var(--text-muted)',
              fontWeight: '700', flexShrink: 0,
            }}>
              {r.status === 'imported' ? '✓' : r.status === 'skipped' ? '⚠' : '○'}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {r.status === 'imported' && `Imported ${r.count} ${r.resource.toLowerCase()}`}
              {r.status === 'skipped' && `Couldn't import ${r.resource} because ${r.reason}`}
              {r.status === 'empty' && `No ${r.resource.toLowerCase()} found in your store`}
            </span>
          </div>
        ))}
        {stillGoing && <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Still working — this continues in the background either way.</div>}
      </div>

      {!stillGoing && missingScopes.length > 0 && (
        <Alert variant="error">
          You can continue using tResolv now — reconnect Shopify later with an updated access token to import
          {' '}{missingScopes.map(s => SCOPE_LABELS[s] || s).join(' and ')}. In Shopify Admin, go to Settings →
          Apps and sales channels → Develop apps → your app → Configuration, add the missing scope(s), save,
          then reinstall the app and reconnect Shopify here.
        </Alert>
      )}

      <button onClick={onNext} style={primaryBtn(false)}>
        {stillGoing ? 'Continue (import keeps running) →' : 'Continue →'}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────── Step 3: Gmail ──

function StepGmail({ brandId, onNext }) {
  const [polling, setPolling] = useState(false);
  const [gmailError, setGmailError] = useState('');

  const connectGmail = async () => {
    setPolling(true);
    setGmailError('');
    try {
      const res = await client.get(`/api/brands/${brandId}/gmail/auth-url`);
      const authUrl = res.data?.auth_url || res.data?.url;
      if (authUrl) {
        window.location.href = authUrl;
      } else {
        setGmailError("tResolv couldn't generate a Google sign-in link. Try again, or skip this step and connect from Settings later.");
        setPolling(false);
      }
    } catch (err) {
      const fallback = err.response
        ? 'Could not start Gmail connection. Try again, or skip this step and connect from Settings later.'
        : "Couldn't reach tResolv's server. Check your internet connection and try again.";
      setGmailError(extractErrorMessage(err, fallback));
      setPolling(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('gmail_connected') === '1') {
      onNext();
    } else if (params.get('gmail_error')) {
      setGmailError(gmailOAuthErrorMessage(params.get('gmail_error')));
      window.history.replaceState({}, '', window.location.pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Connect your inbox</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Now connect the inbox where customers contact you. Resolv reads it for new support emails and replies on your behalf.
        </p>
      </div>

      <div style={{ padding: '20px', background: 'var(--bg-secondary)', borderRadius: '6px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {['Automatically reads new support emails', 'Sends AI-drafted replies from your address', 'Never shares your emails with third parties'].map(text => (
          <div key={text} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '14px', color: 'var(--text-secondary)' }}>
            <span style={{ color: 'var(--success)', fontWeight: '700', flexShrink: 0 }}>✓</span>
            {text}
          </div>
        ))}
      </div>

      <GmailUnverifiedNotice />

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        <button onClick={connectGmail} disabled={polling} style={{ ...primaryBtn(polling), display: 'flex', alignItems: 'center', gap: '8px' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
          {polling ? 'Redirecting...' : 'Connect Gmail'}
        </button>
        <button onClick={onNext} style={skipBtn}>Skip for now</button>
      </div>
      <Alert variant="error">{gmailError}</Alert>
    </div>
  );
}

// ────────────────────────────────────────── Step 4: Reply Style ──

function StepStyle({ brandId, onNext }) {
  const [presets, setPresets] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!brandId) { setLoading(false); return; }
    client.get(`/api/v2/brands/${brandId}/reply-style`)
      .then(res => {
        setPresets(res.data?.presets || []);
        setSelected(res.data?.preset || 'warm_friendly');
      })
      .catch(() => setError('Could not load Reply Style presets.'))
      .finally(() => setLoading(false));
  }, [brandId]);

  const handleNext = async () => {
    if (!brandId || !selected) { onNext(); return; }
    setSaving(true);
    setError('');
    try {
      await client.patch(`/api/v2/brands/${brandId}/reply-style`, { mode: 'preset', preset: selected });
      localStorage.setItem('resolv_reply_style_done', 'true');
      onNext();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save Reply Style. You can change this later in Settings.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>How should Luna reply?</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Pick a starting style. This becomes active immediately — you can fine-tune it later in Settings.
        </p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '70px', borderRadius: '6px' }} />)}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '12px' }}>
          {presets.map(p => (
            <div
              key={p.id}
              onClick={() => setSelected(p.id)}
              style={{
                border: selected === p.id ? '2px solid var(--accent)' : '1px solid var(--border)',
                borderRadius: '6px', padding: '14px', cursor: 'pointer',
                background: selected === p.id ? 'var(--bg-secondary)' : 'var(--bg-primary)',
              }}
            >
              <div style={{ fontWeight: '700', fontSize: '13px', marginBottom: '4px' }}>
                {p.label}{selected === p.id && ' ✓'}
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>{p.description}</div>
              {p.example_replies?.[0] && (
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', borderLeft: '2px solid var(--border)', paddingLeft: '8px' }}>
                  "{p.example_replies[0]}"
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <Alert variant="error">{error}</Alert>

      <button onClick={handleNext} disabled={saving || loading} style={primaryBtn(saving || loading)}>
        {saving ? 'Saving...' : 'Continue →'}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────── Step 5: Test Luna ──

// Deliberately general/policy questions, not order-specific — a brand-new
// store has no real orders yet, so a canned "Where is my order #1234?" would
// reliably come back "I couldn't find that order," which is the *correct*
// safe behavior (never inventing order data) but looks like a broken demo at
// the exact moment a merchant is deciding whether this thing works. These
// questions instead exercise the RAG/policy-answering path, which works
// immediately after Shopify import with zero real orders involved.
const SAMPLE_QUESTIONS = [
  "What's your return policy?",
  'Do you ship internationally?',
  'How long does shipping usually take?',
];

function StepTestLuna({ brandId, onNext }) {
  const [replies, setReplies] = useState({});
  const [loadingQ, setLoadingQ] = useState(null);
  const [error, setError] = useState('');
  const [customQuestion, setCustomQuestion] = useState('');

  const ask = async (question) => {
    setLoadingQ(question);
    setError('');
    try {
      const res = await client.post(`/api/v2/brands/${brandId}/test-reply`, { message: question });
      if (res.data?.provider_outage) {
        setReplies(r => ({ ...r, [question]: null }));
        setError("Luna's AI models are all at capacity right now (usage limit reached across every connected provider). This is temporary — try again in a few minutes.");
      } else {
        setReplies(r => ({ ...r, [question]: res.data?.reply || '(no reply generated)' }));
        setError('');
        localStorage.setItem('resolv_test_reply_done', 'true');
      }
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not generate a test reply.'));
    } finally {
      setLoadingQ(null);
    }
  };

  const askCustom = () => {
    if (!customQuestion.trim()) return;
    ask(customQuestion.trim());
    setCustomQuestion('');
  };

  const hasAnyReply = Object.keys(replies).length > 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Test Luna</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Your AI employee is ready. Try a real question below — this runs through the actual support agent, not a demo.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {SAMPLE_QUESTIONS.map(q => (
          <div key={q} style={{ border: '1px solid var(--border)', borderRadius: '6px', padding: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
              <div style={{ fontSize: '14px', fontWeight: '500' }}>"{q}"</div>
              <button
                onClick={() => ask(q)}
                disabled={loadingQ === q}
                style={{ padding: '6px 14px', borderRadius: '4px', fontSize: '12px', fontWeight: '600', background: 'var(--accent)', color: 'white', cursor: loadingQ === q ? 'not-allowed' : 'pointer', flexShrink: 0 }}
              >
                {loadingQ === q ? 'Asking...' : replies[q] ? 'Ask again' : 'Ask Luna'}
              </button>
            </div>
            {replies[q] && (
              <div style={{ marginTop: '10px', padding: '12px', background: 'var(--bg-secondary)', borderRadius: '6px', fontSize: '13px', color: 'var(--text-primary)', whiteSpace: 'pre-line' }}>
                {replies[q]}
              </div>
            )}
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '8px' }}>
        <input
          value={customQuestion}
          onChange={e => setCustomQuestion(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && askCustom()}
          placeholder="Or ask your own question..."
          style={{ ...inputStyle, flex: 1 }}
        />
        <button onClick={askCustom} disabled={!customQuestion.trim() || loadingQ === customQuestion} style={{ padding: '0 18px', borderRadius: '4px', fontSize: '13px', fontWeight: '600', background: 'var(--bg-tertiary)', color: 'var(--text-primary)', border: '1px solid var(--border)', cursor: 'pointer' }}>
          Ask
        </button>
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        Tip: for a real order-tracking example, ask about one of your actual order numbers — a made-up one will correctly come back "not found" rather than invented.
      </div>

      <Alert variant="error">{error}</Alert>

      <button onClick={onNext} style={{ ...primaryBtn(false), padding: '13px 32px', fontSize: '15px' }}>
        {hasAnyReply ? 'Continue →' : 'Skip →'}
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────── Step 6: Go Live ──

function StepGoLive({ brandId, shopifyConnected, gmailConnected, onFinish }) {
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState('');

  const loadStatus = () => {
    setLoading(true);
    client.get('/api/ai-mode', { params: { store_id: brandId } })
      .then(res => setLive(res.data?.mode === 'autopilot'))
      .catch(() => setError('Could not check activation status.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { if (brandId) loadStatus(); }, [brandId]);

  const handleGoLive = async () => {
    if (!window.confirm(
      "Go live now? tResolv will start reading and replying to real customer emails and chats. " +
      "Refunds, cancellations, and address changes will still wait for your approval."
    )) return;
    setActivating(true);
    setError('');
    try {
      await client.patch('/api/ai-mode', { mode: 'active', store_id: brandId });
      setLive(true);
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not activate tResolv. Please try again.'));
    } finally {
      setActivating(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Go Live</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          tResolv is ready. Go live when you're ready to let it handle real customer conversations.
        </p>
      </div>

      {loading ? (
        <div className="skeleton" style={{ height: '60px', borderRadius: '6px' }} />
      ) : live ? (
        <div style={{ padding: '16px 20px', background: 'var(--success-bg, #ECFDF5)', border: '1px solid var(--success, #10B981)', borderRadius: '6px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '18px' }}>🟢</span>
          <span style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>
            tResolv is live — it's handling real customer conversations now.
          </span>
        </div>
      ) : (
        <div style={{ padding: '16px 20px', background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '6px', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '18px' }}>⏸</span>
          <span style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-secondary)' }}>
            tResolv is paused — it is not responding to real customers yet.
          </span>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '13px', color: 'var(--text-muted)' }}>
        <div>{shopifyConnected ? '✓' : '○'} Shopify {shopifyConnected ? 'connected' : 'not connected yet'}</div>
        <div>{gmailConnected ? '✓' : '○'} Inbox {gmailConnected ? 'connected' : 'not connected yet'}</div>
      </div>

      <Alert variant="error">{error}</Alert>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {!live && (
          <button onClick={handleGoLive} disabled={activating} style={{ ...primaryBtn(activating), padding: '13px 32px', fontSize: '15px' }}>
            {activating ? 'Activating...' : 'Go Live →'}
          </button>
        )}
        <button onClick={onFinish} style={live ? { ...primaryBtn(false), padding: '13px 32px', fontSize: '15px' } : skipBtn}>
          {live ? 'Go to Dashboard →' : "I'll do this later →"}
        </button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────── Main flow ──

export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [brandId, setBrandId] = useState(null);
  const [shopifyConnected, setShopifyConnected] = useState(false);
  const [gmailConnected, setGmailConnected] = useState(false);
  const [loadingBrand, setLoadingBrand] = useState(true);
  const [brandLoadError, setBrandLoadError] = useState(false);
  const [notAuthenticated, setNotAuthenticated] = useState(false);

  const loadBrand = () => {
    setLoadingBrand(true);
    setBrandLoadError(false);
    setNotAuthenticated(false);
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      if (list.length > 0) {
        const brand = list[0];
        setBrandId(brand.id);
        const isShopifyConnected = !!brand.shopify_connected || !!brand.shopify_domain;
        setShopifyConnected(isShopifyConnected);
        setGmailConnected(!!brand.gmail_connected);

        // Root cause of the "redirect back to onboarding" bug: step always
        // started at 1 regardless of real progress, so any fresh visit to
        // /onboarding — including the Dashboard checklist's own "Continue
        // setup" button, which never targeted a specific step — restarted
        // the whole flow from "Connect Shopify" even when everything past
        // it was already done. Resume at the first genuinely incomplete
        // step instead. reply_style_mode/preset come from the brand row
        // itself (server truth), not localStorage, since that's more
        // reliable than a flag that can be missing on another device/
        // browser or after clearing site data.
        const resumeAt = (gmailDone) => {
          if (!isShopifyConnected) return 1;
          const replyStyleDone = !!(brand.reply_style_mode || brand.reply_style_preset)
            || localStorage.getItem('resolv_reply_style_done') === 'true';
          if (!gmailDone) return 3;
          if (!replyStyleDone) return 4;
          return 5;
        };

        if (isShopifyConnected) {
          // loadingBrand must stay true until this resolves — otherwise the
          // skeleton disappears one tick before setStep() lands, and the
          // stale default (1) flashes on screen for a frame, which is
          // exactly the "looks like it reset to step 1" symptom this fix
          // is for.
          return client.get(`/api/v2/brands/${brand.id}/shopify/import-status`)
            .then(r => {
              const imported = (r.data?.sources || []).some(s => s.status === 'completed');
              setStep(imported ? resumeAt(!!brand.gmail_connected) : 2);
            })
            .catch(() => setStep(resumeAt(!!brand.gmail_connected)));
        }
        setStep(1);
      } else {
        // Signup auto-creates a brand server-side, so an empty list here means
        // something actually went wrong, not just "no brand yet".
        setBrandLoadError(true);
      }
    }).catch((err) => {
      // No .catch() here previously meant a slow/cold-start timeout (Render
      // free tier, ~30s+) silently left brandId at null, and every later
      // step (e.g. Connect Shopify) then called /api/v2/brands/null/... and
      // failed with a confusing "Brand not found" instead of a clear retry.
      //
      // A 401 is a different failure entirely — no amount of retrying fixes
      // a missing/invalid token, so showing "server waking up, try again"
      // for that case is actively misleading. The client's response
      // interceptor already redirects to /login when a token existed and
      // got rejected; this only catches the remaining case (no token at
      // all) so the message here matches what's actually wrong.
      if (err?.response?.status === 401) {
        setNotAuthenticated(true);
      } else {
        setBrandLoadError(true);
      }
    }).finally(() => setLoadingBrand(false));
  };

  useEffect(() => { loadBrand(); }, []);

  const handleFinish = () => {
    navigate('/dashboard');
  };

  if (loadingBrand) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="skeleton" style={{ width: '400px', height: '200px', borderRadius: '8px' }} />
      </div>
    );
  }

  if (notAuthenticated) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '40px', maxWidth: '440px', textAlign: 'center' }}>
          <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '10px' }}>You've been signed out</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5', marginBottom: '20px' }}>
            Your session isn't valid anymore — log in again to continue setting up your account.
          </p>
          <button onClick={() => navigate('/login')} style={{ padding: '10px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600', background: 'var(--accent)', color: 'white', cursor: 'pointer' }}>
            Go to login
          </button>
          <div style={{ marginTop: '14px', fontSize: '12px', color: 'var(--text-muted)' }}>
            <HelpContactLink variant="inline" context="Onboarding — signed out" label="Need help instead?" />
          </div>
        </div>
      </div>
    );
  }

  if (brandLoadError || !brandId) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '40px', maxWidth: '440px', textAlign: 'center' }}>
          <h2 style={{ fontSize: '18px', fontWeight: '700', marginBottom: '10px' }}>Couldn't load your account</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5', marginBottom: '20px' }}>
            This can happen right after signup while our server wakes up. Try again in a few seconds.
          </p>
          <button onClick={loadBrand} style={{ padding: '10px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600', background: 'var(--accent)', color: 'white', cursor: 'pointer' }}>
            Retry
          </button>
          <div style={{ marginTop: '14px', fontSize: '12px', color: 'var(--text-muted)' }}>
            Still stuck? <HelpContactLink variant="inline" context="Onboarding — couldn't load account" label="Email us" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-secondary)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', position: 'relative' }}>
      <div style={{ position: 'absolute', top: '20px', right: '24px' }}>
        <HelpContactLink context={`Onboarding — step ${step}`} />
      </div>
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '40px', width: '100%', maxWidth: '600px' }}>
        <div style={{ marginBottom: '32px' }}>
          <div style={{ fontWeight: '800', fontSize: '18px', letterSpacing: '-0.5px', marginBottom: '4px' }}>
            <span style={{ color: 'var(--accent)' }}>t</span>
            <span style={{ color: 'var(--text-primary)' }}>Resolv</span>
          </div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
            Let's get your AI support employee working.
          </div>
          <ProgressBar step={step} />
        </div>

        {step === 1 && <StepShopify brandId={brandId} onNext={() => setStep(2)} onConnected={() => setShopifyConnected(true)} />}
        {step === 2 && <StepImport brandId={brandId} shopifyConnected={shopifyConnected} onNext={() => setStep(3)} />}
        {step === 3 && <StepGmail brandId={brandId} onNext={() => { setGmailConnected(true); setStep(4); }} />}
        {step === 4 && <StepStyle brandId={brandId} onNext={() => setStep(5)} />}
        {step === 5 && <StepTestLuna brandId={brandId} onNext={() => setStep(6)} />}
        {step === 6 && <StepGoLive brandId={brandId} shopifyConnected={shopifyConnected} gmailConnected={gmailConnected} onFinish={handleFinish} />}
      </div>
    </div>
  );
}
