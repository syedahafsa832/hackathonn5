import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  History as HistoryIcon,
  Clock,
  Check,
  XCircle,
  Loader2,
  Filter,
  ChevronDown,
  RefreshCw,
  DollarSign,
  Package,
  MapPin,
  Mail,
  Bot,
  User,
  AlertTriangle,
  Zap,
} from "lucide-react";
import apiClient from "../services/apiClient";

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

// Event type badge
function EventBadge({ eventType }) {
  const config = {
    created: { bg: "rgba(59, 130, 246, 0.2)", border: "rgba(59, 130, 246, 0.3)", color: "#3B82F6", icon: Zap },
    approved: { bg: "rgba(34, 197, 94, 0.2)", border: "rgba(34, 197, 94, 0.3)", color: "#22C55E", icon: Check },
    rejected: { bg: "rgba(239, 68, 68, 0.2)", border: "rgba(239, 68, 68, 0.3)", color: "#EF4444", icon: XCircle },
    executed: { bg: "rgba(168, 85, 247, 0.2)", border: "rgba(168, 85, 247, 0.3)", color: "#A855F7", icon: Check },
    failed: { bg: "rgba(239, 68, 68, 0.2)", border: "rgba(239, 68, 68, 0.3)", color: "#EF4444", icon: AlertTriangle },
    email_received: { bg: "rgba(249, 115, 22, 0.2)", border: "rgba(249, 115, 22, 0.3)", color: "#F97316", icon: Mail },
    ai_processed: { bg: "rgba(14, 165, 233, 0.2)", border: "rgba(14, 165, 233, 0.3)", color: "#0EA5E9", icon: Bot },
  };

  const { bg, border, color, icon: Icon } = config[eventType] || config.created;

  return (
    <div
      className="flex items-center gap-1.5 px-2 py-1 rounded text-xs"
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      <Icon className="w-3 h-3" style={{ color }} />
      <span style={{ color }}>{eventType.replace(/_/g, " ")}</span>
    </div>
  );
}

// Action type icon
function ActionTypeIcon({ actionType }) {
  const icons = {
    refund: DollarSign,
    cancel_order: Package,
    change_address: MapPin,
  };
  const Icon = icons[actionType] || Zap;
  return <Icon className="w-4 h-4" style={{ color: "#A0A0A0" }} />;
}

// History row component
function HistoryRow({ log, onExpand }) {
  const [expanded, setExpanded] = useState(false);

  const formatDate = (dateStr) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-md overflow-hidden"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-4 flex-1">
          <ActionTypeIcon actionType={log.details?.action_type} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium truncate" style={{ color: "#F5F5F5" }}>
                {log.details?.action_type?.replace(/_/g, " ") || "Action"}
              </span>
              {log.details?.order_id && (
                <span className="text-xs" style={{ color: "#808080" }}>
                  #{log.details.order_id}
                </span>
              )}
            </div>
            <div className="text-xs" style={{ color: "#808080" }}>
              {log.actor || "System"} - {formatDate(log.created_at)}
            </div>
          </div>
          <EventBadge eventType={log.event_type || log.event} />
          <ChevronDown
            className={`w-4 h-4 transition-transform ${expanded ? "rotate-180" : ""}`}
            style={{ color: "#808080" }}
          />
        </div>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div
              className="px-4 py-3 text-xs space-y-2"
              style={{ background: "rgba(0, 0, 0, 0.2)", borderTop: "1px solid rgba(255, 255, 255, 0.05)" }}
            >
              {log.details && Object.entries(log.details).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <span style={{ color: "#808080" }}>{key.replace(/_/g, " ")}:</span>
                  <span style={{ color: "#A0A0A0" }}>
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </span>
                </div>
              ))}
              {log.error_message && (
                <div className="flex gap-2 p-2 rounded" style={{ background: "rgba(239, 68, 68, 0.1)" }}>
                  <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: "#EF4444" }} />
                  <span style={{ color: "#EF4444" }}>{log.error_message}</span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// Filter dropdown
