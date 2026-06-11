import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import client from '../api/client';

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    document.title = "Sign in — tResolv";
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await client.post('/api/v1/auth/login', { email, password });
      const token = res.data.access_token;
      if (!token) throw new Error('No token returned');
      localStorage.setItem('resolv_token', token);
      navigate('/dashboard');
    } catch (err) {
      if (!err.response) {
        setError('Cannot reach the server. Make sure the backend is running on port 8000.');
      } else {
        const msg = err.response?.data?.detail || err.response?.data?.error || 'Invalid email or password.';
        setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      background: '#09090B',
      backgroundImage: 'radial-gradient(circle, rgba(51,65,85,0.4) 1px, transparent 1px)',
      backgroundSize: '24px 24px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '24px',
    }}>
      <div style={{
        background: '#111113',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '12px',
        padding: '40px',
        width: '100%',
        maxWidth: '400px',
        boxShadow: '0 0 0 1px rgba(0,0,0,0.5), 0 24px 48px rgba(0,0,0,0.4)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{ fontSize: '24px', fontWeight: '700', letterSpacing: '-0.5px', marginBottom: '6px' }}>
            <span style={{ color: '#06B6D4' }}>t</span><span style={{ color: 'white' }}>Resolv</span>
          </div>
          <div style={{ fontSize: '14px', color: '#64748B' }}>
            Your AI support employee
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#94A3B8', marginBottom: '6px' }}>
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
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '6px',
                fontSize: '14px',
                background: '#18181B',
                color: 'white',
                transition: 'all 0.15s',
              }}
              onFocus={e => {
                e.target.style.borderColor = '#06B6D4';
                e.target.style.boxShadow = '0 0 0 3px rgba(6,182,212,0.15)';
              }}
              onBlur={e => {
                e.target.style.borderColor = 'rgba(255,255,255,0.1)';
                e.target.style.boxShadow = 'none';
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#94A3B8', marginBottom: '6px' }}>
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              placeholder="••••••••"
              style={{
                width: '100%',
                padding: '10px 12px',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '6px',
                fontSize: '14px',
                background: '#18181B',
                color: 'white',
                transition: 'all 0.15s',
              }}
              onFocus={e => {
                e.target.style.borderColor = '#06B6D4';
                e.target.style.boxShadow = '0 0 0 3px rgba(6,182,212,0.15)';
              }}
              onBlur={e => {
                e.target.style.borderColor = 'rgba(255,255,255,0.1)';
                e.target.style.boxShadow = 'none';
              }}
            />
          </div>

          {error && (
            <div style={{
              padding: '10px 12px',
              background: 'var(--danger-light)',
              border: '1px solid #FCA5A5',
              borderRadius: '4px',
              color: 'var(--danger)',
              fontSize: '13px',
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              height: '40px',
              borderRadius: '6px',
              background: loading ? '#475569' : '#06B6D4',
              color: 'white',
              fontWeight: '500',
              fontSize: '14px',
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s',
              marginTop: '8px',
              border: 'none'
            }}
            onMouseEnter={e => { if (!loading) e.target.style.background = '#0891B2'; }}
            onMouseLeave={e => { if (!loading) e.target.style.background = '#06B6D4'; }}
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        <div style={{ textAlign: 'center', marginTop: '20px', fontSize: '13px', color: '#94A3B8' }}>
          Don't have an account?{' '}
          <Link to="/signup" style={{ color: '#06B6D4', fontWeight: '500', textDecoration: 'none' }} onMouseEnter={e => e.target.style.textDecoration = 'underline'} onMouseLeave={e => e.target.style.textDecoration = 'none'}>
            Sign up
          </Link>
        </div>
      </div>
    </div>
  );
}
