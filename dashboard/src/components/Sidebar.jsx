import { useState, useEffect } from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Inbox, ShieldAlert, Store, Settings, ShieldQuestion, ShieldCheck, ChevronLeft, ChevronRight, Zap } from 'lucide-react';
import { useEscalations, useActions, useQuarantineCount, useMe } from '../hooks/useApi';

const NAV = [
  { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/tickets', label: 'Conversations', icon: Inbox },
  { path: '/brands', label: 'Brands', icon: Store },
  { path: '/actions', label: 'Escalations', icon: ShieldAlert, badge: true },
  { path: '/quarantine', label: 'Quarantine', icon: ShieldQuestion, quarantineBadge: true },
  { path: '/settings', label: 'Settings', icon: Settings },
  { path: '/upgrade', label: 'Upgrade', icon: Zap },
];

const COLLAPSE_KEY = 'resolv_sidebar_collapsed';
const EXPANDED_WIDTH = '220px';
const COLLAPSED_WIDTH = '64px';

export default function Sidebar({ mobileOpen, onCloseMobile }) {
  const { data: escalations = [] } = useEscalations();
  const { data: actions = [] } = useActions('pending');
  const { data: quarantineCount = 0 } = useQuarantineCount();
  const { data: me } = useMe();
  const pendingCount = actions.length + escalations.length;

  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === 'true');

  // .layout-main's CSS reads var(--sidebar-width) for its margin-left, so
  // updating the variable here keeps the main content in sync without
  // Layout.jsx needing to know anything about collapse state.
  useEffect(() => {
    document.documentElement.style.setProperty('--sidebar-width', collapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH);
    localStorage.setItem(COLLAPSE_KEY, String(collapsed));
  }, [collapsed]);

  const navItems = me?.is_super_admin
    ? [...NAV, { path: '/admin', label: 'Admin', icon: ShieldCheck }]
    : NAV;

  const renderContent = (onLinkClick, isCollapsed) => (
    <>
      <div style={{
        height: '56px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: isCollapsed ? 'center' : 'flex-start',
        padding: isCollapsed ? '0' : '0 20px',
        borderBottom: '1px solid #E4E4E7',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: '700', fontSize: '17px', letterSpacing: '-0.3px' }}>
          <span style={{ color: '#06B6D4' }}>t</span>
          {!isCollapsed && <span style={{ color: '#0F172A' }}>Resolv</span>}
        </span>
      </div>

      <nav style={{ flex: 1, padding: '8px 0', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {navItems.map(({ path, label, icon: Icon, badge, quarantineBadge }) => {
          const count = badge ? pendingCount : quarantineBadge ? quarantineCount : 0;
          return (
            <NavLink
              key={path}
              to={path}
              onClick={onLinkClick}
              title={isCollapsed ? label : undefined}
              style={({ isActive }) => ({
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                justifyContent: isCollapsed ? 'center' : 'flex-start',
                gap: '10px',
                height: '36px',
                padding: isCollapsed ? '0' : (isActive ? '0 12px 0 10px' : '0 12px'),
                margin: isCollapsed ? '2px 10px' : '2px 8px',
                borderRadius: '6px',
                textDecoration: 'none',
                fontWeight: '500',
                fontSize: '14px',
                color: isActive ? '#0E7490' : '#475569',
                background: isActive ? '#ECFEFF' : 'transparent',
                borderLeft: !isCollapsed && isActive ? '2px solid #06B6D4' : 'none',
                transition: 'all 0.1s',
              })}
              onMouseEnter={e => { if (!e.currentTarget.classList.contains('active')) { e.currentTarget.style.background = '#F1F5F9'; e.currentTarget.style.color = '#0F172A'; } }}
              onMouseLeave={e => { if (!e.currentTarget.classList.contains('active')) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#475569'; } }}
            >
              <Icon size={16} />
              {!isCollapsed && <span style={{ flex: 1 }}>{label}</span>}
              {count > 0 && (
                <span style={{
                  background: badge ? '#06B6D4' : '#F59E0B',
                  color: 'white',
                  borderRadius: '10px',
                  fontSize: '11px',
                  fontWeight: '600',
                  padding: isCollapsed ? '0' : '1px 6px',
                  minWidth: isCollapsed ? '8px' : '18px',
                  height: isCollapsed ? '8px' : 'auto',
                  textAlign: 'center',
                  position: isCollapsed ? 'absolute' : 'static',
                  top: isCollapsed ? '2px' : 'auto',
                  right: isCollapsed ? '6px' : 'auto',
                }}>
                  {isCollapsed ? '' : count}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      {!isCollapsed && (
        <div style={{ padding: '12px 20px', borderTop: '1px solid #E4E4E7', fontSize: '11px', color: '#94A3B8' }}>
          tResolv v1.0
        </div>
      )}

      {/* Collapse toggle — desktop only, not shown on the mobile overlay */}
      {onLinkClick === undefined && (
        <button
          onClick={() => setCollapsed(c => !c)}
          aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
            height: '36px',
            margin: isCollapsed ? '4px 10px 10px' : '4px 8px 10px',
            borderRadius: '6px',
            border: '1px solid #E4E4E7',
            background: 'white',
            color: '#64748B',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: '500',
          }}
        >
          {isCollapsed ? <ChevronRight size={14} /> : <><ChevronLeft size={14} /> Collapse</>}
        </button>
      )}
    </>
  );

  const asideStyle = {
    width: collapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH,
    flexShrink: 0,
    background: '#FAFAFA',
    borderRight: '1px solid #E4E4E7',
    height: '100vh',
    flexDirection: 'column',
    transition: 'width 0.15s ease',
  };

  return (
    <>
      {/* Desktop sidebar — hidden under 768px via .app-sidebar */}
      <aside className="app-sidebar" style={{ ...asideStyle, position: 'fixed', top: 0, left: 0, zIndex: 100 }}>
        {renderContent(undefined, collapsed)}
      </aside>

      {/* Mobile overlay sidebar — always full width/labels regardless of desktop collapse state */}
      {mobileOpen && (
        <div
          className="mobile-sidebar-overlay open"
          style={{ position: 'fixed', inset: 0, zIndex: 200 }}
        >
          <div
            style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)' }}
            onClick={onCloseMobile}
          />
          <aside style={{ ...asideStyle, width: EXPANDED_WIDTH, position: 'absolute', top: 0, left: 0, bottom: 0, height: '100%', display: 'flex' }}>
            {renderContent(onCloseMobile, false)}
          </aside>
        </div>
      )}
    </>
  );
}
