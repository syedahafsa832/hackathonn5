import { useEffect, useState, useCallback, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import client from '../api/client';
import ChatWidget from '../components/ChatWidget';
import Alert from '../components/Alert';
import GmailUnverifiedNotice from '../components/GmailUnverifiedNotice';
import ShopifyConnectionNotice from '../components/ShopifyConnectionNotice';
import { gmailOAuthErrorMessage } from '../components/gmailOAuthErrors';

const inputStyle = {
  width: '100%',
  padding: '9px 12px',
  border: '1px solid #E4E4E7',
  borderRadius: '6px',
  fontSize: '14px',
  background: 'white',
  color: '#0F172A',
  boxSizing: 'border-box',
};

const SHOPIFY_OAUTH_ERROR_MESSAGES = {
  denied: 'Shopify connection was cancelled.',
  invalid_state: 'That connection link expired or was invalid. Please try again.',
  missing_params: 'Shopify did not return the expected information. Please try again.',
  token_exchange_failed: 'Could not complete the connection with Shopify. Please try again.',
  domain_taken: 'This Shopify store is already connected to a different tResolv account.',
  connection_failed: 'Could not connect to Shopify. Please check your store URL and try again.',
};

// ──────────────────────────────────────────────────────── Email Tab ──

function EmailTab() {
  const [gmailStatus, setGmailStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);
  // True from the moment "Connect"/"Reconnect" is clicked until the browser
  // actually navigates to Google (or the request fails) — the one window
  // where the user has clicked something but sees no other feedback yet.
  const [connecting, setConnecting] = useState(false);
  // Raw gmail_error code from the OAuth redirect, mapped to specific copy via
  // gmailOAuthErrorMessage() below — kept separate from `msg` (used for
  // unrelated success/failure toasts elsewhere on this tab) so the Gmail
  // card can render its own distinct "Connection Failed" state.
  const [gmailErrorCode, setGmailErrorCode] = useState(null);
  // Same brandId-resolution pattern as ShopifyTab/ReplyStyleTab below and
  // Onboarding.jsx's Go Live step: /api/ai-mode defaults to the global
  // DEFAULT_STORE server-side when store_id is omitted, which silently
  // read/wrote the WRONG brand's AI mode in this multi-tenant SaaS. Every
  // call below must pass this brand's own id.
  const [brandId, setBrandId] = useState(null);
  // null = not yet known / unavailable, distinct from any real mode string —
  // used to render the AI Mode section's own disabled/error state instead of
  // ever guessing a mode for a brand we couldn't resolve.
  const [mode, setMode] = useState(null);
  const [threshold, setThreshold] = useState(80);
  const [savingMode, setSavingMode] = useState(false);
  const [msg, setMsg] = useState('');
  const thresholdTimer = useRef(null);

  const loadStatus = useCallback(async (currentBrandId) => {
    setLoadingStatus(true);
    try {
      const [gmailRes, accountRes] = await Promise.all([
        client.get('/api/v1/settings/gmail/status').catch(() => ({ data: { connected: false } })),
        client.get('/api/v1/settings/account').catch(() => ({ data: {} })),
      ]);
      setGmailStatus(gmailRes.data);
      setThreshold(accountRes.data?.settings?.confidence_threshold ?? accountRes.data?.confidence_threshold ?? 80);

      if (!currentBrandId) {
        // No valid brand resolved — never call /api/ai-mode. Without an
        // explicit store_id it silently falls back to the shared global
        // DEFAULT_STORE server-side, which would read/write the wrong
        // brand's AI mode. Leave mode unknown so the section below shows
        // its own error state instead.
        setMode(null);
        return;
      }
      try {
        const modeRes = await client.get('/api/ai-mode', { params: { store_id: currentBrandId } });
        setMode(modeRes.data?.mode || 'supervised');
      } catch {
        setMode(null);
      }
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      const id = list[0]?.id || null;
      setBrandId(id);
      loadStatus(id);
    }).catch(() => loadStatus(null));
    // If we're returning from Gmail OAuth, refresh status and clean up URL
    const params = new URLSearchParams(window.location.search);
    if (params.get('gmail_connected')) {
      setMsg('Gmail connected successfully!');
      window.history.replaceState({}, '', window.location.pathname);
    } else if (params.get('gmail_error')) {
      setGmailErrorCode(params.get('gmail_error'));
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [loadStatus]);

  const handleConnect = async () => {
    setConnecting(true);
    setGmailErrorCode(null);
    try {
      // Get the Google OAuth URL via authenticated API call (Bearer token sent by axios)
      const res = await client.get('/api/v1/settings/gmail/connect');
      const authUrl = res.data?.auth_url;
      if (!authUrl) {
        setMsg('Could not get Gmail auth URL. Check GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET env vars.');
        setConnecting(false);
        return;
      }
      // Navigate the browser directly to Google's consent screen — leaves
      // `connecting` true until the page actually unloads, so the button
      // reads "Connecting..." right up to the redirect.
      window.location.href = authUrl;
    } catch (err) {
      setConnecting(false);
      // No response at all (network down / backend unreachable) reads very
      // differently from a clean 4xx/5xx with a real detail message — worth
      // telling apart rather than falling through to the same generic line.
      if (!err.response) {
        setMsg("Couldn't reach tResolv's server to start the connection. Check your internet connection and try again.");
      } else {
        setMsg(err.response?.data?.detail || 'Failed to start Gmail connection. Make sure a brand exists first.');
      }
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect Gmail? Resolv will stop monitoring your inbox.')) return;
    setDisconnecting(true);
    try {
      await client.delete('/api/v1/settings/gmail/disconnect');
      setGmailStatus({ connected: false, email: null });
      setMsg('Gmail disconnected.');
    } catch {
      setMsg('Failed to disconnect Gmail.');
    } finally {
      setDisconnecting(false);
    }
  };

  const handleModeChange = async (newMode) => {
    // No valid brand resolved — never call /api/ai-mode without store_id.
    if (!brandId) return;
    setSavingMode(true);
    setMsg('');
    try {
      await client.patch('/api/ai-mode', { mode: newMode, store_id: brandId });
      setMode(newMode);
    } catch {
      setMsg('Failed to update AI mode.');
    } finally {
      setSavingMode(false);
    }
  };

  const handleThresholdChange = (val) => {
    setThreshold(val);
    clearTimeout(thresholdTimer.current);
    thresholdTimer.current = setTimeout(async () => {
      try {
        await client.patch('/api/v1/settings/account', { confidence_threshold: val });
      } catch {
        setMsg('Failed to save threshold.');
      }
    }, 500);
  };

  if (loadingStatus) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '520px' }}>
        {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '80px', borderRadius: '6px' }} />)}
      </div>
    );
  }

  // Five distinct, unambiguous states — never just "connected: true/false".
  // connecting takes priority (a click just happened); otherwise the
  // freshest server-confirmed state (connected) wins over a possibly-stale
  // error from an earlier attempt; then a fresh OAuth failure; then the
  // needs_reconnect signal computed backend-side from a revoked token
  // (brand_gmail.py / saas_settings.py — gmail_connected=false but
  // gmail_email still on file); otherwise the plain first-time state.
  const connectionState = connecting
    ? 'connecting'
    : gmailStatus?.connected
    ? 'connected'
    : gmailErrorCode
    ? 'failed'
    : gmailStatus?.needs_reconnect
    ? 'needs_reconnect'
    : 'not_connected';

  const dotColor = {
    connected: '#10B981', connecting: '#06B6D4', failed: '#EF4444',
    needs_reconnect: '#F59E0B', not_connected: '#94A3B8',
  }[connectionState];

  const statusLabel = {
    connected: 'Connected', connecting: 'Connecting…', failed: 'Connection Failed',
    needs_reconnect: 'Connection Expired', not_connected: 'Not Connected',
  }[connectionState];

  const statusLabelColor = {
    connected: '#10B981', connecting: '#06B6D4', failed: '#EF4444',
    needs_reconnect: '#B45309', not_connected: '#64748B',
  }[connectionState];

  const connectButtonLabel = {
    connecting: 'Connecting…', needs_reconnect: 'Reconnect Gmail →', failed: 'Try Again →',
  }[connectionState] || 'Connect Gmail →';

  return (
    <div style={{ maxWidth: '520px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <Alert variant={msg.includes('Failed') || msg.includes("Couldn't") ? 'error' : 'success'}>{msg}</Alert>

      {/* Gmail Connection */}
      <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px' }}>
        <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B', marginBottom: '16px' }}>Gmail Connection</div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: connectionState === 'connected' ? '12px' : '4px' }}>
          <div style={{
            width: '10px', height: '10px', borderRadius: '50%', background: dotColor, flexShrink: 0,
            animation: connectionState === 'connecting' ? 'pulse 1.2s ease-in-out infinite' : 'none',
          }} />
          <div style={{ fontSize: '14px', fontWeight: '600', color: statusLabelColor }}>{statusLabel}</div>
        </div>

        {connectionState === 'connected' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ fontSize: '13px', color: '#475569', marginTop: '-8px' }}>{gmailStatus.email}</div>
            {gmailStatus.last_polled_at && (
              <div style={{ fontSize: '12px', color: '#94A3B8' }}>
                Inbox checked every 60 seconds
              </div>
            )}
            <button
              onClick={handleDisconnect}
              disabled={disconnecting}
              style={{ alignSelf: 'flex-start', padding: '7px 14px', borderRadius: '6px', border: '1px solid #FECACA', background: '#FEF2F2', color: '#EF4444', fontSize: '13px', fontWeight: '500', cursor: disconnecting ? 'not-allowed' : 'pointer' }}
              onMouseEnter={e => { if(!disconnecting) e.target.style.background = '#FEE2E2'; }}
              onMouseLeave={e => { if(!disconnecting) e.target.style.background = '#FEF2F2'; }}
            >
              {disconnecting ? 'Disconnecting...' : 'Disconnect'}
            </button>
          </div>
        )}

        {connectionState !== 'connected' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', alignItems: 'flex-start' }}>
            {connectionState === 'failed' && (
              <div style={{
                width: '100%', padding: '12px 14px', background: '#FEF2F2', border: '1px solid #FECACA',
                borderRadius: '6px', fontSize: '13px', color: '#991B1B', lineHeight: '1.5',
              }}>
                {gmailOAuthErrorMessage(gmailErrorCode)}
              </div>
            )}

            {connectionState === 'needs_reconnect' && (
              <div style={{
                width: '100%', padding: '12px 14px', background: '#FFFBEB', border: '1px solid #FDE68A',
                borderRadius: '6px', fontSize: '13px', color: '#92400E', lineHeight: '1.5',
              }}>
                Google access for <strong>{gmailStatus?.email}</strong> was revoked or expired — this can
                happen after a password change, a Google security review, or removing tResolv's access in
                your Google Account. tResolv has stopped monitoring this inbox. Reconnect to resume — your
                settings and past conversations are unaffected.
              </div>
            )}

            <div style={{ fontSize: '14px', color: '#475569', lineHeight: '1.5' }}>
              Resolv will monitor this inbox every 60 seconds for new customer emails and send replies from it — directly from your address.
            </div>
            <div style={{ width: '100%' }}>
              <GmailUnverifiedNotice />
            </div>
            <button
              onClick={handleConnect}
              disabled={connecting}
              style={{ padding: '9px 18px', borderRadius: '6px', background: connecting ? '#94A3B8' : '#06B6D4', color: 'white', fontWeight: '600', fontSize: '14px', border: 'none', cursor: connecting ? 'not-allowed' : 'pointer', transition: 'background 0.15s' }}
              onMouseEnter={e => { if (!connecting) e.target.style.background = '#0891B2'; }}
              onMouseLeave={e => { if (!connecting) e.target.style.background = '#06B6D4'; }}
            >
              {connectButtonLabel}
            </button>
            <div style={{ fontSize: '12px', color: '#94A3B8' }}>
              Google permissions required: read emails, send emails, mark as read
            </div>
          </div>
        )}
      </div>

      {/* AI Mode */}
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '20px' }}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>AI Mode</div>
        {!brandId ? (
          <Alert variant="error">Couldn't determine your store, so AI Mode can't be shown or changed right now. Try reloading the page.</Alert>
        ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[
            { id: 'autopilot', label: 'Autopilot', desc: `Resolv sends replies automatically when confidence ≥ ${threshold}%. Refunds and cancellations always require your approval.` },
            { id: 'supervised', label: 'Supervised', desc: 'All AI replies are saved as drafts. You approve before anything sends.' },
          ].map(({ id, label, desc }) => (
            <div
              key={id}
              onClick={() => !savingMode && handleModeChange(id)}
              style={{ padding: '14px', border: `1px solid ${mode === id ? 'var(--accent)' : 'var(--border-strong)'}`, borderRadius: '6px', cursor: savingMode ? 'not-allowed' : 'pointer', background: mode === id ? 'var(--bg-secondary)' : 'var(--bg-primary)' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
                <div style={{ width: '14px', height: '14px', borderRadius: '50%', border: '2px solid var(--accent)', background: mode === id ? 'var(--accent)' : 'transparent', flexShrink: 0 }} />
                <div style={{ fontWeight: '600', fontSize: '14px', color: 'var(--text-primary)' }}>{label}</div>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: '24px' }}>{desc}</div>
            </div>
          ))}
        </div>
        )}

        {/* Confidence Threshold */}
        <div style={{ marginTop: '16px' }}>
          <label style={{ fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', display: 'block', marginBottom: '8px' }}>
            Auto-send threshold: <span style={{ color: 'var(--accent)', fontFamily: 'DM Mono, monospace' }}>{threshold}%</span>
          </label>
          <input
            type="range"
            min={70}
            max={95}
            step={5}
            value={threshold}
            onChange={e => handleThresholdChange(Number(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
            Resolv only sends automatically when it is this confident the reply is correct.
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────── Shopify Tab ──

function ShopifyHealthCard() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      const brand = list.find(b => b.shopify_connected) || list[0];
      if (!brand?.id) { setLoading(false); return; }
      client.get(`/api/v2/brands/${brand.id}/shopify/health`)
        .then(r => setHealth(r.data))
        .catch(() => setHealth(null))
        .finally(() => setLoading(false));
    }).catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="skeleton" style={{ height: '160px', borderRadius: '8px' }} />;
  if (!health?.connected) return null;

  const healthy = health.status === 'healthy';
  const scopeChip = (s, healthyChip) => (
    <span key={s} style={{
      fontSize: '12px', padding: '2px 8px', borderRadius: '4px',
      background: healthyChip ? '#ECFDF5' : '#FEF2F2', color: healthyChip ? '#065F46' : '#B91C1C',
    }}>{s}</span>
  );

  return (
    <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B' }}>Shopify connection health</div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: healthy ? '#10B981' : '#F59E0B', flexShrink: 0 }} />
        <div style={{ fontSize: '14px', fontWeight: '600', color: healthy ? '#10B981' : '#F59E0B' }}>
          {healthy ? 'Healthy' : 'Needs permission update'}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', rowGap: '8px', fontSize: '13px', color: '#475569' }}>
        <div style={{ fontWeight: '600' }}>Connected store</div><div>{health.domain}</div>
        <div style={{ fontWeight: '600' }}>Connected app</div><div>{health.app_name || 'Unknown'}</div>
      </div>

      <div>
        <div style={{ fontSize: '13px', fontWeight: '600', color: '#475569', marginBottom: '6px' }}>Granted scopes</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {health.granted_scopes?.length
            ? health.granted_scopes.map(s => scopeChip(s, true))
            : <span style={{ fontSize: '12px', color: '#94A3B8' }}>None</span>}
        </div>
      </div>

      {health.missing_scopes?.length > 0 && (
        <div>
          <div style={{ fontSize: '13px', fontWeight: '600', color: '#475569', marginBottom: '6px' }}>Missing scopes</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {health.missing_scopes.map(s => scopeChip(s, false))}
          </div>
        </div>
      )}
    </div>
  );
}

function ShopifyTab() {
  const [shopifyStatus, setShopifyStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [brandId, setBrandId] = useState(null);
  const [shopDomain, setShopDomain] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');
  const [showUpdateToken, setShowUpdateToken] = useState(false);
  const [newToken, setNewToken] = useState('');
  const [updatingToken, setUpdatingToken] = useState(false);

  const loadStatus = useCallback(() => {
    setLoadingStatus(true);
    client.get('/api/v1/settings/shopify')
      .then(res => setShopifyStatus(res.data))
      .catch(() => setShopifyStatus({ connected: false }))
      .finally(() => setLoadingStatus(false));
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      if (list[0]?.id) setBrandId(list[0].id);
    }).catch(() => {});
  }, []);

  // Picks up the redirect back from the OAuth callback (backend/src/api/routes/shopify_auth.py).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('shopify_connected') === '1') {
      setMsg(`Connected: ${params.get('shop_name') || params.get('shop_domain') || 'Shopify store'}`);
      loadStatus();
      window.history.replaceState({}, '', window.location.pathname);
    } else if (params.get('shopify_error')) {
      setError(SHOPIFY_OAUTH_ERROR_MESSAGES[params.get('shopify_error')] || 'Could not connect to Shopify. Please try again.');
      window.history.replaceState({}, '', window.location.pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isConnected = shopifyStatus?.connected;

  const handleConnect = async () => {
    if (!shopDomain.trim()) {
      setError('Store URL is required.');
      return;
    }
    if (!brandId) {
      setError("Couldn't find your workspace. Please refresh and try again.");
      return;
    }
    setConnecting(true);
    setError('');
    setMsg('');
    try {
      const shop = shopDomain.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');
      const res = await client.get(`/api/v2/brands/${brandId}/shopify/oauth/start`, { params: { shop, return_to: 'settings' } });
      window.location.href = res.data.auth_url;
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail?.error || 'Could not start the Shopify connection. Check your store URL.');
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect Shopify? AI actions requiring order data will be disabled.')) return;
    setDisconnecting(true);
    try {
      await client.post('/api/v1/settings/shopify/disconnect');
      setMsg('Shopify disconnected.');
      loadStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to disconnect Shopify.');
    } finally {
      setDisconnecting(false);
    }
  };

  const handleUpdateToken = async () => {
    if (!newToken.trim()) {
      setError('Enter a new access token.');
      return;
    }
    setUpdatingToken(true);
    setError('');
    setMsg('');
    try {
      const res = await client.post('/api/v1/settings/shopify/connect', {
        shop_domain: shopifyStatus.shop_domain,
        access_token: newToken.trim(),
      });
      setMsg(`Token updated for ${res.data.shop_name || shopifyStatus.shop_domain}.`);
      setNewToken('');
      setShowUpdateToken(false);
      loadStatus();
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail?.error || 'Failed to update token. Check the token and try again.');
    } finally {
      setUpdatingToken(false);
    }
  };

  if (loadingStatus) {
    return <div className="skeleton" style={{ height: '200px', borderRadius: '8px', maxWidth: '520px' }} />;
  }

  return (
    <div style={{ maxWidth: '520px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <Alert variant="success">{msg}</Alert>
      <Alert variant="error">{error}</Alert>

      {isConnected && <ShopifyHealthCard />}

      {isConnected ? (
        <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B', marginBottom: '8px' }}>Shopify Connection</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#10B981', flexShrink: 0 }} />
            <div>
              <div style={{ fontSize: '14px', fontWeight: '600', color: '#10B981' }}>
                Connected: {shopifyStatus?.shop_name || 'Shopify Store'}
              </div>
              <div style={{ fontSize: '13px', color: '#475569' }}>{shopifyStatus?.shop_domain}</div>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {['Order lookup by number', 'Refund processing', 'Order cancellation', 'Shipping address changes', 'Order reship', 'Inventory check'].map(cap => (
              <div key={cap} style={{ fontSize: '13px', color: '#475569', display: 'flex', gap: '8px' }}>
                <span style={{ color: '#10B981' }}>✓</span>{cap}
              </div>
            ))}
          </div>

          {/* Update API Token */}
          <div style={{ borderTop: '1px solid #E4E4E7', paddingTop: '14px' }}>
            <button
              onClick={() => { setShowUpdateToken(v => !v); setNewToken(''); setError(''); }}
              style={{ fontSize: '13px', color: '#06B6D4', background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontWeight: '500' }}
            >
              {showUpdateToken ? '↑ Cancel token update' : '↓ Update API access token'}
            </button>
            {showUpdateToken && (
              <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div style={{ fontSize: '12px', color: '#94A3B8', lineHeight: '1.5' }}>
                  Generate a new Admin API access token in Shopify → Settings → Apps → Develop apps, then paste it below.
                </div>
                <input
                  type="password"
                  value={newToken}
                  onChange={e => setNewToken(e.target.value)}
                  placeholder="shpat_... or shpca_..."
                  style={inputStyle}
                  onFocus={e => e.target.style.borderColor = '#06B6D4'}
                  onBlur={e => e.target.style.borderColor = '#E4E4E7'}
                />
                <button
                  onClick={handleUpdateToken}
                  disabled={updatingToken}
                  style={{ alignSelf: 'flex-start', padding: '9px 18px', borderRadius: '6px', border: 'none', background: updatingToken ? '#E4E4E7' : '#06B6D4', color: updatingToken ? '#94A3B8' : 'white', fontSize: '14px', fontWeight: '600', cursor: updatingToken ? 'not-allowed' : 'pointer' }}
                >
                  {updatingToken ? 'Updating...' : 'Update Token'}
                </button>
              </div>
            )}
          </div>

          <button
            onClick={handleDisconnect}
            disabled={disconnecting}
            style={{ alignSelf: 'flex-start', padding: '7px 14px', borderRadius: '6px', border: '1px solid #FECACA', background: '#FEF2F2', color: '#EF4444', fontSize: '13px', fontWeight: '500', cursor: disconnecting ? 'not-allowed' : 'pointer', transition: 'background 0.15s' }}
            onMouseEnter={e => { if(!disconnecting) e.target.style.background = '#FEE2E2'; }}
            onMouseLeave={e => { if(!disconnecting) e.target.style.background = '#FEF2F2'; }}
          >
            {disconnecting ? 'Disconnecting...' : 'Disconnect Shopify'}
          </button>
        </div>
      ) : (
        <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B' }}>Connect your Shopify store</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
            <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#94A3B8', flexShrink: 0 }} />
            <div style={{ fontSize: '14px', fontWeight: '600', color: '#64748B' }}>Not Connected</div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#475569', marginBottom: '5px' }}>Store URL</label>
            <input
              value={shopDomain}
              onChange={e => setShopDomain(e.target.value)}
              placeholder="mystore.myshopify.com"
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
          </div>

          <ShopifyConnectionNotice />

          <button
            onClick={handleConnect}
            disabled={connecting}
            style={{ padding: '9px 18px', borderRadius: '6px', border: 'none', background: connecting ? '#E4E4E7' : '#06B6D4', color: connecting ? '#94A3B8' : 'white', fontWeight: '600', fontSize: '14px', cursor: connecting ? 'not-allowed' : 'pointer', alignSelf: 'flex-start', transition: 'background 0.15s' }}
            onMouseEnter={e => { if(!connecting) e.target.style.background = '#0891B2'; }}
            onMouseLeave={e => { if(!connecting) e.target.style.background = '#06B6D4'; }}
          >
            {connecting ? 'Connecting...' : 'Connect Shopify Store →'}
          </button>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────── Knowledge Base Tab ──

function KnowledgeBaseTab() {
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ title: '', content: '' });
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState('');
  // Same brandId-resolution pattern as ShopifyTab above — the v1 tenant-only
  // knowledge-base endpoints wrote rows with no brand_id, which the live
  // agent's brand-scoped retrieval (match_brand_rag_chunks) can never see.
  // The v2 brand-scoped endpoints are the ones the agent actually reads from.
  const [brandId, setBrandId] = useState(null);

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      if (list[0]?.id) setBrandId(list[0].id);
    }).catch(() => {});
  }, []);

  const fetchSources = useCallback(() => {
    if (!brandId) return;
    setLoading(true);
    setError('');
    client.get(`/api/v2/brands/${brandId}/knowledge/sources`)
      .then(res => setSources(Array.isArray(res.data) ? res.data : (res.data?.sources || [])))
      .catch(() => setError('Failed to load knowledge base sources.'))
      .finally(() => setLoading(false));
  }, [brandId]);

  useEffect(() => { fetchSources(); }, [fetchSources]);

  const handleUpload = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      setUploadMsg('Title and content are required.');
      return;
    }
    if (!brandId) {
      setUploadMsg("Couldn't find your workspace. Please refresh and try again.");
      return;
    }
    setUploading(true);
    setUploadMsg('');
    try {
      await client.post(`/api/v2/brands/${brandId}/knowledge/upload`, {
        name: form.title.trim(),
        content: form.content.trim(),
      });
      setForm({ title: '', content: '' });
      setUploadMsg('Document uploaded successfully.');
      fetchSources();
    } catch {
      setUploadMsg('Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id) => {
    if (!brandId) return;
    try {
      await client.delete(`/api/v2/brands/${brandId}/knowledge/sources/${id}`);
      setSources(s => s.filter(src => src.id !== id));
    } catch {
      setError('Failed to delete source.');
    }
  };

  return (
    <div style={{ maxWidth: '600px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>Add Document</div>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Title</label>
          <input
            value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="e.g. Return Policy"
            style={inputStyle}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Content</label>
          <textarea
            value={form.content}
            onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
            placeholder="Paste your document text here..."
            rows={6}
            style={{ ...inputStyle, resize: 'vertical', lineHeight: '1.5' }}
          />
        </div>
        <Alert variant={uploadMsg.includes('fail') || uploadMsg.includes('required') ? 'error' : 'success'}>{uploadMsg}</Alert>
        <button
          onClick={handleUpload}
          disabled={uploading}
          style={{ padding: '9px 20px', borderRadius: '4px', background: uploading ? 'var(--bg-tertiary)' : 'var(--accent)', color: uploading ? 'var(--text-muted)' : 'white', fontWeight: '500', fontSize: '14px', cursor: uploading ? 'not-allowed' : 'pointer', alignSelf: 'flex-start' }}
        >
          {uploading ? 'Uploading...' : 'Upload'}
        </button>
      </div>

      <div>
        <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)', marginBottom: '12px' }}>Knowledge Sources</div>
        <div style={{ marginBottom: error ? '12px' : 0 }}>
          <Alert variant="error">{error}</Alert>
        </div>
        {loading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '48px', borderRadius: '4px' }} />)}
          </div>
        ) : sources.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)', border: '1px dashed var(--border)', borderRadius: '6px', fontSize: '14px' }}>
            Add your return policy, shipping info, and FAQs. Resolv uses this to answer questions accurately.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {sources.map(src => (
              <div key={src.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)' }}>
                <div>
                  <div style={{ fontWeight: '500', fontSize: '14px', color: 'var(--text-primary)' }}>{src.name || src.title}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                    {src.created_at ? new Date(src.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(src.id)}
                  style={{ padding: '5px 12px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--danger)', fontSize: '12px', cursor: 'pointer' }}
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────── Email Filter Tab ──

function FilterTab() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [form, setForm] = useState({
    blocked_domains: '',
    whitelisted_domains: '',
    max_auto_replies: 2,
    promotion_filter_enabled: true,
    loop_protection_enabled: true,
    // Guardian fields (feature 006)
    support_only_mode: true,
    auto_reply_enabled: true,
    confidence_threshold: 0.75,
  });

  useEffect(() => {
    setLoading(true);
    client.get('/api/v1/settings/email-filter')
      .then(res => {
        const d = res.data;
        setSettings(d);
        setForm({
          blocked_domains: (d.blocked_domains || []).join(', '),
          whitelisted_domains: (d.whitelisted_domains || []).join(', '),
          max_auto_replies: d.max_auto_replies ?? 2,
          promotion_filter_enabled: d.promotion_filter_enabled ?? true,
          loop_protection_enabled: d.loop_protection_enabled ?? true,
          // Guardian fields (feature 006)
          support_only_mode: d.support_only_mode ?? true,
          auto_reply_enabled: d.auto_reply_enabled ?? true,
          confidence_threshold: d.confidence_threshold ?? 0.75,
        });
      })
      .catch(() => setMsg('Failed to load filter settings.'))
      .finally(() => setLoading(false));
  }, []);

  const parseDomains = (str) =>
    str.split(',').map(d => d.trim().toLowerCase()).filter(Boolean);

  const handleSave = async () => {
    setSaving(true);
    setMsg('');
    const maxReplies = Number(form.max_auto_replies);
    if (isNaN(maxReplies) || maxReplies < 0 || maxReplies > 10) {
      setMsg('Max auto-replies must be between 0 and 10.');
      setSaving(false);
      return;
    }
    const threshold = parseFloat(form.confidence_threshold);
    if (isNaN(threshold) || threshold < 0 || threshold > 1) {
      setMsg('Confidence threshold must be between 0.0 and 1.0.');
      setSaving(false);
      return;
    }
    try {
      await client.patch('/api/v1/settings/email-filter', {
        blocked_domains: parseDomains(form.blocked_domains),
        whitelisted_domains: parseDomains(form.whitelisted_domains),
        max_auto_replies: maxReplies,
        promotion_filter_enabled: form.promotion_filter_enabled,
        loop_protection_enabled: form.loop_protection_enabled,
        // Guardian fields (feature 006)
        support_only_mode: form.support_only_mode,
        auto_reply_enabled: form.auto_reply_enabled,
        confidence_threshold: threshold,
      });
      setMsg('Filter settings saved. Changes take effect within 60 seconds.');
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Failed to save filter settings.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '520px' }}>
        {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '60px', borderRadius: '6px' }} />)}
      </div>
    );
  }

  const labelStyle = { display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' };
  const hintStyle = { fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' };
  const cardStyle = { background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' };
  const toggleRowStyle = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 0', borderTop: '1px solid var(--border)' };

  return (
    <div style={{ maxWidth: '520px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <Alert variant={msg.includes('Failed') || msg.includes('must') ? 'error' : 'success'}>{msg}</Alert>

      {/* Domain Lists */}
      <div style={cardStyle}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Domain Rules</div>
        <div>
          <label style={labelStyle}>Blocked Domains</label>
          <input
            value={form.blocked_domains}
            onChange={e => setForm(f => ({ ...f, blocked_domains: e.target.value }))}
            placeholder="spamco.io, newsletter.example.com"
            style={inputStyle}
          />
          <div style={hintStyle}>Comma-separated. Emails from these domains are always blocked.</div>
        </div>
        <div>
          <label style={labelStyle}>Whitelisted Domains</label>
          <input
            value={form.whitelisted_domains}
            onChange={e => setForm(f => ({ ...f, whitelisted_domains: e.target.value }))}
            placeholder="trusteddomain.com, partner-crm.io"
            style={inputStyle}
          />
          <div style={hintStyle}>These bypass sender-pattern and Gmail category checks. Auto-reply header checks still apply.</div>
        </div>
      </div>

      {/* Loop Protection */}
      <div style={cardStyle}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Loop Protection</div>
        <div>
          <label style={labelStyle}>Max Auto-Replies per Thread</label>
          <input
            type="number"
            min={0}
            max={10}
            value={form.max_auto_replies}
            onChange={e => setForm(f => ({ ...f, max_auto_replies: e.target.value }))}
            style={{ ...inputStyle, width: '80px' }}
          />
          <div style={hintStyle}>0 = AI replies always require human approval. Max 10.</div>
        </div>
        <div style={toggleRowStyle}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: '500', color: 'var(--text-primary)' }}>Loop Protection</div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>Stop AI replies when a thread exceeds the threshold above</div>
          </div>
          <input
            type="checkbox"
            checked={form.loop_protection_enabled}
            onChange={e => setForm(f => ({ ...f, loop_protection_enabled: e.target.checked }))}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
        </div>
      </div>

      {/* Content Filtering */}
      <div style={cardStyle}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Content Filtering</div>
        <div style={{ ...toggleRowStyle, borderTop: 'none', paddingTop: 0 }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: '500', color: 'var(--text-primary)' }}>Promotion Filter</div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>Block emails with promotional keywords (unsubscribe, webinar, discount, etc.)</div>
          </div>
          <input
            type="checkbox"
            checked={form.promotion_filter_enabled}
            onChange={e => setForm(f => ({ ...f, promotion_filter_enabled: e.target.checked }))}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
        </div>
      </div>

      {/* AI Guardian */}
      <div style={cardStyle}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>AI Guardian (Layer 4–5)</div>

        <div style={toggleRowStyle}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: '500', color: 'var(--text-primary)' }}>Support-Only Mode</div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>Only <strong>customer_support</strong> emails create tickets. All other emails are silently discarded.</div>
          </div>
          <input
            type="checkbox"
            checked={form.support_only_mode}
            onChange={e => setForm(f => ({ ...f, support_only_mode: e.target.checked }))}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
        </div>

        <div style={toggleRowStyle}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: '500', color: 'var(--text-primary)' }}>Send AI Replies Automatically</div>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>When disabled, tickets are created but no AI reply is sent. Human agents reply manually.</div>
          </div>
          <input
            type="checkbox"
            checked={form.auto_reply_enabled}
            onChange={e => setForm(f => ({ ...f, auto_reply_enabled: e.target.checked }))}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
        </div>

        <div>
          <label style={labelStyle}>
            Confidence Threshold: <span style={{ color: 'var(--accent)', fontFamily: 'DM Mono, monospace' }}>{Math.round((form.confidence_threshold || 0) * 100)}%</span>
          </label>
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={form.confidence_threshold}
            onChange={e => setForm(f => ({ ...f, confidence_threshold: e.target.value }))}
            style={{ ...inputStyle, width: '100px' }}
          />
          <div style={hintStyle}>Minimum AI confidence to create a ticket. Below this threshold the email goes to the Quarantine Queue for human review. (0.75 = 75%)</div>
        </div>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        style={{ padding: '10px 22px', borderRadius: '4px', background: saving ? 'var(--bg-tertiary)' : 'var(--accent)', color: saving ? 'var(--text-muted)' : 'white', fontWeight: '600', fontSize: '14px', cursor: saving ? 'not-allowed' : 'pointer', alignSelf: 'flex-start' }}
      >
        {saving ? 'Saving...' : 'Save Filter Settings'}
      </button>
    </div>
  );
}

