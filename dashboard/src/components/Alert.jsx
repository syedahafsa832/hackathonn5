import { useEffect } from 'react';

const VARIANTS = {
  success: { bg: '#ECFDF5', border: '#A7F3D0', color: '#16A34A', icon: '✓' },
  error: { bg: '#FEF2F2', border: '#FECACA', color: '#EF4444', icon: '✗' },
};

/**
 * Single reusable success/error message component. Replaces four different
 * ad-hoc patterns that had accumulated across the app (window.alert(),
 * inline styled banners, an auto-dismissing span, and an embedded result
 * box) with one visual treatment. Purely presentational — callers own
 * whatever state controls when `children` is truthy; pass `onDismiss` +
 * `autoDismissMs` to replace a window.alert()'s "click OK to go away".
 */
export default function Alert({ variant = 'error', children, onDismiss, autoDismissMs, style }) {
  const v = VARIANTS[variant] || VARIANTS.error;

  useEffect(() => {
    if (!children || !autoDismissMs || !onDismiss) return;
    const timer = setTimeout(onDismiss, autoDismissMs);
    return () => clearTimeout(timer);
  }, [children, autoDismissMs, onDismiss]);

  if (!children) return null;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'flex-start',
      gap: '8px',
      padding: '10px 14px',
      borderRadius: '6px',
      background: v.bg,
      border: `1px solid ${v.border}`,
      color: v.color,
      fontSize: '13px',
      fontWeight: '500',
      lineHeight: '1.4',
      ...style,
    }}>
      <span style={{ flexShrink: 0 }}>{v.icon}</span>
      <span style={{ flex: 1 }}>{children}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: '14px', padding: 0, opacity: 0.6, lineHeight: 1, flexShrink: 0 }}
        >
          ×
        </button>
      )}
    </div>
  );
}
