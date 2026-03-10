import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  RefreshCw,
  TrendingUp,
  Store,
  Package,
  Clock,
  Brain,
  Loader2,
  DollarSign,
  CheckCircle,
} from "lucide-react";

const API_BASE_URL = "https://hackathonn5-production.up.railway.app";

// Components
function MetricCard({ title, value, subtitle, icon: Icon, glowColor, isCurrency, loading }) {
  if (loading) {
    return (
      <div className="relative p-6 rounded-md animate-pulse" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.1)" }}>
        <div className="w-10 h-10 rounded-md mb-4" style={{ background: "rgba(255,255,255,0.1)" }} />
        <div className="w-32 h-8 rounded" style={{ background: "rgba(255,255,255,0.1)" }} />
        <div className="w-24 h-4 rounded mt-2" style={{ background: "rgba(255,255,255,0.1)" }} />
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative p-6 rounded-md"
      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.1)", boxShadow: glowColor ? `0 0 40px ${glowColor}20` : "none" }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="p-2 rounded-md" style={{ background: glowColor ? `${glowColor}15` : "rgba(255,255,255,0.05)" }}>
          <Icon className="w-5 h-5" style={{ color: glowColor || "#F5F5F5" }} />
        </div>
      </div>
      <div className="text-3xl font-serif font-medium" style={{ color: "#F5F5F5" }}>
        {isCurrency ? `$${Number(value || 0).toLocaleString()}` : value}
      </div>
      <div className="text-sm mt-1" style={{ color: "rgba(245,245,245,0.6)" }}>{title}</div>
      <div className="text-xs mt-2" style={{ color: "rgba(245,245,245,0.4)" }}>{subtitle}</div>
    </motion.div>
  );
}

