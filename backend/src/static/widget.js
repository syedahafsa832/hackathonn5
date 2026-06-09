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
  var ACCENT      = _cfg.color      || scriptEl.getAttribute('data-color') || '#FFFFFF';
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

  /* ── Helpers ─────────────────────────────────────────────────────── */
  function hexToRgba(hex, alpha) {
    hex = (hex || '#6366F1').replace('#', '');
    if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    var r = parseInt(hex.slice(0,2),16),
        g = parseInt(hex.slice(2,4),16),
        b = parseInt(hex.slice(4,6),16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

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

  var css =
    '@import url(\'https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600&display=swap\');' +

    '#resolv-bubble,#resolv-panel,#resolv-panel *{box-sizing:border-box;font-family:\'Sora\',-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif}' +

    /* ── LAUNCHER ── */
    '#resolv-bubble{' +
      'position:fixed;bottom:28px;' + hPos + ';' +
      'width:60px;height:60px;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'border:none;cursor:pointer;' +
      'display:flex;align-items:center;justify-content:center;' +
      'box-shadow:0 0 0 0 ' + ACCENT + '44,0 8px 32px ' + ACCENT + '66,inset 0 1px 0 rgba(255,255,255,.2);' +
      'transition:transform .25s cubic-bezier(.34,1.56,.64,1),box-shadow .25s;' +
      'z-index:9999;' +
      'animation:resolv-launcher-pulse 3s ease-in-out infinite' +
    '}' +
    '@keyframes resolv-launcher-pulse{' +
      '0%,100%{box-shadow:0 0 0 0 ' + ACCENT + '44,0 8px 32px ' + ACCENT + '66,inset 0 1px 0 rgba(255,255,255,.2)}' +
      '50%{box-shadow:0 0 0 8px ' + ACCENT + '00,0 8px 32px ' + ACCENT + '66,inset 0 1px 0 rgba(255,255,255,.2)}' +
    '}' +
    '#resolv-bubble:hover{transform:scale(1.08)}' +
    '#resolv-bubble:active{transform:scale(.95)}' +
    '#resolv-bubble svg{pointer-events:none;transition:transform .3s}' +
    '#resolv-bubble.open svg{transform:rotate(45deg)}' +

    /* ── PANEL ── */
    '#resolv-panel{' +
      'position:fixed;bottom:100px;' + hPos + ';' +
      'width:380px;height:560px;' +
      'border-radius:24px;overflow:hidden;' +
      'display:flex;flex-direction:column;' +
      'z-index:9998;' +
      'transform-origin:' + pOrigin + ';' +
      'transition:opacity .3s cubic-bezier(.34,1.2,.64,1),transform .3s cubic-bezier(.34,1.2,.64,1);' +
      'opacity:0;transform:scale(.85) translateY(16px);pointer-events:none;' +
      'background:linear-gradient(160deg,rgba(255,255,255,.04) 0%,rgba(255,255,255,.01) 100%);' +
      'backdrop-filter:blur(24px) saturate(1.5);' +
      '-webkit-backdrop-filter:blur(24px) saturate(1.5);' +
      'border:1px solid rgba(255,255,255,.1);' +
      'box-shadow:0 2px 0 rgba(255,255,255,.08) inset,0 -1px 0 rgba(0,0,0,.4) inset,0 40px 80px rgba(0,0,0,.6),0 8px 32px rgba(0,0,0,.4),0 0 0 .5px rgba(0,0,0,.3)' +
    '}' +
    '#resolv-panel::before{' +
      'content:\'\';position:absolute;inset:0;' +
      'background:linear-gradient(160deg,#13111f 0%,#0d0b18 60%,#110d1e 100%);' +
      'z-index:-1;border-radius:inherit' +
    '}' +
    '#resolv-panel::after{' +
      'content:\'\';position:absolute;inset:0;' +
      'background:radial-gradient(ellipse 60% 40% at 70% 0%,' + ACCENT + '22 0%,transparent 70%);' +
      'pointer-events:none;z-index:0' +
    '}' +
    '#resolv-panel.open{opacity:1;transform:scale(1) translateY(0);pointer-events:all}' +

    /* ── HEADER ── */
    '#resolv-header{' +
      'position:relative;z-index:1;' +
      'padding:18px 20px 16px;' +
      'display:flex;align-items:center;gap:12px;' +
      'border-bottom:1px solid rgba(255,255,255,.07);' +
      'background:linear-gradient(180deg,rgba(108,99,255,.15) 0%,transparent 100%);' +
      'flex-shrink:0' +
    '}' +
    '#resolv-header-left{display:flex;align-items:center;gap:12px}' +
    '#resolv-avatar{' +
      'width:40px;height:40px;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'display:flex;align-items:center;justify-content:center;' +
      'font-size:16px;font-weight:600;color:#fff;flex-shrink:0;' +
      'box-shadow:0 0 16px ' + ACCENT + '55,0 4px 8px rgba(0,0,0,.3);' +
      'position:relative' +
    '}' +
    '#resolv-avatar::after{' +
      'content:\'\';position:absolute;bottom:1px;right:1px;' +
      'width:10px;height:10px;border-radius:50%;' +
      'background:#22c55e;border:2px solid #13111f' +
    '}' +
    '#resolv-title{font-size:14px;font-weight:600;color:#fff;margin:0;letter-spacing:-.01em}' +
    '#resolv-subtitle{' +
      'font-size:11px;color:rgba(255,255,255,.45);margin:1px 0 0;' +
      'display:flex;align-items:center;gap:4px' +
    '}' +
    '#resolv-subtitle::before{' +
      'content:\'\';width:6px;height:6px;border-radius:50%;background:#22c55e;display:inline-block' +
    '}' +
    '#resolv-close{' +
      'margin-left:auto;width:32px;height:32px;border-radius:50%;' +
      'background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);' +
      'color:rgba(255,255,255,.5);display:flex;align-items:center;justify-content:center;' +
      'cursor:pointer;font-size:16px;transition:background .15s,color .15s;flex-shrink:0' +
    '}' +
    '#resolv-close:hover{background:rgba(255,255,255,.12);color:#fff}' +

    /* ── MESSAGES ── */
    '#resolv-messages{' +
      'flex:1;overflow-y:auto;padding:20px 16px;' +
      'display:flex;flex-direction:column;gap:16px;' +
      'position:relative;z-index:1;scroll-behavior:smooth' +
    '}' +
    '#resolv-messages::-webkit-scrollbar{width:4px}' +
    '#resolv-messages::-webkit-scrollbar-track{background:transparent}' +
    '#resolv-messages::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:2px}' +
    '.resolv-msg{display:flex;flex-direction:column;max-width:85%}' +
    '.resolv-msg.user{align-self:flex-end;align-items:flex-end}' +
    '.resolv-msg.bot{align-self:flex-start;align-items:flex-start;max-width:90%}' +
    '.resolv-msg-row{display:flex;align-items:flex-end;gap:8px}' +
    '.resolv-msg.user .resolv-msg-row{flex-direction:row-reverse}' +
    '.resolv-msg-avatar{' +
      'width:28px;height:28px;flex-shrink:0;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'display:flex;align-items:center;justify-content:center;' +
      'font-size:11px;font-weight:600;color:#fff;margin-bottom:2px' +
    '}' +
    '.resolv-bubble{' +
      'padding:10px 14px;border-radius:18px;' +
      'font-size:13.5px;line-height:1.55;font-weight:400;' +
      'word-break:break-word;white-space:pre-wrap' +
    '}' +
    '.resolv-msg.bot .resolv-bubble{' +
      'background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);' +
      'color:rgba(255,255,255,.9);border-bottom-left-radius:6px' +
    '}' +
    '.resolv-msg.user .resolv-bubble{' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'color:#fff;border-bottom-right-radius:6px;' +
      'box-shadow:0 4px 16px ' + ACCENT + '44' +
    '}' +
    '.resolv-ts{font-size:10px;color:rgba(255,255,255,.22);margin-top:3px;padding:0 4px}' +

    /* ── SMART TYPING ── */
    '.resolv-thinking{' +
      'display:flex;align-items:center;gap:8px;' +
      'padding:10px 14px;' +
      'background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);' +
      'border-radius:18px;border-bottom-left-radius:6px' +
    '}' +
    '.resolv-thinking-dots{display:flex;gap:4px;flex-shrink:0}' +
    '.resolv-thinking-dots span{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.4);animation:resolv-bounce 1.2s ease-in-out infinite}' +
    '.resolv-thinking-dots span:nth-child(2){animation-delay:.2s}' +
    '.resolv-thinking-dots span:nth-child(3){animation-delay:.4s}' +
    '@keyframes resolv-bounce{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-5px);opacity:1}}' +
    '.resolv-thinking-text{font-size:11px;color:rgba(255,255,255,.35);font-style:italic;transition:opacity .3s}' +

    /* ── ORDER CARD ── */
    '@keyframes resolv-slide-up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}' +
    '.resolv-order-card{' +
      'background:rgba(255,255,255,.05);' +
      'border:1px solid rgba(255,255,255,.10);' +
      'border-radius:14px;padding:14px 16px;margin-bottom:8px;width:100%;' +
      'animation:resolv-slide-up .25s ease-out' +
    '}' +
    '.resolv-order-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px}' +
    '.resolv-order-title{font-size:13px;font-weight:600;color:rgba(255,255,255,.9);flex:1}' +
    '.resolv-status-badge{' +
      'font-size:10px;letter-spacing:.08em;text-transform:uppercase;' +
      'border-radius:20px;padding:2px 8px;font-weight:600;flex-shrink:0' +
    '}' +
    '.resolv-order-divider{height:1px;background:rgba(255,255,255,.07);margin:0 0 10px}' +
    '.resolv-order-item{font-size:13px;color:rgba(255,255,255,.85);font-weight:500;margin-bottom:2px}' +
    '.resolv-order-meta{font-size:12px;color:rgba(255,255,255,.4);margin-bottom:4px}' +
    '.resolv-order-cancelled-info{margin-top:8px;font-size:12px;color:rgba(255,255,255,.45)}' +
    '.resolv-order-cancelled-info div{margin-top:3px;display:flex;align-items:center;gap:4px}' +
    '.resolv-order-tracking{margin-top:10px;font-size:12px;color:rgba(255,255,255,.45);display:flex;align-items:center;gap:4px}' +
    '.resolv-order-tracking a{color:' + ACCENT + ';text-decoration:none}' +
    '.resolv-order-tracking a:hover{text-decoration:underline}' +

    /* ── ACTION RESULT CARD ── */
    '.resolv-action-card{border-radius:14px;padding:14px 16px;margin-bottom:8px;width:100%;animation:resolv-slide-up .25s ease-out}' +
    '.resolv-action-card.refund{background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.2)}' +
    '.resolv-action-card.cancel{background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.2)}' +
    '.resolv-action-card.address{background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.2)}' +
    '.resolv-action-card.restore{background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2)}' +
    '.resolv-action-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;gap:8px}' +
    '.resolv-action-title{font-size:13px;font-weight:600;color:rgba(255,255,255,.9);flex:1}' +
    '.resolv-action-badge{font-size:10px;letter-spacing:.06em;text-transform:uppercase;border-radius:20px;padding:2px 8px;font-weight:600;flex-shrink:0}' +
    '.resolv-action-card.refund  .resolv-action-badge{background:rgba(16,185,129,.15);color:#10b981;border:1px solid rgba(16,185,129,.3)}' +
    '.resolv-action-card.cancel  .resolv-action-badge{background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3)}' +
    '.resolv-action-card.address .resolv-action-badge{background:rgba(99,102,241,.15);color:#6366f1;border:1px solid rgba(99,102,241,.3)}' +
    '.resolv-action-card.restore .resolv-action-badge{background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3)}' +
    '.resolv-action-divider{height:1px;background:rgba(255,255,255,.07);margin:0 0 8px}' +
    '.resolv-action-detail{font-size:12px;color:rgba(255,255,255,.5);margin-bottom:3px}' +

    /* ── SATISFACTION RATING ── */
    '.resolv-rating{' +
      'background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);' +
      'border-radius:14px;padding:12px 14px;margin-top:6px;width:100%;' +
      'animation:resolv-slide-up .3s ease-out' +
    '}' +
    '.resolv-rating p{font-size:12px;color:rgba(255,255,255,.5);margin:0 0 8px}' +
    '.resolv-rating-btns{display:flex;gap:8px}' +
    '.resolv-rating-btn{' +
      'padding:5px 14px;border-radius:20px;border:1px solid rgba(255,255,255,.15);' +
      'background:rgba(255,255,255,.06);color:rgba(255,255,255,.7);' +
      'font-size:12px;cursor:pointer;transition:background .15s;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '.resolv-rating-btn:hover{background:rgba(255,255,255,.12)}' +

    /* ── SUGGESTIONS ── */
    '#resolv-suggestions{padding:0 16px 10px;display:flex;gap:6px;flex-wrap:wrap;position:relative;z-index:1}' +
    '.resolv-suggestion{' +
      'padding:5px 11px;border:1px solid ' + ACCENT + 'aa;' +
      'border-radius:20px;background:' + hexToRgba(ACCENT, 0.10) + ';' +
      'color:' + ACCENT + ';font-size:12px;cursor:pointer;' +
      'transition:background .15s;white-space:nowrap;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '.resolv-suggestion:hover{background:' + hexToRgba(ACCENT, 0.20) + '}' +

    /* ── EMAIL BAR ── */
    '#resolv-email-bar{' +
      'padding:10px 16px;background:rgba(255,255,255,.04);' +
      'border-top:1px solid rgba(255,255,255,.06);' +
      'font-size:12px;color:rgba(255,255,255,.6);' +
      'display:flex;flex-direction:column;gap:8px;position:relative;z-index:1' +
    '}' +
    '#resolv-email-bar p{margin:0;color:rgba(255,255,255,.55)}' +
    '#resolv-email-row{display:flex;gap:6px}' +
    '#resolv-email-input{' +
      'flex:1;padding:6px 10px;background:rgba(255,255,255,.06);' +
      'border:1px solid rgba(255,255,255,.12);border-radius:8px;' +
      'color:rgba(255,255,255,.9);font-size:12px;outline:none;' +
      'font-family:\'Sora\',-apple-system,sans-serif' +
    '}' +
    '#resolv-email-input:focus{border-color:' + ACCENT + '88}' +
    '#resolv-email-submit{' +
      'padding:6px 12px;background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'color:#fff;border:none;border-radius:8px;font-size:12px;cursor:pointer;font-weight:600' +
    '}' +
    '#resolv-email-skip{background:none;border:none;color:rgba(255,255,255,.35);font-size:11px;cursor:pointer;padding:0;text-decoration:underline;align-self:center}' +

    /* ── FOOTER ── */
    '#resolv-footer{' +
      'padding:12px 14px 16px;' +
      'border-top:1px solid rgba(255,255,255,.07);' +
      'background:linear-gradient(0deg,rgba(0,0,0,.2) 0%,transparent 100%);' +
      'display:flex;align-items:center;gap:10px;flex-shrink:0;' +
      'position:relative;z-index:1' +
    '}' +
    '#resolv-input{' +
      'flex:1;background:rgba(255,255,255,.06);' +
      'border:1px solid rgba(255,255,255,.1);border-radius:14px;' +
      'padding:10px 14px;font-size:13.5px;font-weight:400;' +
      'color:rgba(255,255,255,.9);' +
      'font-family:\'Sora\',-apple-system,sans-serif;' +
      'outline:none;transition:border-color .2s,background .2s;' +
      'resize:none;min-height:40px;max-height:96px;line-height:1.4' +
    '}' +
    '#resolv-input::placeholder{color:rgba(255,255,255,.25)}' +
    '#resolv-input:focus{border-color:' + ACCENT + '88;background:rgba(255,255,255,.09)}' +
    '#resolv-send{' +
      'width:38px;height:38px;flex-shrink:0;border-radius:50%;' +
      'background:linear-gradient(135deg,' + ACCENT + ' 0%,#a78bfa 100%);' +
      'border:none;cursor:pointer;' +
      'display:flex;align-items:center;justify-content:center;' +
      'transition:transform .2s,opacity .2s;' +
      'box-shadow:0 4px 12px ' + ACCENT + '55' +
    '}' +
    '#resolv-send:hover{transform:scale(1.08)}' +
    '#resolv-send:active{transform:scale(.94)}' +
    '#resolv-send:disabled{opacity:.4;cursor:default;transform:none}' +
    '#resolv-send svg{pointer-events:none}' +

    /* ── BADGE ── */
    '#resolv-badge{' +
      'position:absolute;top:-2px;right:-2px;' +
      'width:18px;height:18px;border-radius:50%;' +
      'background:#ef4444;border:2px solid #0d0b18;' +
      'font-size:10px;font-weight:700;color:#fff;' +
      'display:none;align-items:center;justify-content:center' +
    '}' +

    /* ── POWERED BY ── */
    '#resolv-powered{' +
      'text-align:center;font-size:10px;color:rgba(255,255,255,.18);' +
      'padding:0 0 10px;position:relative;z-index:1;letter-spacing:.03em' +
    '}' +
    '#resolv-powered a{color:rgba(255,255,255,.3);text-decoration:none}' +

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
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
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
          '<path d="M17.5 10L3.5 3.5L6.5 10L3.5 16.5L17.5 10Z" fill="white" stroke="white" stroke-width="1.2" stroke-linejoin="round"/>' +
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

  /* ── Smart thinking indicator ────────────────────────────────────── */
  var typingEl = null;
  var thinkingInterval = null;

  function showSmartTyping(userMessage) {
    if (typingEl) return;
    var hasOrderNum = /\b\d{3,6}\b/.test(userMessage || '');
    var steps = hasOrderNum
      ? ['Reading your message…', 'Checking order details…', 'Pulling Shopify data…', 'Writing reply…']
      : ['Reading your message…', 'Writing reply…'];
    var stepIdx = 0;

    var wrap = document.createElement('div');
    wrap.className = 'resolv-msg bot';
    var row = document.createElement('div');
    row.className = 'resolv-msg-row';
    var av = document.createElement('div');
    av.className = 'resolv-msg-avatar';
    av.textContent = BOT_NAME.charAt(0);
    var t = document.createElement('div');
    t.className = 'resolv-thinking';
    t.innerHTML = '<div class="resolv-thinking-dots"><span></span><span></span><span></span></div>' +
                  '<div class="resolv-thinking-text">' + steps[0] + '</div>';
    row.appendChild(av);
    row.appendChild(t);
    wrap.appendChild(row);
    msgContainer.appendChild(wrap);
    typingEl = wrap;
    scrollBottom();

    var textEl = t.querySelector('.resolv-thinking-text');
    thinkingInterval = setInterval(function () {
      stepIdx = (stepIdx + 1) % steps.length;
      textEl.style.opacity = '0';
      setTimeout(function () {
        textEl.textContent = steps[stepIdx];
        textEl.style.opacity = '1';
      }, 150);
    }, 1400);
  }

  function hideSmartTyping() {
    if (thinkingInterval) { clearInterval(thinkingInterval); thinkingInterval = null; }
    if (typingEl) { typingEl.remove(); typingEl = null; }
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
    showSmartTyping(userText);
    scrollBottom();

    apiPost('/api/v2/widget/chat', {
      brand_id:       BRAND_ID,
      session_id:     sessionId,
      message:        userText,
      customer_email: emailCaptured || undefined,
    }, function (err, data) {
      hideSmartTyping();
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
