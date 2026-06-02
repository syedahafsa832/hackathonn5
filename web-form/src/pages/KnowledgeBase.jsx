import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen,
  Plus,
  Trash2,
  Search,
  FileText,
  Check,
  XCircle,
  Loader2,
  RefreshCw,
  Upload,
  X,
  ChevronDown,
  Database,
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

// Status badge component
function StatusBadge({ status }) {
  const config = {
    completed: { bg: "rgba(34, 197, 94, 0.2)", border: "rgba(34, 197, 94, 0.3)", color: "#22C55E", icon: Check },
    processing: { bg: "rgba(59, 130, 246, 0.2)", border: "rgba(59, 130, 246, 0.3)", color: "#3B82F6", icon: Loader2 },
    failed: { bg: "rgba(239, 68, 68, 0.2)", border: "rgba(239, 68, 68, 0.3)", color: "#EF4444", icon: XCircle },
  };

  const { bg, border, color, icon: Icon } = config[status] || config.processing;

  return (
    <div
      className="flex items-center gap-1.5 px-2 py-1 rounded text-xs"
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      <Icon className={`w-3 h-3 ${status === "processing" ? "animate-spin" : ""}`} style={{ color }} />
      <span style={{ color }}>{status}</span>
    </div>
  );
}

// Stats card component
function StatsCard({ icon: Icon, label, value }) {
  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-md"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <Icon className="w-5 h-5" style={{ color: "#A0A0A0" }} />
      <div>
        <div className="text-lg font-semibold" style={{ color: "#F5F5F5" }}>{value}</div>
        <div className="text-xs" style={{ color: "#808080" }}>{label}</div>
      </div>
    </div>
  );
}

