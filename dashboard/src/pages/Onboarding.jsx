import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import Alert from '../components/Alert';

const STEP_LABELS = ['Connect Shopify', 'Import your store', 'Connect inbox', 'Customize Luna', 'Test AI'];

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
    <div style={{ display: 'flex', alignItems: 'center', gap: '0', marginBottom: '40px', flexWrap: 'wrap' }}>
      {STEP_LABELS.map((label, i) => {
        const s = i + 1;
        return (
          <div key={s} style={{ display: 'flex', alignItems: 'center', flex: s < total ? '1' : 'none', minWidth: '90px' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%', flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: '600', fontSize: '13px',
              background: s < step ? 'var(--success)' : s === step ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: s <= step ? 'white' : 'var(--text-muted)',
            }}>
              {s < step ? '✓' : s}
            </div>
            <div style={{ fontSize: '11px', color: s === step ? 'var(--accent)' : 'var(--text-muted)', marginLeft: '6px', whiteSpace: 'nowrap', fontWeight: s === step ? '600' : '400' }}>
              {label}
            </div>
            {s < total && (
              <div style={{ flex: 1, height: '2px', background: s < step ? 'var(--success)' : 'var(--border)', margin: '0 10px', minWidth: '16px' }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────── Step 1: Shopify ──

function StepShopify({ brandId, onNext, onConnected }) {
  const [shopifyDomain, setShopifyDomain] = useState('');
  const [shopifyApiKey, setShopifyApiKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleConnect = async () => {
    if (!shopifyDomain.trim() || !shopifyApiKey.trim()) {
      setError('Store URL and Admin API key are both required.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await client.post(`/api/v2/brands/${brandId}/shopify/connect`, {
        shop_domain: shopifyDomain.trim(),
        access_token: shopifyApiKey.trim(),
      });
      onConnected();
      onNext();
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not connect to Shopify. Check your store URL and API key.'));
    } finally {
      setLoading(false);
    }
  };

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
      <div>
        <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Admin API key</label>
        <input type="password" value={shopifyApiKey} onChange={e => setShopifyApiKey(e.target.value)} placeholder="shpat_..." style={inputStyle} />
      </div>

      <Alert variant="error">{error}</Alert>

      <div style={{ display: 'flex', gap: '12px' }}>
        <button onClick={handleConnect} disabled={loading} style={primaryBtn(loading)}>
          {loading ? 'Connecting...' : 'Connect →'}
        </button>
        <button onClick={onNext} style={skipBtn}>Skip for now</button>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────── Step 2: Import store ──

const CATEGORY_LABELS = {
  'Products': (s) => `${s.metadata?.count ?? s.chunk_count ?? ''} products imported`.trim(),
  'Return Policy': () => 'Return policy imported',
  'Shipping Policy': () => 'Shipping information imported',
  'FAQ Pages': () => 'FAQ pages imported',
  'Store Pages': () => 'Store pages imported',
};

function categoryLabel(source) {
  const base = Object.keys(CATEGORY_LABELS).find(k => source.name?.startsWith(k));
  if (!base) return source.name;
  return CATEGORY_LABELS[base](source);
}

const SCOPE_LABELS = { read_products: 'Products', read_content: 'Online Store content (Pages, Blogs)' };

function StepImport({ brandId, shopifyConnected, onNext }) {
  const [status, setStatus] = useState('not_started');
  const [sources, setSources] = useState([]);
  const [missingScopes, setMissingScopes] = useState([]);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!shopifyConnected || !brandId) return;
    client.post(`/api/v2/brands/${brandId}/shopify/import`).catch(() => {});

    const poll = () => {
      client.get(`/api/v2/brands/${brandId}/shopify/import-status`).then(res => {
        setStatus(res.data?.status || 'not_started');
        setSources(res.data?.sources || []);
        setMissingScopes(res.data?.missing_scopes || []);
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

  const doneSources = sources.filter(s => s.status === 'completed');
  const stillGoing = status === 'running';

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

      {!stillGoing && missingScopes.length > 0 && (
        <Alert variant="error">
          Your Shopify app is missing permission to read {missingScopes.map(s => SCOPE_LABELS[s] || s).join(' and ')}.
          In Shopify Admin, go to Settings → Apps and sales channels → Develop apps → your app → Configuration,
          add the missing scope(s), save, then reinstall the app and reconnect Shopify here with the new access token.
        </Alert>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px 20px', background: 'var(--bg-secondary)', borderRadius: '6px' }}>
        {sources.length === 0 && stillGoing && (
          <div style={{ fontSize: '14px', color: 'var(--text-muted)' }}>Starting import…</div>
        )}
        {sources.length === 0 && !stillGoing && missingScopes.length === 0 && (
          <div style={{ fontSize: '14px', color: 'var(--text-muted)' }}>Nothing found to import — you can add knowledge manually later in Settings.</div>
        )}
        {sources.map(s => (
          <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '14px' }}>
            <span style={{ color: s.status === 'completed' ? 'var(--success)' : s.status === 'failed' ? 'var(--danger)' : 'var(--text-muted)', fontWeight: '700', flexShrink: 0 }}>
              {s.status === 'completed' ? '✓' : s.status === 'failed' ? '✕' : '○'}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>{categoryLabel(s)}</span>
          </div>
        ))}
        {stillGoing && <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Still working — this continues in the background either way.</div>}
      </div>

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
      if (authUrl) window.location.href = authUrl;
    } catch (err) {
      setGmailError(extractErrorMessage(err, 'Could not start Gmail connection.'));
      setPolling(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('gmail_connected') === '1') {
      onNext();
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

const SAMPLE_QUESTIONS = [
  'Where is my order #1234?',
  'I want to return my order.',
  'When will my package arrive?',
];

function StepTestLuna({ brandId, onFinish }) {
  const [replies, setReplies] = useState({});
  const [loadingQ, setLoadingQ] = useState(null);
  const [error, setError] = useState('');
  const [customQuestion, setCustomQuestion] = useState('');

  const ask = async (question) => {
    setLoadingQ(question);
    setError('');
    try {
      const res = await client.post(`/api/v2/brands/${brandId}/test-reply`, { message: question });
      setReplies(r => ({ ...r, [question]: res.data?.reply || '(no reply generated)' }));
      localStorage.setItem('resolv_test_reply_done', 'true');
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

      <Alert variant="error">{error}</Alert>

      <button onClick={onFinish} style={{ ...primaryBtn(false), padding: '13px 32px', fontSize: '15px' }}>
        {hasAnyReply ? 'Go to Dashboard →' : 'Skip and go to Dashboard →'}
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────── Main flow ──

export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [brandId, setBrandId] = useState(null);
  const [shopifyConnected, setShopifyConnected] = useState(false);
  const [loadingBrand, setLoadingBrand] = useState(true);
  const [brandLoadError, setBrandLoadError] = useState(false);

  const loadBrand = () => {
    setLoadingBrand(true);
    setBrandLoadError(false);
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      if (list.length > 0) {
        setBrandId(list[0].id);
        setShopifyConnected(!!list[0].shopify_connected || !!list[0].shopify_domain);
      } else {
        // Signup auto-creates a brand server-side, so an empty list here means
        // something actually went wrong, not just "no brand yet".
        setBrandLoadError(true);
      }
    }).catch(() => {
      // No .catch() here previously meant a slow/cold-start timeout (Render
      // free tier, ~30s+) silently left brandId at null, and every later
      // step (e.g. Connect Shopify) then called /api/v2/brands/null/... and
      // failed with a confusing "Brand not found" instead of a clear retry.
      setBrandLoadError(true);
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
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-secondary)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
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
        {step === 3 && <StepGmail brandId={brandId} onNext={() => setStep(4)} />}
        {step === 4 && <StepStyle brandId={brandId} onNext={() => setStep(5)} />}
        {step === 5 && <StepTestLuna brandId={brandId} onFinish={handleFinish} />}
      </div>
    </div>
  );
}
