/**
 * Inbox Page - Ticket List with Filtering and Pagination
 * Displays all tickets with status filters and pagination controls
 */

import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mail,
  Filter,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  User,
  Search,
  Loader2,
  Zap,
  ChevronDown,
  X,
  Menu,
  MessageSquare,
} from "lucide-react";

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || "https://hackathonn5-production.up.railway.app";

// Status configuration
const STATUS_CONFIG = {
  open: { label: "Open", color: "#3B82F6", bg: "rgba(59, 130, 246, 0.15)" },
  pending: { label: "Pending", color: "#F59E0B", bg: "rgba(245, 158, 11, 0.15)" },
  ai_responded: { label: "AI Responded", color: "#8B5CF6", bg: "rgba(139, 92, 246, 0.15)" },
  human_responded: { label: "Human Responded", color: "#10B981", bg: "rgba(16, 185, 129, 0.15)" },
  resolved: { label: "Resolved", color: "#22C55E", bg: "rgba(34, 197, 94, 0.15)" },
  escalated: { label: "Escalated", color: "#EF4444", bg: "rgba(239, 68, 68, 0.15)" },
  closed: { label: "Closed", color: "#6B7280", bg: "rgba(107, 114, 128, 0.15)" },
};

// Priority configuration
const PRIORITY_CONFIG = {
  urgent: { label: "Urgent", color: "#EF4444" },
  high: { label: "High", color: "#F59E0B" },
  normal: { label: "Normal", color: "#3B82F6" },
  low: { label: "Low", color: "#6B7280" },
};

// Channel configuration
const CHANNEL_CONFIG = {
  email: { label: "Email", icon: Mail },
  web_form: { label: "Web Form", icon: Zap },
  whatsapp: { label: "WhatsApp", icon: MessageSquare },
  chat: { label: "Chat", icon: MessageSquare },
};

// Skeleton loader for ticket list
function SkeletonTicketList() {
  return (
    <div className="space-y-2 p-4">
      {[1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="p-4 animate-pulse"
          style={{
            borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
            background: "rgba(255, 255, 255, 0.02)",
            borderRadius: "8px",
          }}
        >
          <div className="flex items-start justify-between mb-2">
            <div className="w-20 h-4 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
            <div className="w-16 h-5 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
          </div>
          <div className="w-32 h-4 rounded mb-2" style={{ background: "rgba(255,255,255,0.1)" }} />
          <div className="w-24 h-3 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
        </div>
      ))}
    </div>
  );
}

// Toast notification
function Toast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  const colors = {
    success: { bg: "rgba(34, 197, 94, 0.2)", border: "rgba(34, 197, 94, 0.5)", icon: "#22C55E" },
    error: { bg: "rgba(239, 68, 68, 0.2)", border: "rgba(239, 68, 68, 0.5)", icon: "#EF4444" },
    loading: { bg: "rgba(59, 130, 246, 0.2)", border: "rgba(59, 130, 246, 0.5)", icon: "#3B82F6" },
  };

  const c = colors[type];

  return (
    <motion.div
      initial={{ opacity: 0, y: 50, x: "-50%" }}
      animate={{ opacity: 1, y: 0, x: "-50%" }}
      exit={{ opacity: 0, y: 50, x: "-50%" }}
      className="fixed bottom-6 left-1/2 z-50 flex items-center gap-3 px-4 py-3 rounded-md"
      style={{ background: c.bg, border: `1px solid ${c.border}`, backdropFilter: "blur(12px)" }}
    >
      {type === "loading" ? (
        <Loader2 className="w-4 h-4 animate-spin" style={{ color: c.icon }} />
      ) : type === "success" ? (
        <CheckCircle className="w-4 h-4" style={{ color: c.icon }} />
      ) : (
        <AlertCircle className="w-4 h-4" style={{ color: c.icon }} />
      )}
      <span className="text-sm" style={{ color: "#F5F5F5" }}>
        {message}
      </span>
    </motion.div>
  );
}

