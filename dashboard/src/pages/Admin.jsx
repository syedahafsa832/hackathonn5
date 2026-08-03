import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Crown } from 'lucide-react';
import api from '../api/services';
import Alert from '../components/Alert';

const PAID_PLANS = ['starter', 'growth', 'enterprise'];

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function Dot({ ok }) {
  return (
    <span style={{
      display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%',
      background: ok ? '#16A34A' : '#CBD5E1',
    }} title={ok ? 'Connected' : 'Not connected'} />
  );
}

export default function Admin() {
  const [tenants, setTenants] = useState([]);
  const [upgradeRequests, setUpgradeRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activating, setActivating] = useState(null);
  const [activateError, setActivateError] = useState('');

  useEffect(() => {
    document.title = 'Admin — tResolv';
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [t, u] = await Promise.all([
        api.getAdminTenants(),
        api.getAdminUpgradeRequests().catch(() => ({ requests: [] })),
      ]);
      setTenants(t.tenants || []);
      setUpgradeRequests(u.requests || []);
    } catch (e) {
      setError(e.response?.status === 403
        ? "You don't have access to this page."
        : (e.response?.data?.detail || 'Failed to load admin data'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const activate = async (id) => {
    setActivating(id);
    setActivateError('');
    try {
      await api.activateUpgradeRequest(id);
      await load();
    } catch (e) {
      setActivateError(e.response?.data?.detail || 'Failed to activate upgrade');
    } finally {
      setActivating(null);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '24px' }}>
        <div className="skeleton" style={{ height: '200px', borderRadius: '8px' }} />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '24px' }}>
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          padding: '60px 24px', border: '1px solid #E4E4E7', borderRadius: '8px', background: 'white', gap: '12px',
        }}>
          <div style={{ width: '48px', height: '48px', borderRadius: '50%', background: '#FFFBEB', color: '#F59E0B', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '22px' }}>🔒</div>
          <div style={{ fontSize: '16px', fontWeight: '600', color: '#1E293B' }}>{error}</div>
          <div style={{ fontSize: '13px', color: '#94A3B8', textAlign: 'center' }}>You may not have the required permissions, or something went wrong loading this page.</div>
          <Link to="/dashboard" style={{ marginTop: '4px', padding: '8px 18px', background: '#06B6D4', color: 'white', borderRadius: '6px', textDecoration: 'none', fontSize: '13px', fontWeight: '600' }}>
            ← Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  const pendingRequests = upgradeRequests.filter(r => r.status === 'pending');

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '28px' }}>
      <div>
        <h2 style={{ fontSize: '16px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>
          Tenants ({tenants.length})
        </h2>
        <p style={{ fontSize: '13px', color: '#64748B', marginBottom: '16px' }}>
          Read-only overview — plan, signup date, connection status, ticket volume.
        </p>

        <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#F8FAFC' }}>
                {['Company / Email', 'Plan', 'Signed Up', 'Last Login', 'Gmail', 'Shopify', 'Tickets'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '11px', fontWeight: '600', color: '#64748B', borderBottom: '1px solid #E4E4E7', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tenants.map(t => (
                <tr key={t.id} style={{ borderBottom: '1px solid #F1F5F9', height: '52px' }}>
                  <td style={{ padding: '0 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontWeight: '500', color: '#0F172A' }}>
                      {PAID_PLANS.includes(t.plan) && <Crown size={13} color="#D97706" />}
                      {t.company_name || '—'} {t.is_super_admin && <span style={{ fontSize: '10px', color: '#06B6D4', fontWeight: '600', marginLeft: '4px' }}>ADMIN</span>}
                    </div>
                    <div style={{ fontSize: '12px', color: '#64748B' }}>{t.email}</div>
                  </td>
                  <td style={{ padding: '0 16px', fontSize: '13px', color: '#1E293B' }}>{t.plan_label || t.plan}</td>
                  <td style={{ padding: '0 16px', fontSize: '12px', color: '#64748B', fontFamily: 'DM Mono, monospace' }}>{formatDate(t.created_at)}</td>
                  <td style={{ padding: '0 16px', fontSize: '12px', color: '#64748B', fontFamily: 'DM Mono, monospace' }}>{formatDate(t.last_login_at)}</td>
                  <td style={{ padding: '0 16px' }}><Dot ok={t.brands?.some(b => b.gmail_connected)} /></td>
                  <td style={{ padding: '0 16px' }}><Dot ok={t.brands?.some(b => b.shopify_connected)} /></td>
                  <td style={{ padding: '0 16px', fontSize: '13px', color: '#1E293B' }}>{t.ticket_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h2 style={{ fontSize: '16px', fontWeight: '600', color: '#0F172A', marginBottom: '4px' }}>
          Upgrade Requests {pendingRequests.length > 0 && `(${pendingRequests.length} pending)`}
        </h2>
        <p style={{ fontSize: '13px', color: '#64748B', marginBottom: '16px' }}>
          Manually activate after confirming payment (bank transfer) — no automatic billing.
        </p>
        <div style={{ marginBottom: activateError ? '16px' : 0 }}>
          <Alert variant="error" onDismiss={() => setActivateError('')} autoDismissMs={5000}>{activateError}</Alert>
        </div>

        {upgradeRequests.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', color: '#94A3B8', fontSize: '13px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px' }}>
            No upgrade requests yet.
          </div>
        ) : (
          <div style={{ background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#F8FAFC' }}>
                  {['Name', 'Email', 'Brand', 'Requested Plan', 'Transaction Ref', 'Status', 'Submitted', ''].map(h => (
                    <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: '11px', fontWeight: '600', color: '#64748B', borderBottom: '1px solid #E4E4E7', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {upgradeRequests.map(r => (
                  <tr key={r.id} style={{ borderBottom: '1px solid #F1F5F9', height: '48px' }}>
                    <td style={{ padding: '0 16px', color: '#1E293B' }}>{r.name}</td>
                    <td style={{ padding: '0 16px', color: '#1E293B' }}>{r.email}</td>
                    <td style={{ padding: '0 16px', color: '#1E293B' }}>{r.brand || '—'}</td>
                    <td style={{ padding: '0 16px', color: '#1E293B', textTransform: 'capitalize' }}>{r.requested_plan}</td>
                    <td style={{ padding: '0 16px', color: r.transaction_reference ? '#1E293B' : '#CBD5E1', fontFamily: 'DM Mono, monospace', fontSize: '12px', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.transaction_reference || ''}>
                      {r.transaction_reference || '—'}
                    </td>
                    <td style={{ padding: '0 16px' }}>
                      <span style={{
                        fontSize: '11px', fontWeight: '600', padding: '2px 8px', borderRadius: '10px',
                        background: r.status === 'activated' ? '#ECFDF5' : '#FFFBEB',
                        color: r.status === 'activated' ? '#16A34A' : '#D97706',
                        textTransform: 'capitalize',
                      }}>
                        {r.status}
                      </span>
                    </td>
                    <td style={{ padding: '0 16px', fontSize: '12px', color: '#64748B', fontFamily: 'DM Mono, monospace' }}>{formatDate(r.created_at)}</td>
                    <td style={{ padding: '0 16px' }}>
                      {r.status === 'pending' && (
                        <button
                          onClick={() => activate(r.id)}
                          disabled={activating === r.id}
                          style={{
                            fontSize: '12px', fontWeight: '600', color: 'white', background: '#06B6D4',
                            border: 'none', borderRadius: '4px', padding: '5px 10px', cursor: 'pointer',
                            opacity: activating === r.id ? 0.6 : 1,
                          }}
                        >
                          {activating === r.id ? 'Activating…' : 'Activate'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
