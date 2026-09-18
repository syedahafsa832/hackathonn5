import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setLoggedInCookie } from '../api/sessionCookie';

/**
 * Lands here after the backend-mediated Google OAuth redirect flow
 * (GET /api/v1/auth/google/callback) sends the browser back with tokens in
 * the URL hash fragment — same convention ResetPassword.jsx uses for a
 * recovery token, and for the same reason: a hash fragment is never sent to
 * any server, so it doesn't linger in server logs.
 */
export default function GoogleAuthCallback() {
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const initialized = useRef(false);

  useEffect(() => {
    document.title = "Signing in: tResolv";
    if (initialized.current) return; // React 18 StrictMode double-invoke guard
    initialized.current = true;

    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const accessToken = params.get('access_token');
    const refreshToken = params.get('refresh_token');
    const expiresIn = params.get('expires_in');
    window.history.replaceState(null, '', window.location.pathname);

    if (!accessToken) {
      setError('Google sign-in did not complete. Please try again.');
      return;
    }

    localStorage.setItem('resolv_token', accessToken);
    if (refreshToken) localStorage.setItem('resolv_refresh_token', refreshToken);
    setLoggedInCookie(expiresIn ? Number(expiresIn) : undefined);
    // Dashboard/App already redirects a tenant who hasn't finished
    // onboarding to /onboarding — same target Login.jsx's own Google/email
    // success paths already navigate to for an existing session.
    navigate('/dashboard', { replace: true });
  }, [navigate]);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', padding: '24px' }}>
      {error ? (
        <div style={{ textAlign: 'center' }}>
          <p style={{ color: 'var(--error, #EF4444)', marginBottom: '16px' }}>{error}</p>
          <a href="/login" style={{ color: 'var(--accent)' }}>Back to sign in</a>
        </div>
      ) : (
        <p style={{ color: 'var(--text-muted)' }}>Signing you in…</p>
      )}
    </div>
  );
}
