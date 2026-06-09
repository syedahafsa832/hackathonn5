'use client'

import { useState, useCallback, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { LauncherButton } from './LauncherButton'
import { ActionCards } from './ActionCards'
import { ResolutionCanvas } from './ResolutionCanvas'
import type {
  Message,
  OrbState,
  ResolutionStep,
  OrderData,
  WidgetProps,
  ApiResponse,
  ResolutionStepId,
} from './types'

/* ── Constants ───────────────────────────────────────────────── */

const STEP_ORDER: ResolutionStepId[] = [
  'understanding',
  'gathering',
  'acting',
  'verifying',
  'resolved',
]

const INITIAL_STEPS: ResolutionStep[] = [
  { id: 'understanding', label: 'Understanding',  detail: 'Detecting what you need',           status: 'pending' },
  { id: 'gathering',     label: 'Gathering data', detail: 'Checking your order',               status: 'pending' },
  { id: 'acting',        label: 'Taking action',  detail: 'Making changes',                    status: 'pending' },
  { id: 'verifying',     label: 'Verifying',      detail: 'Confirming everything looks right', status: 'pending' },
  { id: 'resolved',      label: 'Resolved',       detail: undefined,                           status: 'pending' },
]

const panelVariants = {
  closed: {
    opacity: 0,
    scale: 0.88,
    y: 16,
    transition: { duration: 0.2, ease: [0.4, 0, 1, 1] as const },
  },
  open: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const },
  },
}

/* ── Session storage helpers ─────────────────────────────────── */

const SESSION_TTL = 4 * 60 * 60 * 1000 // 4 hours

