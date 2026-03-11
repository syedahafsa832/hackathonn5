import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mail,
  MessageSquare,
  ShoppingBag,
  ChevronRight,
  Send,
  Settings,
  User,
  AlertTriangle,
  CheckCircle,
  Clock,
  Star,
  DollarSign,
  Tag,
  FileText,
  Edit3,
  RefreshCw,
  Store,
  CreditCard,
  Package,
  Zap,
} from "lucide-react";

// API Base URL - Railway backend
const API_BASE_URL = "https://hackathonn5-production.up.railway.app";

// Mock ghost draft response
const mockGhostDraft = `Subject: Re: Your Return Request - We're Here to Help

Hi Sarah,

Thank you for reaching out to us. I completely understand your frustration with the sizing issue.

After reviewing your case, I'm pleased to offer you the following options:

1. **Exchange for Size XL** - We have your preferred size in stock and ready to ship immediately
2. **Full Refund** - Process a 100% refund to your original payment method
3. **Store Credit** - Receive 110% credit for future purchases

Which option would you prefer? I'm happy to assist you further.

Best regards,
AI Support Assistant`;

// Ticket Sidebar Component
function TicketSidebar({ tickets, selectedTicketId, onSelectTicket }) {
  const sentimentColors = {
    frustrated: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444", border: "rgba(239, 68, 68, 0.3)" },
    inquisitive: { bg: "rgba(59, 130, 246, 0.15)", text: "#3B82F6", border: "rgba(59, 130, 246, 0.3)" },
    neutral: { bg: "rgba(156, 163, 175, 0.15)", text: "#9CA3AF", border: "rgba(156, 163, 175, 0.3)" },
  };

  const statusColors = {
    escalated: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444" },
    pending: { bg: "rgba(245, 158, 11, 0.15)", text: "#F59E0B" },
  };

  return (
    <div
      className="w-80 flex-shrink-0 overflow-y-auto"
      style={{
        borderRight: "1px solid rgba(255, 255, 255, 0.05)",
        background: "rgba(255, 255, 255, 0.02)",
      }}
    >
      <div className="p-4" style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}>
        <h2
          className="text-sm font-medium uppercase tracking-wider"
          style={{ color: "rgba(245, 245, 245, 0.8)" }}
        >
          Escalated & Pending Tickets
        </h2>
      </div>
      <div>
        {tickets.map((ticket) => {
          const isSelected = ticket.id === selectedTicketId;
          const sentiment = sentimentColors[ticket.sentiment];
          const status = statusColors[ticket.status];

          return (
            <motion.button
              key={ticket.id}
              onClick={() => onSelectTicket(ticket.id)}
              className="w-full text-left p-4 transition-colors"
              style={{
                background: isSelected ? "rgba(0, 229, 255, 0.1)" : "transparent",
                borderLeft: isSelected ? "2px solid #00E5FF" : "2px solid transparent",
                borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
              }}
              whileHover={{ background: "rgba(255, 255, 255, 0.05)" }}
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <ticket.channelIcon
                    className="w-3.5 h-3.5"
                    style={{ color: "rgba(245, 245, 245, 0.5)" }}
                  />
                  <span
                    className="text-xs font-mono"
                    style={{ color: "rgba(245, 245, 245, 0.4)" }}
                  >
                    {ticket.id}
                  </span>
                </div>
                <span
                  className="text-xs px-2 py-0.5 rounded-sm capitalize"
                  style={{
                    background: sentiment.bg,
                    color: sentiment.text,
                    border: `1px solid ${sentiment.border}`,
                  }}
                >
                  {ticket.sentiment}
                </span>
              </div>
              <div className="text-sm font-medium mb-1" style={{ color: "#F5F5F5" }}>
                {ticket.customerName}
              </div>
              <div className="text-xs mb-2" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                {ticket.intent}
              </div>
              <div className="flex items-center justify-between">
                <span
                  className="text-xs px-2 py-0.5 rounded-sm"
                  style={{
                    background: status.bg,
                    color: status.text,
                  }}
                >
                  {ticket.status}
                </span>
                <span className="text-xs" style={{ color: "rgba(245, 245, 245, 0.4)" }}>
                  {ticket.createdAt}
                </span>
              </div>
            </motion.button>
          );
        })}
      </div>
    </div>
  );
}

