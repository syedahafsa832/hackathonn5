import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Settings as SettingsIcon,
  Store,
  Mail,
  Shield,
  Save,
  RefreshCw,
  Check,
  XCircle,
  Loader2,
  Eye,
  EyeOff,
  AlertCircle,
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

// Section card wrapper
function SettingsSection({ title, icon: Icon, children }) {
  return (
    <div
      className="rounded-md overflow-hidden"
      style={{
        background: "rgba(255, 255, 255, 0.03)",
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <div
        className="flex items-center gap-3 px-4 py-3"
        style={{ borderBottom: "1px solid rgba(255, 255, 255, 0.05)" }}
      >
        <Icon className="w-4 h-4" style={{ color: "#A0A0A0" }} />
        <h3 className="text-sm font-medium" style={{ color: "#F5F5F5" }}>
          {title}
        </h3>
      </div>
      <div className="p-4 space-y-4">{children}</div>
    </div>
  );
}

// Input field component
function InputField({ label, type = "text", value, onChange, placeholder, showPassword, onTogglePassword, disabled }) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1.5" style={{ color: "#A0A0A0" }}>
        {label}
      </label>
      <div className="relative">
        <input
          type={showPassword !== undefined ? (showPassword ? "text" : "password") : type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className="w-full px-3 py-2 rounded-md text-sm outline-none transition-all"
          style={{
            background: "rgba(0, 0, 0, 0.3)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            color: "#F5F5F5",
          }}
        />
        {showPassword !== undefined && (
          <button
            type="button"
            onClick={onTogglePassword}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-white/10"
          >
            {showPassword ? (
              <EyeOff className="w-4 h-4" style={{ color: "#A0A0A0" }} />
            ) : (
              <Eye className="w-4 h-4" style={{ color: "#A0A0A0" }} />
            )}
          </button>
        )}
      </div>
    </div>
  );
}

// Toggle switch component
function Toggle({ label, description, checked, onChange }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-sm" style={{ color: "#F5F5F5" }}>{label}</div>
        {description && (
          <div className="text-xs mt-0.5" style={{ color: "#808080" }}>{description}</div>
        )}
      </div>
      <button
        onClick={() => onChange(!checked)}
        className="relative w-10 h-5 rounded-full transition-colors"
        style={{
          background: checked ? "rgba(34, 197, 94, 0.3)" : "rgba(255, 255, 255, 0.1)",
          border: `1px solid ${checked ? "rgba(34, 197, 94, 0.5)" : "rgba(255, 255, 255, 0.1)"}`,
        }}
      >
        <div
          className="absolute top-0.5 w-4 h-4 rounded-full transition-transform"
          style={{
            background: checked ? "#22C55E" : "#A0A0A0",
            transform: checked ? "translateX(22px)" : "translateX(2px)",
          }}
        />
      </button>
    </div>
  );
}

