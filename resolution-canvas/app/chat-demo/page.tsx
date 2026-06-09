import dynamic from 'next/dynamic'

// Load widget client-side only — it's a fixed-position overlay
const ChatWidget = dynamic(
  () => import('@/components/chat-widget/ChatWidget').then((m) => m.ChatWidget),
  { ssr: false }
)

const PRODUCTS = [
  { name: 'Essential Hoodie V10', price: 'Rs 120', tag: 'BESTSELLER', emoji: '👕' },
  { name: 'Linen Wide-Leg Pants', price: 'Rs 95',  tag: 'NEW',        emoji: '👖' },
  { name: 'Woven Crop Top',       price: 'Rs 65',  tag: 'NEW',        emoji: '🧵' },
]

export default function ChatDemoPage() {
  return (
    <main
      style={{
        minHeight: '100vh',
        background: '#f7f7f5',
        fontFamily:
          'var(--font-geist-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif)',
        color: '#111',
      }}
    >
      {/* ── Nav ── */}
      <nav
        style={{
          padding: '0 40px',
          height: '60px',
          background: '#fff',
          borderBottom: '1px solid #eee',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          position: 'sticky',
          top: 0,
          zIndex: 100,
        }}
      >
        <span
          style={{
            fontWeight: 800,
            letterSpacing: '2px',
            fontSize: '13px',
            textTransform: 'uppercase',
          }}
        >
          Luna Apparel
        </span>
        <div style={{ display: 'flex', gap: '28px', fontSize: '13px', color: '#555' }}>
          {['Shop', 'Collections', 'About', 'Journal'].map((l) => (
            <a
              key={l}
              href="#"
              style={{ textDecoration: 'none', color: 'inherit' }}
            >
              {l}
            </a>
          ))}
        </div>
        <div style={{ fontSize: '13px', color: '#555' }}>🛒 Cart (0)</div>
      </nav>

      {/* ── Hero ── */}
      <section
        style={{
          maxWidth: '820px',
          margin: '88px auto 72px',
          padding: '0 40px',
          textAlign: 'center',
        }}
      >
        <div
          style={{
            display: 'inline-block',
            padding: '4px 14px',
            background: '#eeecff',
            borderRadius: '100px',
            fontSize: '11px',
            fontWeight: 700,
            color: '#6366F1',
            letterSpacing: '0.07em',
            textTransform: 'uppercase',
            marginBottom: '22px',
          }}
        >
          New Summer Drop — 2026
        </div>
        <h1
          style={{
            fontSize: 'clamp(36px, 6vw, 60px)',
            fontWeight: 800,
            letterSpacing: '-0.03em',
            lineHeight: 1.08,
            margin: '0 0 18px',
          }}
        >
          Lightweight
          <br />
          Linen Essentials
        </h1>
        <p
          style={{
            fontSize: '16px',
            color: '#666',
            marginBottom: '36px',
            lineHeight: 1.65,
            maxWidth: '480px',
            margin: '0 auto 36px',
          }}
        >
          Breathable organic linen crafted for the season. Each piece designed to feel
          effortless from morning to evening.
        </p>
        <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
          <button
            style={{
              padding: '13px 30px',
              background: '#111',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: 600,
              cursor: 'pointer',
              letterSpacing: '0.01em',
            }}
          >
            Shop Now
          </button>
          <button
            style={{
              padding: '13px 30px',
              background: 'transparent',
              color: '#111',
              border: '1px solid #ccc',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            View Lookbook
          </button>
        </div>
      </section>

      {/* ── Product grid ── */}
      <section
        style={{
          maxWidth: '900px',
          margin: '0 auto 100px',
          padding: '0 40px',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))',
          gap: '20px',
        }}
      >
        {PRODUCTS.map(({ name, price, tag, emoji }) => (
          <div
            key={name}
            style={{
              background: '#fff',
              borderRadius: '16px',
              overflow: 'hidden',
              boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)',
              cursor: 'pointer',
              transition: 'box-shadow 0.2s, transform 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.boxShadow =
                '0 2px 8px rgba(0,0,0,0.08), 0 12px 32px rgba(0,0,0,0.08)'
              e.currentTarget.style.transform = 'translateY(-3px)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.boxShadow =
                '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)'
              e.currentTarget.style.transform = 'translateY(0)'
            }}
          >
            {/* Product image placeholder */}
            <div
              style={{
                height: '220px',
                background: 'linear-gradient(135deg, #e8e8f4, #d6d4f0)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                position: 'relative',
              }}
            >
              <span style={{ fontSize: '56px' }}>{emoji}</span>
              <span
                style={{
                  position: 'absolute',
                  top: '12px',
                  left: '12px',
                  padding: '3px 9px',
                  background: '#6366F1',
                  color: '#fff',
                  fontSize: '10px',
                  fontWeight: 700,
                  borderRadius: '6px',
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                {tag}
              </span>
            </div>
            <div style={{ padding: '16px 18px' }}>
              <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '4px' }}>
                {name}
              </div>
              <div style={{ fontSize: '13px', color: '#888' }}>{price}</div>
            </div>
          </div>
        ))}
      </section>

      {/* ── Banner strip ── */}
      <div
        style={{
          background: '#111',
          color: '#fff',
          textAlign: 'center',
          padding: '14px',
          fontSize: '13px',
          letterSpacing: '0.03em',
          marginBottom: '60px',
        }}
      >
        Free shipping on orders over Rs 300 &nbsp;·&nbsp; Easy 30-day returns &nbsp;·&nbsp;{' '}
        <strong>Chat with Luna →</strong> for instant support
      </div>

      {/* ── Widget hint ── */}
      <div
        style={{
          position: 'fixed',
          bottom: '96px',
          right: '94px',
          background: '#111',
          color: '#fff',
          padding: '8px 14px',
          borderRadius: '8px',
          fontSize: '12px',
          fontWeight: 500,
          pointerEvents: 'none',
          opacity: 0.85,
          whiteSpace: 'nowrap',
          zIndex: 9998,
        }}
      >
        👋 Click the orb to chat
        <div
          style={{
            position: 'absolute',
            right: '-6px',
            top: '50%',
            transform: 'translateY(-50%)',
            width: 0,
            height: 0,
            borderTop: '6px solid transparent',
            borderBottom: '6px solid transparent',
            borderLeft: '6px solid #111',
          }}
        />
      </div>

      {/* ── Chat Widget ── */}
      <ChatWidget
        brandId={process.env.NEXT_PUBLIC_BRAND_ID || ''}
        orgId={process.env.NEXT_PUBLIC_ORG_ID || ''}
        agentName="Luna"
        accentColor="#6366F1"
        apiBaseUrl={process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}
      />
    </main>
  )
}