// Customer Brief Component
function CustomerBrief({ ticket }) {
  return (
    <div
      className="p-4 rounded-md mb-4"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div
            className="w-10 h-10 rounded-md flex items-center justify-center"
            style={{ background: "rgba(157, 80, 187, 0.2)" }}
          >
            <User className="w-5 h-5" style={{ color: "#9D50BB" }} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
                {ticket.customerName}
              </span>
              {ticket.vipStatus === "VIP" && (
                <Star className="w-3.5 h-3.5" style={{ color: "#F59E0B" }} fill="#F59E0B" />
              )}
            </div>
            <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              {ticket.customerEmail}
            </div>
          </div>
        </div>
        <div className="text-right">
          <div className="flex items-center gap-1 justify-end">
            <span className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              LTV
            </span>
            <DollarSign className="w-3 h-3" style={{ color: "#22C55E" }} />
          </div>
          <span className="text-sm font-medium" style={{ color: "#22C55E" }}>
            ${ticket.ltv.toLocaleString()}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs" style={{ color: "rgba(245, 245, 245, 0.6)" }}>
        <div className="flex items-center gap-1">
          <Tag className="w-3 h-3" />
          <span>Order #{ticket.orderId}</span>
        </div>
        <div className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          <span>Created {ticket.createdAt}</span>
        </div>
      </div>
    </div>
  );
}

// AI Insight Component
function AIInsight({ insight }) {
  return (
    <div
      className="p-4 rounded-md mb-4"
      style={{
        background: "rgba(0, 229, 255, 0.05)",
        border: "1px solid rgba(0, 229, 255, 0.2)",
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Zap className="w-4 h-4" style={{ color: "#00E5FF" }} />
        <span
          className="text-xs font-medium uppercase tracking-wider"
          style={{ color: "#00E5FF" }}
        >
          AI Insight
        </span>
      </div>
      <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.9)", lineHeight: 1.6 }}>
        {insight}
      </p>
    </div>
  );
}

// Ghost Draft Editor Component
function GhostDraftEditor({ draft, onChange }) {
  const [isEditing, setIsEditing] = useState(false);

  return (
    <div
      className="rounded-md overflow-hidden"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-3"
        style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
      >
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4" style={{ color: "#9D50BB" }} />
          <span
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "rgba(245, 245, 245, 0.8)" }}
          >
            Ghost Draft Editor
          </span>
          <span
            className="text-xs px-2 py-0.5 rounded-sm"
            style={{ background: "rgba(157, 80, 187, 0.2)", color: "#9D50BB" }}
          >
            AI Generated
          </span>
        </div>
        <button
          onClick={() => setIsEditing(!isEditing)}
          className="flex items-center gap-1 px-2 py-1 rounded-sm text-xs transition-colors hover:bg-white/5"
          style={{ color: "rgba(245, 245, 245, 0.6)" }}
        >
          <Edit3 className="w-3 h-3" />
          {isEditing ? "Done" : "Edit"}
        </button>
      </div>
      <div className="p-4">
        {isEditing ? (
          <textarea
            value={draft}
            onChange={(e) => onChange(e.target.value)}
            className="w-full h-48 text-sm p-3 rounded-md resize-none focus:outline-none"
            style={{
              background: "rgba(0, 0, 0, 0.3)",
              color: "#F5F5F5",
              border: "1px solid rgba(0, 229, 255, 0.3)",
            }}
          />
        ) : (
          <p
            className="text-sm whitespace-pre-wrap"
            style={{ color: "rgba(245, 245, 245, 0.8)", lineHeight: 1.7 }}
          >
            {draft}
          </p>
        )}
      </div>
    </div>
  );
}

