import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import client from '../api/client';

export default function Signup() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: '', password: '', company_name: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const set = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setLoading(true);
    try {
      const res = await client.post('/api/v1/auth/register', {
        email: form.email,
        password: form.password,
        company_name: form.company_name,
      });
      const token = res.data.access_token;
      if (token) {
        localStorage.setItem('resolv_token', token);
        navigate('/dashboard');
      } else {
        navigate('/login');
      }
    } catch (err) {
      if (!err.response) {
        setError('Cannot reach the server. Make sure the backend is running on port 8000.');
      } else {
        const msg = err.response?.data?.detail || err.response?.data?.error || 'Registration failed. Please try again.';
        setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    width: '100%',
    padding: '10px 12px',
    border: '1px solid var(--border-strong)',
    borderRadius: '4px',
    fontSize: '14px',
    background: 'var(--bg-primary)',
    color: 'var(--text-primary)',
    transition: 'border-color 0.15s',
  };

  const labelStyle = {
    display: 'block',
    fontSize: '13px',
    fontWeight: '500',
    color: 'var(--text-secondary)',
    marginBottom: '6px',
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
        borderRadius: '8px',
        padding: '40px',
        width: '100%',
        maxWidth: '420px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{ fontSize: '28px', fontWeight: '700', color: 'var(--accent)', letterSpacing: '-0.5px', marginBottom: '6px' }}>
            Resolv
          </div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
            Create your account
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={labelStyle}>Company name</label>
            <input
              type="text"
              value={form.company_name}
              onChange={set('company_name')}
              required
              placeholder="Your brand name"
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = 'var(--accent)'}
              onBlur={e => e.target.style.borderColor = 'var(--border-strong)'}
            />
          </div>

          <div>
            <label style={labelStyle}>Email</label>
            <input
              type="email"
              value={form.email}
              onChange={set('email')}
              required
              autoComplete="email"
              placeholder="you@brand.com"
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = 'var(--accent)'}
              onBlur={e => e.target.style.borderColor = 'var(--border-strong)'}
            />
          </div>

          <div>
            <label style={labelStyle}>Password</label>
            <input
              type="password"
              value={form.password}
              onChange={set('password')}
              required
              autoComplete="new-password"
              placeholder="Min. 8 characters"
              style={inputStyle}
              onFocus={e => e.target.style.borderColor = 'var(--accent)'}
              onBlur={e => e.target.style.borderColor = 'var(--border-strong)'}
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
              padding: '11px',
              borderRadius: '4px',
              background: loading ? 'var(--text-muted)' : 'var(--accent)',
              color: 'white',
              fontWeight: '600',
              fontSize: '14px',
              cursor: loading ? 'not-allowed' : 'pointer',
              marginTop: '4px',
              transition: 'background 0.15s',
            }}
            onMouseEnter={e => { if (!loading) e.target.style.background = 'var(--accent-hover)'; }}
            onMouseLeave={e => { if (!loading) e.target.style.background = 'var(--accent)'; }}
          >
            {loading ? 'Creating account...' : 'Create account'}
          </button>
        </form>

        <div style={{ textAlign: 'center', marginTop: '20px', fontSize: '13px', color: 'var(--text-muted)' }}>
          Already have an account?{' '}
          <Link to="/login" style={{ color: 'var(--accent)', fontWeight: '500' }}>
            Sign in
          </Link>
        </div>
      </div>
    </div>
  );
}
