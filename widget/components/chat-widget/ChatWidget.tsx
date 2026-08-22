'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
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
  ChatStreamEvent,
  ActivityStep,
} from './types'

/* ── Timing ──────────────────────────────────────────────────── */

const REQUEST_TIMEOUT_MS = 45_000   // hard cap — treated as a failure, not an endless spinner
const SLOW_RESPONSE_MS = 8_000      // no new activity update by this point -> show generic reassurance

/* ── NDJSON stream reader ───────────────────────────────────────
 * Reads the `?stream=1` response body one line at a time. Each line is a
 * ChatStreamEvent. `onStatus` fires for every real backend-reported activity
 * stage (stage key + label); the final `result` line resolves the returned
 * promise. A `type: "error"` line (already sanitized server-side — no
 * internals) rejects. */
async function readChatStream(
  body: ReadableStream<Uint8Array>,
  onStatus: (stage: string, label: string) => void
): Promise<ApiResponse> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handleLine = (line: string): ApiResponse | null => {
    if (!line.trim()) return null
    let evt: ChatStreamEvent
    try {
      evt = JSON.parse(line)
    } catch {
      return null
    }
    if (evt.type === 'status') {
      onStatus(evt.stage, evt.label)
      return null
    }
    if (evt.type === 'error') {
      throw new Error(evt.message)
    }
    const { type: _type, ...rest } = evt
    return rest as ApiResponse
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 1)
      const result = handleLine(line)
      if (result) return result
    }
  }
  const trailing = handleLine(buffer)
  if (trailing) return trailing
  throw new Error('Connection closed before a reply arrived')
}

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
  // Real resolution steps for the in-flight turn, built only from actual
  // backend `status` events — starts empty every turn, never pre-seeded
  // with a guessed stage. Consecutive events sharing a `stage` update that
  // same step in place instead of appending a duplicate line.
  const [activitySteps, setActivitySteps]     = useState<ActivityStep[]>([])
  const [activitySlow, setActivitySlow]       = useState(false)

  // Persist to sessionStorage whenever messages change
  useEffect(() => {
    if (messages.length > 0) {
      saveSession(brandId, sessionId, messages)
    }
  }, [messages, brandId, sessionId])

  /* ── Poll for the real merchant-approved outcome of a staged cancellation ──
   * A `cancel_staged` action_result only ever reflects what the customer
   * asked for — approval/execution happens later, out-of-band, in the
   * merchant dashboard. This never flips a message to "cancelled"
   * optimistically; it only reflects what /widget/actions/{id}/status
   * reports the backend has actually confirmed with Shopify. One stable
   * interval for the widget's lifetime (reading fresh messages via a ref)
   * avoids restarting/losing polls every time an unrelated message arrives. */
  const messagesRef = useRef(messages)
  useEffect(() => { messagesRef.current = messages }, [messages])

  useEffect(() => {
    if (apiBaseUrl === 'demo') return
    const POLL_INTERVAL_MS = 4000
    const MAX_POLL_MS = 5 * 60 * 1000
    const startedAt = new Map<string, number>()

    const interval = setInterval(() => {
      const now = Date.now()
      const toCheck = messagesRef.current.filter((m) => {
        const ar = m.actionResult
        if (ar?.type !== 'cancel_staged' || !ar.action_id) return false
        if (ar.status === 'executed' || ar.status === 'failed') return false
        if (!startedAt.has(ar.action_id)) startedAt.set(ar.action_id, now)
        return now - (startedAt.get(ar.action_id) as number) < MAX_POLL_MS
      })

      toCheck.forEach((msg) => {
        const actionId = msg.actionResult!.action_id!
        fetch(`${apiBaseUrl}/api/v2/widget/actions/${actionId}/status`)
          .then((res) => (res.ok ? res.json() : null))
          .then((data: { status?: string } | null) => {
            if (data?.status !== 'executed' && data?.status !== 'failed') return
            const finalStatus = data.status
            setMessages((prev) =>
              prev.map((m) =>
                m.id === msg.id && m.actionResult
                  ? { ...m, actionResult: { ...m.actionResult, status: finalStatus } }
                  : m
              )
            )
          })
          .catch(() => { /* transient network error — next tick retries */ })
      })
    }, POLL_INTERVAL_MS)

    return () => clearInterval(interval)
  }, [apiBaseUrl])

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
      setActivitySteps([])
      setActivitySlow(false)
      advanceSteps('understanding')

      const history = [...messages, userMsg]
        .filter((m) => !m.isTyping)
        .map((m) => ({ role: m.role, content: m.text }))

      const userMsgCount = messages.filter((m) => m.role === 'user').length

      // No new activity update by SLOW_RESPONSE_MS -> generic reassurance,
      // never a fresh fabricated stage. Hard-aborts at REQUEST_TIMEOUT_MS so
      // a stalled request never leaves a dead spinner on screen.
      let slowTimer: ReturnType<typeof setTimeout> | null = null
      const armSlowTimer = () => {
        if (slowTimer) clearTimeout(slowTimer)
        slowTimer = setTimeout(() => setActivitySlow(true), SLOW_RESPONSE_MS)
      }
      armSlowTimer()
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

      try {
        let data: ApiResponse

        if (apiBaseUrl === 'demo') {
          await new Promise((r) => setTimeout(r, 1200))
          data = getMockResponse(text, userMsgCount)
        } else {
          const res = await fetch(`${apiBaseUrl}/api/v2/widget/chat?stream=1`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
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

          const contentType = res.headers.get('content-type') || ''
          if (contentType.includes('ndjson') && res.body) {
            data = await readChatStream(res.body, (stage, label) => {
              setActivitySteps((prev) => {
                // Same stage as the current (last) step -> it's a refinement
                // of the same real operation, update in place. A new stage
                // -> the previous step is done, append the new one.
                if (prev.length > 0 && prev[prev.length - 1].stage === stage) {
                  return [...prev.slice(0, -1), { stage, label }]
                }
                return [...prev, { stage, label }]
              })
              setActivitySlow(false)
              armSlowTimer()
            })
          } else {
            // Server short-circuited before any tool ran (rate limit, plan
            // limit) — plain JSON, no activity stages to show.
            data = await res.json()
          }
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
      } catch (err) {
        const isTimeout = err instanceof DOMException && err.name === 'AbortError'
        setOrbState('error')
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== 'typing'),
          {
            id: `err_${Date.now()}`,
            role: 'assistant',
            text: isTimeout
              ? "This is taking longer than expected. Please try again in a moment."
              : 'Sorry, I had a little trouble there. Please try again!',
            timestamp: Date.now(),
            retryText: text.trim(),
          },
        ])
        setTimeout(() => setOrbState('idle'), 3500)
      } finally {
        clearTimeout(timeoutId)
        if (slowTimer) clearTimeout(slowTimer)
        setActivitySlow(false)
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
        // Exposed as a CSS custom property so every descendant can theme
        // off the merchant's configured accent color via var(--accent-color)
        // instead of each component hardcoding its own hex value.
        ['--accent-color' as string]: accentColor,
      } as CSSProperties}
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
                      activitySteps={activitySteps}
                      activitySlow={activitySlow}
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
