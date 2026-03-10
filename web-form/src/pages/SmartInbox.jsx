import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mail,
  MessageSquare,
  ShoppingBag,
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
  ChevronRight,
  Loader2,
  X,
  Check,
  XCircle,
} from "lucide-react";

// Toast notification component
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const bgColors = {
    success: "rgba(34, 197, 94, 0.2)",
    error: "rgba(239, 68, 68, 0.2)",
    loading: "rgba(59, 130, 246, 0.2)",
  };

  const borderColors = {
    success: "rgba(34, 197, 94, 0.5)",
    error: "rgba(239, 68, 68, 0.5)",
    loading: "rgba(59, 130, 246, 0.5)",
  };

  const icons = {
    success: Check,
    error: XCircle,
    loading: Loader2,
  };

  const Icon = icons[type];

  return (
    <motion.div
      initial={{ opacity: 0, y: 50, x: "-50%" }}
      animate={{ opacity: 1, y: 0, x: "-50%" }}
      exit={{ opacity: 0, y: 50, x: "-50%" }}
      className="fixed bottom-6 left-1/2 z-50 flex items-center gap-3 px-4 py-3 rounded-md"
      style={{
        background: bgColors[type],
        border: `1px solid ${borderColors[type]}`,
        backdropFilter: "blur(12px)",
      }}
    >
      <Icon
        className={`w-4 h-4 ${type === "loading" ? "animate-spin" : ""}`}
        style={{ color: type === "success" ? "#22C55E" : type === "error" ? "#EF4444" : "#3B82F6" }}
      />
      <span className="text-sm" style={{ color: "#F5F5F5" }}>
        {message}
      </span>
    </motion.div>
  );
}

// Mock ticket data
const mockTickets = [
  {
    id: "TKT-1001",
    customerName: "Sarah Mitchell",
    customerEmail: "sarah.mitchell@email.com",
    orderId: "ORD-1002",
    sentiment: "frustrated",
    sentimentScore: 8,
    status: "pending",
    vipStatus: "VIP",
    ltv: 2450,
    intent: "exchange",
    requestedItem: "Size XL",
    messageContent:
      "Hi, I ordered a Navy Blue T-Shirt in Size M but received Size L. I need Size XL instead. Can you please exchange this for me? The fit is completely wrong and I need this for an event this weekend.",
    channel: "email",
    channelIcon: Mail,
    createdAt: "2 hours ago",
  },
  {
    id: "TKT-1002",
    customerName: "James Chen",
    customerEmail: "james.chen@techmail.com",
    orderId: "ORD-1245",
    sentiment: "neutral",
    sentimentScore: 5,
    status: "pending",
    vipStatus: "Regular",
    ltv: 380,
    intent: "refund",
    requestedItem: null,
    messageContent:
      "I'd like to request a refund for my recent order. The delivery was later than expected and I've decided to go with a different brand.",
    channel: "chat",
    channelIcon: MessageSquare,
    createdAt: "4 hours ago",
  },
  {
    id: "TKT-1003",
    customerName: "Emily Rodriguez",
    customerEmail: "emily.r@designer.co",
    orderId: "ORD-1567",
    sentiment: "happy",
    sentimentScore: 3,
    status: "pending",
    vipStatus: "VIP",
    lvt: 5200,
    intent: "order_status",
    requestedItem: null,
    messageContent:
      "Hi, just checking on my order #1567. When will it be delivered? Thanks!",
    channel: "instagram",
    channelIcon: ShoppingBag,
    createdAt: "6 hours ago",
  },
];

// Mock processed data from backend
const mockProcessedData = {
  shopifyAudit: {
    order_id: "ORD-1002",
    order_date: "2024-01-15T10:30:00Z",
    order_total: 85.0,
    items: [{ title: "Premium Cotton T-Shirt", quantity: 1, price: 85.0, variant: "Size M / Navy Blue" }],
    return_window_open: true,
    days_remaining: 15,
    items_count: 1,
  },
  inventoryCheck: {
    item_id: "VAR-1002-XL",
    item_name: "T-Shirt Size XL",
    available_quantity: 25,
    in_stock: true,
  },
  aiRecommendation: {
    intent: "exchange",
    requested_item: "Size XL",
    sentiment_score: 8,
    sentiment_label: "Frustrated",
    recommended_action: "Exchange (Revenue Saved)",
    revenue_at_stake: 85.0,
    reasoning:
      "Item is in stock (25 available). Exchange saves the full $85.00. Customer is frustrated, so a quick exchange with free shipping would turn this negative experience positive.",
  },
};

