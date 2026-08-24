import { useCallback } from 'react';
import client from '../api/client';

export function useAuth() {
  const token = localStorage.getItem('resolv_token');
  const isAuthenticated = !!token;

  const logout = useCallback(() => {
    // Best-effort — invalidates the session server-side, but the user is
    // logged out locally regardless of whether this call succeeds.
    client.post('/api/v1/auth/logout').catch(() => {});
    localStorage.removeItem('resolv_token');
    localStorage.removeItem('resolv_refresh_token');
    // Hard reload clears all React state and cached API responses
    window.location.href = '/login';
  }, []);

  return { token, isAuthenticated, logout };
}
