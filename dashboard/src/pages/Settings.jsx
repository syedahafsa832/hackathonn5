import { useEffect, useState, useCallback, useRef } from 'react';
import client from '../api/client';
import ChatWidget from '../components/ChatWidget';

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

// ──────────────────────────────────────────────────────── Email Tab ──

function EmailTab() {
  const [gmailStatus, setGmailStatus] = useState(null);
  const [queueStatus, setQueueStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);
  const [mode, setMode] = useState('supervised');
  const [threshold, setThreshold] = useState(80);
  const [savingMode, setSavingMode] = useState(false);
  const [msg, setMsg] = useState('');
  const thresholdTimer = useRef(null);

  const loadStatus = useCallback(() => {
    setLoadingStatus(true);
    Promise.all([
      client.get('/api/v1/settings/gmail/status').catch(() => ({ data: { connected: false } })),
      client.get('/api/v1/settings/gmail/queue-status').catch(() => ({ data: {} })),
      client.get('/api/ai-mode').catch(() => ({ data: { mode: 'supervised' } })),
      client.get('/api/v1/settings/account').catch(() => ({ data: {} })),
    ]).then(([gmailRes, queueRes, modeRes, accountRes]) => {
      setGmailStatus(gmailRes.data);
      setQueueStatus(queueRes.data);
      setMode(modeRes.data?.mode || 'supervised');
      setThreshold(accountRes.data?.settings?.confidence_threshold ?? accountRes.data?.confidence_threshold ?? 80);
    }).finally(() => setLoadingStatus(false));
  }, []);

  useEffect(() => {
    loadStatus();
    // If we're returning from Gmail OAuth, refresh status and clean up URL
    const params = new URLSearchParams(window.location.search);
    if (params.get('gmail_connected')) {
      setMsg('Gmail connected successfully!');
      window.history.replaceState({}, '', window.location.pathname);
    } else if (params.get('gmail_error')) {
      setMsg(`Gmail connection failed: ${params.get('gmail_error')}`);
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [loadStatus]);

  const handleConnect = async () => {
    try {
      // Get the Google OAuth URL via authenticated API call (Bearer token sent by axios)
      const res = await client.get('/api/v1/settings/gmail/connect');
      const authUrl = res.data?.auth_url;
      if (!authUrl) {
        setMsg('Could not get Gmail auth URL. Check GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET env vars.');
        return;
      }
      // Navigate the browser directly to Google's consent screen
      window.location.href = authUrl;
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Failed to start Gmail connection. Make sure a brand exists first.');
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
    setSavingMode(true);
    setMsg('');
    try {
      await client.patch('/api/ai-mode', { mode: newMode });
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

  return (
    <div style={{ maxWidth: '520px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {msg && (
        <div style={{ fontSize: '13px', color: msg.includes('Failed') ? 'var(--danger)' : 'var(--success)', padding: '8px 12px', background: msg.includes('Failed') ? 'var(--danger-light)' : 'var(--success-light)', borderRadius: '4px' }}>
          {msg}
        </div>
      )}

      {/* Gmail Connection */}
      <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '24px' }}>
        <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B', marginBottom: '16px' }}>Gmail Connection</div>
        {gmailStatus?.connected ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#10B981', flexShrink: 0 }} />
              <div>
                <div style={{ fontSize: '14px', fontWeight: '600', color: '#10B981' }}>Connected</div>
                <div style={{ fontSize: '13px', color: '#475569' }}>{gmailStatus.email}</div>
              </div>
            </div>
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
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
              <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#94A3B8', flexShrink: 0 }} />
              <div style={{ fontSize: '14px', fontWeight: '600', color: '#64748B' }}>Not Connected</div>
            </div>
            <div style={{ fontSize: '14px', color: '#475569', lineHeight: '1.5' }}>
              Resolv will monitor this inbox every 60 seconds for new customer emails and send replies from it — directly from your address.
            </div>
            <button
              onClick={handleConnect}
              style={{ padding: '9px 18px', borderRadius: '6px', background: '#06B6D4', color: 'white', fontWeight: '600', fontSize: '14px', border: 'none', cursor: 'pointer', transition: 'background 0.15s' }}
              onMouseEnter={e => e.target.style.background = '#0891B2'}
              onMouseLeave={e => e.target.style.background = '#06B6D4'}
            >
              Connect Gmail →
            </button>
            <div style={{ fontSize: '12px', color: '#94A3B8' }}>
              Google permissions required: read emails, send emails, mark as read
            </div>
          </div>
        )}
      </div>

      {/* Send Queue Status */}
      {queueStatus && (
        <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '20px' }}>
          <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Send Queue Status</div>
          <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
            {[
              { label: 'Queued', value: queueStatus.queued_count ?? 0 },
              { label: 'Sent (24h)', value: queueStatus.sent_24h ?? 0 },
              { label: 'Failed', value: queueStatus.failed_count ?? 0, danger: true },
            ].map(({ label, value, danger }) => (
              <div key={label}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontSize: '20px', fontWeight: '700', color: danger && value > 0 ? 'var(--danger)' : 'var(--text-primary)', fontFamily: 'DM Mono, monospace' }}>{value}</div>
              </div>
            ))}
          </div>
          {(queueStatus.failed_count ?? 0) > 0 && (
            <div style={{ marginTop: '12px', padding: '10px 12px', background: 'var(--warning-light, #fef3c7)', borderRadius: '6px', fontSize: '13px', color: 'var(--warning, #92400e)' }}>
              ⚠ {queueStatus.failed_count} email(s) failed to send. Check your Gmail connection.
            </div>
          )}
        </div>
      )}

      {/* AI Mode */}
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '20px' }}>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>AI Mode</div>
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

