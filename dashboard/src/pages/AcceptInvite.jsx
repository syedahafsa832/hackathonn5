import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import client, { extractErrorMessage } from '../api/client';
import Alert from '../components/Alert';
import { setLoggedInCookie } from '../api/sessionCookie';

// Invited user's entry point: preview the invite, then sign up or log in
// through the EXISTING auth endpoints (same as Signup/Login), then accept.
export default function AcceptInvite() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get('token') || '';

  const [invite, setInvite] = useState(null);
  const [mode, setMode] = useState('signup'); // 'signup' | 'login'
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');

  useEffect(() => {
    if (!token) { setPreviewError('Missing invite link.'); return; }
    client.get(`/api/v1/team/invites/${token}`)
      .then(res => setInvite(res.data))
      .catch(err => setPreviewError(extractErrorMessage(err, 'This invite is invalid or has expired.')));
  }, [token]);

  const finishAccept = async () => {
    const res = await client.post(`/api/v1/team/invites/${token}/accept`);
    return res.data;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const path = mode === 'signup' ? '/api/v1/auth/register' : '/api/v1/auth/login';
      const body = mode === 'signup'
        ? { email: invite.email, password, company_name: invite.company_name }
        : { email: invite.email, password };
      const res = await client.post(path, body);
      const { access_token, refresh_token, expires_in } = res.data;
      if (!access_token) {
        setError('Check your email to confirm your account, then come back to this link to accept the invite.');
        return;
      }
      localStorage.setItem('resolv_token', access_token);
      if (refresh_token) localStorage.setItem('resolv_refresh_token', refresh_token);
      setLoggedInCookie(expires_in);

      await finishAccept();
      navigate('/dashboard');
    } catch (err) {
      setError(extractErrorMessage(err, 'Something went wrong. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = { width: '100%', padding: '10px 12px', border: '1px solid var(--border-strong)', borderRadius: '4px', fontSize: '14px' };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', background: 'var(--bg-secondary)' }}>
      <div style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', borderRadius: '8px', padding: '40px', width: '100%', maxWidth: '420px' }}>
        <div style={{ fontSize: '22px', fontWeight: '700', marginBottom: '12px' }}>Join the team</div>

        {previewError ? (
          <Alert variant="error">{previewError}</Alert>
        ) : !invite ? (
          <div style={{ color: 'var(--text-muted)' }}>Loading invite...</div>
        ) : invite.status !== 'pending' ? (
          <Alert variant="error">This invite has already been used or revoked.</Alert>
        ) : (
          <>
            <div style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
              You've been invited to join <strong>{invite.company_name}</strong> on tResolv as an <strong>{invite.role}</strong>.
              Sign up or log in as <strong>{invite.email}</strong> to accept.
            </div>

            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
              <button type="button" onClick={() => setMode('signup')} style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid var(--border)', background: mode === 'signup' ? 'var(--accent)' : 'transparent', color: mode === 'signup' ? 'white' : 'var(--text-primary)', fontWeight: 600, cursor: 'pointer' }}>New here</button>
              <button type="button" onClick={() => setMode('login')} style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid var(--border)', background: mode === 'login' ? 'var(--accent)' : 'transparent', color: mode === 'login' ? 'white' : 'var(--text-primary)', fontWeight: 600, cursor: 'pointer' }}>I have an account</button>
            </div>

            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <input type="password" required minLength={mode === 'signup' ? 8 : undefined} placeholder={mode === 'signup' ? 'Create a password (min. 8 characters)' : 'Password'} value={password} onChange={e => setPassword(e.target.value)} style={inputStyle} />
              <Alert variant="error">{error}</Alert>
              <button type="submit" disabled={loading} style={{ padding: '11px', borderRadius: '4px', background: 'var(--accent)', color: 'white', fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer' }}>
                {loading ? 'Working...' : mode === 'signup' ? 'Create account & join' : 'Log in & join'}
              </button>
            </form>
          </>
        )}

        <div style={{ textAlign: 'center', marginTop: '20px', fontSize: '13px' }}>
          <Link to="/login" style={{ color: 'var(--accent)' }}>Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
