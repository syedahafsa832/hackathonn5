import { isImpersonating, getImpersonatedTenant, exitImpersonation } from '../utils/impersonation';

// Shown across every authenticated page while an admin is viewing a
// customer's account via platform_admin.py's impersonate endpoint — so it's
// never ambiguous whose data is on screen, and there's always one click
// back to the admin's own session.
export default function ImpersonationBanner() {
  if (!isImpersonating()) return null;
  const tenant = getImpersonatedTenant();

  return (
    <div style={{
      background: '#7C2D12', color: '#FFF7ED', fontSize: '13px', fontWeight: '600',
      padding: '8px 16px', display: 'flex', alignItems: 'center', justifyContent: 'center',
      gap: '10px', flexShrink: 0, textAlign: 'center',
    }}>
      <span>
        Viewing as {tenant?.company_name || tenant?.email || 'customer'}
        {tenant?.company_name && tenant?.email ? ` (${tenant.email})` : ''}
      </span>
      <button
        onClick={exitImpersonation}
        style={{
          background: '#FFF7ED', color: '#7C2D12', border: 'none', borderRadius: '4px',
          padding: '3px 10px', fontSize: '12px', fontWeight: '700', cursor: 'pointer',
        }}
      >
        Exit
      </button>
    </div>
  );
}