function ActivityItem({ item, index }) {
  const statusColors = { success: "#22C55E", escalated: "#EF4444", pending: "#F59E0B" };
  const StatusIcon = item.status === "success" ? CheckCircle : item.status === "escalated" ? AlertTriangle : Clock;

  const formatTime = (timestamp) => {
    if (!timestamp) return "Just now";
    const diff = Math.floor((new Date() - new Date(timestamp)) / 60000);
    if (diff < 1) return "Just now";
    if (diff < 60) return `${diff}m ago`;
    if (diff < 1440) return `${Math.floor(diff / 60)}h ago`;
    return `${Math.floor(diff / 1440)}d ago`;
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
      className="flex items-start gap-4 py-3"
      style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
    >
      <div className="text-xs font-mono mt-1" style={{ color: "rgba(245,245,245,0.4)" }}>{formatTime(item.executed_at)}</div>
      <div className="p-1.5 rounded-md" style={{ background: "rgba(255,255,255,0.05)" }}>
        {item.action_type === "Exchange" ? (
          <TrendingUp className="w-3.5 h-3.5" style={{ color: "#22C55E" }} />
        ) : (
          <DollarSign className="w-3.5 h-3.5" style={{ color: "#EF4444" }} />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
            {item.action_type === "Exchange" ? "Exchange Saved" : "Refund Processed"}
          </span>
          <StatusIcon className="w-3.5 h-3.5" style={{ color: statusColors[item.status] || "#22C55E" }} />
        </div>
        <div className="text-xs mt-0.5 truncate" style={{ color: "rgba(245,245,245,0.5)" }}>
          Order #{item.order_id} - {item.item_name}
        </div>
        {item.revenue_at_stake && (
          <div className="text-xs font-medium mt-0.5" style={{ color: "#22C55E" }}>
            ${Number(item.revenue_at_stake).toFixed(2)}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function CircularProgress({ percentage, label, sublabel, loading }) {
  if (loading) {
    return <div className="relative w-24 h-24 animate-pulse" style={{ background: "rgba(255,255,255,0.1)", borderRadius: "50%" }} />;
  }
  const circumference = 2 * Math.PI * 40;
  const offset = circumference - ((percentage || 0) / 100) * circumference;
  return (
    <div className="relative w-24 h-24">
      <svg className="w-full h-full transform -rotate-90" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="40" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="8" />
        <motion.circle cx="50" cy="50" r="40" fill="none" stroke="#9D50BB" strokeWidth="8" strokeLinecap="round" strokeDasharray={circumference} initial={{ strokeDashoffset: circumference }} animate={{ strokeDashoffset: offset }} transition={{ duration: 1.5 }} style={{ filter: "drop-shadow(0 0 10px rgba(157,80,187,0.5))" }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-lg font-medium" style={{ color: "#F5F5F5" }}>{percentage || 0}%</span>
      </div>
      <div className="absolute -bottom-6 left-0 right-0 text-center">
        <div className="text-xs font-medium" style={{ color: "#F5F5F5" }}>{label}</div>
        <div className="text-xs" style={{ color: "rgba(245,245,245,0.5)" }}>{sublabel}</div>
      </div>
    </div>
  );
}

export default function RevenueCommandCenter() {
  const [metrics, setMetrics] = useState({ revenueProtected: 0, automationRate: 0, highRiskEscalations: 0 });
  const [activityFeed, setActivityFeed] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const [statsRes, executedRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/actions/stats`),
        fetch(`${API_BASE_URL}/api/actions/executed?limit=10`)
      ]);

      const statsData = await statsRes.json();
      const executedData = await executedRes.json();

      setMetrics({
        revenueProtected: statsData.revenue_saved || 0,
        automationRate: statsData.total > 0 ? Math.round(((statsData.executed || 0) / statsData.total) * 100) : 0,
        highRiskEscalations: statsData.high_risk || 0,
      });

      setActivityFeed(executedData.feed || []);
    } catch (error) {
      console.error("Failed to fetch dashboard data:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen" style={{ background: "#090909" }}>
      <header className="sticky top-0 z-50 flex items-center justify-between px-6 py-4" style={{ background: "rgba(9,9,9,0.8)", backdropFilter: "blur(12px)", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
        <div className="flex items-center gap-2">
          <Brain className="w-6 h-6" style={{ color: "#00E5FF" }} />
          <span className="text-lg font-medium" style={{ color: "#F5F5F5" }}>AI Ops Console</span>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-md" style={{ background: "rgba(0,229,255,0.1)", border: "1px solid rgba(0,229,255,0.3)" }}>
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <span className="text-xs font-medium" style={{ color: "#00E5FF" }}>System Healthy</span>
        </div>
      </header>

      <main className="p-6">
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
          <h1 className="text-2xl font-medium" style={{ color: "#F5F5F5" }}>Revenue Command Center</h1>
          <p className="text-sm mt-1" style={{ color: "rgba(245,245,245,0.5)" }}>Founder View - Real-time AI Performance Monitoring</p>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <MetricCard title="Revenue Protected" value={metrics.revenueProtected} subtitle="Refunds converted to Exchanges" icon={TrendingUp} glowColor="#9D50BB" isCurrency loading={loading} />
          <div className="flex items-center justify-center p-6 rounded-md" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.1)" }}>
            <CircularProgress percentage={metrics.automationRate} label="Automation" sublabel="Tickets resolved" loading={loading} />
          </div>
          <MetricCard title="High-Risk Escalations" value={metrics.highRiskEscalations} subtitle="Active tickets requiring human intervention" icon={AlertTriangle} glowColor="#EF4444" loading={loading} />
        </div>

        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="rounded-md overflow-hidden" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.1)" }}>
          <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4" style={{ color: "#00E5FF" }} />
              <span className="text-sm font-medium uppercase" style={{ color: "rgba(245,245,245,0.8)" }}>Nerve Center - Live Action Feed</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs" style={{ color: "rgba(245,245,245,0.5)" }}>Live</span>
            </div>
          </div>
          <div className="px-6 py-2 max-h-[400px] overflow-y-auto">
            {loading ? (
              <div className="flex items-center justify-center py-8"><Loader2 className="w-6 h-6 animate-spin" style={{ color: "#00E5FF" }} /></div>
            ) : activityFeed.length === 0 ? (
              <div className="text-center py-8" style={{ color: "rgba(245,245,245,0.5)" }}>No executed actions yet. Approve exchanges to see the feed.</div>
            ) : (
              <AnimatePresence>{activityFeed.map((item, i) => <ActivityItem key={item.id || i} item={item} index={i} />)}</AnimatePresence>
            )}
          </div>
        </motion.div>

        <div className="mt-6 p-4 rounded-md flex items-center justify-between" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.1)" }}>
          <div className="flex items-center gap-6">
            {[{ label: "Shopify API", icon: Store }, { label: "Inventory", icon: Package }, { label: "AI Engine", icon: Brain }].map(item => (
              <div key={item.label} className="flex items-center gap-2">
                <item.icon className="w-4 h-4" style={{ color: "rgba(245,245,245,0.6)" }} />
                <span className="text-xs" style={{ color: "rgba(245,245,245,0.6)" }}>{item.label}</span>
                <div className="w-2 h-2 rounded-full" style={{ background: "#22C55E" }} />
              </div>
            ))}
          </div>
          <button onClick={() => window.location.reload()} className="p-2 rounded-md hover:bg-white/5"><RefreshCw className="w-4 h-4" style={{ color: "rgba(245,245,245,0.5)" }} /></button>
        </div>
      </main>
    </div>
  );
}
