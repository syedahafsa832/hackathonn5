import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Inbox, ShieldAlert, Store, Settings, ShieldQuestion } from 'lucide-react';
import client from '../api/client';

const NAV = [
  { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/tickets', label: 'Conversations', icon: Inbox },
  { path: '/actions', label: 'Escalations', icon: ShieldAlert, badge: true },
  { path: '/quarantine', label: 'Quarantine', icon: ShieldQuestion, quarantineBadge: true },
  { path: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar() {
  const [pendingCount, setPendingCount] = useState(0);
  const [quarantineCount, setQuarantineCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (!localStorage.getItem('resolv_token')) return;

    const fetchCounts = () => {
      Promise.all([
        client.get('/api/v1/actions/pending').catch(() => ({ data: [] })),
        client.get('/api/tickets', { params: { status: 'escalated' } }).catch(() => ({ data: [] })),
        client.get('/api/v1/quarantine').catch(() => ({ data: { pending: 0 } })),
      ]).then(([actionsRes, escalatedRes, quarantineRes]) => {
        if (cancelled) return;
        const actions = Array.isArray(actionsRes.data) ? actionsRes.data : actionsRes.data?.actions || [];
        const escalated = Array.isArray(escalatedRes.data) ? escalatedRes.data : escalatedRes.data?.tickets || [];
        setPendingCount(actions.length + escalated.length);
        setQuarantineCount(quarantineRes.data?.pending || 0);
      });
    };

    fetchCounts();
    const interval = setInterval(fetchCounts, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  return (
    <aside style={{
      width: 'var(--sidebar-width)',
      flexShrink: 0,
      background: 'var(--bg-secondary)',
      borderRight: '1px solid var(--border)',
      height: '100vh',
      position: 'fixed',
      top: 0,
      left: 0,
      display: 'flex',
      flexDirection: 'column',
      zIndex: 100,
    }}>
      <div style={{
        height: 'var(--topbar-height)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 20px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: '700', fontSize: '18px', color: 'var(--accent)', letterSpacing: '-0.3px' }}>
          Resolv
        </span>
      </div>

      <nav style={{ flex: 1, padding: '8px 8px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {NAV.map(({ path, label, icon: Icon, badge, quarantineBadge }) => (
          <NavLink
            key={path}
            to={path}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '9px 12px',
              borderRadius: '6px',
              textDecoration: 'none',
              fontWeight: isActive ? '600' : '400',
              fontSize: '14px',
              color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
              background: isActive ? 'var(--accent-light)' : 'transparent',
              transition: 'all 0.1s',
            })}
            onMouseEnter={e => { if (!e.currentTarget.classList.contains('active')) e.currentTarget.style.background = 'var(--bg-tertiary)'; }}
            onMouseLeave={e => { if (!e.currentTarget.style.background?.includes('accent')) e.currentTarget.style.background = 'transparent'; }}
          >
            <Icon size={16} />
            <span style={{ flex: 1 }}>{label}</span>
            {badge && pendingCount > 0 && (
              <span style={{
                background: 'var(--danger)',
                color: 'white',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: '600',
                padding: '1px 6px',
                fontFamily: 'DM Mono, monospace',
                minWidth: '18px',
                textAlign: 'center',
              }}>
                {pendingCount}
              </span>
            )}
            {quarantineBadge && quarantineCount > 0 && (
              <span style={{
                background: 'var(--warning, #f59e0b)',
                color: 'white',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: '600',
                padding: '1px 6px',
                fontFamily: 'DM Mono, monospace',
                minWidth: '18px',
                textAlign: 'center',
              }}>
                {quarantineCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border)', fontSize: '11px', color: 'var(--text-muted)' }}>
        Resolv v1.0
      </div>
    </aside>
  );
}
