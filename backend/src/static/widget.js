/* tResolv Chat Widget v3.0
   Embed on any website:
   <script>
     window.tResolvConfig = {
       brandId:     "BRAND_UUID",
       botName:     "Luna",
       color:       "#6366F1",
       brandLabel:  "AI Support",
       position:    "bottom-right",      // "bottom-right" | "bottom-left"
       quickActions: null,               // null = defaults
     };
   </script>
   <script src="https://YOUR_API_URL/widget.js" async></script>
*/
(function () {
  'use strict';

  /* ── Config ──────────────────────────────────────────────────────── */
  var scriptEl = document.currentScript || (function () {
    var s = document.getElementsByTagName('script');
    return s[s.length - 1];
  })();

  var _cfg = window.tResolvConfig || {};

  var BRAND_ID    = _cfg.brandId    || scriptEl.getAttribute('data-brand');
  var API_BASE    = _cfg.apiBase    || scriptEl.getAttribute('data-api-base') ||
                    scriptEl.src.replace(/\/widget\.js(\?.*)?$/, '');
  // '#FFFFFF' was the old default - an invisible/broken launcher gradient
  // when a merchant hasn't set a color yet. A real brand-neutral accent is
  // the correct fallback theme until a merchant's own brands.primary_color
  // flows into this config (see dashboard Settings > Widget tab).
  var ACCENT      = _cfg.color      || scriptEl.getAttribute('data-color') || '#6366F1';
  var BOT_NAME    = _cfg.botName    || scriptEl.getAttribute('data-bot-name') || 'Luna';
  var BRAND_LABEL = _cfg.brandLabel || scriptEl.getAttribute('data-brand-label') || 'AI Support';
  var POSITION    = _cfg.position   || 'bottom-right';
  var isRight     = POSITION !== 'bottom-left';

  var DEFAULT_QUICK_ACTIONS = [
    { label: '📦 Track Order',    message: 'Where is my order?' },
    { label: '↩️ Return Item',    message: 'I want to return something' },
    { label: '💳 Refund Status',  message: 'What\'s my refund status?' },
    { label: '💬 Something else', message: null },
  ];
  var QUICK_ACTIONS = _cfg.quickActions || DEFAULT_QUICK_ACTIONS;

  if (!BRAND_ID) {
    console.warn('[tResolv] brandId is required. Set window.tResolvConfig = { brandId: "..." }');
    return;
  }

  /* ── Session ─────────────────────────────────────────────────────── */
  var SESSION_KEY = 'resolv_session_' + BRAND_ID;
  var sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = 'cs_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }
  var OPEN_KEY = 'resolv_open_' + BRAND_ID;

  /* ── State ───────────────────────────────────────────────────────── */
  var messages = [];
  var emailCaptured = sessionStorage.getItem('resolv_email_' + BRAND_ID) || null;
  var exchangeCount = 0;
  var emailPromptShown = false;
  var ratingShown = false;

  /* ── API ─────────────────────────────────────────────────────────── */
  function apiPost(path, data, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', API_BASE + path, true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onload = function () {
      try { cb(null, JSON.parse(xhr.responseText)); } catch (e) { cb(e); }
    };
    xhr.onerror = function () { cb(new Error('Network error')); };
    xhr.send(JSON.stringify(data));
  }

  function apiGet(path, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', API_BASE + path, true);
    xhr.onload = function () {
      try { cb(null, JSON.parse(xhr.responseText)); } catch (e) { cb(e); }
    };
    xhr.onerror = function () { cb(new Error('Network error')); };
    xhr.send();
  }

  /* Reads the `?stream=1` newline-delimited JSON response one line at a
   * time as bytes arrive (XHR readystate 3 / onprogress, since responseText
   * grows cumulatively). `onStatus(stage, label)` fires for every real
   * backend activity event; `cb(err, result)` fires once with the final
   * result - either the streamed "result" frame, a streamed "error" frame,
   * or (when the server short-circuited before any tool ran - rate limit,
   * plan limit, human takeover) the plain non-streamed JSON body. */
  function apiPostStream(path, data, onStatus, cb) {
    var xhr = new XMLHttpRequest();
    var sep = path.indexOf('?') === -1 ? '?' : '&';
    xhr.open('POST', API_BASE + path + sep + 'stream=1', true);
    xhr.setRequestHeader('Content-Type', 'application/json');

    var lastLen = 0;
    var buffer = '';
    var done = false;

    function finish(err, result) {
      if (done) return;
      done = true;
      cb(err, result);
    }

    function consume() {
      var text = xhr.responseText || '';
      if (text.length <= lastLen) return;
      buffer += text.slice(lastLen);
      lastLen = text.length;
      var idx;
      while ((idx = buffer.indexOf('\n')) !== -1) {
        var line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 1);
        if (!line) continue;
        var evt;
        try { evt = JSON.parse(line); } catch (e) { continue; }
        if (evt.type === 'status') {
          onStatus(evt.stage, evt.label);
        } else if (evt.type === 'result') {
          finish(null, evt);
        } else if (evt.type === 'error') {
          finish(new Error(evt.message || 'error'), null);
        }
      }
    }

    xhr.onprogress = consume;
    xhr.onload = function () {
      consume();
      if (!done) {
        // No NDJSON envelope arrived at all - plain JSON short-circuit.
        try { finish(null, JSON.parse(buffer || xhr.responseText)); }
        catch (e) { finish(e, null); }
      }
    };
    xhr.onerror = function () { finish(new Error('Network error'), null); };
    xhr.send(JSON.stringify(data));
  }

  /* ── Helpers ─────────────────────────────────────────────────────── */
  function hexToRgba(hex, alpha) {
    hex = (hex || '#6366F1').replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    var r = parseInt(hex.slice(0,2),16),
        g = parseInt(hex.slice(2,4),16),
        b = parseInt(hex.slice(4,6),16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  /* ── Brand color derivation ──────────────────────────────────────────
   * A single merchant-configured ACCENT drives every themed surface below
   * (launcher, header, bubbles, buttons, focus states) - these derive the
   * lighter/darker/contrast variants safely from it instead of each usage
   * hardcoding its own shade (the old CSS hardcoded '#a78bfa' as a second
   * gradient stop everywhere, which only ever looked right for a
   * violet/indigo ACCENT - a merchant with a red or green brand color got
   * a mismatched red-to-violet gradient throughout). */
  function clamp255(n) { return Math.max(0, Math.min(255, Math.round(n))); }
  function hexToRgbTuple(hex) {
    hex = (hex || '#6366F1').replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    return [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16), parseInt(hex.slice(4,6),16)];
  }
  function rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(function (v) {
      var h = clamp255(v).toString(16);
      return h.length === 1 ? '0' + h : h;
    }).join('');
  }
  function mixHex(hex, targetHex, weight) {
    var a = hexToRgbTuple(hex), b = hexToRgbTuple(targetHex);
    return rgbToHex(a[0] + (b[0]-a[0])*weight, a[1] + (b[1]-a[1])*weight, a[2] + (b[2]-a[2])*weight);
  }
  function shadeColor(hex, weight) { return mixHex(hex, '#000000', weight); }
  function tintColor(hex, weight) { return mixHex(hex, '#ffffff', weight); }
  // WCAG relative luminance - decides whether white or near-black text/
  // icons read cleanly on a solid ACCENT background, so a merchant picking
  // a pale/light accent color never ends up with invisible white-on-white text.
  function relativeLuminance(hex) {
    var rgb = hexToRgbTuple(hex).map(function (v) {
      v = v / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126*rgb[0] + 0.7152*rgb[1] + 0.0722*rgb[2];
  }
  function contrastText(hex) { return relativeLuminance(hex) > 0.55 ? '#0F172A' : '#FFFFFF'; }

  function fmtTime(iso) {
    var d = iso ? new Date(iso) : new Date();
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch(e) { return iso; }
  }

  function getStatusColor(status) {
    var s = (status || '').toLowerCase();
    if (s === 'fulfilled' || s === 'shipped')          return '#10B981';
    if (s === 'processing' || s === 'unfulfilled')     return '#F59E0B';
    if (s === 'cancelled')                             return '#EF4444';
    if (s === 'refunded')                              return '#3B82F6';
    return '#6B7280';
  }

  function scrollBottom() {
    msgContainer.scrollTop = msgContainer.scrollHeight;
  }

  /* ── Styles ──────────────────────────────────────────────────────── */
  var hPos = isRight ? 'right:28px;left:auto' : 'left:28px;right:auto';
  var pOrigin = isRight ? 'bottom right' : 'bottom left';

  // Every themed color below derives from these three - never a second
  // hardcoded brand color anywhere in the stylesheet.
  var ACCENT_DARK  = shadeColor(ACCENT, 0.20);   // gradients' second stop, hover/active
  var ACCENT_TEXT  = contrastText(ACCENT);       // text/icons on a solid ACCENT fill
  var ACCENT_TINT  = tintColor(ACCENT, 0.92);    // near-white wash for header/hero backgrounds

  var css =
    '@import url(\'https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&display=swap\');' +

    '#resolv-bubble,#resolv-panel,#resolv-panel *{box-sizing:border-box;font-family:\'Sora\',-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif}' +

    /* ── LAUNCHER ── */
    '#resolv-bubble{' +
      'position:fixed;bottom:28px;' + hPos + ';' +
      'width:60px;height:60px;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'border:none;cursor:pointer;' +
      'display:flex;align-items:center;justify-content:center;' +
      'box-shadow:0 0 0 0 ' + ACCENT + '44,0 8px 24px ' + ACCENT + '55,inset 0 1px 0 rgba(255,255,255,.25);' +
      'transition:transform .25s cubic-bezier(.34,1.56,.64,1),box-shadow .25s;' +
      'z-index:9999;' +
      'animation:resolv-launcher-pulse 3s ease-in-out infinite' +
    '}' +
    '@keyframes resolv-launcher-pulse{' +
      '0%,100%{box-shadow:0 0 0 0 ' + ACCENT + '44,0 8px 24px ' + ACCENT + '55,inset 0 1px 0 rgba(255,255,255,.25)}' +
      '50%{box-shadow:0 0 0 8px ' + ACCENT + '00,0 8px 24px ' + ACCENT + '55,inset 0 1px 0 rgba(255,255,255,.25)}' +
    '}' +
    '#resolv-bubble:hover{transform:scale(1.08)}' +
    '#resolv-bubble:active{transform:scale(.95)}' +
    '#resolv-bubble svg{pointer-events:none;transition:transform .3s}' +
    '#resolv-bubble.open svg{transform:rotate(45deg)}' +

    /* ── PANEL ── */
    '#resolv-panel{' +
      'position:fixed;bottom:100px;' + hPos + ';' +
      'width:380px;height:580px;' +
      'border-radius:20px;overflow:hidden;' +
      'display:flex;flex-direction:column;' +
      'z-index:9998;' +
      'transform-origin:' + pOrigin + ';' +
      'transition:opacity .3s cubic-bezier(.34,1.2,.64,1),transform .3s cubic-bezier(.34,1.2,.64,1);' +
      'opacity:0;transform:scale(.9) translateY(16px);pointer-events:none;' +
      'background:#ffffff;' +
      'border:1px solid rgba(15,23,42,.06);' +
      'box-shadow:0 24px 60px rgba(15,23,42,.16),0 4px 18px rgba(15,23,42,.08)' +
    '}' +
    '#resolv-panel.open{opacity:1;transform:scale(1) translateY(0);pointer-events:all}' +

    /* ── HEADER (soft brand-tinted hero, echoing a friendly "Hi there" home
       screen rather than a flat admin toolbar) ── */
    '#resolv-header{' +
      'position:relative;z-index:1;' +
      'padding:20px 20px 16px;' +
      'display:flex;align-items:center;gap:12px;' +
      'border-bottom:1px solid rgba(15,23,42,.06);' +
      'background:linear-gradient(180deg,' + ACCENT_TINT + ' 0%,#ffffff 100%);' +
      'flex-shrink:0' +
    '}' +
    '#resolv-header-left{display:flex;align-items:center;gap:12px}' +
    '#resolv-avatar{' +
      'width:42px;height:42px;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'display:flex;align-items:center;justify-content:center;' +
      'font-size:16px;font-weight:700;color:' + ACCENT_TEXT + ';flex-shrink:0;' +
      'box-shadow:0 4px 14px ' + ACCENT + '3d;' +
      'position:relative' +
    '}' +
    '#resolv-avatar::after{' +
      'content:\'\';position:absolute;bottom:0;right:0;' +
      'width:11px;height:11px;border-radius:50%;' +
      'background:#22c55e;border:2px solid #ffffff' +
    '}' +
    '#resolv-title{font-size:15px;font-weight:700;color:#0F172A;margin:0;letter-spacing:-.01em}' +
    '#resolv-subtitle{' +
      'font-size:11.5px;color:rgba(15,23,42,.5);margin:2px 0 0;' +
      'display:flex;align-items:center;gap:4px' +
    '}' +
    '#resolv-subtitle::before{' +
      'content:\'\';width:6px;height:6px;border-radius:50%;background:#22c55e;display:inline-block' +
    '}' +
    '#resolv-close{' +
      'margin-left:auto;width:30px;height:30px;border-radius:50%;' +
      'background:rgba(15,23,42,.04);border:1px solid rgba(15,23,42,.06);' +
      'color:rgba(15,23,42,.45);display:flex;align-items:center;justify-content:center;' +
      'cursor:pointer;font-size:15px;transition:background .15s,color .15s;flex-shrink:0' +
    '}' +
    '#resolv-close:hover{background:rgba(15,23,42,.08);color:#0F172A}' +

    /* ── MESSAGES ── */
    '#resolv-messages{' +
      'flex:1;overflow-y:auto;padding:18px 16px;' +
      'background:#ffffff;' +
      'display:flex;flex-direction:column;gap:16px;' +
      'position:relative;z-index:1;scroll-behavior:smooth' +
    '}' +
    '#resolv-messages::-webkit-scrollbar{width:4px}' +
    '#resolv-messages::-webkit-scrollbar-track{background:transparent}' +
    '#resolv-messages::-webkit-scrollbar-thumb{background:rgba(15,23,42,.12);border-radius:2px}' +
    '.resolv-msg{display:flex;flex-direction:column;max-width:85%}' +
    '.resolv-msg.user{align-self:flex-end;align-items:flex-end}' +
    '.resolv-msg.bot{align-self:flex-start;align-items:flex-start;max-width:90%}' +
    '.resolv-msg-row{display:flex;align-items:flex-end;gap:8px}' +
    '.resolv-msg.user .resolv-msg-row{flex-direction:row-reverse}' +
    '.resolv-msg-avatar{' +
      'width:26px;height:26px;flex-shrink:0;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'display:flex;align-items:center;justify-content:center;' +
      'font-size:11px;font-weight:700;color:' + ACCENT_TEXT + ';margin-bottom:2px' +
    '}' +
    '.resolv-bubble{' +
      'padding:10px 14px;border-radius:18px;' +
      'font-size:13.5px;line-height:1.55;font-weight:400;' +
      'word-break:break-word;white-space:pre-wrap' +
    '}' +
    '.resolv-msg.bot .resolv-bubble{' +
      'background:#F1F3F6;border:1px solid rgba(15,23,42,.04);' +
      'color:#0F172A;border-bottom-left-radius:5px' +
    '}' +
    '.resolv-msg.user .resolv-bubble{' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'color:' + ACCENT_TEXT + ';border-bottom-right-radius:5px;' +
      'box-shadow:0 4px 14px ' + ACCENT + '33' +
    '}' +
    '.resolv-ts{font-size:10px;color:rgba(15,23,42,.32);margin-top:3px;padding:0 4px}' +

    /* ── RESOLUTION TIMELINE (persistent, built from real backend events) ── */
    '.resolv-resolution{' +
      'display:flex;flex-direction:column;gap:10px;' +
      'padding:12px 14px;min-width:200px;' +
      'background:#F8FAFC;border:1px solid rgba(15,23,42,.06);' +
      'border-radius:16px;border-bottom-left-radius:5px' +
    '}' +
    '.resolv-resolution-header{display:flex;align-items:center;gap:8px}' +
    '.resolv-resolution-brand{font-size:11px;font-weight:700;color:rgba(15,23,42,.7)}' +
    '.resolv-resolution-badge{' +
      'font-size:9px;font-weight:600;padding:2px 8px;border-radius:999px;' +
      'background:rgba(16,185,129,.12);color:#059669' +
    '}' +
    '.resolv-resolution-steps{display:flex;flex-direction:column;gap:8px}' +
    '.resolv-resolution-step{display:flex;align-items:flex-start;gap:8px}' +
    '.resolv-resolution-dot{' +
      'width:15px;height:15px;flex-shrink:0;margin-top:1px;border-radius:50%;' +
      'display:flex;align-items:center;justify-content:center' +
    '}' +
    '.resolv-resolution-dot.complete{background:#10B981;color:#fff;font-size:9px;line-height:1}' +
    '.resolv-resolution-dot.active{border:2px solid ' + ACCENT + ';background:' + hexToRgba(ACCENT, 0.12) + ';position:relative}' +
    '.resolv-resolution-dot.active::after{' +
      'content:"";position:absolute;inset:-3px;border-radius:50%;border:1.5px solid ' + ACCENT + ';' +
      'animation:resolv-pulse 1.3s ease-in-out infinite' +
    '}' +
    '@keyframes resolv-pulse{0%{transform:scale(1);opacity:.6}70%{transform:scale(1.9);opacity:0}100%{opacity:0}}' +
    '.resolv-resolution-label{font-size:12px;font-weight:600;color:rgba(15,23,42,.8);line-height:1.4}' +
    '.resolv-resolution-sub{font-size:11px;color:#059669;margin-top:2px;line-height:1.4}' +

    /* ── ORDER CARD ── */
    '@keyframes resolv-slide-up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}' +
    '.resolv-order-card{' +
      'background:#ffffff;' +
      'border:1px solid rgba(15,23,42,.08);' +
      'border-radius:14px;padding:14px 16px;margin-bottom:8px;width:100%;' +
      'box-shadow:0 1px 3px rgba(15,23,42,.04);' +
      'animation:resolv-slide-up .25s ease-out' +
    '}' +
    '.resolv-order-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px}' +
    '.resolv-order-title{font-size:13px;font-weight:600;color:#0F172A;flex:1}' +
    '.resolv-status-badge{' +
      'font-size:10px;letter-spacing:.08em;text-transform:uppercase;' +
      'border-radius:20px;padding:2px 8px;font-weight:600;flex-shrink:0' +
    '}' +
    '.resolv-order-divider{height:1px;background:rgba(15,23,42,.06);margin:0 0 10px}' +
    '.resolv-order-item{font-size:13px;color:rgba(15,23,42,.8);font-weight:500;margin-bottom:2px}' +
    '.resolv-order-meta{font-size:12px;color:rgba(15,23,42,.4);margin-bottom:4px}' +
    '.resolv-order-cancelled-info{margin-top:8px;font-size:12px;color:rgba(15,23,42,.5)}' +
    '.resolv-order-cancelled-info div{margin-top:3px;display:flex;align-items:center;gap:4px}' +
    '.resolv-order-tracking{margin-top:10px;font-size:12px;color:rgba(15,23,42,.5);display:flex;align-items:center;gap:4px}' +
    '.resolv-order-tracking a{color:' + ACCENT_DARK + ';text-decoration:none}' +
    '.resolv-order-tracking a:hover{text-decoration:underline}' +

    /* ── ACTION RESULT CARD ── */
    '.resolv-action-card{border-radius:14px;padding:14px 16px;margin-bottom:8px;width:100%;box-shadow:0 1px 3px rgba(15,23,42,.04);animation:resolv-slide-up .25s ease-out}' +
    '.resolv-action-card.refund{background:rgba(16,185,129,.05);border:1px solid rgba(16,185,129,.18)}' +
    '.resolv-action-card.cancel{background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.18)}' +
    '.resolv-action-card.address{background:' + hexToRgba(ACCENT, 0.05) + ';border:1px solid ' + hexToRgba(ACCENT, 0.18) + '}' +
    '.resolv-action-card.restore{background:rgba(245,158,11,.05);border:1px solid rgba(245,158,11,.18)}' +
    '.resolv-action-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;gap:8px}' +
    '.resolv-action-title{font-size:13px;font-weight:600;color:#0F172A;flex:1}' +
    '.resolv-action-badge{font-size:10px;letter-spacing:.06em;text-transform:uppercase;border-radius:20px;padding:2px 8px;font-weight:600;flex-shrink:0}' +
    '.resolv-action-card.refund  .resolv-action-badge{background:rgba(16,185,129,.14);color:#059669;border:1px solid rgba(16,185,129,.3)}' +
    '.resolv-action-card.cancel  .resolv-action-badge{background:rgba(239,68,68,.14);color:#dc2626;border:1px solid rgba(239,68,68,.3)}' +
    '.resolv-action-card.address .resolv-action-badge{background:' + hexToRgba(ACCENT, 0.14) + ';color:' + ACCENT_DARK + ';border:1px solid ' + hexToRgba(ACCENT, 0.3) + '}' +
    '.resolv-action-card.restore .resolv-action-badge{background:rgba(245,158,11,.14);color:#b45309;border:1px solid rgba(245,158,11,.3)}' +
    '.resolv-action-divider{height:1px;background:rgba(15,23,42,.06);margin:0 0 8px}' +
    '.resolv-action-detail{font-size:12px;color:rgba(15,23,42,.55);margin-bottom:3px}' +

    /* ── SATISFACTION RATING ── */
    '.resolv-rating{' +
      'background:#F8FAFC;border:1px solid rgba(15,23,42,.06);' +
      'border-radius:14px;padding:12px 14px;margin-top:6px;width:100%;' +
      'animation:resolv-slide-up .3s ease-out' +
    '}' +
    '.resolv-rating p{font-size:12px;color:rgba(15,23,42,.55);margin:0 0 8px}' +
    '.resolv-rating-btns{display:flex;gap:8px}' +
    '.resolv-rating-btn{' +
      'padding:5px 14px;border-radius:20px;border:1px solid rgba(15,23,42,.12);' +
      'background:#ffffff;color:rgba(15,23,42,.65);' +
      'font-size:12px;cursor:pointer;transition:background .15s,border-color .15s;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '.resolv-rating-btn:hover{background:#F1F3F6;border-color:rgba(15,23,42,.2)}' +

    /* ── SUGGESTIONS (light pill cards, closer to a friendly quick-reply
       row than a dark accent-outlined chip) ── */
    '#resolv-suggestions{padding:0 16px 12px;display:flex;gap:6px;flex-wrap:wrap;position:relative;z-index:1}' +
    '.resolv-suggestion{' +
      'padding:6px 12px;border:1px solid rgba(15,23,42,.1);' +
      'border-radius:20px;background:#ffffff;' +
      'color:' + ACCENT_DARK + ';font-size:12px;font-weight:500;cursor:pointer;' +
      'box-shadow:0 1px 2px rgba(15,23,42,.04);' +
      'transition:background .15s,border-color .15s;white-space:nowrap;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '.resolv-suggestion:hover{background:' + hexToRgba(ACCENT, 0.08) + ';border-color:' + hexToRgba(ACCENT, 0.35) + '}' +

    /* ── EMAIL BAR ── */
    '#resolv-email-bar{' +
      'padding:10px 16px;background:#F8FAFC;' +
      'border-top:1px solid rgba(15,23,42,.06);' +
      'font-size:12px;color:rgba(15,23,42,.6);' +
      'display:flex;flex-direction:column;gap:8px;position:relative;z-index:1' +
    '}' +
    '#resolv-email-bar p{margin:0;color:rgba(15,23,42,.6)}' +
    '#resolv-email-row{display:flex;gap:6px}' +
    '#resolv-email-input{' +
      'flex:1;padding:7px 10px;background:#ffffff!important;' +
      'border:1px solid rgba(15,23,42,.14);border-radius:8px;' +
      'color:#0F172A!important;font-size:12px;outline:none;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '#resolv-email-input:focus{border-color:' + ACCENT + ';box-shadow:0 0 0 3px ' + hexToRgba(ACCENT, 0.15) + '}' +
    '#resolv-email-submit{' +
      'padding:7px 12px;background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'color:' + ACCENT_TEXT + ';border:none;border-radius:8px;font-size:12px;cursor:pointer;font-weight:600' +
    '}' +
    '#resolv-email-skip{background:none;border:none;color:rgba(15,23,42,.4);font-size:11px;cursor:pointer;padding:0;text-decoration:underline;align-self:center}' +

    /* ── FOOTER ── */
    '#resolv-footer{' +
      'padding:12px 14px 14px;' +
      'border-top:1px solid rgba(15,23,42,.06);' +
      'background:#ffffff;' +
      'display:flex;align-items:center;gap:10px;flex-shrink:0;' +
      'position:relative;z-index:1' +
    '}' +
    '#resolv-input{' +
      'flex:1;background:#F8FAFC!important;' +
      'border:1px solid rgba(15,23,42,.1);border-radius:14px;' +
      'padding:10px 14px;font-size:13.5px;font-weight:400;' +
      'color:#0F172A!important;' +
      'font-family:\'Sora\',-apple-system,sans-serif;' +
      'outline:none;transition:border-color .2s,background .2s,box-shadow .2s;' +
      'resize:none;min-height:40px;max-height:96px;line-height:1.4' +
    '}' +
    /* !important above: this widget has no Shadow DOM isolation, so a host
       theme's own global input/textarea color rule (common on Shopify
       themes, often !important itself) can otherwise win the cascade and
       make typed text unreadable - confirmed live on slymode1.bumpa.shop. */
    '#resolv-input::placeholder{color:rgba(15,23,42,.32)!important}' +
    '#resolv-input:focus{border-color:' + ACCENT + ';background:#ffffff;box-shadow:0 0 0 3px ' + hexToRgba(ACCENT, 0.15) + '}' +
    '#resolv-send{' +
      'width:38px;height:38px;flex-shrink:0;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,' + ACCENT_DARK + ' 100%);' +
      'border:none;cursor:pointer;' +
      'display:flex;align-items:center;justify-content:center;' +
      'transition:transform .2s,opacity .2s;' +
      'box-shadow:0 4px 12px ' + ACCENT + '4d' +
    '}' +
    '#resolv-send:hover{transform:scale(1.08)}' +
    '#resolv-send:active{transform:scale(.94)}' +
    '#resolv-send:disabled{opacity:.35;cursor:default;transform:none}' +
    '#resolv-send svg{pointer-events:none}' +

    /* ── BADGE ── */
    '#resolv-badge{' +
      'position:absolute;top:-2px;right:-2px;' +
      'width:18px;height:18px;border-radius:50%;' +
      'background:#ef4444;border:2px solid #ffffff;' +
      'font-size:10px;font-weight:700;color:#fff;' +
      'display:none;align-items:center;justify-content:center' +
    '}' +

    /* ── POWERED BY ── */
    '#resolv-powered{' +
      'text-align:center;font-size:10px;color:rgba(15,23,42,.3);' +
      'padding:0 0 10px;position:relative;z-index:1;letter-spacing:.03em;background:#ffffff' +
    '}' +
    '#resolv-powered a{color:rgba(15,23,42,.4);text-decoration:none}' +

    /* ── MOBILE ── */
    '@media(max-width:480px){' +
      '#resolv-panel{width:100vw;height:100dvh;bottom:0;right:0;left:0;border-radius:0}' +
      '#resolv-bubble{bottom:16px;' + (isRight ? 'right:16px' : 'left:16px') + '}' +
    '}';

  var styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  /* ── Bubble ──────────────────────────────────────────────────────── */
  var bubble = document.createElement('button');
  bubble.id = 'resolv-bubble';
  bubble.setAttribute('aria-label', 'Open chat');
  bubble.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="' + ACCENT_TEXT + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' +
    '</svg>' +
    '<div id="resolv-badge"></div>';

  /* ── Panel ───────────────────────────────────────────────────────── */
  var panel = document.createElement('div');
  panel.id = 'resolv-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Chat with ' + BOT_NAME);
  panel.innerHTML =
    '<div id="resolv-header">' +
      '<div id="resolv-header-left">' +
        '<div id="resolv-avatar">' + BOT_NAME.charAt(0) + '</div>' +
        '<div>' +
          '<div id="resolv-title">' + BOT_NAME + '</div>' +
          '<div id="resolv-subtitle">' + BRAND_LABEL + '</div>' +
        '</div>' +
      '</div>' +
      '<button id="resolv-close" aria-label="Close chat">✕</button>' +
    '</div>' +
    '<div id="resolv-messages"></div>' +
    '<div id="resolv-suggestions"></div>' +
    '<div id="resolv-email-bar" style="display:none">' +
      '<p>Share your email so we can follow up if needed</p>' +
      '<div id="resolv-email-row">' +
        '<input id="resolv-email-input" type="email" placeholder="your@email.com" />' +
        '<button id="resolv-email-submit">Submit</button>' +
      '</div>' +
      '<button id="resolv-email-skip">Skip</button>' +
    '</div>' +
    '<div id="resolv-footer">' +
      '<textarea id="resolv-input" rows="1" placeholder="Message ' + BOT_NAME + '…" maxlength="1000"></textarea>' +
      '<button id="resolv-send" disabled aria-label="Send">' +
        '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">' +
          '<path d="M17.5 10L3.5 3.5L6.5 10L3.5 16.5L17.5 10Z" fill="' + ACCENT_TEXT + '" stroke="' + ACCENT_TEXT + '" stroke-width="1.2" stroke-linejoin="round"/>' +
        '</svg>' +
      '</button>' +
    '</div>' +
    '<div id="resolv-powered">Powered by <a href="https://tresolv.online" target="_blank" rel="noreferrer">tResolv</a></div>';

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  /* ── DOM refs ────────────────────────────────────────────────────── */
  var msgContainer = panel.querySelector('#resolv-messages');
  var suggestBar   = panel.querySelector('#resolv-suggestions');
  var emailBar     = panel.querySelector('#resolv-email-bar');
  var emailInput   = panel.querySelector('#resolv-email-input');
  var emailSubmit  = panel.querySelector('#resolv-email-submit');
  var emailSkip    = panel.querySelector('#resolv-email-skip');
  var input        = panel.querySelector('#resolv-input');
  var sendBtn      = panel.querySelector('#resolv-send');
  var closeBtn     = panel.querySelector('#resolv-close');

  /* ── Simple message renderer (used for history restore) ─────────── */
  function renderMsg(role, text, ts) {
    var isUser = role === 'user';
    var wrap = document.createElement('div');
    wrap.className = 'resolv-msg ' + (isUser ? 'user' : 'bot');

    var row = document.createElement('div');
    row.className = 'resolv-msg-row';

    if (!isUser) {
      var av = document.createElement('div');
      av.className = 'resolv-msg-avatar';
      av.textContent = BOT_NAME.charAt(0);
      row.appendChild(av);
    }

    var bub = document.createElement('div');
    bub.className = 'resolv-bubble';
    bub.textContent = text;
    row.appendChild(bub);
    wrap.appendChild(row);

    var stamp = document.createElement('div');
    stamp.className = 'resolv-ts';
    stamp.textContent = fmtTime(ts);
    wrap.appendChild(stamp);

    msgContainer.appendChild(wrap);
    return wrap;
  }

  /* ── Resolution timeline ──────────────────────────────────────────
   * A persistent, accumulating timeline built ONLY from real backend
   * `status` events (see apiPostStream below) - never a fake/timed
   * animation. Steps never disappear or get replaced: each new stage
   * freezes the previous step as complete and appends a new active one.
   * A handful of stages are the real "confirmation" of the step right
   * before them (a lookup, then a found/verified result for that same
   * lookup) - those are shown as a subtext line under that same step
   * rather than as their own row, matching how the backend actually
   * sequences them. */
  var resolutionEl = null;
  var resolutionStepsEl = null;
  var resolutionSteps = [];
  var RESOLUTION_CONFIRMATION_STAGES = { order_found: 1, product_found: 1, policy_verified: 1 };

  function startResolutionTimeline() {
    resolutionSteps = [];
    var wrap = document.createElement('div');
    wrap.className = 'resolv-msg bot';
    var row = document.createElement('div');
    row.className = 'resolv-msg-row';
    var av = document.createElement('div');
    av.className = 'resolv-msg-avatar';
    av.textContent = BOT_NAME.charAt(0);
    var card = document.createElement('div');
    card.className = 'resolv-resolution';

    var header = document.createElement('div');
    header.className = 'resolv-resolution-header';
    var brand = document.createElement('span');
    brand.className = 'resolv-resolution-brand';
    brand.textContent = 'tResolv';
    var badge = document.createElement('span');
    badge.className = 'resolv-resolution-badge';
    badge.textContent = 'Resolving';
    header.appendChild(brand);
    header.appendChild(badge);

    var stepsWrap = document.createElement('div');
    stepsWrap.className = 'resolv-resolution-steps';

    card.appendChild(header);
    card.appendChild(stepsWrap);
    row.appendChild(av);
    row.appendChild(card);
    wrap.appendChild(row);
    msgContainer.appendChild(wrap);

    resolutionEl = wrap;
    resolutionStepsEl = stepsWrap;
    scrollBottom();
  }

  function renderResolutionStep(step) {
    if (!step.el) {
      step.el = document.createElement('div');
      step.el.className = 'resolv-resolution-step';
      var dot = document.createElement('span');
      dot.className = 'resolv-resolution-dot';
      var text = document.createElement('div');
      var label = document.createElement('div');
      label.className = 'resolv-resolution-label';
      var sub = document.createElement('div');
      sub.className = 'resolv-resolution-sub';
      sub.style.display = 'none';
      text.appendChild(label);
      text.appendChild(sub);
      step.el.appendChild(dot);
      step.el.appendChild(text);
      resolutionStepsEl.appendChild(step.el);
      step.dotEl = dot;
      step.labelEl = label;
      step.subEl = sub;
    }
    step.dotEl.className = 'resolv-resolution-dot ' + step.status;
    // An SVG check (not the '✓' glyph) - text-character checkmarks render
    // visibly off-center in flex-centered circles because most fonts give
    // the glyph asymmetric side-bearings (confirmed live: noticeably
    // right/low-shifted in Sora). A stroke-based SVG has no such bias and
    // centers exactly regardless of font/browser.
    step.dotEl.innerHTML = step.status === 'complete'
      ? '<svg width="8" height="8" viewBox="0 0 16 16" fill="none"><path d="M13.5 4.5L6.5 12L2.5 8.2" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>'
      : '';
    step.labelEl.textContent = step.label;
    if (step.subLabel) {
      step.subEl.textContent = step.subLabel;
      step.subEl.style.display = '';
    }
  }

  // Only called when a real backend status event arrives - never on a
  // timer, never a guessed stage.
  function addResolutionEvent(stage, label) {
    if (!resolutionStepsEl) return;

    if (RESOLUTION_CONFIRMATION_STAGES[stage] && resolutionSteps.length) {
      var prev = resolutionSteps[resolutionSteps.length - 1];
      prev.status = 'complete';
      prev.subLabel = label;
      renderResolutionStep(prev);
      scrollBottom();
      return;
    }

    if (resolutionSteps.length) {
      var last = resolutionSteps[resolutionSteps.length - 1];
      if (last.status !== 'complete') {
        last.status = 'complete';
        renderResolutionStep(last);
      }
    }
    var step = { stage: stage, label: label, subLabel: null, status: 'active', el: null };
    resolutionSteps.push(step);
    renderResolutionStep(step);
    scrollBottom();
  }

  // Freezes whatever's left active as complete and leaves the timeline
  // permanently visible in the transcript - it is not a transient typing
  // indicator, the customer can scroll back and see how the request was
  // actually resolved.
  function finishResolutionTimeline() {
    for (var i = 0; i < resolutionSteps.length; i++) {
      if (resolutionSteps[i].status !== 'complete') {
        resolutionSteps[i].status = 'complete';
        renderResolutionStep(resolutionSteps[i]);
      }
    }
    resolutionEl = null;
    resolutionStepsEl = null;
    resolutionSteps = [];
  }

  // No real status event ever arrived for this turn (e.g. an early-exit
  // plain-JSON response - rate limit, entitlement, quota, human takeover)
  // - remove the empty shell rather than leave an empty "Resolving" card.
  function abortResolutionTimelineIfEmpty() {
    if (resolutionEl && resolutionSteps.length === 0) {
      resolutionEl.remove();
    }
    resolutionEl = null;
    resolutionStepsEl = null;
    resolutionSteps = [];
  }

  /* ── Order card renderer ─────────────────────────────────────────── */
  function renderOrderCard(orderData, container) {
    var status   = (orderData.status || 'processing').toLowerCase();
    var color    = getStatusColor(status);
    var currency = orderData.currency || 'Rs';

    var card = document.createElement('div');
    card.className = 'resolv-order-card';
    card.style.borderLeft = '3px solid ' + color;

    var header = document.createElement('div');
    header.className = 'resolv-order-header';

    var title = document.createElement('div');
    title.className = 'resolv-order-title';
    // agent returns camelCase; accept both forms
    var orderNum = orderData.orderNumber || orderData.order_number || '';
    title.textContent = '🛒  Order #' + orderNum;

    var badge = document.createElement('span');
    badge.className = 'resolv-status-badge';
    badge.textContent = status.toUpperCase();
    badge.style.cssText = 'background:' + hexToRgba(color, 0.15) + ';color:' + color + ';border:1px solid ' + hexToRgba(color, 0.3);

    header.appendChild(title);
    header.appendChild(badge);
    card.appendChild(header);

    var divider = document.createElement('div');
    divider.className = 'resolv-order-divider';
    card.appendChild(divider);

    (orderData.items || []).forEach(function (item) {
      var nameEl = document.createElement('div');
      nameEl.className = 'resolv-order-item';
      nameEl.textContent = item.name + (item.variant ? ' (' + item.variant + ')' : '');
      card.appendChild(nameEl);

      var metaEl = document.createElement('div');
      metaEl.className = 'resolv-order-meta';
      metaEl.textContent = currency + ' ' + item.price;
      card.appendChild(metaEl);
    });

    if (status === 'cancelled') {
      var cancelInfo = document.createElement('div');
      cancelInfo.className = 'resolv-order-cancelled-info';
      var cancelledAt = orderData.cancelledAt || orderData.cancelled_at;
      if (cancelledAt) {
        var d1 = document.createElement('div');
        d1.innerHTML = '✕ Cancelled on ' + fmtDate(cancelledAt);
        cancelInfo.appendChild(d1);
      }
      var payStatus = orderData.paymentStatus || orderData.payment_status;
      if (payStatus === 'paid') {
        var d2 = document.createElement('div');
        d2.style.color = '#10B981';
        d2.innerHTML = '✓ Refund in progress';
        cancelInfo.appendChild(d2);
      }
      card.appendChild(cancelInfo);
    }

    if (orderData.tracking_url) {
      var trackEl = document.createElement('div');
      trackEl.className = 'resolv-order-tracking';
      trackEl.innerHTML = '📦 Tracking available <a href="' + orderData.tracking_url + '" target="_blank" rel="noreferrer">↗</a>';
      card.appendChild(trackEl);
    }

    container.appendChild(card);
  }

  /* ── Action result card renderer ────────────────────────────────── */
  function renderActionCard(actionResult, container) {
    var META = {
      'refund_staged':   { cls: 'refund',  icon: '✓',  title: 'Refund Requested',        badge: 'STAGED' },
      'cancel_staged':   { cls: 'cancel',  icon: '✕',  title: 'Cancellation Requested',  badge: 'STAGED' },
      'address_updated': { cls: 'address', icon: '📍', title: 'Address Updated',    badge: 'DONE'   },
      'restore_staged':  { cls: 'restore', icon: '📦', title: 'Reship Requested',   badge: 'STAGED' },
    };
    var meta = META[actionResult.type] || { cls: 'address', icon: '✓', title: 'Action Taken', badge: 'DONE' };

    var card = document.createElement('div');
    card.className = 'resolv-action-card ' + meta.cls;

    var header = document.createElement('div');
    header.className = 'resolv-action-header';

    var title = document.createElement('div');
    title.className = 'resolv-action-title';
    title.textContent = meta.icon + '  ' + meta.title;

    var badge = document.createElement('span');
    badge.className = 'resolv-action-badge';
    badge.textContent = meta.badge;

    header.appendChild(title);
    header.appendChild(badge);
    card.appendChild(header);

    var divider = document.createElement('div');
    divider.className = 'resolv-action-divider';
    card.appendChild(divider);

    if (actionResult.type === 'refund_staged' && actionResult.amount) {
      var d1 = document.createElement('div');
      d1.className = 'resolv-action-detail';
      d1.textContent = 'Rs ' + actionResult.amount + ' → back to original method';
      card.appendChild(d1);
    }
    if ((actionResult.type === 'cancel_staged' || actionResult.type === 'refund_staged') && actionResult.order_number) {
      var d2 = document.createElement('div');
      d2.className = 'resolv-action-detail';
      d2.textContent = 'Order #' + actionResult.order_number;
      card.appendChild(d2);
    }
    if (actionResult.type === 'address_updated' && actionResult.new_address) {
      var d3 = document.createElement('div');
      d3.className = 'resolv-action-detail';
      d3.textContent = actionResult.new_address;
      card.appendChild(d3);
    }

    var sub = document.createElement('div');
    sub.className = 'resolv-action-detail';
    sub.style.marginTop = '2px';
    sub.textContent = actionResult.type === 'address_updated' ? 'Updated in Shopify' : 'Awaiting merchant approval';
    card.appendChild(sub);

    container.appendChild(card);
  }

  /* ── Satisfaction rating ─────────────────────────────────────────── */
  function renderSatisfactionRating(container) {
    if (ratingShown) return;
    ratingShown = true;

    var wrap = document.createElement('div');
    wrap.className = 'resolv-rating';

    var p = document.createElement('p');
    p.textContent = 'Was that helpful?';
    wrap.appendChild(p);

    var btns = document.createElement('div');
    btns.className = 'resolv-rating-btns';

    ['👍 Yes, thanks', '👎 No'].forEach(function (label, i) {
      var btn = document.createElement('button');
      btn.className = 'resolv-rating-btn';
      btn.textContent = label;
      btn.onclick = function () {
        apiPost('/api/v2/widget/feedback', {
          session_id: sessionId,
          rating: i === 0 ? 'positive' : 'negative',
        }, function () {});
        btns.style.display = 'none';
        p.textContent = 'Thanks for your feedback!';
        p.style.textAlign = 'center';
        setTimeout(function () {
          wrap.style.opacity = '0';
          wrap.style.transition = 'opacity .5s';
          setTimeout(function () { wrap.remove(); }, 500);
        }, 2000);
      };
      btns.appendChild(btn);
    });

    wrap.appendChild(btns);
    container.appendChild(wrap);
  }

  /* ── Suggestions ─────────────────────────────────────────────────── */
  function clearSuggestions() { suggestBar.innerHTML = ''; }

  function showSuggestions(items) {
    clearSuggestions();
    items.forEach(function (item) {
      var label   = typeof item === 'string' ? item : item.label;
      var message = typeof item === 'string' ? item : (item.message || null);
      var btn = document.createElement('button');
      btn.className = 'resolv-suggestion';
      btn.textContent = label;
      btn.onclick = function () {
        clearSuggestions();
        if (message) sendMessage(message);
        else input.focus();
      };
      suggestBar.appendChild(btn);
    });
  }

  function maybeShowEmailCapture() {
    if (emailCaptured || emailPromptShown || exchangeCount < 3) return;
    emailPromptShown = true;
    emailBar.style.display = 'flex';
  }

  /* ── Send ────────────────────────────────────────────────────────── */
  var sending = false;

  function sendMessage(text) {
    if (sending || !text.trim()) return;
    sending = true;
    var userText = text.trim();
    input.value = '';
    sendBtn.disabled = true;
    clearSuggestions();

    var now = new Date().toISOString();
    messages.push({ role: 'user', content: userText, created_at: now });
    renderMsg('user', userText, now);
    startResolutionTimeline();
    scrollBottom();

    apiPostStream('/api/v2/widget/chat', {
      brand_id:       BRAND_ID,
      session_id:     sessionId,
      message:        userText,
      customer_email: emailCaptured || undefined,
    }, function (stage, label) {
      addResolutionEvent(stage, label);
    }, function (err, data) {
      if (resolutionSteps.length) { finishResolutionTimeline(); }
      else { abortResolutionTimelineIfEmpty(); }
      sending = false;

      if (err || !data || data.detail) {
        renderMsg('bot', 'Sorry, I had a little trouble there. Please try again!', new Date().toISOString());
      } else {
        var replyTs = new Date().toISOString();
        messages.push({ role: 'assistant', content: data.reply, created_at: replyTs });

        var botWrap = document.createElement('div');
        botWrap.className = 'resolv-msg bot';

        // Order card (above text bubble)
        if (data.order_data) {
          renderOrderCard(data.order_data, botWrap);
        }

        // Action result card (above text bubble)
        if (data.action_result) {
          renderActionCard(data.action_result, botWrap);
        }

        // Text bubble
        var row = document.createElement('div');
        row.className = 'resolv-msg-row';
        var av = document.createElement('div');
        av.className = 'resolv-msg-avatar';
        av.textContent = BOT_NAME.charAt(0);
        var bub = document.createElement('div');
        bub.className = 'resolv-bubble';
        bub.textContent = data.reply;
        row.appendChild(av);
        row.appendChild(bub);
        botWrap.appendChild(row);

        // Satisfaction rating (after resolution)
        if (data.resolution_complete) {
          renderSatisfactionRating(botWrap);
        }

        var stamp = document.createElement('div');
        stamp.className = 'resolv-ts';
        stamp.textContent = fmtTime(replyTs);
        botWrap.appendChild(stamp);

        msgContainer.appendChild(botWrap);
        exchangeCount++;

        if (data.suggested_actions && data.suggested_actions.length) {
          showSuggestions(data.suggested_actions);
        }
        maybeShowEmailCapture();
      }

      sendBtn.disabled = !input.value.trim();
      scrollBottom();
    });
  }

  /* ── Email capture ───────────────────────────────────────────────── */
  emailSubmit.onclick = function () {
    var val = emailInput.value.trim();
    if (!val || !val.includes('@')) return;
    emailCaptured = val;
    sessionStorage.setItem('resolv_email_' + BRAND_ID, val);
    emailBar.style.display = 'none';
    apiPost('/api/v2/widget/chat/' + sessionId + '/email', { email: val }, function () {});
  };
  emailSkip.onclick = function () {
    emailBar.style.display = 'none';
    emailPromptShown = true;
  };
  emailInput.onkeydown = function (e) {
    if (e.key === 'Enter') emailSubmit.click();
  };

  /* ── Input auto-resize ───────────────────────────────────────────── */
  input.oninput = function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 96) + 'px';
    sendBtn.disabled = !this.value.trim();
  };
  input.onkeydown = function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) sendMessage(this.value.trim());
    }
  };
  sendBtn.onclick = function () { sendMessage(input.value.trim()); };

  /* ── Open / Close ────────────────────────────────────────────────── */
  var isOpen = sessionStorage.getItem(OPEN_KEY) === '1';

  function openPanel() {
    isOpen = true;
    sessionStorage.setItem(OPEN_KEY, '1');
    panel.classList.add('open');
    bubble.classList.add('open');
    bubble.setAttribute('aria-expanded', 'true');
    bubble.setAttribute('aria-label', 'Close chat');
    input.focus();
    scrollBottom();
  }

  function closePanel() {
    isOpen = false;
    sessionStorage.removeItem(OPEN_KEY);
    panel.classList.remove('open');
    bubble.classList.remove('open');
    bubble.setAttribute('aria-expanded', 'false');
    bubble.setAttribute('aria-label', 'Open chat');
  }

  bubble.onclick = function () { isOpen ? closePanel() : openPanel(); };
  closeBtn.onclick = closePanel;

  /* ── Init: restore history or show welcome ───────────────────────── */
  function init() {
    apiGet('/api/v2/widget/chat/' + sessionId, function (err, data) {
      if (!err && data && data.messages && data.messages.length) {
        data.messages.forEach(function (m) {
          renderMsg(m.role === 'user' ? 'user' : 'bot', m.content, m.created_at);
          messages.push(m);
        });
        exchangeCount = Math.floor(data.messages.length / 2);
        if (data.customer_email) emailCaptured = data.customer_email;
      } else {
        var greeting = _cfg.greeting ||
          ('Hey! I’m ' + BOT_NAME + ', your AI support assistant. How can I help you today?\n\nAsk me about your orders, returns, or anything else.');
        renderMsg('bot', greeting, new Date().toISOString());
        showSuggestions(QUICK_ACTIONS);
      }
      scrollBottom();
    });

    if (isOpen) openPanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
