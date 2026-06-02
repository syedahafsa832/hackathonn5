/**
 * Action Detail Modal Component
 * Displays detailed information about an action including history and logs
 */

import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  User,
  Package,
  DollarSign,
  RefreshCw,
  ChevronDown,
  ExternalLink,
  Loader2,
  FileText,
} from "lucide-react";

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || "https://hackathonn5-production.up.railway.app";

// Action type configuration
const ACTION_TYPE_CONFIG = {
  refund: {
    label: "Refund",
    color: "#EF4444",
    bg: "rgba(239, 68, 68, 0.15)",
    icon: DollarSign,
  },
  cancel: {
    label: "Cancel Order",
    color: "#F59E0B",
    bg: "rgba(245, 158, 11, 0.15)",
    icon: XCircle,
  },
  address_change: {
    label: "Address Change",
    color: "#3B82F6",
    bg: "rgba(59, 130, 246, 0.15)",
    icon: Package,
  },
  exchange: {
    label: "Exchange",
    color: "#22C55E",
    bg: "rgba(34, 197, 94, 0.15)",
    icon: RefreshCw,
  },
};

// Status configuration
const STATUS_CONFIG = {
  pending: {
    label: "Pending",
    color: "#F59E0B",
    bg: "rgba(245, 158, 11, 0.15)",
    icon: Clock,
  },
  approved: {
    label: "Approved",
    color: "#22C55E",
    bg: "rgba(34, 197, 94, 0.15)",
    icon: CheckCircle,
  },
  rejected: {
    label: "Rejected",
    color: "#EF4444",
    bg: "rgba(239, 68, 68, 0.15)",
    icon: XCircle,
  },
  executed: {
    label: "Executed",
    color: "#00E5FF",
    bg: "rgba(0, 229, 255, 0.15)",
    icon: CheckCircle,
  },
  failed: {
    label: "Failed",
    color: "#EF4444",
    bg: "rgba(239, 68, 68, 0.15)",
    icon: AlertCircle,
  },
};

// Risk level configuration
const RISK_CONFIG = {
  low: { label: "Low", color: "#22C55E" },
  medium: { label: "Medium", color: "#F59E0B" },
  high: { label: "High", color: "#EF4444" },
};