// Section A: Original Thread
function OriginalThread({ ticket }) {
  const sentimentColors = {
    frustrated: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444", border: "rgba(239, 68, 68, 0.3)" },
    neutral: { bg: "rgba(156, 163, 175, 0.15)", text: "#9CA3AF", border: "rgba(156, 163, 175, 0.3)" },
    happy: { bg: "rgba(34, 197, 94, 0.15)", text: "#22C55E", border: "rgba(34, 197, 94, 0.3)" },
  };

  const sentiment = sentimentColors[ticket.sentiment] || sentimentColors.neutral;

  return (
    <div
      className="rounded-md overflow-hidden h-full"
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
          <Mail className="w-4 h-4" style={{ color: "#00E5FF" }} />
          <span
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "rgba(245, 245, 245, 0.8)" }}
          >
            Original Thread
          </span>
        </div>
        <span
          className="text-xs px-3 py-1 rounded-sm font-medium capitalize"
          style={{
            background: sentiment.bg,
            color: sentiment.text,
            border: `1px solid ${sentiment.border}`,
          }}
        >
          {ticket.sentiment} ({ticket.sentimentScore}/10)
        </span>
      </div>
      <div className="p-4 overflow-y-auto" style={{ maxHeight: "calc(100vh - 280px)" }}>
        <div className="flex items-start gap-3 mb-4">
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
        <div
          className="p-3 rounded-md"
          style={{ background: "rgba(0, 0, 0, 0.2)", border: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.9)", lineHeight: 1.6 }}>
            {ticket.messageContent}
          </p>
        </div>
      </div>
    </div>
  );
}

