import { useState } from 'react';
import { Link } from 'react-router-dom';
import client from '../api/client';
import Alert from '../components/Alert';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await client.post('/api/v1/auth/password/reset-request', { email });
      setSent(true);
    } catch (err) {
      setError('Something went wrong. Please try again.');
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
            Reset your password
          </div>
        </div>

        {sent ? (
          <div style={{ textAlign: 'center' }}>
            <Alert variant="success" style={{ textAlign: 'left' }}>
              If <strong>{email}</strong> has an account, we've sent a password reset link to it.
            </Alert>
            <Link to="/login" style={{ display: 'inline-block', marginTop: '16px', color: 'var(--accent)', fontWeight: '500' }}>
              Back to sign in
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder="you@brand.com"
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
              disabled={loading}
              style={{
                width: '100%',
                height: '40px',
                borderRadius: '6px',
                background: loading ? 'var(--text-muted)' : 'var(--accent)',
                color: 'white',
                fontWeight: '500',
                fontSize: '14px',
                cursor: loading ? 'not-allowed' : 'pointer',
                border: 'none',
              }}
            >
              {loading ? 'Sending...' : 'Send reset link'}
            </button>

            <div style={{ textAlign: 'center', fontSize: '13px', color: 'var(--text-muted)' }}>
              <Link to="/login" style={{ color: 'var(--accent)', fontWeight: '500', textDecoration: 'none' }}>
                Back to sign in
              </Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
