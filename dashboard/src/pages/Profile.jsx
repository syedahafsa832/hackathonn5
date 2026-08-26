import { useState, useEffect } from 'react';
import { Crown } from 'lucide-react';
import client from '../api/client';
import Avatar from '../components/Avatar';
import Alert from '../components/Alert';
import { useAuth } from '../hooks/useAuth';

const PAID_PLANS = ['starter', 'growth', 'enterprise'];

export default function Profile() {
  const { logout } = useAuth();
  const [profile, setProfile] = useState({ full_name: '', email: '', created_at: null });
  const [plan, setPlan] = useState(null);
  const [planLabel, setPlanLabel] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordMsg, setPasswordMsg] = useState('');

  useEffect(() => {
    document.title = 'Profile — tResolv';
  }, []);

  useEffect(() => {
    client.get('/api/v1/settings/account').then(res => {
      const s = res.data?.settings || {};
      setProfile({
        full_name: s.company_name || '',
        email: s.email || '',
        created_at: s.created_at || null,
      });
    }).catch(() => {}).finally(() => setLoading(false));

    // Same endpoint PlanUsageWidget already uses on the Dashboard — reusing it
    // here instead of adding a new one keeps plan_label computed in exactly
    // one place (plan_service.PLAN_LIMITS).
    client.get('/api/v1/settings/usage')
      .then(res => {
        setPlanLabel(res.data?.plan_label || null);
        setPlan(res.data?.plan || null);
      })
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg('');
    try {
      await client.put('/api/v1/settings/account', { company_name: profile.full_name });
      setMsg('Saved.');
    } catch {
      setMsg('Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  const changePassword = async () => {
    setPasswordMsg('');
    if (newPassword.length < 8) {
      setPasswordMsg('New password must be at least 8 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordMsg('Passwords do not match.');
      return;
    }
    setChangingPassword(true);
    try {
      await client.post('/api/v1/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordMsg('Password updated.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      // Re-authentication with the new password is required after a
      // password change, so the session is signed out rather than left
      // running on a token issued under the old one.
      setTimeout(logout, 1200);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setPasswordMsg(typeof detail === 'string' ? detail : 'Could not update password.');
      setChangingPassword(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '24px', maxWidth: '480px' }}>
        <div className="skeleton" style={{ height: '160px', borderRadius: '8px' }} />
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', maxWidth: '480px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '28px' }}>
        <Avatar name={profile.full_name} email={profile.email} size={64} />
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ fontSize: '17px', fontWeight: '700', color: '#0F172A' }}>
              {profile.full_name || 'Unnamed account'}
            </div>
            {planLabel && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '4px',
                fontSize: '11px', fontWeight: '600', color: '#0E7490', background: '#ECFEFF',
                border: '1px solid #A5F3FC', borderRadius: '10px', padding: '2px 9px',
              }}>
                {PAID_PLANS.includes(plan) && <Crown size={11} color="#D97706" />}
                {planLabel}
              </span>
            )}
          </div>
          <div style={{ fontSize: '13px', color: '#64748B' }}>{profile.email}</div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            Name
          </label>
          <input
            value={profile.full_name}
            onChange={e => setProfile(p => ({ ...p, full_name: e.target.value }))}
            placeholder="Your name or company"
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px',
              fontSize: '14px', color: '#0F172A',
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            Email (login)
          </label>
          <input
            value={profile.email}
            disabled
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px',
              fontSize: '14px', color: '#94A3B8', background: '#F8FAFC',
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            Avatar
          </label>
          <p style={{ fontSize: '12px', color: '#94A3B8' }}>
            No avatar uploaded — showing your initials. Avatar upload isn't available yet.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={save}
            disabled={saving}
            style={{
              padding: '8px 18px', borderRadius: '6px', border: 'none', background: '#06B6D4',
              color: 'white', fontSize: '13px', fontWeight: '600', cursor: 'pointer',
              opacity: saving ? 0.6 : 1,
            }}
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          <Alert
            variant={msg === 'Saved.' ? 'success' : 'error'}
            onDismiss={() => setMsg('')}
            autoDismissMs={2500}
          >
            {msg}
          </Alert>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', background: 'white', border: '1px solid #E4E4E7', borderRadius: '8px', padding: '20px', marginTop: '20px' }}>
        <div>
          <div style={{ fontSize: '14px', fontWeight: '600', color: '#0F172A' }}>
            Password
          </div>
          <div style={{ fontSize: '12px', color: '#94A3B8', marginTop: '2px' }}>
            Change your password.
          </div>
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            Current password
          </label>
          <input
            type="password"
            value={currentPassword}
            onChange={e => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px',
              fontSize: '14px', color: '#0F172A',
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            New password
          </label>
          <input
            type="password"
            value={newPassword}
            onChange={e => setNewPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="Min. 8 characters"
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px',
              fontSize: '14px', color: '#0F172A',
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: '500', color: '#64748B', marginBottom: '5px' }}>
            Confirm new password
          </label>
          <input
            type="password"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="Re-enter new password"
            style={{
              width: '100%', padding: '9px 12px', border: '1px solid #E4E4E7', borderRadius: '6px',
              fontSize: '14px', color: '#0F172A',
            }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button
            onClick={changePassword}
            disabled={changingPassword || !currentPassword || !newPassword || !confirmPassword}
            style={{
              padding: '8px 18px', borderRadius: '6px', border: 'none', background: '#06B6D4',
              color: 'white', fontSize: '13px', fontWeight: '600',
              cursor: (changingPassword || !currentPassword || !newPassword || !confirmPassword) ? 'not-allowed' : 'pointer',
              opacity: (changingPassword || !currentPassword || !newPassword || !confirmPassword) ? 0.6 : 1,
            }}
          >
            {changingPassword ? 'Updating…' : 'Change password'}
          </button>
          <Alert
            variant={passwordMsg === 'Password updated.' ? 'success' : 'error'}
            onDismiss={() => setPasswordMsg('')}
            autoDismissMs={2500}
          >
            {passwordMsg}
          </Alert>
        </div>
      </div>
    </div>
  );
}