// Section B: Shopify Intelligence Card
function ShopifyIntelligenceCard({ shopifyAudit, inventoryCheck }) {
  return (
    <div
      className="rounded-md overflow-hidden h-full"
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
            Shopify Intelligence
          </span>
        </div>
      </div>
      <div className="p-4 space-y-4">
        {/* Order Summary */}
        <div>
          <div className="text-xs uppercase tracking-wider mb-2" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            Order #{shopifyAudit.order_id} Summary
          </div>
          <div className="space-y-2">
            {shopifyAudit.items.map((item, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between p-2 rounded-md"
                style={{ background: "rgba(0, 0, 0, 0.2)" }}
              >
                <div>
                  <div className="text-sm" style={{ color: "#F5F5F5" }}>
                    {item.title}
                  </div>
                  <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                    {item.variant}
                  </div>
                </div>
                <div className="text-sm font-medium" style={{ color: "#22C55E" }}>
                  ${item.price.toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Dynamic Inventory Badge */}
        <div>
          <div className="text-xs uppercase tracking-wider mb-2" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            Inventory Status
          </div>
          <div className="flex items-center gap-2">
            <motion.div
              className="w-2 h-2 rounded-full"
              style={{ background: inventoryCheck.in_stock ? "#00E5FF" : "#EF4444" }}
              animate={{
                boxShadow: inventoryCheck.in_stock
                  ? "0 0 10px #00E5FF, 0 0 20px #00E5FF"
                  : "0 0 10px #EF4444",
              }}
              transition={{ duration: 1, repeat: Infinity, repeatType: "reverse" }}
            />
            <span
              className="text-sm font-medium"
              style={{ color: inventoryCheck.in_stock ? "#00E5FF" : "#EF4444" }}
            >
              {inventoryCheck.in_stock ? `${inventoryCheck.available_quantity} units available` : "Out of Stock"}
            </span>
          </div>
        </div>

        {/* Policy Check */}
        <div>
          <div className="text-xs uppercase tracking-wider mb-2" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            30-Day Return Policy
          </div>
          <div className="flex items-center gap-2">
            {shopifyAudit.return_window_open ? (
              <>
                <CheckCircle className="w-4 h-4" style={{ color: "#22C55E" }} />
                <span className="text-sm" style={{ color: "#22C55E" }}>
                  Return Window Open
                </span>
                <span className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                  ({shopifyAudit.days_remaining} days remaining)
                </span>
              </>
            ) : (
              <>
                <XCircle className="w-4 h-4" style={{ color: "#EF4444" }} />
                <span className="text-sm" style={{ color: "#EF4444" }}>
                  Return Window Closed
                </span>
              </>
            )}
          </div>
        </div>

        {/* Order Total */}
        <div
          className="p-3 rounded-md flex items-center justify-between"
          style={{ background: "rgba(157, 80, 187, 0.1)", border: "1px solid rgba(157, 80, 187, 0.3)" }}
        >
          <span className="text-sm" style={{ color: "rgba(245, 245, 245, 0.8)" }}>
            Order Total
          </span>
          <span className="text-lg font-medium" style={{ color: "#9D50BB" }}>
            ${shopifyAudit.order_total.toFixed(2)}
          </span>
        </div>
      </div>
    </div>
  );
}

// Section C: AI Strategy Card
function AIStrategyCard({ recommendation }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.3 }}
      className="rounded-md overflow-hidden h-full"
      style={{
        background: "rgba(0, 229, 255, 0.05)",
        border: "1px solid rgba(0, 229, 255, 0.2)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-3"
        style={{ borderBottom: "1px solid rgba(0, 229, 255, 0.2)" }}
      >
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4" style={{ color: "#00E5FF" }} />
          <span
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: "#00E5FF", letterSpacing: "-0.03em" }}
          >
            AI Operational Recommendation
          </span>
        </div>
      </div>
      <div className="p-4 space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wider mb-1" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            Recommended Action
          </div>
          <div className="text-lg font-medium" style={{ color: "#00E5FF", letterSpacing: "-0.03em" }}>
            {recommendation.recommended_action}
          </div>
        </div>

        <div>
          <div className="text-xs uppercase tracking-wider mb-1" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            Revenue at Stake
          </div>
          <div className="flex items-center gap-2">
            <DollarSign className="w-5 h-5" style={{ color: "#22C55E" }} />
            <span className="text-2xl font-medium" style={{ color: "#22C55E" }}>
              ${recommendation.revenue_at_stake.toFixed(2)}
            </span>
          </div>
        </div>

        <div
          className="p-3 rounded-md"
          style={{ background: "rgba(0, 0, 0, 0.2)", border: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          <div className="text-xs uppercase tracking-wider mb-2" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
            AI Reasoning
          </div>
          <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.9)", lineHeight: 1.6 }}>
            {recommendation.reasoning}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div
            className="p-2 rounded-md"
            style={{ background: "rgba(0, 0, 0, 0.2)" }}
          >
            <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              Intent
            </div>
            <div className="text-sm font-medium capitalize" style={{ color: "#F5F5F5" }}>
              {recommendation.intent}
            </div>
          </div>
          <div
            className="p-2 rounded-md"
            style={{ background: "rgba(0, 0, 0, 0.2)" }}
          >
            <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              Sentiment
            </div>
            <div className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
              {recommendation.sentiment_label} ({recommendation.sentiment_score}/10)
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// Execution Bar
function ExecutionBar({ onAction, loading }) {
  return (
    <div
      className="fixed bottom-0 left-0 right-0 flex items-center justify-between px-6 py-4"
      style={{
        background: "rgba(9, 9, 9, 0.95)",
        backdropFilter: "blur(12px)",
        borderTop: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-wider" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
          Execution Actions
        </span>
      </div>
      <div className="flex items-center gap-3">
        <motion.button
          onClick={() => onAction("exchange")}
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2.5 rounded-sm font-medium text-sm"
          style={{
            background: loading ? "rgba(0, 229, 255, 0.5)" : "#00E5FF",
            color: "#090909",
          }}
          whileHover={!loading ? { scale: 1.02 } : {}}
          whileTap={!loading ? { scale: 0.98 } : {}}
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Package className="w-4 h-4" />
          )}
          Approve Exchange & Send Email
        </motion.button>

        <motion.button
          onClick={() => onAction("store_credit")}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2.5 rounded-sm font-medium text-sm"
          style={{
            background: "rgba(255, 255, 255, 0.1)",
            color: "#F5F5F5",
            border: "1px solid rgba(255, 255, 255, 0.2)",
          }}
          whileHover={!loading ? { background: "rgba(255, 255, 255, 0.15)" } : {}}
        >
          <CreditCard className="w-4 h-4" />
          Issue Store Credit
        </motion.button>

        <motion.button
          onClick={() => onAction("escalate")}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2.5 rounded-sm font-medium text-sm"
          style={{
            background: "rgba(239, 68, 68, 0.1)",
            color: "#EF4444",
            border: "1px solid rgba(239, 68, 68, 0.3)",
          }}
          whileHover={!loading ? { background: "rgba(239, 68, 68, 0.2)" } : {}}
        >
          <User className="w-4 h-4" />
          Escalate to Human
        </motion.button>
      </div>
    </div>
  );
}

// Main Smart Inbox Component
export default function SmartInbox() {
  const [selectedTicketId, setSelectedTicketId] = useState(mockTickets[0].id);
  const [processedData, setProcessedData] = useState(mockProcessedData);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const selectedTicket = mockTickets.find((t) => t.id === selectedTicketId) || mockTickets[0];

  // Simulate API call to process ticket
  useEffect(() => {
    // In production, this would call /api/agentic/process-ticket
    setProcessedData(mockProcessedData);
  }, [selectedTicketId]);

  const handleAction = async (actionType) => {
    setLoading(true);
    setToast({ message: "Processing action...", type: "loading" });

    try {
      // Simulate API call
      await new Promise((resolve) => setTimeout(resolve, 1500));

      const messages = {
        exchange: "Exchange order created in Shopify! Email sent to customer.",
        store_credit: "Store credit issued successfully!",
        escalate: "Ticket escalated to human agent. They'll be notified immediately.",
      };

      setToast({ message: messages[actionType], type: "success" });
    } catch (error) {
      setToast({ message: "Action failed. Please try again.", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-screen" style={{ background: "#090909" }}>
      <AnimatePresence>
        {toast && (
          <Toast
            message={toast.message}
            type={toast.type}
            onClose={() => setToast(null)}
          />
        )}
      </AnimatePresence>

      {/* Ticket Sidebar */}
      <div
        className="w-72 flex-shrink-0 overflow-y-auto"
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
            Decision Queue
          </h2>
        </div>
        <div>
          {mockTickets.map((ticket) => {
            const isSelected = ticket.id === selectedTicketId;
            const sentimentColors = {
              frustrated: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444" },
              neutral: { bg: "rgba(156, 163, 175, 0.15)", text: "#9CA3AF" },
              happy: { bg: "rgba(34, 197, 94, 0.15)", text: "#22C55E" },
            };
            const sentiment = sentimentColors[ticket.sentiment] || sentimentColors.neutral;

            return (
              <motion.button
                key={ticket.id}
                onClick={() => setSelectedTicketId(ticket.id)}
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
                    }}
                  >
                    {ticket.sentiment}
                  </span>
                </div>
                <div className="text-sm font-medium mb-1" style={{ color: "#F5F5F5" }}>
                  {ticket.customerName}
                </div>
                <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                  {ticket.intent}
                </div>
              </motion.button>
            );
          })}
        </div>
      </div>

      {/* Decision Workspace - 3 Columns */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4"
          style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          <div>
            <h1
              className="text-xl font-medium tracking-wide"
              style={{ color: "#F5F5F5", letterSpacing: "-0.03em" }}
            >
              Agentic Decision Hub
            </h1>
            <p className="text-sm mt-1" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              AI-Powered Ticket Processing - COO Command Center
            </p>
          </div>
        </div>

        {/* 3-Column Layout */}
        <div className="flex-1 flex gap-4 p-4 overflow-hidden" style={{ paddingBottom: "80px" }}>
          {/* Section A: Original Thread */}
          <div className="w-1/3">
            <OriginalThread ticket={selectedTicket} />
          </div>

          {/* Section B: Shopify Intelligence */}
          <div className="w-1/3">
            <ShopifyIntelligenceCard
              shopifyAudit={processedData.shopifyAudit}
              inventoryCheck={processedData.inventoryCheck}
            />
          </div>

          {/* Section C: AI Strategy */}
          <div className="w-1/3">
            <AIStrategyCard recommendation={processedData.aiRecommendation} />
          </div>
        </div>
      </div>

      {/* Execution Bar */}
      <ExecutionBar onAction={handleAction} loading={loading} />
    </div>
  );
}