// Connection status badge
function ConnectionBadge({ connected, loading }) {
  if (loading) {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
        style={{ background: "rgba(59, 130, 246, 0.2)", border: "1px solid rgba(59, 130, 246, 0.3)" }}>
        <Loader2 className="w-3 h-3 animate-spin" style={{ color: "#3B82F6" }} />
        <span style={{ color: "#3B82F6" }}>Testing...</span>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
      style={{
        background: connected ? "rgba(34, 197, 94, 0.2)" : "rgba(239, 68, 68, 0.2)",
        border: `1px solid ${connected ? "rgba(34, 197, 94, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
      }}
    >
      {connected ? (
        <>
          <Check className="w-3 h-3" style={{ color: "#22C55E" }} />
          <span style={{ color: "#22C55E" }}>Connected</span>
        </>
      ) : (
        <>
          <XCircle className="w-3 h-3" style={{ color: "#EF4444" }} />
          <span style={{ color: "#EF4444" }}>Not Connected</span>
        </>
      )}
    </div>
  );
}

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingShopify, setTestingShopify] = useState(false);
  const [testingEmail, setTestingEmail] = useState(false);
  const [toast, setToast] = useState(null);

  // Brand selection
  const [brands, setBrands] = useState([]);
  const [selectedBrandId, setSelectedBrandId] = useState(null);

  // Form state
  const [brandName, setBrandName] = useState("");
  const [shopifyDomain, setShopifyDomain] = useState("");
  const [shopifyToken, setShopifyToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [supportEmail, setSupportEmail] = useState("");
  const [aiEnabled, setAiEnabled] = useState(true);
  const [aiAutoRespond, setAiAutoRespond] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.75);
  const [shopifyConnected, setShopifyConnected] = useState(false);
  const [emailConnected, setEmailConnected] = useState(false);

  // Load brands on mount
  useEffect(() => {
    loadBrands();
  }, []);

  // Load brand settings when selection changes
  useEffect(() => {
    if (selectedBrandId) {
      loadBrandSettings(selectedBrandId);
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
    } finally {
      setLoading(false);
    }
  };

  const loadBrandSettings = async (brandId) => {
    try {
      setLoading(true);
      const response = await apiClient.getBrand(brandId);
      const brand = response.brand;

      setBrandName(brand.name || "");
      setShopifyDomain(brand.shopify_domain || "");
      setShopifyToken(""); // Don't show existing token
      setSupportEmail(brand.support_email || "");
      setAiEnabled(brand.ai_enabled !== false);
      setAiAutoRespond(brand.ai_auto_respond || false);
      setConfidenceThreshold(brand.ai_confidence_threshold || 0.75);
      setShopifyConnected(brand.shopify_connected || false);
    } catch (error) {
      console.error("Error loading brand settings:", error);
      setToast({ message: "Failed to load settings", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      setToast({ message: "Saving settings...", type: "loading" });

      await apiClient.updateBrandSettings(selectedBrandId, {
        name: brandName,
        support_email: supportEmail,
        ai_enabled: aiEnabled,
        ai_auto_respond: aiAutoRespond,
        ai_confidence_threshold: confidenceThreshold,
      });

      setToast({ message: "Settings saved successfully", type: "success" });
    } catch (error) {
      console.error("Error saving settings:", error);
      setToast({ message: error.message || "Failed to save settings", type: "error" });
    } finally {
      setSaving(false);
    }
  };

  const handleTestShopify = async () => {
    try {
      setTestingShopify(true);
      setToast({ message: "Testing Shopify connection...", type: "loading" });

      const response = await apiClient.testShopifyConnection(selectedBrandId);

      if (response.success) {
        setShopifyConnected(true);
        setToast({ message: `Connected to ${response.shop_name || "Shopify"}`, type: "success" });
      } else {
        setShopifyConnected(false);
        setToast({ message: response.error || "Connection failed", type: "error" });
      }
    } catch (error) {
      console.error("Error testing Shopify:", error);
      setShopifyConnected(false);
      setToast({ message: error.message || "Connection test failed", type: "error" });
    } finally {
      setTestingShopify(false);
    }
  };

  const handleTestEmail = async () => {
    if (!supportEmail) {
      setToast({ message: "Please enter a support email address", type: "error" });
      return;
    }

    try {
      setTestingEmail(true);
      setToast({ message: "Testing email connection...", type: "loading" });

      // Test email connection via API
      const response = await fetch(
        `${process.env.REACT_APP_API_BASE_URL || "https://hackathonn5-production.up.railway.app"}/api/v2/brands/${selectedBrandId}/email/test`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${localStorage.getItem("authToken") || ""}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ email: supportEmail }),
        }
      );

      const data = await response.json();

      if (response.ok && data.success) {
        setEmailConnected(true);
        setToast({ message: "Email connection successful", type: "success" });
      } else {
        setEmailConnected(false);
        setToast({ message: data.error || "Connection failed", type: "error" });
      }
    } catch (error) {
      console.error("Error testing email:", error);
      setEmailConnected(false);
      setToast({ message: error.message || "Connection test failed", type: "error" });
    } finally {
      setTestingEmail(false);
    }
  };

  const handleConnectShopify = async () => {
    if (!shopifyDomain || !shopifyToken) {
      setToast({ message: "Please enter domain and access token", type: "error" });
      return;
    }

    try {
      setTestingShopify(true);
      setToast({ message: "Connecting to Shopify...", type: "loading" });

      const response = await apiClient.connectShopify(selectedBrandId, {
        shop_domain: shopifyDomain,
        access_token: shopifyToken,
      });

      if (response.success) {
        setShopifyConnected(true);
        setShopifyToken(""); // Clear token after connecting
        setToast({ message: `Connected to ${response.shop_name || "Shopify"}`, type: "success" });
      } else {
        setToast({ message: response.error || "Connection failed", type: "error" });
      }
    } catch (error) {
      console.error("Error connecting Shopify:", error);
      setToast({ message: error.message || "Failed to connect", type: "error" });
    } finally {
      setTestingShopify(false);
    }
  };

  const handleDisconnectShopify = async () => {
    try {
      setTestingShopify(true);
      await apiClient.disconnectShopify(selectedBrandId);
      setShopifyConnected(false);
      setShopifyDomain("");
      setToast({ message: "Shopify disconnected", type: "success" });
    } catch (error) {
      console.error("Error disconnecting Shopify:", error);
      setToast({ message: error.message || "Failed to disconnect", type: "error" });
    } finally {
      setTestingShopify(false);
    }
  };

  if (loading && brands.length === 0) {
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
          <SettingsIcon className="w-5 h-5" style={{ color: "#A0A0A0" }} />
          <h1 className="text-lg md:text-xl font-semibold">Settings</h1>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all hover:opacity-90 w-full md:w-auto justify-center"
          style={{
            background: "rgba(34, 197, 94, 0.2)",
            border: "1px solid rgba(34, 197, 94, 0.3)",
            color: "#22C55E",
          }}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save Changes
        </button>
      </div>

      {/* Brand selector */}
      {brands.length > 1 && (
        <div className="mb-6">
          <label className="block text-xs font-medium mb-1.5" style={{ color: "#A0A0A0" }}>
            Select Brand
          </label>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Shopify Integration */}
        <SettingsSection title="Shopify Integration" icon={Store}>
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm" style={{ color: "#A0A0A0" }}>Connection Status</span>
            <ConnectionBadge connected={shopifyConnected} loading={testingShopify} />
          </div>

          <InputField
            label="Shop Domain"
            value={shopifyDomain}
            onChange={setShopifyDomain}
            placeholder="your-store.myshopify.com"
            disabled={shopifyConnected}
          />

          {!shopifyConnected && (
            <InputField
              label="Access Token"
              value={shopifyToken}
              onChange={setShopifyToken}
              placeholder="shpat_..."
              showPassword={showToken}
              onTogglePassword={() => setShowToken(!showToken)}
            />
          )}

          <div className="flex gap-2 pt-2">
            {shopifyConnected ? (
              <>
                <button
                  onClick={handleTestShopify}
                  disabled={testingShopify}
                  className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-all hover:opacity-90"
                  style={{
                    background: "rgba(59, 130, 246, 0.2)",
                    border: "1px solid rgba(59, 130, 246, 0.3)",
                    color: "#3B82F6",
                  }}
                >
                  <RefreshCw className={`w-4 h-4 ${testingShopify ? "animate-spin" : ""}`} />
                  Test Connection
                </button>
                <button
                  onClick={handleDisconnectShopify}
                  disabled={testingShopify}
                  className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-all hover:opacity-90"
                  style={{
                    background: "rgba(239, 68, 68, 0.2)",
                    border: "1px solid rgba(239, 68, 68, 0.3)",
                    color: "#EF4444",
                  }}
                >
                  <XCircle className="w-4 h-4" />
                  Disconnect
                </button>
              </>
            ) : (
              <button
                onClick={handleConnectShopify}
                disabled={testingShopify || !shopifyDomain || !shopifyToken}
                className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-all hover:opacity-90"
                style={{
                  background: "rgba(34, 197, 94, 0.2)",
                  border: "1px solid rgba(34, 197, 94, 0.3)",
                  color: "#22C55E",
                  opacity: !shopifyDomain || !shopifyToken ? 0.5 : 1,
                }}
              >
                <Store className="w-4 h-4" />
                Connect Store
              </button>
            )}
          </div>
        </SettingsSection>

        {/* Support Email */}
        <SettingsSection title="Support Email" icon={Mail}>
          <div className="flex items-center justify-between">
            <InputField
              label="Support Email Address"
              type="email"
              value={supportEmail}
              onChange={setSupportEmail}
              placeholder="support@yourbrand.com"
            />
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestEmail}
              disabled={testingEmail || !supportEmail}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-all hover:opacity-90"
              style={{
                background: "rgba(59, 130, 246, 0.2)",
                border: "1px solid rgba(59, 130, 246, 0.3)",
                color: "#3B82F6",
                opacity: !supportEmail ? 0.5 : 1,
              }}
            >
              <RefreshCw className={`w-4 h-4 ${testingEmail ? "animate-spin" : ""}`} />
              Test Connection
            </button>
            <ConnectionBadge connected={emailConnected} loading={testingEmail} />
          </div>
          <div className="flex items-start gap-2 p-3 rounded-md" style={{ background: "rgba(59, 130, 246, 0.1)" }}>
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: "#3B82F6" }} />
            <p className="text-xs" style={{ color: "#A0A0A0" }}>
              Incoming emails to this address will create support tickets automatically.
            </p>
          </div>
        </SettingsSection>

        {/* AI Settings */}
        <SettingsSection title="AI Configuration" icon={Zap}>
          <Toggle
            label="Enable AI Responses"
            description="Allow AI to analyze and respond to support tickets"
            checked={aiEnabled}
            onChange={setAiEnabled}
          />

          <Toggle
            label="Auto-Reply Mode"
            description="Automatically send AI responses without human approval"
            checked={aiAutoRespond}
            onChange={setAiAutoRespond}
          />

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "#A0A0A0" }}>
              Confidence Threshold ({Math.round(confidenceThreshold * 100)}%)
            </label>
            <input
              type="range"
              min="0.5"
              max="0.95"
              step="0.05"
              value={confidenceThreshold}
              onChange={(e) => setConfidenceThreshold(parseFloat(e.target.value))}
              className="w-full"
              style={{ accentColor: "#22C55E" }}
            />
            <p className="text-xs mt-1" style={{ color: "#808080" }}>
              AI will only auto-respond when confidence exceeds this threshold
            </p>
          </div>
        </SettingsSection>

        {/* Brand Settings */}
        <SettingsSection title="Brand Information" icon={Shield}>
          <InputField
            label="Brand Name"
            value={brandName}
            onChange={setBrandName}
            placeholder="Your Brand Name"
          />
        </SettingsSection>
      </div>
    </div>
  );
}
