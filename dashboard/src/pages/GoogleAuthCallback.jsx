import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import { setLoggedInCookie } from '../api/sessionCookie';

// Matches AcceptInvite.jsx's PENDING_INVITE_KEY — set there just before the
// Google redirect, read back here once the session tokens above are live.
const PENDING_INVITE_KEY = 'resolv_pending_invite_token';

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

    const inviteToken = sessionStorage.getItem(PENDING_INVITE_KEY);
    if (inviteToken) {
      sessionStorage.removeItem(PENDING_INVITE_KEY);
      // Same accept endpoint the email/password invite flow uses (see
      // AcceptInvite.jsx) — it verifies the invite's email against this
      // token's own verified email claim, so a Google account that doesn't
      // match the invite is rejected here without touching the invite.
      client.post(`/api/v1/team/invites/${inviteToken}/accept`)
        .then(() => navigate('/dashboard', { replace: true }))
        .catch(err => {
          const message = extractErrorMessage(err, 'Could not accept the invite with this Google account.');
          navigate(`/accept-invite?token=${encodeURIComponent(inviteToken)}&oauth_error=${encodeURIComponent(message)}`, { replace: true });
        });
      return;
    }

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