// Action Buttons Component
function ActionButtons({ onApprove, onModifyLogic, onHumanTakeover }) {
  return (
    <div className="flex items-center gap-3 py-4">
      <motion.button
        onClick={onApprove}
        className="flex items-center gap-2 px-6 py-3 rounded-sm font-medium text-sm"
        style={{
          background: "#00E5FF",
          color: "#090909",
        }}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
      >
        <Send className="w-4 h-4" />
        Approve & Send
      </motion.button>

      <div className="relative group">
        <motion.button
          className="flex items-center gap-2 px-4 py-3 rounded-sm font-medium text-sm transition-colors"
          style={{
            background: "rgba(255, 255, 255, 0.1)",
            color: "#F5F5F5",
            border: "1px solid rgba(255, 255, 255, 0.2)",
          }}
          whileHover={{ background: "rgba(255, 255, 255, 0.15)" }}
        >
          <Settings className="w-4 h-4" />
          Modify Logic
          <ChevronRight className="w-3 h-3" />
        </motion.button>
        {/* Dropdown menu */}
        <div
          className="absolute left-0 top-full mt-1 w-48 rounded-md opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-10"
          style={{
            background: "rgba(20, 20, 20, 0.95)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
        >
          <button
            className="w-full text-left px-4 py-2 text-sm hover:bg-white/5"
            style={{ color: "#F5F5F5" }}
          >
            Increase Refund %
          </button>
          <button
            className="w-full text-left px-4 py-2 text-sm hover:bg-white/5"
            style={{ color: "#F5F5F5" }}
          >
            Change Exchange Item
          </button>
          <button
            className="w-full text-left px-4 py-2 text-sm hover:bg-white/5"
            style={{ color: "#F5F5F5" }}
          >
            Add Compensation
          </button>
        </div>
      </div>

      <motion.button
        onClick={onHumanTakeover}
        className="flex items-center gap-2 px-4 py-3 rounded-sm font-medium text-sm ml-auto transition-colors"
        style={{
          background: "rgba(239, 68, 68, 0.1)",
          color: "#EF4444",
          border: "1px solid rgba(239, 68, 68, 0.3)",
        }}
        whileHover={{ background: "rgba(239, 68, 68, 0.2)" }}
      >
        <User className="w-4 h-4" />
        Full Human Takeover
      </motion.button>
    </div>
  );
}

// Shopify Actions Panel Component
function ShopifyActionsPanel({ ticket }) {
  const [orderCreated, setOrderCreated] = useState(false);

  return (
    <div
      className="rounded-md overflow-hidden mt-4"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-3"
        style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
      >
        <div className="flex items-center gap-2">
          <Store className="w-4 h-4" style={{ color: "#22C55E" }} />
          <span
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "rgba(245, 245, 245, 0.8)" }}
          >
            One-Click Shopify Actions
          </span>
        </div>
        <div className="flex items-center gap-1">
          {orderCreated && (
            <span className="flex items-center gap-1 text-xs" style={{ color: "#22C55E" }}>
              <CheckCircle className="w-3 h-3" />
              Draft Order Created
            </span>
          )}
        </div>
      </div>
      <div className="p-4 flex items-center gap-3">
        <motion.button
          onClick={() => setOrderCreated(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-sm text-sm transition-colors"
          style={{
            background: orderCreated ? "rgba(34, 197, 94, 0.2)" : "rgba(255, 255, 255, 0.1)",
            color: orderCreated ? "#22C55E" : "#F5F5F5",
            border: `1px solid ${orderCreated ? "rgba(34, 197, 94, 0.3)" : "rgba(255, 255, 255, 0.2)"}`,
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Package className="w-4 h-4" />
          {orderCreated ? "Order Created" : "Draft Exchange Order"}
        </motion.button>

        <motion.button
          className="flex items-center gap-2 px-4 py-2 rounded-sm text-sm transition-colors"
          style={{
            background: "rgba(255, 255, 255, 0.1)",
            color: "#F5F5F5",
            border: "1px solid rgba(255, 255, 255, 0.2)",
          }}
          whileHover={{ background: "rgba(255, 255, 255, 0.15)" }}
        >
          <CreditCard className="w-4 h-4" />
          Issue Store Credit
        </motion.button>

        <motion.button
          className="flex items-center gap-2 px-4 py-2 rounded-sm text-sm transition-colors"
          style={{
            background: "rgba(255, 255, 255, 0.1)",
            color: "#F5F5F5",
            border: "1px solid rgba(255, 255, 255, 0.2)",
          }}
          whileHover={{ background: "rgba(255, 255, 255, 0.15)" }}
        >
          <RefreshCw className="w-4 h-4" />
          Generate Return Label
        </motion.button>
      </div>
    </div>
  );
}

// Main Smart Approval Inbox Component
export default function SmartApprovalInbox() {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedTicketId, setSelectedTicketId] = useState(null);
  const [draft, setDraft] = useState(mockGhostDraft);

  // Fetch tickets from API on component mount
  useEffect(() => {
    const fetchTickets = async () => {
      try {
        setLoading(true);
        let data = null;

        // Try /api/ai-mode first (Decision Hub endpoint)
        try {
          const response = await fetch(`${API_BASE_URL}/api/ai-mode`);
          if (response.ok) {
            data = await response.json();
          }
        } catch (e) {
          console.warn("/api/ai-mode failed, trying /api/tickets:", e);
        }

        // Fallback to /api/tickets if ai-mode fails or returns empty
        if (!data || !data.tickets || data.tickets.length === 0) {
          const ticketsResponse = await fetch(`${API_BASE_URL}/api/tickets`);
          if (ticketsResponse.ok) {
            const ticketsData = await ticketsResponse.json();
            // Handle both array response and {tickets: [...]} response
            data = { tickets: Array.isArray(ticketsData) ? ticketsData : ticketsData.tickets || [] };
          }
        }

        if (!data || !data.tickets) {
          throw new Error("No tickets data received from API");
        }

        console.log("API Response:", data);

        // Transform API response to match UI format
        const transformedTickets = (data.tickets || []).map((ticket) => ({
          id: ticket.ticket_id || ticket.id,
          customerName: ticket.customer_name || ticket.customerName || "Unknown",
          customerEmail: ticket.customer_email || ticket.customerEmail || "",
          orderId: ticket.order_id || ticket.orderId || "",
          sentiment: ticket.sentiment || "neutral",
          status: ticket.status || "pending",
          vipStatus: ticket.vip_status || ticket.vipStatus || "Regular",
          ltv: ticket.ltv || 0,
          intent: ticket.intent || "General Inquiry",
          aiInsight: ticket.ai_reasoning || ticket.aiInsight || "No AI analysis available",
          channel: ticket.channel || "email",
          channelIcon: ticket.channel === "whatsapp" ? MessageSquare : Mail,
          createdAt: ticket.createdAt || ticket.created_at || "Just now",
        }));

        console.log("Transformed tickets:", transformedTickets);
        setTickets(transformedTickets);

        // Set initial selected ticket if available
        if (transformedTickets.length > 0) {
          setSelectedTicketId(transformedTickets[0].id);
        }
      } catch (err) {
        console.error("Failed to fetch tickets:", err);
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    fetchTickets();
  }, []);

  // Find selected ticket from fetched data
  const selectedTicket =
    tickets.find((t) => t.id === selectedTicketId) || tickets[0] || null;

  // Handle case when no tickets are available
  if (loading) {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: "#090909" }}
      >
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 mx-auto mb-4" style={{ borderColor: "#00E5FF" }}></div>
          <p style={{ color: "rgba(245, 245, 245, 0.7)" }}>Loading tickets...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: "#090909" }}
      >
        <div className="text-center p-8" style={{ background: "rgba(239, 68, 68, 0.1)", borderRadius: "8px" }}>
          <AlertTriangle className="w-12 h-12 mx-auto mb-4" style={{ color: "#EF4444" }} />
          <p style={{ color: "#EF4444" }}>Failed to load tickets</p>
          <p style={{ color: "rgba(245, 245, 245, 0.5)", marginTop: "8px" }}>{error}</p>
        </div>
      </div>
    );
  }

  if (!selectedTicket) {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: "#090909" }}
      >
        <div className="text-center">
          <CheckCircle className="w-16 h-16 mx-auto mb-4" style={{ color: "#22C55E" }} />
          <h2 style={{ color: "#F5F5F5", fontSize: "1.25rem", marginBottom: "8px" }}>All Caught Up!</h2>
          <p style={{ color: "rgba(245, 245, 245, 0.5)" }}>No pending tickets in the queue</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen" style={{ background: "#090909" }}>
      {/* Ticket Sidebar */}
      <TicketSidebar
        tickets={tickets}
        selectedTicketId={selectedTicketId}
        onSelectTicket={setSelectedTicketId}
      />

      {/* Main Workspace */}
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={selectedTicketId}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.3 }}
            className="p-6"
          >
            {/* Deep Context Workspace Header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1
                  className="text-xl font-medium tracking-wide"
                  style={{ color: "#F5F5F5", letterSpacing: "0.03em" }}
                >
                  Smart Approval Inbox
                </h1>
                <p className="text-sm mt-1" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                  Support Team View - Turn 10-min tasks into 2-second clicks
                </p>
              </div>
            </div>

            {/* Customer Brief */}
            <CustomerBrief ticket={selectedTicket} />

            {/* AI Insight */}
            <AIInsight insight={selectedTicket.aiInsight} />

            {/* Ghost Draft Editor */}
            <GhostDraftEditor draft={draft} onChange={setDraft} />

            {/* Primary Action Row */}
            <ActionButtons
              onApprove={() => console.log("Approved")}
              onModifyLogic={() => console.log("Modify logic")}
              onHumanTakeover={() => console.log("Human takeover")}
            />

            {/* One-Click Shopify Actions Panel */}
            <ShopifyActionsPanel ticket={selectedTicket} />
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
