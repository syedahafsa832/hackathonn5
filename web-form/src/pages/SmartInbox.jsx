import React, { useState, useEffect, useCallback } from "react";
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
  AlertCircle,
  WifiOff,
} from "lucide-react";

const API_BASE_URL = "https://hackathonn5-production.up.railway.app";

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

// SaaS Noir Skeleton Loader
function SkeletonCard() {
  return (
    <div
      className="rounded-md overflow-hidden h-full animate-pulse"
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
          <div className="w-4 h-4 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
          <div className="w-24 h-3 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
        </div>
      </div>
      <div className="p-4 space-y-3">
        <div className="w-full h-4 rounded" style={{ background: "rgba(255,255,255,0.05)" }} />
        <div className="w-3/4 h-4 rounded" style={{ background: "rgba(255,255,255,0.05)" }} />
        <div className="w-1/2 h-4 rounded" style={{ background: "rgba(255,255,255,0.05)" }} />
        <div className="w-full h-20 rounded mt-4" style={{ background: "rgba(255,255,255,0.05)" }} />
      </div>
    </div>
  );
}

// Skeleton for sidebar tickets
function SkeletonTicketList() {
  return (
    <div className="space-y-2 p-4">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="p-4 animate-pulse"
          style={{
            borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
          }}
        >
          <div className="flex items-start justify-between mb-2">
            <div className="w-16 h-3 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
            <div className="w-12 h-4 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
          </div>
          <div className="w-24 h-4 rounded mb-1" style={{ background: "rgba(255,255,255,0.1)" }} />
          <div className="w-16 h-3 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
        </div>
      ))}
    </div>
  );
}

