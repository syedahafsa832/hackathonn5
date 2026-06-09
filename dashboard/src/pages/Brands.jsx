import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import client from '../api/client';

function Dot({ connected }) {
  return (
    <span style={{
      display: 'inline-block',
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      background: connected ? 'var(--success)' : 'var(--text-muted)',
      marginRight: '6px',
    }} />
  );
}

function Drawer({ open, onClose, onCreated }) {
  const [name, setName] = useState('');
  const [shopName, setShopName] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [supportEmail, setSupportEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  if (!open) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await client.post('/api/brands', {
        name,
        shopify_shop_name: shopName.replace('.myshopify.com', ''),
        shopify_access_token: accessToken,
        support_email: supportEmail,
      });
      onCreated(res.data?.brand || res.data);
      setName(''); setShopName(''); setAccessToken(''); setSupportEmail('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create brand.');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    width: '100%',
    padding: '9px 12px',
    border: '1px solid var(--border-strong)',
    borderRadius: '4px',
    fontSize: '14px',
    background: 'var(--bg-primary)',
    color: 'var(--text-primary)',
  };

  const labelStyle = {
    display: 'block',
    fontSize: '13px',
    fontWeight: '500',
    color: 'var(--text-secondary)',
    marginBottom: '5px',
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', zIndex: 200 }}
      />
      <div style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: '380px',
        background: 'var(--bg-primary)',
        borderLeft: '1px solid var(--border)',
        zIndex: 201,
        display: 'flex',
        flexDirection: 'column',
        boxShadow: '-4px 0 24px rgba(0,0,0,0.08)',
      }}>
        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '15px', fontWeight: '600' }}>Add Brand</h2>
          <button onClick={onClose} style={{ background: 'none', fontSize: '20px', color: 'var(--text-muted)', cursor: 'pointer' }}>×</button>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px', flex: 1 }}>
          <div>
            <label style={labelStyle}>Brand name</label>
            <input value={name} onChange={e => setName(e.target.value)} required placeholder="My Shopify Brand" style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Shopify store name</label>
            <input value={shopName} onChange={e => setShopName(e.target.value)} required placeholder="your-store (without .myshopify.com)" style={inputStyle} />
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
              e.g. if your store is <strong>mybrand.myshopify.com</strong>, enter <strong>mybrand</strong>
            </div>
          </div>
          <div>
            <label style={labelStyle}>Support email</label>
            <input value={supportEmail} onChange={e => setSupportEmail(e.target.value)} required type="email" placeholder="support@mybrand.com" style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Admin API access token</label>
            <input value={accessToken} onChange={e => setAccessToken(e.target.value)} required type="password" placeholder="shpat_xxxxxxxxxxxx" style={inputStyle} />
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
              Shopify Admin → Settings → Apps → Develop apps → your app → Install → copy token
            </div>
          </div>

          {error && (
            <div style={{ padding: '10px 12px', background: 'var(--danger-light)', borderRadius: '4px', color: 'var(--danger)', fontSize: '13px' }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px', marginTop: 'auto' }}>
            <button type="submit" disabled={loading} style={{ flex: 1, padding: '10px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '600', fontSize: '14px', cursor: loading ? 'not-allowed' : 'pointer' }}>
              {loading ? 'Creating...' : 'Create brand'}
            </button>
            <button type="button" onClick={onClose} style={{ padding: '10px 16px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', fontSize: '14px', cursor: 'pointer', color: 'var(--text-secondary)' }}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

function BrandCard({ brand, highlightGmail }) {
  const [expanded, setExpanded] = useState(highlightGmail || false);
  const [connecting, setConnecting] = useState(false);
  const [connectMsg, setConnectMsg] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [gmailStatus, setGmailStatus] = useState({
    connected: brand.gmail_connected || false,
    email: brand.gmail_email || null,
  });
  const [gmailLoading, setGmailLoading] = useState(false);

  const testShopify = async () => {
    setConnecting(true);
    setConnectMsg('');
    setTestResult(null);
    try {
      const res = await client.post(`/api/brands/${brand.id}/test-connection`);
      const data = res.data;
      setTestResult(data);
      setConnectMsg(data?.success ? 'Connected successfully!' : (data?.error || 'Connection failed.'));
    } catch (err) {
      setConnectMsg(err.response?.data?.detail || 'Connection failed.');
    } finally {
      setConnecting(false);
    }
  };

  const connectGmail = async () => {
    setGmailLoading(true);
    try {
      const res = await client.get(`/api/brands/${brand.id}/gmail/auth-url`);
      window.location.href = res.data.auth_url;
    } catch (err) {
      setGmailLoading(false);
    }
  };

  const disconnectGmail = async () => {
    setGmailLoading(true);
    try {
      await client.post(`/api/brands/${brand.id}/gmail/disconnect`);
      setGmailStatus({ connected: false, email: null });
    } catch {}
    setGmailLoading(false);
  };

  const connected = brand.is_active;

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)', overflow: 'hidden' }}>
      <div
        onClick={() => setExpanded(e => !e)}
        style={{ padding: '20px 24px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-secondary)'}
        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: '4px' }}>
            <Dot connected={connected} />
            <span style={{ fontWeight: '600', fontSize: '15px' }}>{brand.name}</span>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'DM Mono, monospace' }}>
            {brand.shopify_shop_name ? `${brand.shopify_shop_name}.myshopify.com` : 'No domain'}
          </div>
        </div>
        <div style={{ textAlign: 'right', fontSize: '12px', color: 'var(--text-secondary)' }}>
          <div style={{ marginBottom: '2px', fontFamily: 'DM Mono, monospace', fontSize: '14px', fontWeight: '500' }}>
            {brand.ticket_count || brand.tickets_count || '—'} tickets
          </div>
          <div>{connected ? '● Connected' : '○ Not connected'}</div>
        </div>
      </div>

      {expanded && (
        <div style={{ padding: '0 24px 20px', borderTop: '1px solid var(--border)', paddingTop: '16px' }}>
          <div style={{ display: 'flex', gap: '24px', marginBottom: '16px', flexWrap: 'wrap' }}>
            {[
              ['Store', brand.shopify_shop_name || '—'],
              ['Support email', brand.support_email || '—'],
              ['Return window', brand.return_policy_days != null ? `${brand.return_policy_days} days` : '—'],
              ['Auto-approve ≤', brand.auto_approve_threshold != null ? `$${brand.auto_approve_threshold}` : '—'],
            ].map(([label, value]) => (
              <div key={label}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontFamily: 'DM Mono, monospace', fontWeight: '500', fontSize: '13px' }}>{value}</div>
              </div>
            ))}
          </div>

          {testResult?.shop && (
            <div style={{ padding: '8px 12px', background: 'var(--success-light)', borderRadius: '4px', fontSize: '12px', color: 'var(--success)', marginBottom: '12px' }}>
              ✓ {testResult.shop.name} — {testResult.shop.plan_name} plan · {testResult.shop.currency}
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button
              onClick={testShopify}
              disabled={connecting}
              style={{ padding: '7px 14px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px', cursor: connecting ? 'not-allowed' : 'pointer' }}
            >
              {connecting ? 'Testing...' : 'Test Shopify Connection'}
            </button>
          </div>
          {connectMsg && (
            <div style={{ marginTop: '8px', fontSize: '12px', color: connectMsg.includes('success') || connectMsg.includes('Connected') ? 'var(--success)' : 'var(--danger)' }}>
              {connectMsg}
            </div>
          )}

          <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Gmail Inbox</div>
            {gmailStatus.connected ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 10px', background: 'var(--success-light)', borderRadius: '4px', fontSize: '13px', color: 'var(--success)' }}>
                  <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'var(--success)', display: 'inline-block' }} />
                  {gmailStatus.email}
                </div>
                <button
                  onClick={disconnectGmail}
                  disabled={gmailLoading}
                  style={{ padding: '6px 12px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', fontSize: '13px', color: 'var(--text-secondary)', cursor: gmailLoading ? 'not-allowed' : 'pointer' }}
                >
                  {gmailLoading ? 'Disconnecting...' : 'Disconnect'}
                </button>
              </div>
            ) : (
              <button
                onClick={connectGmail}
                disabled={gmailLoading}
                style={{ padding: '7px 14px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', fontSize: '13px', fontWeight: '500', color: 'var(--text-primary)', cursor: gmailLoading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                {gmailLoading ? 'Redirecting...' : 'Connect Gmail'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Brands() {
  const [brands, setBrands] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const gmailConnected = searchParams.get('gmail_connected');
  const gmailEmail = searchParams.get('email');
  const gmailError = searchParams.get('gmail_error');
  const highlightBrandId = searchParams.get('brand_id');

  useEffect(() => {
    setLoading(true);
    client.get('/api/brands').then(res => {
      const d = res.data;
      setBrands(Array.isArray(d) ? d : d?.brands || d?.data || []);
    }).catch(() => {
      setError('Failed to load brands.');
    }).finally(() => setLoading(false));
  }, []);

  const handleCreated = (brand) => {
    if (brand) setBrands(prev => [...prev, brand]);
    setDrawerOpen(false);
  };

  return (
    <div style={{ padding: '24px' }}>
      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} onCreated={handleCreated} />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
          {loading ? 'Loading...' : `${brands.length} brand${brands.length !== 1 ? 's' : ''}`}
        </div>
        <button
          onClick={() => setDrawerOpen(true)}
          style={{ padding: '8px 16px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px' }}
        >
          + Add brand
        </button>
      </div>

      {gmailConnected === '1' && (
        <div style={{ padding: '12px 16px', background: 'var(--success-light)', border: '1px solid var(--success)', borderRadius: '6px', color: 'var(--success)', fontSize: '13px', marginBottom: '16px' }}>
          ✓ Gmail connected{gmailEmail ? `: ${gmailEmail}` : ''}
        </div>
      )}
      {gmailError && (
        <div style={{ padding: '12px 16px', background: 'var(--danger-light)', border: '1px solid #FCA5A5', borderRadius: '6px', color: 'var(--danger)', fontSize: '13px', marginBottom: '16px' }}>
          Gmail connection failed: {gmailError}
        </div>
      )}
      {error && (
        <div style={{ padding: '14px 16px', background: 'var(--danger-light)', border: '1px solid #FCA5A5', borderRadius: '6px', color: 'var(--danger)', fontSize: '13px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'grid', gap: '12px' }}>
          {[1,2,3].map(i => <div key={i} className="skeleton" style={{ height: '80px', borderRadius: '6px' }} />)}
        </div>
      ) : brands.length === 0 ? (
        <div style={{ padding: '64px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)' }}>
          <div style={{ fontSize: '32px', marginBottom: '12px' }}>🏪</div>
          <div style={{ fontWeight: '600', marginBottom: '6px' }}>No brands yet</div>
          <div style={{ color: 'var(--text-muted)', fontSize: '13px', marginBottom: '16px' }}>Add your first Shopify brand to get started</div>
          <button onClick={() => setDrawerOpen(true)} style={{ padding: '9px 18px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px' }}>
            Add brand
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '12px' }}>
          {brands.map(brand => <BrandCard key={brand.id} brand={brand} highlightGmail={brand.id === highlightBrandId || (gmailConnected === '1' && brands.length === 1)} />)}
        </div>
      )}
    </div>
  );
}
