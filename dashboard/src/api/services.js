import client from './client';

const api = {
  // --- TICKETS / CONVERSATIONS ---

  getConversations: async (params = {}) => {
    const query = {};
    if (params.status && params.status !== 'active') query.status = params.status;
    if (params.store_id) query.store_id = params.store_id;
    // Let request failures propagate as real errors instead of swallowing them
    // into an empty array — React Query then keeps the last good list on screen
    // and exposes `error` separately, rather than the UI reading "no conversations".
    const res = await client.get('/api/tickets', { params: query });
    const data = res.data;
    return Array.isArray(data) ? data : data?.tickets || [];
  },

  getTicket: async (id) => {
    const res = await client.get(`/api/tickets/${id}`);
    return res.data;
  },

  // Returns messages array from ticket — always shows the full conversation.
  // Sources merged in priority order:
  //   1. ticket.messages JSONB array (primary, written by poller + processor + send-reply)
  //   2. ticket.ai_reply / ticket.ai_draft (flat fields, written by older code paths)
  //   3. ticket.message (flat customer body, fallback for very old tickets)
  getConversationMessages: async (id) => {
    const res = await client.get(`/api/tickets/${id}`).catch(() => ({ data: null }));
    const ticket = res.data;
    if (!ticket) return [];

    // Parse messages — may come back as a JSON string or already an array
    let msgs = ticket.messages || [];
    if (typeof msgs === 'string') {
      try { msgs = JSON.parse(msgs); } catch { msgs = []; }
    }

    // Build the thread from the messages array
    const thread = msgs.map(m => ({
      ...m,
      role: m.direction === 'inbound' ? 'user' : m.role || 'ai',
      content: m.body || m.content || '',
      isDraft: m.direction === 'draft',
    }));

    // If the messages array has no inbound customer message (old tickets),
    // prepend from ticket.message flat field
    const hasInbound = thread.some(m => m.role === 'user');
    if (!hasInbound) {
      const customerBody = ticket.message || ticket.content || ticket.body || ticket.email_body;
      if (customerBody) {
        thread.unshift({ role: 'user', content: customerBody, created_at: ticket.created_at });
      }
    }

    // If the messages array has no AI/outbound message (older tickets where ai_reply
    // was stored as a flat field but never appended to the messages array), append it.
    // Backend-appended entries are tagged role="assistant" (see message_processor.py /
    // v2_tickets.py respond_to_ticket), never "ai" — checking only "ai" here made this
    // always true-negative, so this fallback fired on every ticket and duplicated the
    // reply that was already in `messages[]`.
    const hasAiMessage = thread.some(m => m.role === 'ai' || m.role === 'assistant');
    if (!hasAiMessage) {
      const aiText = ticket.ai_reply || ticket.ai_draft || ticket.ai_response;
      if (aiText) {
        thread.push({
          role: 'ai',
          content: aiText,
          created_at: ticket.updated_at,
          isDraft: !ticket.ai_reply,
        });
      }
    }

    // Safety-net dedup: collapse consecutive messages with the same role+content.
    // Backend persistence should already guarantee one write per AI reply, but
    // this protects replay rendering even if a duplicate ever slips through.
    const deduped = thread.filter((m, i) => {
      const prev = thread[i - 1];
      return !prev || prev.role !== m.role || prev.content !== m.content;
    });

    return deduped;
  },

  updateTicket: async (id, updates) => {
    const res = await client.patch(`/api/tickets/${id}`, updates);
    return res.data;
  },

  sendReply: async (id, body) => {
    const res = await client.post(`/api/tickets/${id}/send-reply`, { body });
    return res.data;
  },

  cancelOrder: async (ticketId) => {
    const res = await client.post(`/api/v2/tickets/${ticketId}/actions/cancel`, {}, { timeout: 30000 });
    return res.data;
  },

  approveAiResponse: async (id) => {
    const res = await client.post(`/api/tickets/${id}/approve-ai`);
    return res.data;
  },

  getEscalations: async () => {
    const res = await client.get('/api/tickets', { params: { status: 'escalated' } }).catch(() => ({ data: [] }));
    const data = res.data;
    return Array.isArray(data) ? data : data?.tickets || [];
  },

  markAsRead: async (_id) => ({ success: true }),
  takeoverConversation: async (id) => {
    const res = await client.post(`/api/tickets/${id}/takeover`, {});
    return res.data;
  },
  releaseConversation: async (id) => {
    const res = await client.post(`/api/tickets/${id}/release`);
    return res.data;
  },
  sendAdminMessage: async (id, text) => {
    const res = await client.post(`/api/tickets/${id}/send-reply`, { body: text });
    return res.data;
  },

  // --- ACTIONS ---

  getActions: async (params = {}) => {
    // v1 endpoint uses tenant_id isolation (correct for this auth system)
    const path = params.status === 'pending' ? '/api/v1/actions/pending' : '/api/v1/actions/history';
    const res = await client.get(path).catch(() => ({ data: [] }));
    const data = res.data;
    return Array.isArray(data) ? data : data?.actions || [];
  },

  approveAction: async (id) => {
    // v1 approve calls actions_service.approve_action() which runs _post_execution_notify
    const res = await client.post(`/api/v1/actions/${id}/approve`);
    return res.data;
  },

  rejectAction: async (id, reason) => {
    const res = await client.post(`/api/v1/actions/${id}/reject`, { reason });
    return res.data;
  },

  bulkRejectActions: async ({ action_ids, clear_all } = {}) => {
    const res = await client.post('/api/v2/actions/bulk-reject', { action_ids, clear_all });
    return res.data;
  },

  bulkCloseEscalations: async ({ ticket_ids, close_all } = {}) => {
    const res = await client.post('/api/v2/tickets/bulk-escalation-close', { ticket_ids, close_all });
    return res.data;
  },

  // --- BRANDS ---

  // --- CANNED RESPONSES ---
  getCannedResponses: async () => {
    const res = await client.get('/api/v1/canned-responses').catch(() => ({ data: { items: [] } }));
    return res.data?.items || [];
  },

  getBrands: async () => {
    const res = await client.get('/api/brands').catch(() => ({ data: [] }));
    const data = res.data;
    return Array.isArray(data) ? data : data?.brands || [];
  },

  // --- STATS ---

  getStats: async () => {
    const [ticketsRes, actionsRes] = await Promise.all([
      client.get('/api/tickets').catch(() => ({ data: [] })),
      client.get('/api/v1/actions/pending').catch(() => ({ data: [] })),
    ]);
    const tickets = Array.isArray(ticketsRes.data) ? ticketsRes.data : ticketsRes.data?.tickets || [];
    const pendingActions = Array.isArray(actionsRes.data) ? actionsRes.data : actionsRes.data?.actions || [];
    const active = tickets.filter(t => ['open', 'processing', 'human_managing', 'escalated', 'auto_resolved_review', 'review_needed'].includes(t.status) || !t.status);
    // AI Responded = AI sent the email (regardless of escalation for financial actions)
    const aiHandled = tickets.filter(t =>
      t.email_sent === true ||
      ['auto_resolved', 'auto_resolved_review'].includes(t.status)
    );
    const aiHandledPct = tickets.length > 0 ? Math.round((aiHandled.length / tickets.length) * 100) : 0;
    return {
      activeConversations: active.length,
      totalConversations: tickets.length,
      escalatedChats: tickets.filter(t => t.status === 'escalated').length,
      pendingApprovals: pendingActions.length,
      aiHandledPct,
      // avg first response in seconds (from tickets with first_response_at set)
      avgResponseSeconds: (() => {
        const responded = tickets.filter(t => t.first_response_at && t.created_at);
        return responded.length > 0
          ? Math.round(responded.reduce((sum, t) => sum + (new Date(t.first_response_at) - new Date(t.created_at)) / 1000, 0) / responded.length)
          : null;
      })(),
      // CSAT: % of YES responses out of all surveyed tickets
      csatPct: (() => {
        const surveyed = tickets.filter(t => t.csat_sent);
        const positive = surveyed.filter(t => (t.csat_response || '').toUpperCase().trim() === 'YES');
        return surveyed.length > 0 ? Math.round((positive.length / surveyed.length) * 100) : null;
      })(),
    };
  },

  // --- PLATFORM ADMIN ---

  getAdminTenants: async () => {
    const res = await client.get('/api/v2/admin/tenants');
    return res.data;
  },

  getAdminUpgradeRequests: async () => {
    const res = await client.get('/api/v2/admin/upgrade-requests');
    return res.data;
  },

  activateUpgradeRequest: async (id) => {
    const res = await client.post(`/api/v2/admin/upgrade-requests/${id}/activate`);
    return res.data;
  },

  // --- ACCOUNT / UPGRADE ---

  getMe: async () => {
    const res = await client.get('/api/v1/auth/me');
    return res.data;
  },

  requestUpgrade: async (payload) => {
    const res = await client.post('/api/v2/upgrade-requests', payload);
    return res.data;
  },
};

export default api;