// Stats Skeleton
function SkeletonStats() {
  return (
    <div className="flex items-center gap-6">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="w-8 h-8 rounded animate-pulse" style={{ background: "rgba(255,255,255,0.1)" }} />
          <div>
            <div className="w-12 h-3 rounded animate-pulse mb-1" style={{ background: "rgba(255,255,255,0.1)" }} />
            <div className="w-8 h-5 rounded animate-pulse" style={{ background: "rgba(255,255,255,0.1)" }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// Section A: Original Thread
function OriginalThread({ ticket }) {
  // Map sentiment based on action type and risk score
  const getSentiment = () => {
    if (!ticket) return { label: "neutral", score: 5 };
    const actionType = ticket.action_type?.toLowerCase();
    const riskScore = ticket.risk_score?.toLowerCase();

    if (actionType === "refund") {
      return { label: "frustrated", score: riskScore === "high" ? 8 : 6 };
    } else if (actionType === "exchange") {
      return { label: "happy", score: 4 };
    }
    return { label: "neutral", score: 5 };
  };

  const sentimentConfig = {
    frustrated: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444", border: "rgba(239, 68, 68, 0.3)" },
    neutral: { bg: "rgba(156, 163, 175, 0.15)", text: "#9CA3AF", border: "rgba(156, 163, 175, 0.3)" },
    happy: { bg: "rgba(34, 197, 94, 0.15)", text: "#22C55E", border: "rgba(34, 197, 94, 0.3)" },
  };

  const sentiment = getSentiment();
  const config = sentimentConfig[sentiment.label] || sentimentConfig.neutral;

  // Get message content from order_data or use default
  const messageContent = ticket?.order_data?.message ||
    `Customer requested ${ticket?.action_type?.toLowerCase()} for Order #${ticket?.order_id}`;

  // Get customer email from ticket
  const customerEmail = ticket?.customer_email || "No email provided";
  const customerName = ticket?.customer_name || "Unknown Customer";

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
            background: config.bg,
            color: config.text,
            border: `1px solid ${config.border}`,
          }}
        >
          {sentiment.label} ({sentiment.score}/10)
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
                {customerName}
              </span>
              {/* VIP status could be derived from order data if available */}
            </div>
            <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              {customerEmail}
            </div>
          </div>
        </div>
        <div
          className="p-3 rounded-md"
          style={{ background: "rgba(0, 0, 0, 0.2)", border: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.9)", lineHeight: 1.6 }}>
            {messageContent}
          </p>
        </div>
      </div>
    </div>
  );
}

// Section B: Shopify Intelligence Card
function ShopifyIntelligenceCard({ ticket, loading }) {
  if (loading) {
    return <SkeletonCard />;
  }

  const orderData = ticket?.order_data || {};
  const shopifyAudit = {
    order_id: ticket?.order_id || "N/A",
    order_date: orderData.created_at || null,
    order_total: parseFloat(ticket?.revenue_at_stake) || 0,
    items: orderData.line_items?.map((item) => ({
      title: item.title || "Unknown Item",
      variant: item.variant_title || "Standard",
      price: parseFloat(item.price) || 0,
    })) || [],
    return_window_open: true, // Default based on eligibility
    days_remaining: 15, // Could be calculated from order date
  };

  // Check inventory from exchange_suggestion if available
  const exchangeSuggestion = ticket?.exchange_suggestion || {};
  const inventoryCheck = {
    available_quantity: exchangeSuggestion.available ? 25 : 0,
    in_stock: exchangeSuggestion.available || false,
  };

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
            {shopifyAudit.items.length > 0 ? (
              shopifyAudit.items.map((item, idx) => (
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
              ))
            ) : (
              <div
                className="p-2 rounded-md text-sm"
                style={{ color: "rgba(245, 245, 245, 0.5)", background: "rgba(0, 0, 0, 0.2)" }}
              >
                No items available
              </div>
            )}
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
function AIStrategyCard({ ticket, loading }) {
  if (loading) {
    return <SkeletonCard />;
  }

  const actionType = ticket?.action_type?.toLowerCase() || "unknown";
  const revenueAtStake = parseFloat(ticket?.revenue_at_stake) || 0;
  const aiReasoning = ticket?.ai_reasoning || "No reasoning provided by AI.";
  const riskScore = ticket?.risk_score || "Low";

  // Map action type to recommended action
  const recommendedAction = actionType === "exchange"
    ? "Exchange (Revenue Saved)"
    : actionType === "refund"
    ? "Refund (Process)"
    : "Manual Review Required";

  // Map action type to intent
  const intent = actionType;

  // Get sentiment based on action type and risk
  const sentimentLabel = riskScore === "High" ? "Urgent" : riskScore === "Medium" ? "Concerned" : "Neutral";
  const sentimentScore = riskScore === "High" ? 8 : riskScore === "Medium" ? 6 : 4;

  const recommendation = {
    recommended_action: recommendedAction,
    revenue_at_stake: revenueAtStake,
    reasoning: aiReasoning,
    intent: intent,
    sentiment_label: sentimentLabel,
    sentiment_score: sentimentScore,
  };

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
              Risk Level
            </div>
            <div className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
              {ticket?.risk_score || "Low"}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// Execution Bar
function ExecutionBar({ onApprove, onReject, loading, currentAction }) {
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
          onClick={onApprove}
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2.5 rounded-sm font-medium text-sm"
          style={{
            background: loading && currentAction === "approve" ? "rgba(0, 229, 255, 0.5)" : "#00E5FF",
            color: "#090909",
          }}
          whileHover={!loading ? { scale: 1.02 } : {}}
          whileTap={!loading ? { scale: 0.98 } : {}}
        >
          {loading && currentAction === "approve" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <CheckCircle className="w-4 h-4" />
          )}
          Approve Action
        </motion.button>

        <motion.button
          onClick={onReject}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2.5 rounded-sm font-medium text-sm"
          style={{
            background: loading && currentAction === "reject" ? "rgba(239, 68, 68, 0.5)" : "rgba(239, 68, 68, 0.1)",
            color: "#EF4444",
            border: "1px solid rgba(239, 68, 68, 0.3)",
          }}
          whileHover={!loading ? { background: "rgba(239, 68, 68, 0.2)" } : {}}
        >
          {loading && currentAction === "reject" ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <XCircle className="w-4 h-4" />
          )}
          Reject
        </motion.button>
      </div>
    </div>
  );
}

// Error State Component
function ErrorState({ message, onRetry }) {
  return (
    <div
      className="flex flex-col items-center justify-center h-full"
      style={{ background: "rgba(255, 255, 255, 0.03)" }}
    >
      <div
        className="p-6 rounded-md text-center max-w-md"
        style={{ background: "rgba(239, 68, 68, 0.1)", border: "1px solid rgba(239, 68, 68, 0.3)" }}
      >
        <WifiOff className="w-12 h-12 mx-auto mb-4" style={{ color: "#EF4444" }} />
        <h3 className="text-lg font-medium mb-2" style={{ color: "#EF4444" }}>
          Database Connection Error
        </h3>
        <p className="text-sm mb-4" style={{ color: "rgba(245, 245, 245, 0.7)" }}>
          {message || "Unable to connect to the database. Please check your connection and try again."}
        </p>
        <button
          onClick={onRetry}
          className="flex items-center gap-2 px-4 py-2 rounded-sm font-medium text-sm mx-auto"
          style={{ background: "#EF4444", color: "#FFFFFF" }}
        >
          <RefreshCw className="w-4 h-4" />
          Retry Connection
        </button>
      </div>
    </div>
  );
}

