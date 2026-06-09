import { useState, useRef, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import { useAuth } from '../hooks/useAuth';
import { LogOut, ChevronDown, Store } from 'lucide-react';

const PAGE_TITLES = {
  '/dashboard': 'Dashboard Overview',
  '/tickets': 'Conversations',
  '/actions': 'Escalations',
  '/quarantine': 'Quarantine Queue',
  '/settings': 'Settings',
};

export default function Layout({ children }) {
  const location = useLocation();
  const { logout } = useAuth();

  const title = Object.entries(PAGE_TITLES).find(([path]) =>
    location.pathname.startsWith(path)
  )?.[1] || 'Resolv';

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />

      <div style={{
        marginLeft: 'var(--sidebar-width)',
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
          <h1 style={{ fontSize: '16px', fontWeight: '600', color: 'var(--text-primary)' }}>
            {title}
          </h1>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <button
              onClick={logout}
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
              Sign out
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
