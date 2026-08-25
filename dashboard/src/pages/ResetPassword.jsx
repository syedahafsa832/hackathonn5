import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import client from '../api/client';
import Alert from '../components/Alert';

export default function ResetPassword() {
  const [accessToken, setAccessToken] = useState(null);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    document.title = "Reset password — tResolv";
    // Supabase's recovery email redirects here with the session in the URL
    // hash fragment (#access_token=...&type=recovery), not a query string.
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const token = params.get('access_token');
    if (params.get('type') !== 'recovery' || !token) {
      setError('This reset link is invalid or has expired. Request a new one.');
      return;
    }
    setAccessToken(token);
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      // Bypasses the shared `client` on purpose — its request interceptor
      // always attaches the signed-in user's own resolv_token, which would
      // overwrite this one-time recovery token.
      await axios.post(
        `${client.defaults.baseURL}/api/v1/auth/password/reset-confirm`,
        { new_password: password },
        { headers: { Authorization: `Bearer ${accessToken}` }, timeout: 35000 }
      );
      setDone(true);
    } catch (err) {
      const msg = err.response?.data?.detail || 'Could not reset your password. The link may have expired.';
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--bg-secondary)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '24px',
    }}>
      <div style={{
        background: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        borderRadius: '12px',
        padding: '40px',
        width: '100%',
        maxWidth: '400px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{ fontSize: '24px', fontWeight: '700', letterSpacing: '-0.5px', marginBottom: '6px' }}>
            <span style={{ color: 'var(--accent)' }}>t</span><span style={{ color: 'var(--text-primary)' }}>Resolv</span>
          </div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
            Choose a new password
          </div>
        </div>

        {done ? (
          <div style={{ textAlign: 'center' }}>
            <Alert variant="success" style={{ textAlign: 'left' }}>
              Your password has been updated.
            </Alert>
            <Link to="/login" style={{ display: 'inline-block', marginTop: '16px', color: 'var(--accent)', fontWeight: '500' }}>
              Sign in
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                New password
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                autoComplete="new-password"
                placeholder="Min. 8 characters"
                disabled={!accessToken}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '14px',
                  background: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                Confirm new password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                required
                autoComplete="new-password"
                placeholder="Re-enter password"
                disabled={!accessToken}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '14px',
                  background: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>

            <Alert variant="error">{error}</Alert>

            <button
              type="submit"
              disabled={loading || !accessToken}
              style={{
                width: '100%',
                height: '40px',
                borderRadius: '6px',
                background: (loading || !accessToken) ? 'var(--text-muted)' : 'var(--accent)',
                color: 'white',
                fontWeight: '500',
                fontSize: '14px',
                cursor: (loading || !accessToken) ? 'not-allowed' : 'pointer',
                border: 'none',
              }}
            >
              {loading ? 'Updating...' : 'Update password'}
            </button>

            <div style={{ textAlign: 'center', fontSize: '13px', color: 'var(--text-muted)' }}>
              <Link to="/forgot-password" style={{ color: 'var(--accent)', fontWeight: '500', textDecoration: 'none' }}>
                Request a new link
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