function ShopifyTab() {
  const [shopifyStatus, setShopifyStatus] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [form, setForm] = useState({ shopify_domain: '', access_token: '' });
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

  const isConnected = shopifyStatus?.connected;

  const handleConnect = async () => {
    if (!form.shopify_domain.trim() || !form.access_token.trim()) {
      setError('Both store URL and access token are required.');
      return;
    }
    setConnecting(true);
    setError('');
    setMsg('');
    try {
      const res = await client.post('/api/v1/settings/shopify/connect', {
        shop_domain: form.shopify_domain.trim(),
        access_token: form.access_token.trim(),
      });
      setMsg(`Connected: ${res.data.shop_name || 'Shopify store'}`);
      setForm({ shopify_domain: '', access_token: '' });
      loadStatus();
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail?.error || 'Failed to connect. Check your domain and token.');
    } finally {
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
      {msg && (
        <div style={{ fontSize: '13px', color: 'var(--success)', padding: '8px 12px', background: 'var(--success-light)', borderRadius: '4px' }}>{msg}</div>
      )}
      {error && (
        <div style={{ fontSize: '13px', color: 'var(--danger)', padding: '8px 12px', background: 'var(--danger-light)', borderRadius: '4px' }}>{error}</div>
      )}

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

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px', background: '#F8FAFC', borderRadius: '6px', fontSize: '13px', color: '#475569', lineHeight: '1.7' }}>
            <div><strong>Step 1:</strong> In Shopify, go to Settings → Apps → Develop apps</div>
            <div><strong>Step 2:</strong> Create an app and configure Admin API scopes: <code style={{ fontSize: '12px', background: '#E2E8F0', padding: '1px 4px', borderRadius: '3px' }}>read_orders, write_orders, read_customers, read_products, write_order_edits</code></div>
            <div><strong>Step 3:</strong> Install the app and copy your Admin API access token</div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#475569', marginBottom: '5px' }}>Store URL</label>
            <input
              value={form.shopify_domain}
              onChange={e => setForm(f => ({ ...f, shopify_domain: e.target.value }))}
              placeholder="mystore.myshopify.com"
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#475569', marginBottom: '5px' }}>Admin API Access Token</label>
            <input
              type="password"
              value={form.access_token}
              onChange={e => setForm(f => ({ ...f, access_token: e.target.value }))}
              placeholder="shpat_..."
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = '#06B6D4'}
              onBlur={e => e.target.style.borderColor = '#E4E4E7'}
            />
          </div>
          <button
            onClick={handleConnect}
            disabled={connecting}
            style={{ padding: '9px 18px', borderRadius: '6px', border: 'none', background: connecting ? '#E4E4E7' : '#06B6D4', color: connecting ? '#94A3B8' : 'white', fontWeight: '600', fontSize: '14px', cursor: connecting ? 'not-allowed' : 'pointer', alignSelf: 'flex-start', transition: 'background 0.15s' }}
            onMouseEnter={e => { if(!connecting) e.target.style.background = '#0891B2'; }}
            onMouseLeave={e => { if(!connecting) e.target.style.background = '#06B6D4'; }}
          >
            {connecting ? 'Connecting...' : 'Connect Shopify →'}
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

  const fetchSources = () => {
    setLoading(true);
    setError('');
    client.get('/api/v1/settings/knowledge-base/sources')
      .then(res => setSources(Array.isArray(res.data) ? res.data : (res.data?.sources || [])))
      .catch(() => setError('Failed to load knowledge base sources.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchSources(); }, []);

  const handleUpload = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      setUploadMsg('Title and content are required.');
      return;
    }
    setUploading(true);
    setUploadMsg('');
    try {
      await client.post('/api/v1/settings/knowledge-base/upload', {
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
    try {
      await client.delete(`/api/v1/settings/knowledge-base/sources/${id}`);
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
        {uploadMsg && (
          <div style={{ fontSize: '13px', color: uploadMsg.includes('fail') || uploadMsg.includes('required') ? 'var(--danger)' : 'var(--success)', padding: '8px 12px', background: uploadMsg.includes('fail') || uploadMsg.includes('required') ? 'var(--danger-light)' : 'var(--success-light)', borderRadius: '4px' }}>
            {uploadMsg}
          </div>
        )}
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
        {error && (
          <div style={{ padding: '10px 14px', background: 'var(--danger-light)', color: 'var(--danger)', borderRadius: '4px', fontSize: '13px', marginBottom: '12px' }}>{error}</div>
        )}
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
      {msg && (
        <div style={{ fontSize: '13px', color: msg.includes('Failed') || msg.includes('must') ? 'var(--danger)' : 'var(--success)', padding: '8px 12px', background: msg.includes('Failed') || msg.includes('must') ? 'var(--danger-light)' : 'var(--success-light)', borderRadius: '4px' }}>
          {msg}
        </div>
      )}

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
      {msg && (
        <div style={{ fontSize: '13px', color: msg.includes('Failed') ? 'var(--danger)' : 'var(--success)', padding: '8px 12px', background: msg.includes('Failed') ? 'var(--danger-light)' : 'var(--success-light)', borderRadius: '4px' }}>
          {msg}
        </div>
      )}
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
    try {
      await client.post('/api/v1/canned-responses', form);
      setForm({ title: '', trigger_keywords: '', response_text: '' });
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await client.delete(`/api/v1/canned-responses/${id}`);
      load();
    } catch {}
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
          {error && <div style={{ fontSize: '13px', color: 'var(--danger)' }}>{error}</div>}
          <button type="submit" disabled={saving} style={{ alignSelf: 'flex-start', padding: '8px 18px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '600', fontSize: '13px', cursor: saving ? 'not-allowed' : 'pointer' }}>
            {saving ? 'Saving...' : 'Save Response'}
          </button>
        </form>
      </div>

      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '24px' }}>
        <h3 style={{ fontSize: '14px', fontWeight: '600', marginBottom: '16px' }}>Saved Responses</h3>
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
  const [brands, setBrands] = useState([]);
  const [selectedBrand, setSelectedBrand] = useState(null);
  const [copied, setCopied] = useState(false);
  const [accentColor, setAccentColor] = useState('#06B6D4');

  useEffect(() => {
    client.get('/api/brands').then(res => {
      const list = Array.isArray(res.data) ? res.data : res.data?.brands || [];
      setBrands(list);
      if (list.length > 0) setSelectedBrand(list[0]);
    }).catch(() => {});
  }, []);

  const backendUrl = import.meta.env.VITE_API_BASE_URL ||
    window.location.origin.replace(':5173', ':8001').replace(':3000', ':8001');
  const embedCode = selectedBrand
    ? `<script>
  window.tResolvConfig = {
    brandId:    "${selectedBrand.id}",
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

      {brands.length > 1 && (
        <div style={section}>
          <div style={label}>Select brand</div>
          <select
            value={selectedBrand?.id || ''}
            onChange={e => setSelectedBrand(brands.find(b => b.id === e.target.value))}
            style={{ padding: '8px 12px', borderRadius: '4px', border: '1px solid var(--border)', background: 'var(--bg-primary)', color: 'var(--text-primary)', fontSize: '13px' }}
          >
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </div>
      )}

      <div style={section}>
        <div style={label}>Embed code — paste before {'</body>'} in your Shopify theme</div>
        <div style={{ position: 'relative' }}>
          <pre style={{
            background: 'var(--bg-secondary)', border: '1px solid var(--border)', borderRadius: '6px',
            padding: '14px 16px', fontSize: '12px', color: 'var(--text-primary)',
            fontFamily: 'DM Mono, monospace', overflowX: 'auto', margin: 0,
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {embedCode || 'No brand found — add a brand first.'}
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
        {selectedBrand ? (
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
            <ChatWidget brandId={selectedBrand.id} accentColor={accentColor} />

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
            Select a brand to preview the widget
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

        {msg && (
          <div style={{ marginTop: '12px', fontSize: '13px', color: msg.includes('ailed') ? 'var(--danger)' : 'var(--success)' }}>
            {msg}
          </div>
        )}
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
  { id: 'filter', label: 'Email Filters' },
  { id: 'shopify', label: 'Shopify' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'kb', label: 'Knowledge Base' },
  { id: 'canned', label: 'Canned Responses' },
  { id: 'widget', label: 'Chat Widget' },
  { id: 'account', label: 'Account' },
];

export default function Settings() {
  const [activeTab, setActiveTab] = useState('email');

  useEffect(() => {
    document.title = "Settings — tResolv";
  }, []);

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

  return (
    <div style={{ padding: '24px', maxWidth: '800px', margin: '0', width: '100%' }}>
      <div style={{ display: 'flex', borderBottom: '1px solid #E4E4E7', marginBottom: '28px', overflowX: 'auto' }}>
        {TABS.map(t => (
          <div key={t.id} style={tabStyle(t.id)} onClick={() => setActiveTab(t.id)}>{t.label}</div>
        ))}
      </div>

      <div style={{ maxWidth: '800px' }}>
        {activeTab === 'email' && <EmailTab />}
        {activeTab === 'filter' && <FilterTab />}
        {activeTab === 'shopify' && <ShopifyTab />}
        {activeTab === 'integrations' && <IntegrationsTab />}
        {activeTab === 'kb' && <KnowledgeBaseTab />}
        {activeTab === 'canned' && <CannedResponsesTab />}
        {activeTab === 'widget' && <ChatWidgetTab />}
        {activeTab === 'account' && <AccountTab />}
      </div>
    </div>
  );
}