// Filter dropdown component
function FilterDropdown({ label, value, options, onChange, icon: Icon }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors"
        style={{
          background: value ? "rgba(0, 229, 255, 0.1)" : "rgba(255, 255, 255, 0.05)",
          border: `1px solid ${value ? "rgba(0, 229, 255, 0.3)" : "rgba(255, 255, 255, 0.1)"}`,
          color: value ? "#00E5FF" : "rgba(245, 245, 245, 0.7)",
        }}
      >
        {Icon && <Icon className="w-4 h-4" />}
        <span>{label}</span>
        {value && (
          <span
            className="ml-1 px-1.5 py-0.5 text-xs rounded"
            style={{ background: "rgba(0, 229, 255, 0.2)", color: "#00E5FF" }}
          >
            {value}
          </span>
        )}
        <ChevronDown className={`w-3 h-3 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="absolute top-full left-0 mt-1 py-1 min-w-32 rounded-md shadow-lg z-50"
            style={{
              background: "rgba(30, 30, 30, 0.95)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
            }}
          >
            <button
              onClick={() => {
                onChange(null);
                setIsOpen(false);
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-white/5"
              style={{ color: "rgba(245, 245, 245, 0.8)" }}
            >
              All
            </button>
            {options.map((opt) => (
              <button
                key={opt.value}
                onClick={() => {
                  onChange(opt.value);
                  setIsOpen(false);
                }}
                className="w-full text-left px-3 py-2 text-sm hover:bg-white/5"
                style={{ color: "rgba(245, 245, 245, 0.8)" }}
              >
                {opt.label}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// Main Inbox Component
export default function Inbox() {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState(null);
  const [priorityFilter, setPriorityFilter] = useState(null);
  const [channelFilter, setChannelFilter] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Pagination
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [perPage] = useState(20);
  const [totalPages, setTotalPages] = useState(0);

  // Sort
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState("desc");

  const brandId = localStorage.getItem("brandId") || "default";

  // Build query params
  const buildQueryParams = useCallback(() => {
    const params = new URLSearchParams();
    params.append("limit", perPage);
    params.append("offset", (page - 1) * perPage);
    params.append("order", `${sortOrder === "desc" ? "-" : ""}${sortBy}`);

    if (statusFilter) params.append("status", statusFilter);
    if (priorityFilter) params.append("priority", priorityFilter);
    if (channelFilter) params.append("channel", channelFilter);

    return params.toString();
  }, [page, perPage, sortBy, sortOrder, statusFilter, priorityFilter, channelFilter]);

  // Fetch tickets
  const fetchTickets = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const token = localStorage.getItem("access_token")
        || localStorage.getItem("authToken")
        || "";

      // Try v2 endpoint first; fall back to old /api/tickets on 404/401
      let data = null;
      const v2Params = buildQueryParams()
        + (brandId && brandId !== "default" ? `&brand_id=${brandId}` : "");

      try {
        const v2Res = await fetch(`${API_BASE_URL}/api/v2/tickets?${v2Params}`, {
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        });
        if (v2Res.ok) {
          data = await v2Res.json();
        }
      } catch (_) { /* fall through */ }

      if (!data) {
        // Fall back to old tenant-JWT endpoint
        const oldRes = await fetch(
          `${API_BASE_URL}/api/tickets?${buildQueryParams()}`,
          { headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } }
        );
        if (!oldRes.ok) {
          if (oldRes.status === 401) throw new Error("Authentication required. Please log in.");
          throw new Error(`Failed to fetch tickets: ${oldRes.status}`);
        }
        const raw = await oldRes.json();
        // Old endpoint returns a plain array; normalise to {tickets, total}
        const arr = Array.isArray(raw) ? raw : (raw.tickets || []);
        data = { tickets: arr, total: arr.length };
      }

      setTickets(data.tickets || []);
      setTotal(data.total || 0);
      setTotalPages(Math.ceil((data.total || 0) / perPage));
    } catch (err) {
      console.error("Failed to fetch tickets:", err);
      setError(err.message || "Failed to connect to server");
    } finally {
      setLoading(false);
    }
  }, [brandId, buildQueryParams, perPage]);

  // Fetch on mount and filter changes
  useEffect(() => {
    fetchTickets();
  }, [fetchTickets]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [statusFilter, priorityFilter, channelFilter, searchQuery]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(fetchTickets, 30000);
    return () => clearInterval(interval);
  }, [fetchTickets]);

  // Filter tickets client-side for search
  const filteredTickets = searchQuery
    ? tickets.filter(
        (t) =>
          t.subject?.toLowerCase().includes(searchQuery.toLowerCase()) ||
          t.customer_email?.toLowerCase().includes(searchQuery.toLowerCase()) ||
          t.order_id?.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : tickets;

  const statusOptions = Object.entries(STATUS_CONFIG).map(([value, config]) => ({
    value,
    label: config.label,
  }));

  const priorityOptions = Object.entries(PRIORITY_CONFIG).map(([value, config]) => ({
    value,
    label: config.label,
  }));

  const channelOptions = Object.entries(CHANNEL_CONFIG).map(([value, config]) => ({
    value,
    label: config.label,
  }));

  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="flex h-screen" style={{ background: "#090909" }}>
      <AnimatePresence>
        {toast && (
          <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />
        )}
      </AnimatePresence>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 md:px-6 py-3 md:py-4"
          style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="p-2 rounded-md hover:bg-white/5 transition-colors md:hidden"
            >
              <Menu className="w-5 h-5" style={{ color: "rgba(245, 245, 245, 0.7)" }} />
            </button>
            <div>
              <h1
                className="text-lg md:text-xl font-medium tracking-wide"
                style={{ color: "#F5F5F5", letterSpacing: "-0.03em" }}
              >
                Inbox
              </h1>
              <p className="text-xs md:text-sm mt-0.5 md:mt-1" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                {loading
                  ? "Loading..."
                  : `${total} ticket${total !== 1 ? "s" : ""}`}
              </p>
            </div>
          </div>

          <button
            onClick={fetchTickets}
            disabled={loading}
            className="p-2 rounded-md hover:bg-white/5 transition-colors"
            title="Refresh"
          >
            <RefreshCw
              className={`w-5 h-5 ${loading ? "animate-spin" : ""}`}
              style={{ color: "rgba(245, 245, 245, 0.7)" }}
            />
          </button>
        </div>

        {/* Filters Bar */}
        <div
          className="flex items-center gap-2 md:gap-3 px-3 md:px-6 py-2 md:py-3 flex-wrap"
          style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
        >
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] md:min-w-64">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
              style={{ color: "rgba(245, 245, 245, 0.5)" }}
            />
            <input
              type="text"
              placeholder="Search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 rounded-md text-sm"
              style={{
                background: "rgba(255, 255, 255, 0.05)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                color: "#F5F5F5",
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-3 top-1/2 -translate-y-1/2"
              >
                <X className="w-4 h-4" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
              </button>
            )}
          </div>

          {/* Status Filter */}
          <FilterDropdown
            label="Status"
            value={statusFilter ? STATUS_CONFIG[statusFilter]?.label : null}
            options={statusOptions}
            onChange={setStatusFilter}
            icon={Filter}
          />

          {/* Priority Filter */}
          <FilterDropdown
            label="Priority"
            value={priorityFilter ? PRIORITY_CONFIG[priorityFilter]?.label : null}
            options={priorityOptions}
            onChange={setPriorityFilter}
            icon={Filter}
          />

          {/* Channel Filter */}
          <FilterDropdown
            label="Channel"
            value={channelFilter ? CHANNEL_CONFIG[channelFilter]?.label : null}
            options={channelOptions}
            onChange={setChannelFilter}
            icon={Filter}
          />

          {/* Clear Filters */}
          {(statusFilter || priorityFilter || channelFilter || searchQuery) && (
            <button
              onClick={() => {
                setStatusFilter(null);
                setPriorityFilter(null);
                setChannelFilter(null);
                setSearchQuery("");
                setPage(1);
              }}
              className="text-xs md:text-sm px-2 md:px-3 py-2 rounded-md hover:bg-white/5"
              style={{ color: "rgba(245, 245, 245, 0.5)" }}
            >
              Clear
            </button>
          )}
        </div>

        {/* Ticket List */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <SkeletonTicketList />
          ) : error ? (
            <div
              className="flex flex-col items-center justify-center h-full"
              style={{ background: "rgba(255, 255, 255, 0.03)" }}
            >
              <div
                className="p-6 rounded-md text-center max-w-md"
                style={{ background: "rgba(239, 68, 68, 0.1)", border: "1px solid rgba(239, 68, 68, 0.3)" }}
              >
                <AlertCircle className="w-12 h-12 mx-auto mb-4" style={{ color: "#EF4444" }} />
                <h3 className="text-lg font-medium mb-2" style={{ color: "#EF4444" }}>
                  Error Loading Tickets
                </h3>
                <p className="text-sm mb-4" style={{ color: "rgba(245, 245, 245, 0.7)" }}>
                  {error}
                </p>
                <button
                  onClick={fetchTickets}
                  className="flex items-center gap-2 px-4 py-2 rounded-sm font-medium text-sm mx-auto"
                  style={{ background: "#EF4444", color: "#FFFFFF" }}
                >
                  <RefreshCw className="w-4 h-4" />
                  Retry
                </button>
              </div>
            </div>
          ) : filteredTickets.length === 0 ? (
            <div
              className="flex flex-col items-center justify-center h-full"
              style={{ background: "rgba(255, 255, 255, 0.03)" }}
            >
              <Mail className="w-16 h-16 mb-4" style={{ color: "rgba(245, 245, 245, 0.3)" }} />
              <h3 className="text-lg font-medium mb-2" style={{ color: "rgba(245, 245, 245, 0.8)" }}>
                No Tickets Found
              </h3>
              <p className="text-sm" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                {searchQuery || statusFilter || priorityFilter || channelFilter
                  ? "Try adjusting your filters"
                  : "No tickets yet. Submit a support request to get started."}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {filteredTickets.map((ticket) => {
                const status = STATUS_CONFIG[ticket.status] || STATUS_CONFIG.open;
                const priority = PRIORITY_CONFIG[ticket.priority] || PRIORITY_CONFIG.normal;
                const ChannelIcon = CHANNEL_CONFIG[ticket.channel]?.icon || Mail;

                return (
                  <motion.div
                    key={ticket.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="p-3 md:p-4 rounded-lg cursor-pointer transition-colors"
                    style={{
                      background: "rgba(255, 255, 255, 0.03)",
                      border: "1px solid rgba(255, 255, 255, 0.05)",
                    }}
                    whileHover={{
                      background: "rgba(255, 255, 255, 0.05)",
                      borderColor: "rgba(255, 255, 255, 0.1)",
                    }}
                    onClick={() => {
                      // Navigate to ticket detail
                      window.location.href = `/support/ticket/${ticket.id}`;
                    }}
                  >
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <ChannelIcon className="w-4 h-4" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
                        <span className="text-xs font-mono" style={{ color: "rgba(245, 245, 245, 0.4)" }}>
                          #{ticket.order_id || ticket.id.slice(0, 8)}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 md:gap-2">
                        <span
                          className="text-xs px-1.5 md:px-2 py-0.5 rounded-sm"
                          style={{ background: status.bg, color: status.color }}
                        >
                          {status.label}
                        </span>
                        <span
                          className="text-xs px-1.5 md:px-2 py-0.5 rounded-sm hidden sm:inline-block"
                          style={{ background: "rgba(255, 255, 255, 0.1)", color: priority.color }}
                        >
                          {priority.label}
                        </span>
                      </div>
                    </div>

                    <div className="text-sm font-medium mb-1 truncate" style={{ color: "#F5F5F5" }}>
                      {ticket.subject || "No subject"}
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1 md:gap-2">
                        <User className="w-3 h-3" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
                        <div className="text-xs truncate max-w-[120px] md:max-w-none" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                          {ticket.customer_email || "No email"}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Clock className="w-3 h-3" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
                        <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                          {ticket.created_at
                            ? new Date(ticket.created_at).toLocaleDateString()
                            : "Unknown"}
                        </div>
                      </div>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          )}
        </div>

        {/* Pagination */}
        {!loading && !error && total > 0 && (
          <div
            className="flex items-center justify-between px-3 md:px-6 py-2 md:py-3"
            style={{ borderTop: "1px solid rgba(255, 255, 255, 0.05)" }}
          >
            <div className="text-xs md:text-sm" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              {(page - 1) * perPage + 1}-{Math.min(page * perPage, total)} / {total}
            </div>

            <div className="flex items-center gap-1 md:gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="p-1.5 md:p-2 rounded-md hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ color: "rgba(245, 245, 245, 0.7)" }}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>

              <div className="flex items-center gap-1">
                {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                  const pageNum = i + 1;
                  return (
                    <button
                      key={pageNum}
                      onClick={() => setPage(pageNum)}
                      className="w-7 md:w-8 h-7 md:h-8 rounded-md text-xs md:text-sm transition-colors"
                      style={{
                        background:
                          page === pageNum ? "rgba(0, 229, 255, 0.2)" : "rgba(255, 255, 255, 0.05)",
                        color: page === pageNum ? "#00E5FF" : "rgba(245, 245, 245, 0.7)",
                        border: page === pageNum ? "1px solid rgba(0, 229, 255, 0.3)" : "none",
                      }}
                    >
                      {pageNum}
                    </button>
                  );
                })}
              </div>

              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="p-1.5 md:p-2 rounded-md hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ color: "rgba(245, 245, 245, 0.7)" }}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