// Source card component
function SourceCard({ source, onDelete, deleting }) {
  const [expanded, setExpanded] = useState(false);

  const formatDate = (dateStr) => {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
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
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <FileText className="w-4 h-4 flex-shrink-0" style={{ color: "#A0A0A0" }} />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate" style={{ color: "#F5F5F5" }}>
              {source.name}
            </div>
            <div className="text-xs" style={{ color: "#808080" }}>
              {source.chunk_count} chunks - {formatDate(source.created_at)}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={source.status} />
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete(source.id);
            }}
            disabled={deleting}
            className="p-1.5 rounded hover:bg-white/10 transition-colors"
          >
            {deleting ? (
              <Loader2 className="w-4 h-4 animate-spin" style={{ color: "#EF4444" }} />
            ) : (
              <Trash2 className="w-4 h-4" style={{ color: "#EF4444" }} />
            )}
          </button>
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
              <div className="flex gap-2">
                <span style={{ color: "#808080" }}>Type:</span>
                <span style={{ color: "#A0A0A0" }}>{source.source_type}</span>
              </div>
              <div className="flex gap-2">
                <span style={{ color: "#808080" }}>Chunks:</span>
                <span style={{ color: "#A0A0A0" }}>{source.chunk_count}</span>
              </div>
              <div className="flex gap-2">
                <span style={{ color: "#808080" }}>Tokens:</span>
                <span style={{ color: "#A0A0A0" }}>{source.total_tokens?.toLocaleString() || "N/A"}</span>
              </div>
              {source.error_message && (
                <div className="p-2 rounded" style={{ background: "rgba(239, 68, 68, 0.1)" }}>
                  <span style={{ color: "#EF4444" }}>{source.error_message}</span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// Upload modal component
function UploadModal({ isOpen, onClose, onUpload, uploading }) {
  const [name, setName] = useState("");
  const [content, setContent] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    onUpload({ name, content });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="relative w-full max-w-lg rounded-lg overflow-hidden"
        style={{
          background: "#1A1A1A",
          border: "1px solid rgba(255, 255, 255, 0.1)",
        }}
      >
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.1)" }}
        >
          <h3 className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
            Add Knowledge Content
          </h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/10">
            <X className="w-4 h-4" style={{ color: "#A0A0A0" }} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "#A0A0A0" }}>
              Title
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Refund Policy"
              className="w-full px-3 py-2 rounded-md text-sm outline-none"
              style={{
                background: "rgba(0, 0, 0, 0.3)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                color: "#F5F5F5",
              }}
            />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "#A0A0A0" }}>
              Content
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Paste your knowledge base content here..."
              rows={10}
              className="w-full px-3 py-2 rounded-md text-sm outline-none resize-none"
              style={{
                background: "rgba(0, 0, 0, 0.3)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                color: "#F5F5F5",
              }}
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-md text-sm"
              style={{
                background: "rgba(255, 255, 255, 0.05)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                color: "#A0A0A0",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={uploading || !name.trim() || !content.trim()}
              className="flex items-center gap-2 px-4 py-2 rounded-md text-sm"
              style={{
                background: "rgba(34, 197, 94, 0.2)",
                border: "1px solid rgba(34, 197, 94, 0.3)",
                color: "#22C55E",
                opacity: uploading || !name.trim() || !content.trim() ? 0.5 : 1,
              }}
            >
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              Upload
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}

export default function KnowledgeBase() {
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [showUpload, setShowUpload] = useState(false);
  const [toast, setToast] = useState(null);

  // Brand selection
  const [brands, setBrands] = useState([]);
  const [selectedBrandId, setSelectedBrandId] = useState(null);

  // Data
  const [sources, setSources] = useState([]);
  const [stats, setStats] = useState({ total_sources: 0, total_chunks: 0, total_tokens: 0 });

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  // Load brands on mount
  useEffect(() => {
    loadBrands();
  }, []);

  // Load data when brand changes
  useEffect(() => {
    if (selectedBrandId) {
      loadData();
    }
  }, [selectedBrandId]);

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

  const loadData = async () => {
    try {
      setLoading(true);
      const [sourcesRes, statsRes] = await Promise.all([
        apiClient.getKnowledgeSources(selectedBrandId),
        apiClient.getKnowledgeStats(selectedBrandId),
      ]);
      setSources(sourcesRes.sources || []);
      setStats(statsRes.stats || { total_sources: 0, total_chunks: 0, total_tokens: 0 });
    } catch (error) {
      console.error("Error loading knowledge base:", error);
      setToast({ message: "Failed to load knowledge base", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  const handleUpload = async (data) => {
    try {
      setUploading(true);
      setToast({ message: "Processing content...", type: "loading" });

      const response = await apiClient.uploadKnowledge(selectedBrandId, data);

      if (response.success) {
        setShowUpload(false);
        setToast({
          message: `Created ${response.chunk_count} chunks from "${data.name}"`,
          type: "success",
        });
        loadData();
      } else {
        setToast({ message: response.error || "Upload failed", type: "error" });
      }
    } catch (error) {
      console.error("Error uploading:", error);
      setToast({ message: error.message || "Upload failed", type: "error" });
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (sourceId) => {
    try {
      setDeleting(sourceId);
      await apiClient.deleteKnowledgeSource(selectedBrandId, sourceId);
      setToast({ message: "Source deleted", type: "success" });
      loadData();
    } catch (error) {
      console.error("Error deleting:", error);
      setToast({ message: error.message || "Delete failed", type: "error" });
    } finally {
      setDeleting(null);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;

    try {
      setSearching(true);
      const response = await apiClient.searchBrandKnowledge(selectedBrandId, searchQuery);
      setSearchResults(response.results || []);
    } catch (error) {
      console.error("Error searching:", error);
      setToast({ message: "Search failed", type: "error" });
    } finally {
      setSearching(false);
    }
  };

  if (loading && sources.length === 0) {
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

      <UploadModal
        isOpen={showUpload}
        onClose={() => setShowUpload(false)}
        onUpload={handleUpload}
        uploading={uploading}
      />

      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-4 md:mb-6">
        <div className="flex items-center gap-3">
          <BookOpen className="w-5 h-5" style={{ color: "#A0A0A0" }} />
          <h1 className="text-lg md:text-xl font-semibold">Knowledge Base</h1>
        </div>
        <button
          onClick={() => setShowUpload(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all hover:opacity-90 w-full md:w-auto justify-center"
          style={{
            background: "rgba(34, 197, 94, 0.2)",
            border: "1px solid rgba(34, 197, 94, 0.3)",
            color: "#22C55E",
          }}
        >
          <Plus className="w-4 h-4" />
          Add Content
        </button>
      </div>

      {/* Brand selector */}
      {brands.length > 1 && (
        <div className="mb-6">
          <select
            value={selectedBrandId || ""}
            onChange={(e) => setSelectedBrandId(e.target.value)}
            className="px-3 py-2 rounded-md text-sm outline-none"
            style={{
              background: "rgba(0, 0, 0, 0.3)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              color: "#F5F5F5",
            }}
          >
            {brands.map((brand) => (
              <option key={brand.id} value={brand.id}>
                {brand.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatsCard icon={FileText} label="Sources" value={stats.total_sources} />
        <StatsCard icon={Database} label="Chunks" value={stats.total_chunks} />
        <StatsCard icon={Zap} label="Tokens" value={stats.total_tokens?.toLocaleString() || "0"} />
      </div>

      {/* Search */}
      <div
        className="flex items-center gap-2 p-4 mb-6 rounded-md"
        style={{
          background: "rgba(255, 255, 255, 0.03)",
          border: "1px solid rgba(255, 255, 255, 0.1)",
        }}
      >
        <Search className="w-4 h-4" style={{ color: "#A0A0A0" }} />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Search knowledge base..."
          className="flex-1 bg-transparent outline-none text-sm"
          style={{ color: "#F5F5F5" }}
        />
        <button
          onClick={handleSearch}
          disabled={searching || !searchQuery.trim()}
          className="px-3 py-1.5 rounded text-xs"
          style={{
            background: "rgba(59, 130, 246, 0.2)",
            border: "1px solid rgba(59, 130, 246, 0.3)",
            color: "#3B82F6",
          }}
        >
          {searching ? <Loader2 className="w-3 h-3 animate-spin" /> : "Search"}
        </button>
      </div>

      {/* Search results */}
      {searchResults.length > 0 && (
        <div className="mb-6">
          <h3 className="text-sm font-medium mb-3" style={{ color: "#A0A0A0" }}>
            Search Results ({searchResults.length})
          </h3>
          <div className="space-y-2">
            {searchResults.map((result, index) => (
              <div
                key={index}
                className="p-3 rounded-md"
                style={{
                  background: "rgba(59, 130, 246, 0.1)",
                  border: "1px solid rgba(59, 130, 246, 0.2)",
                }}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs" style={{ color: "#3B82F6" }}>
                    {result.source_name || "Unknown Source"}
                  </span>
                  <span className="text-xs" style={{ color: "#808080" }}>
                    {(result.similarity * 100).toFixed(0)}% match
                  </span>
                </div>
                <p className="text-sm" style={{ color: "#A0A0A0" }}>
                  {result.content?.substring(0, 200)}...
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sources list */}
      <div>
        <h3 className="text-sm font-medium mb-3" style={{ color: "#A0A0A0" }}>
          Knowledge Sources
        </h3>
        <div className="space-y-2">
          {sources.length === 0 ? (
            <div
              className="flex flex-col items-center justify-center py-16 rounded-md"
              style={{
                background: "rgba(255, 255, 255, 0.03)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
              }}
            >
              <BookOpen className="w-12 h-12 mb-4" style={{ color: "#404040" }} />
              <p className="text-sm mb-2" style={{ color: "#808080" }}>
                No knowledge sources yet
              </p>
              <p className="text-xs" style={{ color: "#606060" }}>
                Add content to help AI respond to customer queries
              </p>
            </div>
          ) : (
            sources.map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                onDelete={handleDelete}
                deleting={deleting === source.id}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
