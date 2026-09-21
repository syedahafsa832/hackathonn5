// Derives the small palette every Luna chat surface themes off of, from a
// single merchant-configured accent color. Shared logic (kept deliberately
// tiny/dependency-free) so a brand color always produces a coherent look
// instead of each component hardcoding its own second gradient stop.

function clamp255(n) {
  return Math.max(0, Math.min(255, Math.round(n)));
}

function hexToRgbTuple(hex) {
  hex = (hex || '#6366F1').replace('#', '');
  if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
  return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map((v) => {
    const h = clamp255(v).toString(16);
    return h.length === 1 ? '0' + h : h;
  }).join('');
}

function mixHex(hex, targetHex, weight) {
  const a = hexToRgbTuple(hex);
  const b = hexToRgbTuple(targetHex);
  return rgbToHex(a[0] + (b[0] - a[0]) * weight, a[1] + (b[1] - a[1]) * weight, a[2] + (b[2] - a[2]) * weight);
}

export function shadeColor(hex, weight) {
  return mixHex(hex, '#000000', weight);
}

export function tintColor(hex, weight) {
  return mixHex(hex, '#ffffff', weight);
}

export function hexToRgba(hex, alpha) {
  const [r, g, b] = hexToRgbTuple(hex);
  return `rgba(${r},${g},${b},${alpha})`;
}

// WCAG relative luminance - decides whether white or near-black text/icons
// read cleanly on a solid accent fill, so a pale/light brand color never
// produces invisible white-on-white text.
function relativeLuminance(hex) {
  const rgb = hexToRgbTuple(hex).map((v) => {
    v = v / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

export function contrastText(hex) {
  return relativeLuminance(hex) > 0.55 ? '#0F172A' : '#FFFFFF';
}

/** The full derived palette a chat surface needs from one accent color. */
export function deriveWidgetTheme(accentColor) {
  const accent = accentColor || '#6366F1';
  return {
    accent,
    accentDark: shadeColor(accent, 0.2),
    accentText: contrastText(accent),
    accentTint: tintColor(accent, 0.92),
  };
}