function makeSessionId() {
  return `cs_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
}

type SavedSession = {
  sessionId: string
  messages: Message[]
  savedAt: number
}

function loadSession(brandId: string): { sessionId: string; messages: Message[] } {
  if (typeof window === 'undefined') return { sessionId: makeSessionId(), messages: [] }
  try {
    const raw = sessionStorage.getItem(`tresolv_session_${brandId}`)
    if (raw) {
      const saved: SavedSession = JSON.parse(raw)
      if (Date.now() - saved.savedAt < SESSION_TTL && saved.messages?.length) {
        return { sessionId: saved.sessionId, messages: saved.messages }
      }
    }
  } catch { /* ignore */ }
  return { sessionId: makeSessionId(), messages: [] }
}

function saveSession(brandId: string, sessionId: string, messages: Message[]) {
  try {
    const payload: SavedSession = {
      sessionId,
      messages: messages.filter((m) => !m.isTyping).slice(-20),
      savedAt: Date.now(),
    }
    sessionStorage.setItem(`tresolv_session_${brandId}`, JSON.stringify(payload))
  } catch { /* ignore */ }
}

/* ── Mock responses (demo mode) ─────────────────────────────── */

function getMockResponse(text: string, msgCount: number): ApiResponse {
  const lower = text.toLowerCase()

  if (lower.includes('track') || lower.includes('order') || lower.includes('where')) {
    return {
      reply:
        "Found your order #1007! It was placed on June 3rd for an Essential Hoodie V10 (Size XS).\n\nUnfortunately this order was cancelled on June 5th. Your payment of Rs 120 has been processed and your refund should arrive within 5 to 7 business days.",
      confidence: 92,
      order_data: {
        orderNumber: '1007',
        items: [{ name: 'Essential Hoodie V10', quantity: 1, price: 'Rs 120' }],
        status: 'cancelled',
        paymentStatus: 'paid',
        cancelledAt: 'Jun 5',
      },
      resolution_step: 'gathering',
    }
  }

  if (lower.includes('refund') || lower.includes('money') || lower.includes('return')) {
    return {
      reply:
        "I've submitted your refund request right now. The Rs 120 will be returned to your original payment method within 5 to 7 business days. You'll receive a confirmation email shortly.",
      confidence: 88,
      action_result: { type: 'refund_staged', amount: 'Rs 120' },
      resolution_step: 'resolved',
      resolution_complete: true,
    }
  }

  if (lower.includes('cancel')) {
    return {
      reply:
        "Your order is already marked as cancelled from our end, no further action needed there. Would you like me to check on the refund status or help with anything else?",
      confidence: 85,
      action_result: { type: 'cancel_staged', order_number: '1007' },
      resolution_step: 'acting',
    }
  }

  if (lower.includes('ship') || lower.includes('delivery') || lower.includes('address')) {
    return {
      reply:
        "Standard shipping takes 5 to 7 business days within Pakistan. Express (2 to 3 days) is available for an additional Rs 200. Would you like me to look up a specific order's tracking info?",
      confidence: 79,
      resolution_step: 'understanding',
    }
  }

  if (lower.includes('size') || lower.includes('exchange')) {
    return {
      reply:
        "We're happy to help with exchanges! To process a size exchange I'll need your order number. Could you share that with me?",
      confidence: 83,
      resolution_step: 'understanding',
    }
  }

  if (msgCount >= 4) {
    return {
      reply:
        "I've noted all the details and escalated this to our support team. A human agent will follow up within 2 hours. You'll hear from us at the email on your account.",
      confidence: 72,
      resolution_step: 'verifying',
      resolution_complete: true,
    }
  }

  return {
    reply:
      "I'm Luna, your AI support assistant. I can help you track orders, process refunds, handle returns, and answer shipping questions. What would you like help with today?",
    confidence: 95,
    resolution_step: 'understanding',
  }
}

/* ── Component ───────────────────────────────────────────────── */

export function ChatWidget({
  brandId,
  orgId,
  agentName = 'Luna',
  accentColor = '#6366F1',
  apiBaseUrl,
}: WidgetProps) {
  // Initialise from sessionStorage
  const [{ sessionId, messages: savedMessages }] = useState(() => loadSession(brandId))
  const hasSavedSession = savedMessages.length > 0

  const [isOpen, setIsOpen]                   = useState(false)
  const [messages, setMessages]               = useState<Message[]>(savedMessages)
  const [orbState, setOrbState]               = useState<OrbState>('idle')
  const [resolutionSteps, setResolutionSteps] = useState<ResolutionStep[]>(INITIAL_STEPS)
  const [orderData, setOrderData]             = useState<OrderData | null>(null)
  const [customerName, setCustomerName]       = useState<string | null>(null)
  const [unreadCount, setUnreadCount]         = useState(0)

  // Persist to sessionStorage whenever messages change
  useEffect(() => {
    if (messages.length > 0) {
      saveSession(brandId, sessionId, messages)
    }
  }, [messages, brandId, sessionId])

  /* ── Step helper ── */
  const advanceSteps = useCallback((upTo: ResolutionStepId) => {
    const upToIdx = STEP_ORDER.indexOf(upTo)
    const isTerminal = upTo === 'resolved'

    setResolutionSteps((prev) =>
      prev.map((step, i) => {
        if (i < upToIdx)                  return { ...step, status: 'complete' as const }
        if (i === upToIdx && isTerminal)  return { ...step, status: 'complete' as const }
        if (i === upToIdx && !isTerminal) return { ...step, status: 'active'   as const }
        return { ...step, status: 'pending' as const }
      })
    )
  }, [])

  /* ── Send message ── */
  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim()) return

      const userMsg: Message = {
        id: crypto.randomUUID ? crypto.randomUUID() : `u_${Date.now()}`,
        role: 'user',
        text: text.trim(),
        timestamp: Date.now(),
      }
      const typingMsg: Message = {
        id: 'typing',
        role: 'assistant',
        text: '',
        timestamp: Date.now(),
        isTyping: true,
      }

      setMessages((prev) => [...prev, userMsg, typingMsg])
      setOrbState('thinking')
      advanceSteps('understanding')

      const history = [...messages, userMsg]
        .filter((m) => !m.isTyping)
        .map((m) => ({ role: m.role, content: m.text }))

      const userMsgCount = messages.filter((m) => m.role === 'user').length

      try {
        let data: ApiResponse

        if (apiBaseUrl === 'demo') {
          await new Promise((r) => setTimeout(r, 1200))
          data = getMockResponse(text, userMsgCount)
        } else {
          const res = await fetch(`${apiBaseUrl}/api/v2/widget/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: text.trim(),
              session_id: sessionId,
              brand_id: brandId,
              org_id: orgId,
              customer_name: customerName ?? undefined,
              source: 'chat',
              conversation_history: history,
            }),
          })
          if (!res.ok) throw new Error(`API ${res.status}`)
          data = await res.json()
        }

        if (data.customer_name && !customerName) setCustomerName(data.customer_name)
        if (data.order_data) setOrderData(data.order_data)

        if (data.resolution_step) {
          advanceSteps(data.resolution_step)
          if (data.resolution_step === 'resolved') {
            setOrbState('resolved')
            setTimeout(() => setOrbState('idle'), 4000)
          } else if (data.resolution_step === 'acting') {
            setOrbState('acting')
          } else {
            setOrbState('idle')
          }
        } else {
          setOrbState('idle')
        }

        const replyMsg: Message = {
          id: crypto.randomUUID ? crypto.randomUUID() : `a_${Date.now()}`,
          role: 'assistant',
          text: data.reply,
          confidence: data.confidence,
          timestamp: Date.now(),
          orderData: data.order_data,
          actionResult: data.action_result,
          resolutionComplete: data.resolution_complete ?? false,
        }

        setMessages((prev) => [...prev.filter((m) => m.id !== 'typing'), replyMsg])

        if (!isOpen) setUnreadCount((c) => c + 1)
      } catch {
        setOrbState('error')
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== 'typing'),
          {
            id: `err_${Date.now()}`,
            role: 'assistant',
            text: 'Sorry, I had a little trouble there. Please try again!',
            timestamp: Date.now(),
          },
        ])
        setTimeout(() => setOrbState('idle'), 3500)
      }
    },
    [messages, sessionId, brandId, orgId, customerName, isOpen, advanceSteps, apiBaseUrl]
  )

  const handleOpen = () => {
    setIsOpen(true)
    setUnreadCount(0)
  }

  const handleClose = () => setIsOpen(false)

  const showActionCards = messages.length === 0

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 0,
        right: 0,
        zIndex: 9999,
        pointerEvents: 'none',
      }}
    >
      {/* ── Panel ── */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            key="panel"
            variants={panelVariants}
            initial="closed"
            animate="open"
            exit="closed"
            className="rc-panel-wrap"
            style={{
              position: 'absolute',
              bottom: '84px',
              right: '24px',
              width: '420px',
              transformOrigin: 'bottom right',
              pointerEvents: 'all',
            }}
          >
            <div
              className="resolution-panel"
              style={{ height: showActionCards ? 'auto' : '580px' }}
            >
              <AnimatePresence mode="wait">
                {showActionCards ? (
                  <motion.div
                    key="action-cards"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0, scale: 0.97 }}
                    transition={{ duration: 0.2 }}
                  >
                    <ActionCards
                      agentName={agentName}
                      accentColor={accentColor}
                      orbState={orbState}
                      onClose={handleClose}
                      onSend={sendMessage}
                    />
                  </motion.div>
                ) : (
                  <motion.div
                    key="resolution-canvas"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.25 }}
                    style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
                  >
                    <ResolutionCanvas
                      messages={messages}
                      orbState={orbState}
                      resolutionSteps={resolutionSteps}
                      orderData={orderData}
                      agentName={agentName}
                      accentColor={accentColor}
                      sessionId={sessionId}
                      apiBaseUrl={apiBaseUrl}
                      hasSavedSession={hasSavedSession}
                      onClose={handleClose}
                      onSend={sendMessage}
                    />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Launcher ── */}
      <div
        style={{
          position: 'absolute',
          bottom: '24px',
          right: '24px',
          pointerEvents: 'all',
        }}
      >
        <LauncherButton
          isOpen={isOpen}
          unreadCount={unreadCount}
          accentColor={accentColor}
          onClick={isOpen ? handleClose : handleOpen}
        />
      </div>
    </div>
  )
}
