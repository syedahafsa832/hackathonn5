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
      width: '220px',
      flexShrink: 0,
      background: '#FAFAFA',
      borderRight: '1px solid #E4E4E7',
      height: '100vh',
      position: 'fixed',
      top: 0,
      left: 0,
      display: 'flex',
      flexDirection: 'column',
      zIndex: 100,
    }}>
      <div style={{
        height: '56px',
        display: 'flex',
        alignItems: 'center',
        padding: '0 20px',
        borderBottom: '1px solid #E4E4E7',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: '700', fontSize: '17px', letterSpacing: '-0.3px' }}>
          <span style={{ color: '#06B6D4' }}>t</span><span style={{ color: '#0F172A' }}>Resolv</span>
        </span>
      </div>

      <nav style={{ flex: 1, padding: '8px 0', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {NAV.map(({ path, label, icon: Icon, badge, quarantineBadge }) => (
          <NavLink
            key={path}
            to={path}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              height: '36px',
              padding: isActive ? '0 12px 0 10px' : '0 12px',
              margin: '2px 8px',
              borderRadius: '6px',
              textDecoration: 'none',
              fontWeight: '500',
              fontSize: '14px',
              color: isActive ? '#0E7490' : '#475569',
              background: isActive ? '#ECFEFF' : 'transparent',
              borderLeft: isActive ? '2px solid #06B6D4' : 'none',
              transition: 'all 0.1s',
            })}
            onMouseEnter={e => { if (!e.currentTarget.classList.contains('active')) { e.currentTarget.style.background = '#F1F5F9'; e.currentTarget.style.color = '#0F172A'; } }}
            onMouseLeave={e => { if (!e.currentTarget.classList.contains('active')) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#475569'; } }}
          >
            <Icon size={16} />
            <span style={{ flex: 1 }}>{label}</span>
            {badge && pendingCount > 0 && (
              <span style={{
                background: '#06B6D4',
                color: 'white',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: '600',
                padding: '1px 6px',
                minWidth: '18px',
                textAlign: 'center',
              }}>
                {pendingCount}
              </span>
            )}
            {quarantineBadge && quarantineCount > 0 && (
              <span style={{
                background: '#F59E0B',
                color: 'white',
                borderRadius: '10px',
                fontSize: '11px',
                fontWeight: '600',
                padding: '1px 6px',
                minWidth: '18px',
                textAlign: 'center',
              }}>
                {quarantineCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <div style={{ padding: '12px 20px', borderTop: '1px solid #E4E4E7', fontSize: '11px', color: '#94A3B8' }}>
        tResolv v1.0
      </div>
    </aside>
  );
}
