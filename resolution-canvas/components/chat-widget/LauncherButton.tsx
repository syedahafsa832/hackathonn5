'use client'

import { motion, AnimatePresence } from 'framer-motion'

type Props = {
  isOpen: boolean
  unreadCount: number
  accentColor: string
  onClick: () => void
}

function OrbIcon() {
  return (
    <motion.svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      animate={{ rotate: 360 }}
      transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
    >
      {/* Center dot */}
      <circle cx="12" cy="12" r="3.5" fill="white" />
      {/* Three arcs at 120° intervals suggesting orbital paths */}
      <path
        d="M12 3.5 A8.5 8.5 0 0 1 19.86 8.25"
        stroke="white"
        strokeWidth="1.8"
        strokeLinecap="round"
        opacity="0.85"
        fill="none"
      />
      <path
        d="M19.86 15.75 A8.5 8.5 0 0 1 4.14 15.75"
        stroke="white"
        strokeWidth="1.8"
        strokeLinecap="round"
        opacity="0.85"
        fill="none"
      />
      <path
        d="M4.14 8.25 A8.5 8.5 0 0 1 12 3.5"
        stroke="white"
        strokeWidth="1.8"
        strokeLinecap="round"
        opacity="0.45"
        fill="none"
      />
    </motion.svg>
  )
}

function CloseIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path
        d="M18 6L6 18M6 6l12 12"
        stroke="white"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function LauncherButton({ isOpen, unreadCount, accentColor, onClick }: Props) {
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ scale: 1.07 }}
      whileTap={{ scale: 0.93 }}
      style={{
        position: 'relative',
        width: '56px',
        height: '56px',
        borderRadius: '50%',
        background: `radial-gradient(circle at 40% 35%, ${accentColor}, #8B5CF6)`,
        border: 'none',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: `0 6px 28px ${accentColor}55, 0 2px 8px rgba(0,0,0,0.4)`,
      }}
      aria-label={isOpen ? 'Close support chat' : 'Open support chat'}
    >
      {/* Pulse ring — CSS animation, no JS */}
      <span
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: '50%',
          animation: isOpen ? 'none' : 'launcher-pulse 2.5s ease-out infinite',
          pointerEvents: 'none',
        }}
      />

      {/* Icon morph */}
      <AnimatePresence mode="wait">
        <motion.span
          key={isOpen ? 'close' : 'orb'}
          initial={{ opacity: 0, rotate: -90, scale: 0.55 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 90, scale: 0.55 }}
          transition={{ duration: 0.22, ease: 'easeOut' }}
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        >
          {isOpen ? <CloseIcon /> : <OrbIcon />}
        </motion.span>
      </AnimatePresence>

      {/* Unread badge */}
      <AnimatePresence>
        {!isOpen && unreadCount > 0 && (
          <motion.span
            key="badge"
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 400, damping: 20 }}
            style={{
              position: 'absolute',
              top: '-2px',
              right: '-2px',
              width: '18px',
              height: '18px',
              borderRadius: '50%',
              background: '#EF4444',
              border: '2px solid #080B14',
              fontSize: '10px',
              fontWeight: 700,
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: 'system-ui, sans-serif',
            }}
          >
            {unreadCount > 9 ? '9+' : unreadCount}
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  )
}
