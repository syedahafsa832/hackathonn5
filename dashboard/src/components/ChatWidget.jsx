import { useState, useEffect, useRef, useMemo } from "react";
import { deriveWidgetTheme } from "../utils/widgetTheme";

const AGENT_NAME = "Luna";
const API_BASE = import.meta.env.VITE_API_URL || "https://backend.tresolv.online";

const INITIAL_MESSAGE = {
  id: "init",
  role: "assistant",
  text: "Hi there 👋 I'm Luna, your AI support assistant.\nAsk me about your orders, returns, or anything else.",
  ts: Date.now(),
};

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TypingDots() {
  return (
    <div className="typing-dots">
      <span /><span /><span />
    </div>
  );
}

function Message({ msg, agentName }) {
  const isUser = msg.role === "user";
  return (
    <div className={`msg-row ${isUser ? "user" : "agent"}`}>
      {!isUser && (
        <div className="avatar-ring">
          <div className="avatar-inner">{agentName.charAt(0)}</div>
        </div>
      )}
      <div className="bubble-wrap">
        {!isUser && <span className="agent-name">{agentName}</span>}
        <div className={`bubble ${isUser ? "bubble-user" : "bubble-agent"}`}>
          {msg.typing ? <TypingDots /> : msg.text.split("\n").map((line, i) => (
            <span key={i}>{line}{i < msg.text.split("\n").length - 1 && <br />}</span>
          ))}
        </div>
        <span className="ts">{formatTime(msg.ts)}</span>
      </div>
    </div>
  );
}

