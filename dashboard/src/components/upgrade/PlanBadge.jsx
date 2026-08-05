const TONES = {
  cyan: { bg: '#ECFEFF', color: '#0E7490', border: '#A5F3FC' },
  success: { bg: '#ECFDF5', color: '#15803D', border: '#A7F3D0' },
  warning: { bg: '#FFFBEB', color: '#B45309', border: '#FDE68A' },
};

export default function PlanBadge({ tone = 'cyan', children, style }) {
  const t = TONES[tone] || TONES.cyan;
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: '4px',
        fontSize: '11px', fontWeight: '700', letterSpacing: '0.01em',
        padding: '3px 10px', borderRadius: '999px',
        background: t.bg, color: t.color, border: `1px solid ${t.border}`,
        ...style,
      }}
    >
      {children}
    </span>
  );
}