// ────────────────────────────────────────────────────── Reply Style Tab ──

const STYLE_FIELD_LABELS = {
  tone: 'Tone',
  greeting_style: 'Greeting',
  closing_style: 'Closing / sign-off',
  emoji_usage: 'Emoji use',
  sentence_length: 'Sentence length',
  paragraph_style: 'Paragraph structure',
  use_bullets: 'Bullet points',
  use_customer_name: 'Customer name usage',
};

function ReplyStyleTab() {
  const [brandId, setBrandId] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [savingMode, setSavingMode] = useState(false);
  const [examples, setExamples] = useState([]);
  const [newExampleQuestion, setNewExampleQuestion] = useState('');
  const [newExampleReply, setNewExampleReply] = useState('');
  const [addingExample, setAddingExample] = useState(false);

  // Identity (assistant name, signature) is a separate concept from Reply
  // Style — it's who the AI is, not how it sounds. Kept as its own
  // state/save action, not part of the reply_style_* payload.
  const [agentName, setAgentName] = useState('');
  const [emailSignature, setEmailSignature] = useState('');
  const [savingIdentity, setSavingIdentity] = useState(false);
  const [identityMsg, setIdentityMsg] = useState('');

  const load = useCallback((id) => {
    setLoading(true);
    setError('');
    client.get(`/api/v2/brands/${id}/reply-style`)
      .then(res => setData(res.data))
      .catch(() => setError('Failed to load Reply Style settings.'))
      .finally(() => setLoading(false));
  }, []);

  const loadIdentity = useCallback((id) => {
    client.get(`/api/v2/brands/${id}`)
      .then(res => {
        const brand = res.data?.brand || res.data;
        setAgentName(brand?.agent_name || 'Luna');
        setEmailSignature(brand?.email_signature || '');
      })
      .catch(() => {});
  }, []);

  const saveIdentity = async () => {
    if (!brandId) return;
    setSavingIdentity(true);
    setIdentityMsg('');
    try {
      await client.patch(`/api/v2/brands/${brandId}`, { agent_name: agentName, email_signature: emailSignature });
      setIdentityMsg('Saved!');
    } catch (err) {
      setIdentityMsg(err.response?.data?.detail || 'Failed to save.');
    } finally {
      setSavingIdentity(false);
    }
  };

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      if (list.length > 0) {
        setBrandId(list[0].id);
        load(list[0].id);
        loadIdentity(list[0].id);
      } else {
        setLoading(false);
      }
    }).catch(() => setLoading(false));
  }, [load, loadIdentity]);

  useEffect(() => {
    if (!brandId) return;
    client.get(`/api/v2/brands/${brandId}/reply-style/examples`)
      .then(res => setExamples(res.data?.examples || []))
      .catch(() => {});
  }, [brandId]);

  const choosePreset = async (presetId) => {
    if (!brandId) return;
    setSavingMode(true);
    setMsg('');
    try {
      await client.patch(`/api/v2/brands/${brandId}/reply-style`, { mode: 'preset', preset: presetId });
      localStorage.setItem('resolv_reply_style_done', 'true');
      setMsg('Reply Style updated.');
      load(brandId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update Reply Style.');
    } finally {
      setSavingMode(false);
    }
  };

  const setDisabled = async (disabled) => {
    if (!brandId) return;
    setSavingMode(true);
    try {
      await client.patch(`/api/v2/brands/${brandId}/reply-style`, { mode: disabled ? 'disabled' : 'preset' });
      localStorage.setItem('resolv_reply_style_done', 'true');
      load(brandId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update Reply Style.');
    } finally {
      setSavingMode(false);
    }
  };

  const toggleControl = async (field, value) => {
    if (!brandId) return;
    try {
      await client.patch(`/api/v2/brands/${brandId}/reply-style`, { [field]: value });
      localStorage.setItem('resolv_reply_style_done', 'true');
      load(brandId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update setting.');
    }
  };

  const regenerate = async () => {
    if (!brandId) return;
    setRegenerating(true);
    setError('');
    setMsg('');
    try {
      await client.post(`/api/v2/brands/${brandId}/reply-style/regenerate`);
      localStorage.setItem('resolv_reply_style_done', 'true');
      setMsg('Reply Style regenerated from your recent approved replies.');
      load(brandId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not regenerate — check the approved reply count below.');
    } finally {
      setRegenerating(false);
    }
  };

  const switchToLearned = async () => {
    if (!brandId) return;
    setSwitching(true);
    setError('');
    try {
      await client.post(`/api/v2/brands/${brandId}/reply-style/switch-to-learned`);
      localStorage.setItem('resolv_reply_style_done', 'true');
      setMsg('Switched to your Learned Style.');
      load(brandId);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to switch to Learned Style.');
    } finally {
      setSwitching(false);
    }
  };

  const addExample = async () => {
    if (!brandId || !newExampleQuestion.trim() || !newExampleReply.trim()) return;
    setAddingExample(true);
    try {
      // Stored as a single "content" string - no backend/schema change.
      // The Customer:/Luna's reply: framing is what feeds style-profile
      // extraction (reply_style_service._uploaded_example_texts), same as
      // any other uploaded example text today.
      const content = `Customer: ${newExampleQuestion.trim()}\n\nLuna's reply: ${newExampleReply.trim()}`;
      await client.post(`/api/v2/brands/${brandId}/reply-style/examples`, { content });
      localStorage.setItem('resolv_reply_style_done', 'true');
      setNewExampleQuestion('');
      setNewExampleReply('');
      const res = await client.get(`/api/v2/brands/${brandId}/reply-style/examples`);
      setExamples(res.data?.examples || []);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to add example.');
    } finally {
      setAddingExample(false);
    }
  };

  const deleteExample = async (id) => {
    if (!brandId) return;
    try {
      await client.delete(`/api/v2/brands/${brandId}/reply-style/examples/${id}`);
      setExamples(ex => ex.filter(e => e.id !== id));
    } catch {
      setError('Failed to delete example.');
    }
  };

  const card = { background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '20px 24px' };
  const sectionTitle = { fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '6px' };

  if (loading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: '80px', borderRadius: '6px' }} />)}
      </div>
    );
  }

  if (!data) {
    return <Alert variant="error">{error || 'Reply Style is not available for this brand yet.'}</Alert>;
  }

  const mode = data.mode || 'preset';
  const presets = data.presets || [];
  const progressPct = Math.min(100, Math.round((data.approved_reply_count / Math.max(1, data.min_replies_required)) * 100));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '760px' }}>
      <div style={card}>
        <div style={sectionTitle}>Identity</div>
        <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
          Who your AI is — its name and how it signs off. Separate from Reply Style, which controls tone and wording.
        </div>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Assistant Name</div>
            <input
              value={agentName}
              onChange={e => setAgentName(e.target.value)}
              placeholder="Luna"
              maxLength={20}
              style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', fontSize: '13px', width: '160px' }}
            />
          </div>
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Email Signature</div>
            <textarea
              value={emailSignature}
              onChange={e => setEmailSignature(e.target.value)}
              placeholder={`Best,\n${agentName || 'Luna'}`}
              rows={2}
              style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', fontSize: '13px', width: '260px', resize: 'vertical', fontFamily: 'inherit' }}
            />
          </div>
          <button
            onClick={saveIdentity}
            disabled={savingIdentity}
            style={{ padding: '7px 14px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px', cursor: savingIdentity ? 'not-allowed' : 'pointer' }}
          >
            {savingIdentity ? 'Saving...' : 'Save'}
          </button>
          {identityMsg && (
            <span style={{ fontSize: '12px', color: identityMsg === 'Saved!' ? 'var(--success)' : 'var(--danger)' }}>
              {identityMsg}
            </span>
          )}
        </div>
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <div>
            <div style={sectionTitle}>Reply Style</div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              Controls how your AI agent's replies sound — tone, greetings, formatting. It never changes facts, refund decisions, or policy.
            </div>
          </div>
          <div style={{
            fontSize: '11px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.04em',
            padding: '4px 10px', borderRadius: '999px',
            background: mode === 'disabled' ? 'var(--bg-tertiary)' : mode === 'learned' ? '#ECFEFF' : '#F0FDF4',
            color: mode === 'disabled' ? 'var(--text-muted)' : mode === 'learned' ? '#0E7490' : '#15803D',
          }}>
            {mode === 'disabled' ? 'Disabled' : mode === 'learned' ? 'Learned' : 'Preset'}
          </div>
        </div>

        <Alert variant="error" onDismiss={() => setError('')}>{error}</Alert>
        <Alert variant="success" onDismiss={() => setMsg('')}>{msg}</Alert>

        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)', marginTop: '8px' }}>
          <input type="checkbox" checked={mode === 'disabled'} onChange={e => setDisabled(e.target.checked)} disabled={savingMode} />
          Disable Reply Style (use a neutral default tone)
        </label>
      </div>

      {mode !== 'disabled' && data.learned_profile && mode !== 'learned' && (
        <div style={{ ...card, background: '#ECFEFF', borderColor: '#A5F3FC' }}>
          <div style={{ fontSize: '13px', fontWeight: '600', color: '#0E7490', marginBottom: '8px' }}>
            We've learned how your team naturally replies.
          </div>
          <button
            onClick={switchToLearned}
            disabled={switching}
            style={{ padding: '8px 16px', borderRadius: '4px', background: '#06B6D4', color: 'white', fontWeight: '600', fontSize: '13px', cursor: switching ? 'not-allowed' : 'pointer' }}
          >
            {switching ? 'Switching...' : 'Switch to Learned Style'}
          </button>
        </div>
      )}

      {mode !== 'disabled' && (
        <div style={card}>
          <div style={sectionTitle}>Presets</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
            Pick a starting style. This is active immediately.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '12px' }}>
            {presets.map(p => {
              const active = mode === 'preset' && data.preset === p.id;
              return (
                <div
                  key={p.id}
                  onClick={() => !savingMode && choosePreset(p.id)}
                  style={{
                    border: active ? '2px solid var(--accent)' : '1px solid var(--border)',
                    borderRadius: '6px', padding: '14px', cursor: savingMode ? 'not-allowed' : 'pointer',
                    background: active ? '#ECFEFF' : 'var(--bg-secondary)',
                  }}
                >
                  <div style={{ fontWeight: '700', fontSize: '13px', marginBottom: '4px', color: 'var(--text-primary)' }}>
                    {p.label}{active && ' ✓'}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>{p.description}</div>
                  {p.example_replies?.[0] && (
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', borderLeft: '2px solid var(--border)', paddingLeft: '8px' }}>
                      "{p.example_replies[0]}"
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {mode === 'learned' && data.learned_profile && (
        <div style={card}>
          <div style={sectionTitle}>Detected Characteristics</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px', marginBottom: '14px' }}>
            {Object.entries(STYLE_FIELD_LABELS).map(([key, label]) => (
              <div key={key}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontSize: '13px', color: 'var(--text-primary)', fontWeight: '500' }}>
                  {typeof data.learned_profile[key] === 'boolean'
                    ? (data.learned_profile[key] ? 'Yes' : 'No')
                    : (data.learned_profile[key] || '—')}
                </div>
              </div>
            ))}
          </div>
          {data.reasoning && (
            <div style={{ background: 'var(--bg-secondary)', borderRadius: '6px', padding: '12px 14px', fontSize: '12px', color: 'var(--text-secondary)', whiteSpace: 'pre-line', marginBottom: '14px' }}>
              {data.reasoning}
            </div>
          )}
          <button
            onClick={regenerate}
            disabled={regenerating}
            style={{ padding: '8px 16px', borderRadius: '4px', background: 'var(--bg-tertiary)', color: 'var(--text-primary)', fontWeight: '600', fontSize: '13px', border: '1px solid var(--border)', cursor: regenerating ? 'not-allowed' : 'pointer' }}
          >
            {regenerating ? 'Regenerating...' : 'Regenerate from recent replies'}
          </button>
        </div>
      )}

      {mode !== 'disabled' && (
        <div style={card}>
          <div style={{ ...sectionTitle, display: 'flex', alignItems: 'center', gap: '6px' }}>
            Learning
            <span
              title="Every time your team approves (or edits and approves) a reply, Luna learns a little more about how your team actually writes. Nothing changes automatically — your current tone stays exactly as-is until enough approvals build up."
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: '15px', height: '15px', borderRadius: '50%', border: '1px solid var(--border)',
                color: 'var(--text-muted)', fontSize: '10px', fontWeight: '600', cursor: 'help', flexShrink: 0,
              }}
            >
              i
            </span>
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '10px' }}>
            {data.approved_reply_count} of {data.min_replies_required} approved replies needed to learn your team's real writing style.
          </div>
          <div style={{ height: '6px', borderRadius: '999px', background: 'var(--bg-tertiary)', overflow: 'hidden', marginBottom: '16px' }}>
            <div style={{ height: '100%', width: `${progressPct}%`, background: 'var(--accent)' }} />
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            <input
              type="checkbox"
              checked={!!data.learn_automatically}
              onChange={e => toggleControl('learn_automatically', e.target.checked)}
            />
            Learn automatically from approved replies
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <input
              type="checkbox"
              checked={!!data.use_uploaded_only}
              onChange={e => toggleControl('use_uploaded_only', e.target.checked)}
            />
            Use uploaded examples only (ignore ticket history)
          </label>
        </div>
      )}

      {mode !== 'disabled' && (
        <div style={card}>
          <div style={sectionTitle}>Uploaded Examples (optional)</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            Seed data for faster personalization. Not required — approved replies work on their own.
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '14px' }}>
            Show Luna how your team would reply. You don't need to use the exact wording a real customer would use.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '14px' }}>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Example customer message</div>
              <textarea
                value={newExampleQuestion}
                onChange={e => setNewExampleQuestion(e.target.value)}
                placeholder="Hey, can I change the size of my order?"
                rows={2}
                style={{ ...inputStyle, resize: 'vertical' }}
              />
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Example ideal reply</div>
              <textarea
                value={newExampleReply}
                onChange={e => setNewExampleReply(e.target.value)}
                placeholder="Of course! If your order hasn't shipped yet, we can help you change the size."
                rows={2}
                style={{ ...inputStyle, resize: 'vertical' }}
              />
            </div>
            <button
              onClick={addExample}
              disabled={addingExample || !newExampleQuestion.trim() || !newExampleReply.trim()}
              style={{ alignSelf: 'flex-start', padding: '8px 18px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '600', fontSize: '13px', cursor: addingExample ? 'not-allowed' : 'pointer' }}
            >
              Add
            </button>
          </div>
          {examples.length === 0 ? (
            <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No examples uploaded yet.</div>
          ) : examples.map(ex => {
            const parts = (ex.content || '').split('\n\nLuna\'s reply: ');
            const question = parts.length === 2 ? parts[0].replace(/^Customer: /, '') : null;
            const reply = parts.length === 2 ? parts[1] : null;
            return (
              <div key={ex.id} style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', padding: '10px 0', borderTop: '1px solid var(--border)' }}>
                {question ? (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    <div><span style={{ color: 'var(--text-muted)' }}>Customer:</span> {question}</div>
                    <div style={{ marginTop: '2px' }}><span style={{ color: 'var(--text-muted)' }}>Luna:</span> {reply}</div>
                  </div>
                ) : (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{ex.content}</div>
                )}
                <button onClick={() => deleteExample(ex.id)} style={{ fontSize: '11px', color: 'var(--danger)', background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0 }}>Delete</button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────── Account Tab ──

function AccountTab() {
  const [profile, setProfile] = useState({ full_name: '', email: '' });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    client.get('/api/v1/settings/account').then(res => {
      const u = res.data?.settings || res.data;
      setProfile({ full_name: u?.full_name || u?.company_name || u?.name || '', email: u?.email || '' });
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg('');
    try {
      await client.put('/api/v1/settings/account', { company_name: profile.full_name });
      setMsg('Saved successfully.');
    } catch {
      setMsg('Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '400px' }}>
        {[1,2].map(i => <div key={i} className="skeleton" style={{ height: '44px', borderRadius: '4px' }} />)}
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '400px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div>
        <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Name</label>
        <input
          value={profile.full_name}
          onChange={e => setProfile(p => ({ ...p, full_name: e.target.value }))}
          style={inputStyle}
        />
      </div>
      <div>
        <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '5px' }}>Email</label>
        <input value={profile.email} readOnly style={{ ...inputStyle, background: 'var(--bg-secondary)', color: 'var(--text-muted)' }} />
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>Email cannot be changed here</div>
      </div>
      <Alert variant={msg.includes('Failed') ? 'error' : 'success'}>{msg}</Alert>
      <button
        onClick={save}
        disabled={saving}
        style={{ padding: '9px 20px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '14px', cursor: saving ? 'not-allowed' : 'pointer', alignSelf: 'flex-start' }}
      >
        {saving ? 'Saving...' : 'Save changes'}
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────── Canned Responses Tab ──

function CannedResponsesTab() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ title: '', trigger_keywords: '', response_text: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [deleteError, setDeleteError] = useState('');

  const load = () => {
    setLoading(true);
    client.get('/api/v1/canned-responses')
      .then(res => setItems(res.data?.items || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.title || !form.trigger_keywords || !form.response_text) {
      setError('All fields are required.');
      return;
    }
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await client.post('/api/v1/canned-responses', form);
      setForm({ title: '', trigger_keywords: '', response_text: '' });
      setSuccess('Canned response saved.');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    setDeleteError('');
    try {
      await client.delete(`/api/v1/canned-responses/${id}`);
      load();
    } catch (err) {
      setDeleteError(err.response?.data?.detail || 'Failed to delete response.');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '24px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '16px' }}>Add Canned Response</h3>
        <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <input
            placeholder="Title (e.g. Return Policy)"
            value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            style={{ padding: '8px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)' }}
          />
          <input
            placeholder="Trigger keywords (comma-separated, e.g. return,refund,money back)"
            value={form.trigger_keywords}
            onChange={e => setForm(f => ({ ...f, trigger_keywords: e.target.value }))}
            style={{ padding: '8px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)' }}
          />
          <textarea
            placeholder="Response text (shown verbatim to customer)"
            value={form.response_text}
            onChange={e => setForm(f => ({ ...f, response_text: e.target.value }))}
            rows={4}
            style={{ padding: '8px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px', background: 'var(--bg-primary)', resize: 'vertical' }}
          />
          <Alert variant="error">{error}</Alert>
          <Alert variant="success">{success}</Alert>
          <button type="submit" disabled={saving} style={{ alignSelf: 'flex-start', padding: '8px 18px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '600', fontSize: '13px', cursor: saving ? 'not-allowed' : 'pointer' }}>
            {saving ? 'Saving...' : 'Save Response'}
          </button>
        </form>
      </div>

      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '24px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '16px' }}>Saved Responses</h3>
        <div style={{ marginBottom: deleteError ? '12px' : 0 }}>
          <Alert variant="error" onDismiss={() => setDeleteError('')}>{deleteError}</Alert>
        </div>
        {loading ? (
          [1,2].map(i => <div key={i} className="skeleton" style={{ height: '60px', borderRadius: '6px', marginBottom: '8px' }} />)
        ) : items.length === 0 ? (
          <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>No canned responses yet. Add one above.</div>
        ) : items.map(item => (
          <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
            <div>
              <div style={{ fontWeight: '600', fontSize: '13px', marginBottom: '3px' }}>{item.title}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Keywords: {item.trigger_keywords}</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.response_text}</div>
            </div>
            <button onClick={() => handleDelete(item.id)} style={{ fontSize: '12px', color: 'var(--danger)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px', flexShrink: 0 }}>Delete</button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────── Chat Widget Tab ──

function ChatWidgetTab() {
  const [brand, setBrand] = useState(null);
  const [copied, setCopied] = useState(false);
  const [accentColor, setAccentColor] = useState('#06B6D4');

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      setBrand(list[0] || null);
    }).catch(() => {});
  }, []);

  const backendUrl = import.meta.env.VITE_API_BASE_URL ||
    window.location.origin.replace(':5173', ':8001').replace(':3000', ':8001');
  const embedCode = brand
    ? `<script>
  window.tResolvConfig = {
    brandId:    "${brand.id}",
    botName:    "Luna",
    color:      "#FFFFFF",
    brandLabel: "AI Support"
  };
</script>
<script src="${backendUrl}/widget.js" async></script>`
    : '';

  const copy = () => {
    navigator.clipboard.writeText(embedCode).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const section = { marginBottom: '28px' };
  const label = { fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '8px' };
  const card = { background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '20px 24px' };

  return (
    <div>
      <div style={{ ...card, marginBottom: '20px' }}>
        <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '6px' }}>Chat Widget</div>
        <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
          Add a floating chat bubble to your Shopify store. Customers can ask questions, look up their orders,
          and request refunds or changes — all handled by Luna in real time.
        </div>
      </div>

      <div style={section}>
        <div style={label}>Embed code — paste before {'</body>'} in your Shopify theme</div>
        <div style={{ position: 'relative' }}>
          <pre style={{
            background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '6px',
            padding: '14px 16px', fontSize: '12px', color: 'var(--text-primary)',
            fontFamily: 'DM Mono, monospace', overflowX: 'auto', margin: 0,
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {embedCode || 'No store found — check your Shopify connection in Settings.'}
          </pre>
          {embedCode && (
            <button
              onClick={copy}
              style={{
                position: 'absolute', top: '10px', right: '10px',
                padding: '4px 10px', fontSize: '11px', borderRadius: '4px',
                background: copied ? 'var(--success)' : 'var(--accent)', color: '#fff',
                border: 'none', cursor: 'pointer', fontWeight: '600',
              }}
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}
        </div>
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
          In Shopify: Online Store → Themes → Edit code → theme.liquid → paste before {'</body>'}
        </div>
      </div>

      <div style={section}>
        <div style={label}>Live Preview</div>
        {brand ? (
          <div className="demo-phone-frame" style={{
            width: '320px',
            height: '520px',
            border: '12px solid #1a1a1a',
            borderRadius: '36px',
            position: 'relative',
            transform: 'translate(0, 0)',
            overflow: 'hidden',
            background: '#ffffff',
            boxShadow: '0 20px 40px rgba(0,0,0,0.15)',
            margin: '20px auto 0',
            display: 'flex',
            flexDirection: 'column'
          }}>
            {/* Phone notch/speaker header */}
            <div style={{
              height: '24px',
              background: '#1a1a1a',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              color: '#fff',
              fontSize: '10px',
              fontFamily: 'sans-serif',
              padding: '0 20px',
              flexShrink: 0
            }}>
              <div style={{ width: '60px', height: '12px', background: '#000', borderRadius: '0 0 8px 8px' }} />
            </div>

            {/* Simulated Store Header */}
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
              <span style={{ fontSize: '11px', fontWeight: 'bold', letterSpacing: '1px' }}>LUNA APPAREL</span>
              <div style={{ width: '16px', height: '12px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <span style={{ height: '2px', background: '#333', width: '100%' }} />
                <span style={{ height: '2px', background: '#333', width: '100%' }} />
                <span style={{ height: '2px', background: '#333', width: '100%' }} />
              </div>
            </div>

            {/* Simulated Store Hero */}
            <div style={{ flex: 1, padding: '32px 20px', textAlign: 'center', background: '#fcfcfd', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
              <h4 style={{ fontSize: '18px', fontWeight: '800', margin: '0 0 8px' }}>Summer Drop</h4>
              <p style={{ fontSize: '11px', color: '#666', margin: '0 0 16px', lineHeight: '1.4' }}>Shop the new lightweight organic linen essentials.</p>
              <button style={{ background: '#111', color: '#fff', border: 'none', padding: '8px 16px', borderRadius: '4px', fontSize: '11px', fontWeight: '600', cursor: 'pointer' }}>Shop Now</button>
            </div>

            {/* The ChatWidget */}
            <ChatWidget brandId={brand.id} accentColor={accentColor} />

            {/* Style overrides to keep the widget confined within the phone frame */}
            <style>{`
              .demo-phone-frame .tw-panel {
                position: absolute !important;
                bottom: 0 !important;
                right: 0 !important;
                width: 100% !important;
                height: calc(100% - 24px) !important;
                border-radius: 0 !important;
                border: none !important;
                z-index: 100 !important;
              }
              .demo-phone-frame .tw-launcher {
                position: absolute !important;
                bottom: 20px !important;
                right: 20px !important;
                z-index: 99 !important;
                box-shadow: 0 4px 16px rgba(0,0,0,0.15) !important;
              }
            `}</style>
          </div>
        ) : (
          <div style={{ ...card, padding: '32px', textAlign: 'center', color: 'var(--text-secondary)' }}>
            Connect your Shopify store to preview the widget
          </div>
        )}
      </div>

      <div style={section}>
        <div style={label}>Customization (window.tResolvConfig keys)</div>
        <div style={{ ...card, fontSize: '12px', fontFamily: 'DM Mono, monospace', color: 'var(--text-secondary)', lineHeight: '2' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ color: 'var(--accent)' }}>color</span>: &quot;{accentColor}&quot; — widget accent color
            <input
              type="color"
              value={accentColor}
              onChange={e => setAccentColor(e.target.value)}
              style={{
                width: '24px',
                height: '18px',
                padding: 0,
                border: '1px solid var(--border)',
                borderRadius: '3px',
                cursor: 'pointer',
                background: 'none',
                verticalAlign: 'middle'
              }}
            />
          </div>
          <div><span style={{ color: 'var(--accent)' }}>botName</span>: &quot;Luna&quot; — AI agent name shown in header</div>
          <div><span style={{ color: 'var(--accent)' }}>brandLabel</span>: &quot;AI Support&quot; — subtitle in header</div>
          <div><span style={{ color: 'var(--accent)' }}>apiBase</span>: &quot;https://…&quot; — override API URL (optional)</div>
        </div>
      </div>

      <div style={section}>
        <div style={label}>How it works</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {[
            ['1', 'Customer clicks the blue chat bubble on your store'],
            ['2', 'Luna greets them and offers quick replies'],
            ['3', 'Customer asks about an order → Luna looks it up in Shopify instantly'],
            ['4', 'Cancel / refund requests appear in your Escalations queue for approval'],
            ['5', 'All chat sessions appear in your Conversations page'],
          ].map(([n, t]) => (
            <div key={n} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', fontSize: '13px', color: 'var(--text-secondary)' }}>
              <div style={{ width: '22px', height: '22px', borderRadius: '50%', background: 'var(--accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px', fontWeight: '700', flexShrink: 0, marginTop: '1px' }}>{n}</div>
              <div style={{ paddingTop: '2px' }}>{t}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────── Integrations Tab ──

function IntegrationsTab() {
  const [aftershipKey, setAftershipKey] = useState('');
  const [status, setStatus]             = useState(null); // { connected, key_preview }
  const [loading, setLoading]           = useState(true);
  const [saving, setSaving]             = useState(false);
  const [msg, setMsg]                   = useState('');

  useEffect(() => {
    client.get('/api/v1/settings/aftership')
      .then(r => setStatus(r.data))
      .catch(() => setStatus({ connected: false }))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    if (!aftershipKey.trim()) return;
    setSaving(true);
    setMsg('');
    try {
      await client.post('/api/v1/settings/aftership', { aftership_api_key: aftershipKey.trim() });
      setMsg('Aftership key saved. Luna will now give live tracking updates.');
      setAftershipKey('');
      const r = await client.get('/api/v1/settings/aftership');
      setStatus(r.data);
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Failed to save key.');
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    if (!window.confirm('Remove Aftership key? Luna will fall back to sharing raw tracking links.')) return;
    setSaving(true);
    try {
      await client.delete('/api/v1/settings/aftership');
      setStatus({ connected: false });
      setMsg('Aftership key removed.');
    } catch {
      setMsg('Failed to remove key.');
    } finally {
      setSaving(false);
    }
  };

  const card  = { background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '6px', padding: '20px 24px', marginBottom: '20px' };
  const label = { fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '8px' };
  const input = { width: '100%', padding: '8px 12px', borderRadius: '4px', border: '1px solid var(--border-strong)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'DM Mono, monospace' };

  return (
    <div>
      {/* Aftership */}
      <div style={card}>
        <div style={{ fontSize: '14px', fontWeight: '700', color: 'var(--text-primary)', marginBottom: '4px' }}>
          Aftership — Live Tracking
        </div>
        <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.6', marginBottom: '16px' }}>
          Connect Aftership to let Luna give customers real-time tracking updates
          ("Your order is in Lahore, expected June 10.") instead of raw tracking links.
          Supports TCS, Leopards, Trax, BlueEx, PostEx, M&amp;P, Speedex, DHL and 1100+ carriers.
        </div>

        {loading ? (
          <div className="skeleton" style={{ height: '36px', borderRadius: '4px', width: '200px' }} />
        ) : status?.connected ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '13px', padding: '5px 12px', background: 'var(--success-light)', color: 'var(--success)', borderRadius: '4px', fontWeight: '600' }}>
              ✓ Connected {status.key_preview ? `(key ending ${status.key_preview})` : ''}
            </span>
            <button
              onClick={handleRemove}
              disabled={saving}
              style={{ padding: '5px 12px', fontSize: '12px', borderRadius: '4px', border: '1px solid var(--danger)', color: 'var(--danger)', background: 'transparent', cursor: 'pointer' }}
            >
              Remove key
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxWidth: '440px' }}>
            <div style={label}>Aftership API Key</div>
            <input
              type="password"
              value={aftershipKey}
              onChange={e => setAftershipKey(e.target.value)}
              placeholder="at_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              style={input}
              onKeyDown={e => e.key === 'Enter' && handleSave()}
            />
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Get your key at{' '}
              <a href="https://admin.aftership.com/apps/api" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                admin.aftership.com → Apps → API
              </a>
              {' '}· Free plan: 100 trackings/month
            </div>
            <button
              onClick={handleSave}
              disabled={saving || !aftershipKey.trim()}
              style={{ alignSelf: 'flex-start', padding: '7px 18px', borderRadius: '4px', background: 'var(--accent)', color: '#fff', fontSize: '13px', fontWeight: '600', border: 'none', cursor: 'pointer', opacity: saving || !aftershipKey.trim() ? 0.5 : 1 }}
            >
              {saving ? 'Saving…' : 'Save key'}
            </button>
          </div>
        )}

        <div style={{ marginTop: msg ? '12px' : 0 }}>
          <Alert variant={msg.includes('ailed') ? 'error' : 'success'}>{msg}</Alert>
        </div>
      </div>

      {/* Carrier reference */}
      <div style={{ ...card, background: 'var(--bg-secondary)' }}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px' }}>Pakistan carrier support</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '6px' }}>
          {['TCS', 'Leopards', 'Trax', 'BlueEx', 'PostEx', 'M&P / MNP', 'Speedex', 'Swyft', 'Call Courier', 'DHL', 'FedEx'].map(c => (
            <div key={c} style={{ fontSize: '12px', color: 'var(--text-secondary)', padding: '4px 8px', background: 'var(--bg-primary)', borderRadius: '4px', border: '1px solid var(--border)' }}>
              {c}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────── Main Settings Page ──

const TABS = [
  { id: 'email', label: 'Email' },
  { id: 'shopify', label: 'Shopify' },
  { id: 'kb', label: 'Knowledge Base' },
  { id: 'reply-style', label: 'Reply Style' },
  { id: 'widget', label: 'Chat Widget' },
  { id: 'account', label: 'Account' },
];

// Moved under the collapsible "Advanced" section — same tabs, same
// components, just regrouped in the nav so the top-level bar isn't 8 wide.
const ADVANCED_TABS = [
  { id: 'filter', label: 'Email Filters' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'canned', label: 'Canned Responses' },
];

export default function Settings() {
  const location = useLocation();
  const [activeTab, setActiveTab] = useState(location.state?.tab || 'email');
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    document.title = "Settings — tResolv";
  }, []);

  // If a deep link or prior session lands on an Advanced tab, keep the
  // section expanded so the active tab isn't hidden.
  useEffect(() => {
    if (ADVANCED_TABS.some(t => t.id === activeTab)) setAdvancedOpen(true);
  }, [activeTab]);

  const tabStyle = (id) => ({
    padding: '10px 20px',
    borderBottom: activeTab === id ? '2px solid #06B6D4' : '2px solid transparent',
    color: activeTab === id ? '#06B6D4' : '#64748B',
    fontWeight: '600',
    fontSize: '14px',
    cursor: 'pointer',
    transition: 'all 0.2s',
    whiteSpace: 'nowrap',
  });

  const isAdvancedActive = ADVANCED_TABS.some(t => t.id === activeTab);

  return (
    <div style={{ padding: '24px', maxWidth: '800px', margin: '0', width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid #E4E4E7', overflowX: 'auto' }}>
        {TABS.map(t => (
          <div key={t.id} style={tabStyle(t.id)} onClick={() => setActiveTab(t.id)}>{t.label}</div>
        ))}
        <div
          style={{ ...tabStyle('__advanced__'), color: isAdvancedActive ? '#06B6D4' : '#94A3B8', display: 'flex', alignItems: 'center', gap: '4px' }}
          onClick={() => setAdvancedOpen(o => !o)}
        >
          Advanced
          <span style={{ fontSize: '10px', transform: advancedOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>▾</span>
        </div>
      </div>

      {advancedOpen && (
        <div style={{ display: 'flex', gap: '4px', padding: '10px 0', marginBottom: '4px', flexWrap: 'wrap' }}>
          {ADVANCED_TABS.map(t => (
            <div
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              style={{
                padding: '6px 14px',
                borderRadius: '6px',
                fontSize: '13px',
                fontWeight: '600',
                cursor: 'pointer',
                background: activeTab === t.id ? '#ECFEFF' : '#F8FAFC',
                color: activeTab === t.id ? '#0E7490' : '#64748B',
                border: '1px solid ' + (activeTab === t.id ? '#A5F3FC' : '#E4E4E7'),
              }}
            >
              {t.label}
            </div>
          ))}
        </div>
      )}

      <div style={{ maxWidth: '800px', marginTop: '20px' }}>
        {activeTab === 'email' && <EmailTab />}
        {activeTab === 'filter' && <FilterTab />}
        {activeTab === 'shopify' && <ShopifyTab />}
        {activeTab === 'integrations' && <IntegrationsTab />}
        {activeTab === 'kb' && <KnowledgeBaseTab />}
        {activeTab === 'canned' && <CannedResponsesTab />}
        {activeTab === 'reply-style' && <ReplyStyleTab />}
        {activeTab === 'widget' && <ChatWidgetTab />}
        {activeTab === 'account' && <AccountTab />}
      </div>
    </div>
  );
}
