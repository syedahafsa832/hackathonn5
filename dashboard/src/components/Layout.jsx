import { useState, useRef, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Sidebar from './Sidebar';
import Avatar from './Avatar';
import HelpContactLink from './HelpContactLink';
import { useAuth } from '../hooks/useAuth';
import { useMe } from '../hooks/useApi';
import { LogOut, ChevronDown, Store, Menu } from 'lucide-react';

const PAGE_TITLES = {
  '/dashboard': 'Dashboard Overview',
  '/tickets': 'Conversations',
  '/actions': 'Escalations',
  '/quarantine': 'Quarantine Queue',
  '/brands': 'Store',
  '/settings': 'Settings',
  '/admin': 'Admin',
  '/upgrade': 'Upgrade',
  '/profile': 'Profile',
};

export default function Layout({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const { data: me, isLoading: meLoading } = useMe();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const matchedTitle = Object.entries(PAGE_TITLES).find(([path]) =>
    location.pathname.startsWith(path)
  )?.[1];
  const title = matchedTitle || (
    <>
      <span style={{ color: '#06B6D4' }}>t</span>
      <span style={{ color: 'var(--text-primary)' }}>Resolv</span>
    </>
  );
  // Plain-string version of the page name for HelpContactLink's mailto
  // subject — `title` above can be JSX (the tResolv logo fallback), which
  // isn't usable there.
  const pageLabel = matchedTitle || 'tResolv';

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar mobileOpen={mobileMenuOpen} onCloseMobile={() => setMobileMenuOpen(false)} />

      <div className="layout-main" style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        <header style={{
          height: 'var(--topbar-height)',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          background: 'var(--bg-primary)',
          flexShrink: 0,
          zIndex: 50,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button
              className="mobile-menu-btn"
              onClick={() => setMobileMenuOpen(true)}
              aria-label="Open menu"
            >
              <Menu size={18} />
            </button>
            <h1 style={{ fontSize: '16px', fontWeight: '600', color: 'var(--text-primary)' }}>
              {title}
            </h1>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <HelpContactLink context={pageLabel} />
            <button
              onClick={() => navigate('/profile')}
              aria-label="Profile"
              title="Profile"
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', display: 'flex' }}
            >
              {meLoading ? (
                <div className="skeleton" style={{ width: 30, height: 30, borderRadius: '50%', flexShrink: 0 }} />
              ) : (
                <Avatar name={me?.company_name} email={me?.email} size={30} />
              )}
            </button>
            <button
              onClick={logout}
              aria-label="Sign out"
              title="Sign out"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 12px',
                borderRadius: '4px',
                background: 'transparent',
                color: 'var(--text-secondary)',
                fontSize: '13px',
                fontWeight: '500',
                border: '1px solid var(--border)',
                cursor: 'pointer',
              }}
            >
              <LogOut size={14} />
              <span className="topbar-btn-label">Sign out</span>
            </button>
          </div>
        </header>

        <main style={{ flex: 1, overflow: 'auto', background: 'var(--bg-secondary)' }}>
          {children}
        </main>
      </div>
    </div>
  );
}
