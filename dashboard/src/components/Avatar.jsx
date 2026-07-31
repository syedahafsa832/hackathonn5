const COLORS = ['#06B6D4', '#8B5CF6', '#F59E0B', '#10B981', '#EF4444', '#3B82F6', '#EC4899'];

function colorFor(seed) {
  if (!seed) return COLORS[0];
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = seed.charCodeAt(i) + ((hash << 5) - hash);
  return COLORS[Math.abs(hash) % COLORS.length];
}

function initialsFor(name, email) {
  const source = (name || '').trim() || (email || '').split('@')[0] || '?';
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

/**
 * Avatar with an image if avatarUrl is set, otherwise initials on a
 * deterministically-colored circle (same person always gets the same color).
 */
export default function Avatar({ name, email, avatarUrl, size = 32 }) {
  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={name || email || 'Profile'}
        style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }}
      />
    );
  }
  return (
    <div
      title={name || email}
      style={{
        width: size, height: size, borderRadius: '50%', flexShrink: 0,
        background: colorFor(email || name),
        color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontWeight: '700', fontSize: Math.round(size * 0.4), letterSpacing: '-0.02em',
      }}
    >
      {initialsFor(name, email)}
    </div>
  );
}