function FilterDropdown({ value, onChange, options }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="px-3 py-2 rounded-md text-sm outline-none"
      style={{
        background: "rgba(0, 0, 0, 0.3)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
        color: "#F5F5F5",
      }}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export default function History() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [logs, setLogs] = useState([]);
  const [toast, setToast] = useState(null);

  // Brand selection
  const [brands, setBrands] = useState([]);
  const [selectedBrandId, setSelectedBrandId] = useState(null);

  // Filters
  const [eventFilter, setEventFilter] = useState("all");

  // Load brands on mount
  useEffect(() => {
    loadBrands();
  }, []);

  // Load history when brand changes
  useEffect(() => {
    if (selectedBrandId) {
      loadHistory();
    }
  }, [selectedBrandId, eventFilter]);

  const loadBrands = async () => {
    try {
      const response = await apiClient.getBrands();
      setBrands(response.brands || []);
      if (response.brands?.length > 0) {
        setSelectedBrandId(response.brands[0].id);
      }
    } catch (error) {
      console.error("Error loading brands:", error);
      setToast({ message: "Failed to load brands", type: "error" });
      setLoading(false);
    }
  };

  const loadHistory = async () => {
    try {
      setLoading(true);
      const params = {};
      if (eventFilter !== "all") {
        params.event_type = eventFilter;
      }

      const response = await apiClient.getHistory(selectedBrandId, params);
      setLogs(response.logs || response.history || []);
    } catch (error) {
      console.error("Error loading history:", error);
      setToast({ message: "Failed to load history", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await loadHistory();
    setRefreshing(false);
    setToast({ message: "History refreshed", type: "success" });
  };

  const eventOptions = [
    { value: "all", label: "All Events" },
    { value: "created", label: "Created" },
    { value: "approved", label: "Approved" },
    { value: "rejected", label: "Rejected" },
    { value: "executed", label: "Executed" },
    { value: "failed", label: "Failed" },
    { value: "email_received", label: "Email Received" },
    { value: "ai_processed", label: "AI Processed" },
  ];

  if (loading && logs.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "#0A0A0A" }}>
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: "#A0A0A0" }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen p-4 md:p-6" style={{ background: "#0A0A0A", color: "#F5F5F5" }}>
      <AnimatePresence>
        {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
      </AnimatePresence>

      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-4 md:mb-6">
        <div className="flex items-center gap-3">
          <HistoryIcon className="w-5 h-5" style={{ color: "#A0A0A0" }} />
          <h1 className="text-lg md:text-xl font-semibold">Activity History</h1>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-all hover:opacity-90 w-full md:w-auto justify-center"
          style={{
            background: "rgba(255, 255, 255, 0.05)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            color: "#A0A0A0",
          }}
        >
          <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col md:flex-row items-start md:items-center gap-2 md:gap-4 mb-4 md:mb-6">
        {brands.length > 1 && (
          <FilterDropdown
            value={selectedBrandId || ""}
            onChange={setSelectedBrandId}
            options={brands.map((b) => ({ value: b.id, label: b.name }))}
          />
        )}
        <FilterDropdown
          value={eventFilter}
          onChange={setEventFilter}
          options={eventOptions}
        />
        <div className="flex-1" />
        <div className="text-sm" style={{ color: "#808080" }}>
          {logs.length} events
        </div>
      </div>

      {/* History list */}
      <div className="space-y-2">
        {logs.length === 0 ? (
          <div
            className="flex flex-col items-center justify-center py-16 rounded-md"
            style={{
              background: "rgba(255, 255, 255, 0.03)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
            }}
          >
            <Clock className="w-12 h-12 mb-4" style={{ color: "#404040" }} />
            <p className="text-sm" style={{ color: "#808080" }}>
              No activity history yet
            </p>
          </div>
        ) : (
          logs.map((log, index) => (
            <HistoryRow key={log.id || index} log={log} />
          ))
        )}
      </div>
    </div>
  );
}