export default function ChatWidget({
  brandId,
  orgId,
  agentName = AGENT_NAME,
  // '#06B6D4' - a sensible fallback theme for a brand that hasn't
  // configured brands.primary_color yet (see Settings.jsx's ChatWidgetTab,
  // which now reads the real value when one exists).
  accentColor = "#06B6D4",
}) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  const [customerName, setCustomerName] = useState("");
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  // Every themed surface below reads from this derived palette instead of
  // hardcoding a second gradient color - keeps any merchant's accent
  // coherent instead of only ever looking right for violet/indigo.
  const theme = useMemo(() => deriveWidgetTheme(accentColor), [accentColor]);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage(text) {
    if (!text.trim() || loading) return;
    const userMsg = { id: crypto.randomUUID(), role: "user", text: text.trim(), ts: Date.now() };
    const typingMsg = { id: "typing", role: "assistant", text: "", typing: true, ts: Date.now() };

    setMessages((prev) => [...prev, userMsg, typingMsg]);
    setInput("");
    setLoading(true);

    const conversationHistory = [...messages, userMsg]
      .filter((m) => !m.typing && m.id !== "init")
      .map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.text }));

    try {
      const res = await fetch(`${API_BASE}/api/v2/widget/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text.trim(),
          session_id: sessionId,
          brand_id: brandId,
          org_id: orgId,
          customer_name: customerName || undefined,
          conversation_history: conversationHistory,
        }),
      });

      const data = await res.json();
      const replyText = data.reply || data.message || "I'm having trouble responding right now. Our team has been notified.";

      if (data.customer_name && !customerName) {
        setCustomerName(data.customer_name);
      }

      setMessages((prev) => [
        ...prev.filter((m) => m.id !== "typing"),
        { id: crypto.randomUUID(), role: "assistant", text: replyText, ts: Date.now() },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== "typing"),
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: "Sorry, I'm having connectivity issues. Please try again shortly.",
          ts: Date.now(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKey(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  const { accent, accentDark, accentText, accentTint } = theme;

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&display=swap');

        .tresolv-widget * { box-sizing: border-box; font-family: 'Sora', sans-serif; }

        /* ── LAUNCHER ─────────────────────────────── */
        .tw-launcher {
          position: fixed;
          bottom: 28px; right: 28px;
          width: 60px; height: 60px;
          border-radius: 50%;
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          border: none;
          cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          box-shadow:
            0 0 0 0 ${accent}44,
            0 8px 24px ${accent}55,
            inset 0 1px 0 rgba(255,255,255,0.25);
          transition: transform 0.25s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.25s;
          z-index: 9999;
          animation: launcher-pulse 3s ease-in-out infinite;
        }
        @keyframes launcher-pulse {
          0%,100% { box-shadow: 0 0 0 0 ${accent}44, 0 8px 24px ${accent}55, inset 0 1px 0 rgba(255,255,255,0.25); }
          50% { box-shadow: 0 0 0 8px ${accent}00, 0 8px 24px ${accent}55, inset 0 1px 0 rgba(255,255,255,0.25); }
        }
        .tw-launcher:hover { transform: scale(1.08); }
        .tw-launcher:active { transform: scale(0.95); }
        .tw-launcher svg { width: 26px; height: 26px; transition: transform 0.3s; }
        .tw-launcher.open svg { transform: rotate(45deg); }

        /* ── PANEL ─────────────────────────────────── */
        .tw-panel {
          position: fixed;
          bottom: 100px; right: 28px;
          width: 380px;
          height: 580px;
          border-radius: 20px;
          overflow: hidden;
          display: flex; flex-direction: column;
          z-index: 9998;
          transform-origin: bottom right;
          transition: opacity 0.3s cubic-bezier(0.34,1.2,0.64,1),
                      transform 0.3s cubic-bezier(0.34,1.2,0.64,1);
          opacity: 0;
          transform: scale(0.9) translateY(16px);
          pointer-events: none;
          background: #ffffff;
          border: 1px solid rgba(15,23,42,0.06);
          box-shadow: 0 24px 60px rgba(15,23,42,0.16), 0 4px 18px rgba(15,23,42,0.08);
        }
        .tw-panel.open {
          opacity: 1;
          transform: scale(1) translateY(0);
          pointer-events: all;
        }

        /* ── HEADER ─────────────────────────────────── */
        .tw-header {
          position: relative; z-index: 1;
          padding: 20px 20px 16px;
          display: flex; align-items: center; gap: 12px;
          border-bottom: 1px solid rgba(15,23,42,0.06);
          background: linear-gradient(180deg, ${accentTint} 0%, #ffffff 100%);
          flex-shrink: 0;
        }
        .tw-header-avatar {
          width: 42px; height: 42px;
          border-radius: 50%;
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          display: flex; align-items: center; justify-content: center;
          font-size: 16px; font-weight: 700; color: ${accentText};
          flex-shrink: 0;
          box-shadow: 0 4px 14px ${accent}3d;
          position: relative;
        }
        .tw-header-avatar::after {
          content: '';
          position: absolute; bottom: 0; right: 0;
          width: 11px; height: 11px;
          border-radius: 50%;
          background: #22c55e;
          border: 2px solid #ffffff;
        }
        .tw-header-info { flex: 1; }
        .tw-header-name {
          font-size: 15px; font-weight: 700;
          color: #0F172A;
          margin: 0;
          letter-spacing: -0.01em;
        }
        .tw-header-status {
          font-size: 11.5px;
          color: rgba(15,23,42,0.5);
          margin: 2px 0 0;
          display: flex; align-items: center; gap: 4px;
        }
        .tw-header-status::before {
          content: '';
          width: 6px; height: 6px;
          border-radius: 50%; background: #22c55e;
          display: inline-block;
        }
        .tw-close-btn {
          width: 30px; height: 30px;
          border-radius: 50%;
          background: rgba(15,23,42,0.04);
          border: 1px solid rgba(15,23,42,0.06);
          color: rgba(15,23,42,0.45);
          display: flex; align-items: center; justify-content: center;
          cursor: pointer; font-size: 15px;
          transition: background 0.15s, color 0.15s;
          flex-shrink: 0;
        }
        .tw-close-btn:hover { background: rgba(15,23,42,0.08); color: #0F172A; }

        /* ── MESSAGES ───────────────────────────────── */
        .tw-messages {
          flex: 1;
          overflow-y: auto;
          padding: 18px 16px;
          background: #ffffff;
          display: flex; flex-direction: column; gap: 16px;
          position: relative; z-index: 1;
          scroll-behavior: smooth;
        }
        .tw-messages::-webkit-scrollbar { width: 4px; }
        .tw-messages::-webkit-scrollbar-track { background: transparent; }
        .tw-messages::-webkit-scrollbar-thumb { background: rgba(15,23,42,0.12); border-radius: 2px; }

        .msg-row { display: flex; gap: 8px; align-items: flex-end; }
        .msg-row.user { flex-direction: row-reverse; }

        .avatar-ring {
          width: 26px; height: 26px; flex-shrink: 0;
          border-radius: 50%;
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          padding: 1.5px;
        }
        .avatar-inner {
          width: 100%; height: 100%;
          border-radius: 50%;
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          display: flex; align-items: center; justify-content: center;
          font-size: 11px; font-weight: 700; color: ${accentText};
        }

        .bubble-wrap { display: flex; flex-direction: column; gap: 3px; max-width: 75%; }
        .msg-row.user .bubble-wrap { align-items: flex-end; }

        .agent-name { font-size: 10px; font-weight: 500; color: rgba(15,23,42,0.35); padding-left: 2px; }

        .bubble {
          padding: 10px 14px;
          border-radius: 18px;
          font-size: 13.5px;
          line-height: 1.55;
          font-weight: 400;
        }
        .bubble-agent {
          background: #F1F3F6;
          border: 1px solid rgba(15,23,42,0.04);
          color: #0F172A;
          border-bottom-left-radius: 5px;
        }
        .bubble-user {
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          color: ${accentText};
          border-bottom-right-radius: 5px;
          box-shadow: 0 4px 14px ${accent}33;
        }
        .ts {
          font-size: 10px; color: rgba(15,23,42,0.32);
          padding: 0 4px;
        }

        /* typing dots */
        .typing-dots { display: flex; gap: 4px; align-items: center; height: 16px; }
        .typing-dots span {
          width: 6px; height: 6px; border-radius: 50%;
          background: rgba(15,23,42,0.28);
          animation: dot-bounce 1.2s ease-in-out infinite;
        }
        .typing-dots span:nth-child(2) { animation-delay: 0.2s; }
        .typing-dots span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes dot-bounce {
          0%,60%,100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-5px); opacity: 1; }
        }

        /* ── INPUT ──────────────────────────────────── */
        .tw-input-area {
          position: relative; z-index: 1;
          padding: 12px 14px 14px;
          border-top: 1px solid rgba(15,23,42,0.06);
          background: #ffffff;
          display: flex; align-items: center; gap: 10px;
          flex-shrink: 0;
        }
        .tw-input {
          flex: 1;
          background: #F8FAFC;
          border: 1px solid rgba(15,23,42,0.1);
          border-radius: 14px;
          padding: 10px 14px;
          font-size: 13.5px; font-weight: 400;
          color: #0F172A;
          font-family: 'Sora', sans-serif;
          outline: none;
          transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
          resize: none;
          height: 40px;
          line-height: 1.4;
        }
        .tw-input::placeholder { color: rgba(15,23,42,0.32); }
        .tw-input:focus {
          border-color: ${accent};
          background: #ffffff;
          box-shadow: 0 0 0 3px ${accent}26;
        }
        .tw-send {
          width: 38px; height: 38px; flex-shrink: 0;
          border-radius: 50%;
          background: linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%);
          border: none; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          transition: transform 0.2s, opacity 0.2s;
          box-shadow: 0 4px 12px ${accent}4d;
        }
        .tw-send:hover { transform: scale(1.08); }
        .tw-send:active { transform: scale(0.94); }
        .tw-send:disabled { opacity: 0.35; cursor: default; transform: none; }
        .tw-send svg { width: 16px; height: 16px; }

        /* ── FOOTER BRAND ───────────────────────────── */
        .tw-footer-brand {
          text-align: center;
          font-size: 10px;
          color: rgba(15,23,42,0.3);
          padding: 0 0 10px;
          position: relative; z-index: 1;
          letter-spacing: 0.03em;
          background: #ffffff;
        }
        .tw-footer-brand a { color: rgba(15,23,42,0.4); text-decoration: none; }

        /* ── NOTIF BADGE ────────────────────────────── */
        .tw-badge {
          position: absolute; top: -2px; right: -2px;
          width: 18px; height: 18px;
          border-radius: 50%;
          background: #ef4444;
          border: 2px solid #ffffff;
          font-size: 10px; font-weight: 600;
          color: #fff;
          display: flex; align-items: center; justify-content: center;
        }
      `}</style>

      <div className="tresolv-widget">
        {/* ── PANEL ── */}
        <div className={`tw-panel ${open ? "open" : ""}`} role="dialog" aria-label={`Luna AI Support`}>
          <div className="tw-header">
            <div className="tw-header-avatar">{agentName.charAt(0)}</div>
            <div className="tw-header-info">
              <p className="tw-header-name">{agentName}</p>
              <p className="tw-header-status">Online · AI Support</p>
            </div>
            <button className="tw-close-btn" onClick={() => setOpen(false)} aria-label="Close chat">
              ✕
            </button>
          </div>

          <div className="tw-messages" role="log" aria-live="polite">
            {messages.map((msg) => (
              <Message key={msg.id} msg={msg} agentName={agentName} />
            ))}
            <div ref={bottomRef} />
          </div>

          <div className="tw-input-area">
            <input
              ref={inputRef}
              className="tw-input"
              placeholder={`Message ${agentName}…`}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              disabled={loading}
              aria-label="Type a message"
              maxLength={500}
            />
            <button
              className="tw-send"
              onClick={() => sendMessage(input)}
              disabled={loading || !input.trim()}
              aria-label="Send message"
            >
              <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M17.5 10L3.5 3.5L6.5 10L3.5 16.5L17.5 10Z" fill={accentText} stroke={accentText} strokeWidth="1.2" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>

          <div className="tw-footer-brand">
            Powered by <a href="https://tresolv.online" target="_blank" rel="noreferrer">tResolv</a>
          </div>
        </div>

        {/* ── LAUNCHER ── */}
        <button
          className={`tw-launcher ${open ? "open" : ""}`}
          onClick={() => setOpen((o) => !o)}
          aria-label={open ? "Close support chat" : "Open support chat"}
        >
          {open ? (
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M18 6L6 18M6 6l12 12" stroke={accentText} strokeWidth="2.2" strokeLinecap="round"/>
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2v10z" stroke={accentText} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </button>
      </div>
    </>
  );
}