// Main Smart Inbox Component
export default function SmartInbox() {
  const [tickets, setTickets] = useState([]);
  const [stats, setStats] = useState(null);
  const [selectedTicketId, setSelectedTicketId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingStats, setLoadingStats] = useState(true);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [currentAction, setCurrentAction] = useState(null);

  // Fetch stats from API
  const fetchStats = useCallback(async () => {
    try {
      setLoadingStats(true);
      const response = await fetch(`${API_BASE_URL}/api/actions/stats`);

      if (!response.ok) {
        throw new Error(`Stats API error: ${response.status}`);
      }

      const data = await response.json();
      setStats(data);
    } catch (err) {
      console.error("Failed to fetch stats:", err);
      // Don't set error for stats - just show skeleton
    } finally {
      setLoadingStats(false);
    }
  }, []);

  // Fetch tickets from API
  const fetchTickets = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(`${API_BASE_URL}/api/actions/pending?limit=50`);

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const data = await response.json();

      if (data.actions && Array.isArray(data.actions)) {
        setTickets(data.actions);

        // Auto-select first pending ticket
        const pendingTicket = data.actions.find((t) => t.status === "Pending");
        if (pendingTicket && !selectedTicketId) {
          setSelectedTicketId(pendingTicket.id);
        } else if (data.actions.length > 0 && !selectedTicketId) {
          setSelectedTicketId(data.actions[0].id);
        }
      } else {
        setTickets([]);
      }
    } catch (err) {
      console.error("Failed to fetch tickets:", err);
      setError(err.message || "Failed to connect to database");
    } finally {
      setLoading(false);
    }
  }, [selectedTicketId]);

  // Initial fetch
  useEffect(() => {
    fetchTickets();
    fetchStats();
  }, [fetchTickets, fetchStats]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      fetchTickets();
      fetchStats();
    }, 30000);

    return () => clearInterval(interval);
  }, [fetchTickets, fetchStats]);

  const selectedTicket = tickets.find((t) => t.id === selectedTicketId) || tickets[0];

  // Handle Approve action
  const handleApprove = async () => {
    if (!selectedTicketId) return;

    setActionLoading(true);
    setCurrentAction("approve");
    setToast({ message: "Approving action...", type: "loading" });

    try {
      const response = await fetch(`${API_BASE_URL}/api/actions/approve/${selectedTicketId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ approved_by: "admin" }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Failed to approve action");
      }

      setToast({ message: "Action approved successfully!", type: "success" });

      // Refresh tickets list
      await fetchTickets();
      await fetchStats();
    } catch (error) {
      console.error("Approve error:", error);
      setToast({ message: error.message || "Failed to approve action", type: "error" });
    } finally {
      setActionLoading(false);
      setCurrentAction(null);
    }
  };

  // Handle Reject action
  const handleReject = async () => {
    if (!selectedTicketId) return;

    const rejectionNote = prompt("Please enter a reason for rejecting this action:");

    if (!rejectionNote) {
      return; // User cancelled
    }

    setActionLoading(true);
    setCurrentAction("reject");
    setToast({ message: "Rejecting action...", type: "loading" });

    try {
      const response = await fetch(`${API_BASE_URL}/api/actions/reject/${selectedTicketId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          rejection_note: rejectionNote,
          rejected_by: "admin",
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Failed to reject action");
      }

      setToast({ message: "Action rejected successfully!", type: "success" });

      // Refresh tickets list
      await fetchTickets();
      await fetchStats();
    } catch (error) {
      console.error("Reject error:", error);
      setToast({ message: error.message || "Failed to reject action", type: "error" });
    } finally {
      setActionLoading(false);
      setCurrentAction(null);
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
          <div className="flex items-center justify-between mb-3">
            <h2
              className="text-sm font-medium uppercase tracking-wider"
              style={{ color: "rgba(245, 245, 245, 0.8)" }}
            >
              Decision Queue
            </h2>
            <button
              onClick={() => {
                fetchTickets();
                fetchStats();
              }}
              className="p-1.5 rounded-sm hover:bg-white/5 transition-colors"
              title="Refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
            </button>
          </div>

          {/* Stats Bar */}
          {loadingStats ? (
            <SkeletonStats />
          ) : stats ? (
            <div className="flex items-center gap-3 text-xs">
              <div className="flex items-center gap-1">
                <Clock className="w-3 h-3" style={{ color: "#F59E0B" }} />
                <span style={{ color: "rgba(245, 245, 245, 0.7)" }}>{stats.pending || 0}</span>
              </div>
              <div className="flex items-center gap-1">
                <CheckCircle className="w-3 h-3" style={{ color: "#22C55E" }} />
                <span style={{ color: "rgba(245, 245, 245, 0.7)" }}>{stats.approved || 0}</span>
              </div>
              <div className="flex items-center gap-1">
                <XCircle className="w-3 h-3" style={{ color: "#EF4444" }} />
                <span style={{ color: "rgba(245, 245, 245, 0.7)" }}>{stats.rejected || 0}</span>
              </div>
            </div>
          ) : null}
        </div>

        {/* Loading State */}
        {loading ? (
          <SkeletonTicketList />
        ) : error ? (
          <div className="p-4">
            <div
              className="p-3 rounded-md text-center text-sm"
              style={{ background: "rgba(239, 68, 68, 0.1)", color: "#EF4444" }}
            >
              <AlertCircle className="w-5 h-5 mx-auto mb-2" />
              {error}
            </div>
          </div>
        ) : tickets.length === 0 ? (
          <div className="p-4 text-center">
            <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              No pending actions
            </p>
          </div>
        ) : (
          <div>
            {tickets.map((ticket) => {
              const isSelected = ticket.id === selectedTicketId;

              // Map action type to sentiment color
              const sentimentColors = {
                refund: { bg: "rgba(239, 68, 68, 0.15)", text: "#EF4444" },
                exchange: { bg: "rgba(34, 197, 94, 0.15)", text: "#22C55E" },
              };

              const sentiment = sentimentColors[ticket.action_type?.toLowerCase()] || sentimentColors.refund;

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
                      <span
                        className="text-xs font-mono"
                        style={{ color: "rgba(245, 245, 245, 0.4)" }}
                      >
                        #{ticket.order_id}
                      </span>
                    </div>
                    <span
                      className="text-xs px-2 py-0.5 rounded-sm capitalize"
                      style={{
                        background: sentiment.bg,
                        color: sentiment.text,
                      }}
                    >
                      {ticket.action_type}
                    </span>
                  </div>
                  <div className="text-sm font-medium mb-1" style={{ color: "#F5F5F5" }}>
                    {ticket.customer_name || "Unknown"}
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                      {ticket.action_type}
                    </div>
                    {ticket.revenue_at_stake && (
                      <div className="text-xs font-medium" style={{ color: "#22C55E" }}>
                        ${parseFloat(ticket.revenue_at_stake).toFixed(2)}
                      </div>
                    )}
                  </div>
                </motion.button>
              );
            })}
          </div>
        )}
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
              {loading ? "Syncing with Shopify..." : `${tickets.length} actions in queue`}
            </p>
          </div>
        </div>

        {/* 3-Column Layout */}
        <div className="flex-1 flex gap-4 p-4 overflow-hidden" style={{ paddingBottom: "80px" }}>
          {/* Error State */}
          {error && !tickets.length ? (
            <div className="w-full">
              <ErrorState message={error} onRetry={fetchTickets} />
            </div>
          ) : (
            <>
              {/* Section A: Original Thread */}
              <div className="w-1/3">
                <OriginalThread ticket={selectedTicket} />
              </div>

              {/* Section B: Shopify Intelligence */}
              <div className="w-1/3">
                <ShopifyIntelligenceCard ticket={selectedTicket} loading={loading} />
              </div>

              {/* Section C: AI Strategy */}
              <div className="w-1/3">
                <AIStrategyCard ticket={selectedTicket} loading={loading} />
              </div>
            </>
          )}
        </div>
      </div>

      {/* Execution Bar */}
      <ExecutionBar
        onApprove={handleApprove}
        onReject={handleReject}
        loading={actionLoading}
        currentAction={currentAction}
      />
    </div>
  );
}
