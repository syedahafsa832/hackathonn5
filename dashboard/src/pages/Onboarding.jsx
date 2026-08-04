import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import Alert from '../components/Alert';

const STEP_LABELS = ['Your brand', 'Reply style', 'Connect Gmail', 'Ready'];

function ProgressBar({ step }) {
  const total = STEP_LABELS.length;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0', marginBottom: '40px' }}>
      {STEP_LABELS.map((label, i) => {
        const s = i + 1;
        return (
          <div key={s} style={{ display: 'flex', alignItems: 'center', flex: s < total ? '1' : 'none' }}>
            <div style={{
              width: '32px', height: '32px', borderRadius: '50%', flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: '600', fontSize: '14px',
              background: s < step ? 'var(--success)' : s === step ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: s <= step ? 'white' : 'var(--text-muted)',
              transition: 'all 0.3s',
            }}>
              {s < step ? '✓' : s}
            </div>
            <div style={{ fontSize: '12px', color: s === step ? 'var(--accent)' : 'var(--text-muted)', marginLeft: '6px', whiteSpace: 'nowrap', fontWeight: s === step ? '600' : '400' }}>
              {label}
            </div>
            {s < total && (
              <div style={{ flex: 1, height: '2px', background: s < step ? 'var(--success)' : 'var(--border)', margin: '0 12px', transition: 'background 0.3s' }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function Step1({ onNext }) {
  const [brandName, setBrandName] = useState('');
  const [shopifyDomain, setShopifyDomain] = useState('');
  const [shopifyApiKey, setShopifyApiKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const inputStyle = {
    width: '100%', padding: '10px 12px', border: '1px solid var(--border-strong)',
    borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)',
    color: 'var(--text-primary)', boxSizing: 'border-box',
  };

  const handleNext = async () => {
    if (!brandName.trim()) { setError('Brand name is required.'); return; }
    setLoading(true);
    setError('');
    try {
      const slug = brandName.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      const brandRes = await client.post('/api/v2/brands', {
        name: brandName.trim(),
        slug: slug || 'my-brand',
      });
      const newBrandId = brandRes.data?.brand?.id || brandRes.data?.id;
      if (!newBrandId) throw new Error('Failed to create brand');

      let activeBrandId = newBrandId;
      if (shopifyDomain.trim() && shopifyApiKey.trim()) {
        try {
          const shopRes = await client.post(`/api/v2/brands/${newBrandId}/shopify/connect`, {
            shop_domain: shopifyDomain.trim(),
            access_token: shopifyApiKey.trim(),
          });
          // Use the active brand ID from the response (may differ after domain conflict resolution)
          if (shopRes.data?.brand_id) activeBrandId = shopRes.data.brand_id;
        } catch {
          // Shopify connection optional — continue
        }
      }
      onNext(activeBrandId);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to create brand. Try again.'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Set up your brand</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Tell us about your store. You can connect Shopify now or skip and do it later in Settings.
        </p>
      </div>

      <div>
        <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>
          Brand name <span style={{ color: 'var(--danger)' }}>*</span>
        </label>
        <input value={brandName} onChange={e => setBrandName(e.target.value)} placeholder="e.g. Luna Apparel" style={inputStyle} />
      </div>

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: '20px' }}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px' }}>
          Shopify (optional)
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Store URL</label>
            <input value={shopifyDomain} onChange={e => setShopifyDomain(e.target.value)} placeholder="yourstore.myshopify.com" style={inputStyle} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Admin API key</label>
            <input type="password" value={shopifyApiKey} onChange={e => setShopifyApiKey(e.target.value)} placeholder="shpat_..." style={inputStyle} />
          </div>
        </div>
      </div>

      <Alert variant="error">{error}</Alert>

      <button
        onClick={handleNext}
        disabled={loading || !brandName.trim()}
        style={{
          padding: '11px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600',
          background: !brandName.trim() || loading ? 'var(--bg-tertiary)' : 'var(--accent)',
          color: !brandName.trim() || loading ? 'var(--text-muted)' : 'white',
          cursor: !brandName.trim() || loading ? 'not-allowed' : 'pointer',
          alignSelf: 'flex-start',
        }}
      >
        {loading ? 'Creating...' : 'Continue →'}
      </button>
    </div>
  );
}

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
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>How should your AI sound?</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Pick a starting style. This becomes active immediately — you can fine-tune it later in Settings, or let it learn your team's own writing style over time.
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

      <button
        onClick={handleNext}
        disabled={saving || loading}
        style={{
          padding: '11px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600',
          background: saving || loading ? 'var(--bg-tertiary)' : 'var(--accent)',
          color: saving || loading ? 'var(--text-muted)' : 'white',
          cursor: saving || loading ? 'not-allowed' : 'pointer',
          alignSelf: 'flex-start',
        }}
      >
        {saving ? 'Saving...' : 'Continue →'}
      </button>
    </div>
  );
}

function Step2({ brandId, onNext }) {
  const [polling, setPolling] = useState(false);
  const [gmailError, setGmailError] = useState('');
  const pollRef = useRef(null);

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

  // Poll for Gmail connection after returning from OAuth
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const gmailConnected = params.get('gmail_connected');
    if (gmailConnected === '1') {
      onNext();
    }
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px' }}>Connect Gmail</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.5' }}>
          Resolv reads your inbox for new support emails and replies on your behalf. Your emails never leave Google.
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
        <button
          onClick={connectGmail}
          disabled={polling}
          style={{
            padding: '11px 24px', borderRadius: '4px', fontSize: '14px', fontWeight: '600',
            background: 'var(--accent)', color: 'white', cursor: polling ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', gap: '8px',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
          {polling ? 'Redirecting...' : 'Connect Gmail'}
        </button>
        <button
          onClick={onNext}
          style={{ padding: '11px 20px', borderRadius: '4px', fontSize: '14px', color: 'var(--text-secondary)', background: 'transparent', border: '1px solid var(--border)', cursor: 'pointer' }}
        >
          Skip for now
        </button>
      </div>
      <Alert variant="error">{gmailError}</Alert>
    </div>
  );
}

function Step3({ onFinish }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', alignItems: 'center', textAlign: 'center', padding: '20px 0' }}>
      <div style={{
        width: '72px', height: '72px', borderRadius: '50%', background: 'var(--success-light)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '32px',
      }}>
        ✓
      </div>

      <div>
        <h2 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px', color: 'var(--success)' }}>You are ready</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '14px', lineHeight: '1.6', maxWidth: '340px' }}>
          Resolv is set up. The moment your first support email arrives, we will draft a reply and put it in your approval queue.
        </p>
      </div>

      <button
        onClick={onFinish}
        style={{
          padding: '13px 32px', borderRadius: '4px', fontSize: '15px', fontWeight: '700',
          background: 'var(--accent)', color: 'white', cursor: 'pointer',
        }}
      >
        Go to Dashboard →
      </button>
    </div>
  );
}

export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [brandId, setBrandId] = useState(null);

  const handleStep1Next = (newBrandId) => {
    setBrandId(newBrandId);
    setStep(2);
  };

  const handleStyleNext = () => setStep(3);
  const handleStep2Next = () => setStep(4);

  const handleFinish = () => {
    localStorage.setItem('resolv_onboarding_complete', 'true');
    navigate('/dashboard');
  };

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-secondary)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '40px', width: '100%', maxWidth: '520px' }}>
        <div style={{ marginBottom: '32px' }}>
          <div style={{ fontWeight: '800', fontSize: '20px', letterSpacing: '-0.5px', marginBottom: '24px' }}>
            <span style={{ color: 'var(--accent)' }}>t</span>
            <span style={{ color: 'var(--text-primary)' }}>Resolv</span>
          </div>
          <ProgressBar step={step} />
        </div>

        {step === 1 && <Step1 onNext={handleStep1Next} />}
        {step === 2 && <StepStyle brandId={brandId} onNext={handleStyleNext} />}
        {step === 3 && <Step2 brandId={brandId} onNext={handleStep2Next} />}
        {step === 4 && <Step3 onFinish={handleFinish} />}
      </div>
    </div>
  );
}
