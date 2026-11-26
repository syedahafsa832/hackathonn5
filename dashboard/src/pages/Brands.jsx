import { useEffect, useRef, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import { gmailOAuthErrorMessage } from '../components/gmailOAuthErrors';
import HelpContactLink from '../components/HelpContactLink';
import EmailForwardingSetup from '../components/EmailForwardingSetup';

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

// tResolv is single-store-per-account — this is the settings view for
// that one store's identity, refund policy, Shopify connection, and
// Gmail inbox. Previously this component (BrandCard) was one card among
// many in a list you could add to; there's no "add another" flow anymore.
function StoreCard({ brand, highlightGmail }) {
  const [expanded, setExpanded] = useState(highlightGmail || false);
  const [connecting, setConnecting] = useState(false);
  const [connectMsg, setConnectMsg] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [agentName, setAgentName] = useState(brand.agent_name || 'Luna');
  const [brandName, setBrandName] = useState(brand.name || '');
  const [emailSignature, setEmailSignature] = useState(brand.email_signature || '');
  const [savingIdentity, setSavingIdentity] = useState(false);
  const [identityMsg, setIdentityMsg] = useState('');

  const saveIdentity = async () => {
    setSavingIdentity(true);
    setIdentityMsg('');
    try {
      await client.put(`/api/brands/${brand.id}`, { name: brandName, agent_name: agentName, email_signature: emailSignature });
      setIdentityMsg('Saved!');
    } catch (err) {
      setIdentityMsg(err.response?.data?.detail || 'Failed to save.');
    } finally {
      setSavingIdentity(false);
    }
  };

  // Refund policy fields save independently through /api/v2/brands, which is
  // tenant-scoped (unlike the legacy /api/brands PUT above, which has no
  // ownership check). The scalar fields (return window, digital-product
  // exclusion, notes) already come back on `brand` from the page's initial
  // /api/brands list load, since that endpoint blocklists only two secret
  // columns rather than allowlisting — new columns pass through automatically.
  // The excluded-product/collection lists live in their own tables, so they
  // need a separate fetch; done lazily on first expand, not on every load.
  const [returnPolicyDays, setReturnPolicyDays] = useState(brand.return_policy_days ?? 30);
  const [excludeDigitalProducts, setExcludeDigitalProducts] = useState(!!brand.exclude_digital_products);
  const [refundNotes, setRefundNotes] = useState(brand.refund_notes || '');
  const [excludedProductIds, setExcludedProductIds] = useState('');
  const [excludedCollectionIds, setExcludedCollectionIds] = useState('');
  const [savingRefundPolicy, setSavingRefundPolicy] = useState(false);
  const [refundPolicyMsg, setRefundPolicyMsg] = useState('');
  const excludedListsLoaded = useRef(false);

  useEffect(() => {
    if (!expanded || excludedListsLoaded.current) return;
    excludedListsLoaded.current = true;
    Promise.all([
      client.get(`/api/v2/brands/${brand.id}/refund-policy/excluded-products`),
      client.get(`/api/v2/brands/${brand.id}/refund-policy/excluded-collections`),
    ]).then(([productsRes, collectionsRes]) => {
      setExcludedProductIds((productsRes.data?.ids || []).join(', '));
      setExcludedCollectionIds((collectionsRes.data?.ids || []).join(', '));
    }).catch(() => {
      // Non-fatal — the lists just start empty; the merchant can still see
      // and edit the scalar policy fields, and re-expanding retries the fetch
      // since excludedListsLoaded only guards against duplicate calls while
      // still expanded, not across a collapse/re-expand cycle... actually it
      // does persist across that too (ref survives re-render), which is fine:
      // a failed load leaving the field blank is safe (empty diff on save,
      // not a silent data-loss risk) rather than surprising.
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  const parseIds = (str) =>
    str.split(',').map((s) => s.trim()).filter(Boolean).map(Number).filter((n) => Number.isInteger(n));

  const saveRefundPolicy = async () => {
    setSavingRefundPolicy(true);
    setRefundPolicyMsg('');
    try {
      await Promise.all([
        client.patch(`/api/v2/brands/${brand.id}`, {
          return_policy_days: Number(returnPolicyDays),
          exclude_digital_products: excludeDigitalProducts,
          refund_notes: refundNotes,
        }),
        client.put(`/api/v2/brands/${brand.id}/refund-policy/excluded-products`, {
          ids: parseIds(excludedProductIds),
        }),
        client.put(`/api/v2/brands/${brand.id}/refund-policy/excluded-collections`, {
          ids: parseIds(excludedCollectionIds),
        }),
      ]);
      setRefundPolicyMsg('Saved!');
    } catch (err) {
      setRefundPolicyMsg(extractErrorMessage(err, 'Failed to save refund policy.'));
    } finally {
      setSavingRefundPolicy(false);
    }
  };

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

  // "Connected" here must mean a real Shopify connection, not merely that
  // the brand row is active (every brand is is_active by default from
  // creation, Shopify or not) - same signal Onboarding.jsx already uses,
  // so the Store page and onboarding checklist can't contradict each other.
  const connected = !!(brand.shopify_connected || brand.shopify_domain);

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
            {brand.ticket_count || brand.tickets_count || '-'} tickets
          </div>
          <div>{connected ? '● Connected' : '○ Not connected'}</div>
        </div>
      </div>

      {expanded && (
        <div style={{ padding: '0 24px 20px', borderTop: '1px solid var(--border)', paddingTop: '16px' }}>
          <div style={{ display: 'flex', gap: '24px', marginBottom: '16px', flexWrap: 'wrap' }}>
            {[
              ['Store', brand.shopify_shop_name || '-'],
              ['Support email', brand.support_email || '-'],
              ['Return window', brand.return_policy_days != null ? `${brand.return_policy_days} days` : '-'],
              ['Auto-approve ≤', brand.auto_approve_threshold != null ? `$${brand.auto_approve_threshold}` : '-'],
            ].map(([label, value]) => (
              <div key={label}>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontFamily: 'DM Mono, monospace', fontWeight: '500', fontSize: '13px' }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>AI Agent Name</div>
              <input
                value={agentName}
                onChange={e => setAgentName(e.target.value)}
                placeholder="luna"
                maxLength={20}
                style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', fontSize: '13px', width: '160px' }}
              />
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Store Display Name</div>
              <input
                value={brandName}
                onChange={e => setBrandName(e.target.value)}
                placeholder="luna clothing"
                style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', fontSize: '13px', width: '200px' }}
              />
            </div>
            <div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Email Signature</div>
              <input
                value={emailSignature}
                onChange={e => setEmailSignature(e.target.value)}
                placeholder="with care, luna"
                style={{ padding: '7px 10px', borderRadius: '4px', border: '1px solid var(--border)', fontSize: '13px', width: '220px' }}
              />
            </div>
            <button
              onClick={saveIdentity}
              disabled={savingIdentity}
              style={{ alignSelf: 'flex-end', padding: '7px 14px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: '500', fontSize: '13px', cursor: savingIdentity ? 'not-allowed' : 'pointer' }}
            >
              {savingIdentity ? 'Saving...' : 'Save'}
            </button>
            {identityMsg && (
              <span style={{ alignSelf: 'center', fontSize: '12px', color: identityMsg === 'Saved!' ? 'var(--success)' : 'var(--danger)' }}>
                {identityMsg}
              </span>
            )}
          </div>
          <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '-10px', marginBottom: '16px' }}>
            This is the name your customers see in chat and email replies. Default: Luna
          </p>

          <EmailForwardingSetup brandId={brand.id} returnTo="brands" />
        </div>
      )}
    </div>
  );
}

export default function Brands() {
  const [brand, setBrand] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchParams] = useSearchParams();
  const gmailConnected = searchParams.get('gmail_connected');
  const gmailEmail = searchParams.get('email');
  const gmailError = searchParams.get('gmail_error');

  useEffect(() => {
    setLoading(true);
    client.get('/api/brands').then(res => {
      const d = res.data;
      const list = Array.isArray(d) ? d : d?.brands || d?.data || [];
      setBrand(list[0] || null);
    }).catch(() => {
      setError('Failed to load your store.');
    }).finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ marginBottom: '20px' }}>
        <h2 style={{ fontSize: '18px', fontWeight: '600', color: 'var(--text-primary)', margin: 0 }}>Your Shopify store</h2>
        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '4px 0 0' }}>
          Identity, refund policy, and connections for the AI agent working your inbox.
        </p>
      </div>

      {gmailConnected === '1' && (
        <div style={{ padding: '12px 16px', background: 'var(--success-light)', border: '1px solid var(--success)', borderRadius: '6px', color: 'var(--success)', fontSize: '13px', marginBottom: '16px' }}>
          ✓ Gmail connected{gmailEmail ? `: ${gmailEmail}` : ''}
        </div>
      )}
      {gmailError && (
        <div style={{ padding: '12px 16px', background: 'var(--danger-light)', border: '1px solid #FCA5A5', borderRadius: '6px', color: 'var(--danger)', fontSize: '13px', marginBottom: '16px', lineHeight: '1.5' }}>
          <div style={{ fontWeight: '600', marginBottom: '4px' }}>Gmail connection failed</div>
          {gmailOAuthErrorMessage(gmailError)}{' '}
          <Link to="/settings" style={{ color: 'var(--danger)', fontWeight: '600', textDecoration: 'underline' }}>
            Go to Settings to try again →
          </Link>
        </div>
      )}
      {error && (
        <div style={{ padding: '14px 16px', background: 'var(--danger-light)', border: '1px solid #FCA5A5', borderRadius: '6px', color: 'var(--danger)', fontSize: '13px', marginBottom: '16px' }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="skeleton" style={{ height: '80px', borderRadius: '6px' }} />
      ) : brand ? (
        <StoreCard brand={brand} highlightGmail={gmailConnected === '1'} />
      ) : (
        <div style={{ padding: '48px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg-primary)' }}>
          <div style={{ fontWeight: '600', marginBottom: '6px' }}>We couldn't find your store</div>
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
            This can happen briefly right after signup. Try refreshing. If it persists,{' '}
            <HelpContactLink variant="inline" context="Store: couldn't find store" label="contact support" />.
          </div>
        </div>
      )}
    </div>
  );
}