// Skeleton loader for logs
function SkeletonLogs() {
  return (
    <div className="space-y-2">
      {[1, 2, 3].map((i) => (
        <div key={i} className="flex items-start gap-3 animate-pulse">
          <div
            className="w-8 h-8 rounded-full"
            style={{ background: "rgba(255,255,255,0.1)" }}
          />
          <div className="flex-1">
            <div
              className="w-32 h-4 rounded mb-1"
              style={{ background: "rgba(255,255,255,0.1)" }}
            />
            <div
              className="w-48 h-3 rounded"
              style={{ background: "rgba(255,255,255,0.1)" }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// Log item component
function LogItem({ log }) {
  const isApproved = log.event_type === "action_approved";
  const isRejected = log.event_type === "action_rejected";
  const isExecuted = log.event_type === "action_executed";

  const getIcon = () => {
    if (isApproved || isExecuted) return <CheckCircle className="w-4 h-4" style={{ color: "#22C55E" }} />;
    if (isRejected) return <XCircle className="w-4 h-4" style={{ color: "#EF4444" }} />;
    return <Clock className="w-4 h-4" style={{ color: "rgba(245, 245, 245, 0.5)" }} />;
  };

  const getTitle = () => {
    if (isApproved) return "Action Approved";
    if (isRejected) return "Action Rejected";
    if (isExecuted) return "Action Executed";
    return log.event_type?.replace(/_/g, " ") || "Event";
  };

  return (
    <div className="flex items-start gap-3">
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
        style={{ background: "rgba(255, 255, 255, 0.05)" }}
      >
        {getIcon()}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
            {getTitle()}
          </span>
          {log.created_by && (
            <span className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
              by {log.created_by}
            </span>
          )}
        </div>
        <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
          {log.created_at ? new Date(log.created_at).toLocaleString() : "Unknown time"}
        </div>
        {log.note && (
          <div
            className="mt-2 p-2 rounded text-sm"
            style={{
              background: "rgba(0, 0, 0, 0.2)",
              color: "rgba(245, 245, 245, 0.8)",
            }}
          >
            {log.note}
          </div>
        )}
        {log.error_message && (
          <div
            className="mt-2 p-2 rounded text-sm"
            style={{
              background: "rgba(239, 68, 68, 0.1)",
              color: "#EF4444",
              border: "1px solid rgba(239, 68, 68, 0.3)",
            }}
          >
            {log.error_message}
          </div>
        )}
      </div>
    </div>
  );
}

// Main Action Detail Modal
export default function ActionDetailModal({ action, onClose, onApprove, onReject }) {
  const [logs, setLogs] = useState([]);
  const [loadingLogs, setLoadingLogs] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);

  const brandId = localStorage.getItem("brandId") || "default";

  // Fetch action logs
  useEffect(() => {
    const fetchLogs = async () => {
      if (!action?.id) return;

      try {
        setLoadingLogs(true);
        const response = await fetch(
          `${API_BASE_URL}/api/v2/brands/${brandId}/actions/${action.id}/logs`,
          {
            headers: {
              Authorization: `Bearer ${localStorage.getItem("authToken") || ""}`,
              "Content-Type": "application/json",
            },
          }
        );

        if (response.ok) {
          const data = await response.json();
          setLogs(data.logs || []);
        }
      } catch (err) {
        console.error("Failed to fetch action logs:", err);
      } finally {
        setLoadingLogs(false);
      }
    };

    fetchLogs();
  }, [action?.id, brandId]);

  if (!action) return null;

  const actionType = ACTION_TYPE_CONFIG[action.action_type?.toLowerCase()] || ACTION_TYPE_CONFIG.refund;
  const status = STATUS_CONFIG[action.status?.toLowerCase()] || STATUS_CONFIG.pending;
  const risk = RISK_CONFIG[action.risk_score?.toLowerCase()] || RISK_CONFIG.low;
  const ActionIcon = actionType.icon;

  // Handle approve
  const handleApprove = async () => {
    if (!onApprove) return;
    setActionLoading(true);
    try {
      await onApprove();
    } finally {
      setActionLoading(false);
    }
  };

  // Handle reject
  const handleReject = async () => {
    if (!onReject) return;
    setActionLoading(true);
    try {
      await onReject();
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        style={{ background: "rgba(0, 0, 0, 0.8)" }}
        onClick={onClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 20 }}
          className="w-full max-w-2xl max-h-[90vh] overflow-hidden rounded-lg"
          style={{
            background: "#121212",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-6 py-4"
            style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
          >
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-md flex items-center justify-center"
                style={{ background: actionType.bg }}
              >
                <ActionIcon className="w-5 h-5" style={{ color: actionType.color }} />
              </div>
              <div>
                <h2 className="text-lg font-medium" style={{ color: "#F5F5F5" }}>
                  {actionType.label} Action
                </h2>
                <div className="flex items-center gap-2 mt-1">
                  <span
                    className="text-xs px-2 py-0.5 rounded-sm"
                    style={{ background: status.bg, color: status.color }}
                  >
                    {status.label}
                  </span>
                  <span
                    className="text-xs px-2 py-0.5 rounded-sm"
                    style={{ background: "rgba(255, 255, 255, 0.1)", color: risk.color }}
                  >
                    {risk.label} Risk
                  </span>
                </div>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 rounded-md hover:bg-white/5 transition-colors"
            >
              <X className="w-5 h-5" style={{ color: "rgba(245, 245, 245, 0.5)" }} />
            </button>
          </div>

          {/* Content */}
          <div className="p-6 overflow-y-auto" style={{ maxHeight: "calc(90vh - 180px)" }}>
            {/* Customer Info */}
            <div className="mb-6">
              <h3
                className="text-xs font-medium uppercase tracking-wider mb-3"
                style={{ color: "rgba(245, 245, 245, 0.5)" }}
              >
                Customer Information
              </h3>
              <div
                className="p-4 rounded-md"
                style={{ background: "rgba(255, 255, 255, 0.03)" }}
              >
                <div className="flex items-center gap-3 mb-3">
                  <div
                    className="w-10 h-10 rounded-full flex items-center justify-center"
                    style={{ background: "rgba(157, 80, 187, 0.2)" }}
                  >
                    <User className="w-5 h-5" style={{ color: "#9D50BB" }} />
                  </div>
                  <div>
                    <div className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
                      {action.customer_name || "Unknown Customer"}
                    </div>
                    <div className="text-xs" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                      {action.customer_email || "No email"}
                    </div>
                  </div>
                </div>
                {action.order_id && (
                  <div className="text-sm">
                    <span style={{ color: "rgba(245, 245, 245, 0.5)" }}>Order: </span>
                    <span style={{ color: "#F5F5F5" }}>#{action.order_id}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Action Details */}
            <div className="mb-6">
              <h3
                className="text-xs font-medium uppercase tracking-wider mb-3"
                style={{ color: "rgba(245, 245, 245, 0.5)" }}
              >
                Action Details
              </h3>
              <div
                className="p-4 rounded-md space-y-3"
                style={{ background: "rgba(255, 255, 255, 0.03)" }}
              >
                {action.amount && (
                  <div className="flex items-center justify-between">
                    <span style={{ color: "rgba(245, 245, 245, 0.5)" }}>Amount</span>
                    <span className="text-sm font-medium" style={{ color: "#22C55E" }}>
                      ${parseFloat(action.amount).toFixed(2)}
                    </span>
                  </div>
                )}
                {action.order_data?.reason && (
                  <div>
                    <span style={{ color: "rgba(245, 245, 245, 0.5)" }}>Reason</span>
                    <p className="text-sm mt-1" style={{ color: "#F5F5F5" }}>
                      {action.order_data.reason}
                    </p>
                  </div>
                )}
                {action.ai_reasoning && (
                  <div>
                    <span style={{ color: "rgba(245, 245, 245, 0.5)" }}>AI Reasoning</span>
                    <p className="text-sm mt-1" style={{ color: "#F5F5F5" }}>
                      {action.ai_reasoning}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Timeline / Logs */}
            <div>
              <h3
                className="text-xs font-medium uppercase tracking-wider mb-3"
                style={{ color: "rgba(245, 245, 245, 0.5)" }}
              >
                Timeline
              </h3>
              <div
                className="p-4 rounded-md"
                style={{ background: "rgba(255, 255, 255, 0.03)" }}
              >
                {loadingLogs ? (
                  <SkeletonLogs />
                ) : logs.length === 0 ? (
                  <div className="text-sm text-center py-4" style={{ color: "rgba(245, 245, 245, 0.5)" }}>
                    No activity yet
                  </div>
                ) : (
                  <div className="space-y-4">
                    {logs.map((log, idx) => (
                      <LogItem key={log.id || idx} log={log} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Footer Actions */}
          {action.status === "pending" && (
            <div
              className="flex items-center justify-end gap-3 px-6 py-4"
              style={{ borderTop: "1px solid rgba(255, 255, 255, 0.05)" }}
            >
              <button
                onClick={handleReject}
                disabled={actionLoading}
                className="flex items-center gap-2 px-4 py-2 rounded-sm font-medium text-sm"
                style={{
                  background: "rgba(239, 68, 68, 0.1)",
                  color: "#EF4444",
                  border: "1px solid rgba(239, 68, 68, 0.3)",
                }}
              >
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <XCircle className="w-4 h-4" />}
                Reject
              </button>
              <button
                onClick={handleApprove}
                disabled={actionLoading}
                className="flex items-center gap-2 px-5 py-2 rounded-sm font-medium text-sm"
                style={{
                  background: actionLoading ? "rgba(0, 229, 255, 0.5)" : "#00E5FF",
                  color: "#090909",
                }}
              >
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                Approve Action
              </button>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
