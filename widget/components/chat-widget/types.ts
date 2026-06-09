export type OrbState =
  | 'idle'
  | 'thinking'
  | 'acting'
  | 'resolved'
  | 'escalated'
  | 'error'

export type ResolutionStep = {
  id: string
  label: string
  detail?: string
  status: 'pending' | 'active' | 'complete'
}

export type ActionResult = {
  type: 'cancel_staged' | 'refund_staged' | 'address_updated' | 'restore_staged'
  order_number?: string
  amount?: string
  new_address?: string
}

export type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
  confidence?: number
  timestamp: number
  isTyping?: boolean
  orderData?: OrderData
  actionResult?: ActionResult
  resolutionComplete?: boolean
}

export type OrderItem = {
  name: string
  quantity: number
  price: string
}

export type OrderData = {
  orderNumber: string
  items: OrderItem[]
  status: 'fulfilled' | 'pending' | 'cancelled' | 'processing' | 'refunded' | 'restocked' | 'unfulfilled'
  paymentStatus: 'paid' | 'pending' | 'refunded'
  cancelledAt?: string
  tracking_url?: string
  total?: string
  currency?: string
}

export type QuickAction = {
  label: string
  icon: string
  message: string | null
}

export type WidgetProps = {
  brandId: string
  orgId: string
  agentName?: string      // default: "Luna"
  accentColor?: string    // default: "#6366F1"
  apiBaseUrl: string      // pass "demo" to use built-in mock
}

export type ApiRequest = {
  message: string
  session_id: string
  brand_id: string
  org_id: string
  customer_name?: string
  source: 'chat'
  conversation_history: { role: 'user' | 'assistant'; content: string }[]
}

export type ResolutionStepId =
  | 'understanding'
  | 'gathering'
  | 'acting'
  | 'verifying'
  | 'resolved'

export type ApiResponse = {
  reply: string
  confidence?: number
  order_data?: OrderData
  action_result?: ActionResult
  resolution_step?: ResolutionStepId
  resolution_complete?: boolean
  customer_name?: string
}
