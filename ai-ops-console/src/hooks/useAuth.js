import { useCallback } from 'react';

export function useAuth() {
  const token = localStorage.getItem('resolv_token');
  const isAuthenticated = !!token;

  const logout = useCallback(() => {
    localStorage.removeItem('resolv_token');
    // Hard reload clears all React state and cached API responses
    window.location.href = '/login';
  }, []);

  return { token, isAuthenticated, logout };
}
