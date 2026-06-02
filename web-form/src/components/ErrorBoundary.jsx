/**
 * Error Boundary Component
 * Catches JavaScript errors in the component tree and displays fallback UI
 */

import React from "react";
import { AlertTriangle, RefreshCw, Home } from "lucide-react";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log the error to console (or error reporting service)
    console.error("Error Boundary caught an error:", error, errorInfo);
    this.setState({
      error,
      errorInfo,
    });
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleGoHome = () => {
    window.location.href = "/";
  };

  render() {
    if (this.state.hasError) {
      // Custom fallback UI
      if (this.props.fallback) {
        return this.props.fallback(this.state.error, this.props.resetError);
      }

      return (
        <div
          className="min-h-screen flex items-center justify-center p-4"
          style={{ background: "#090909" }}
        >
          <div
            className="max-w-md w-full p-6 rounded-lg text-center"
            style={{
              background: "rgba(255, 255, 255, 0.03)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
            }}
          >
            <div
              className="w-16 h-16 mx-auto mb-4 rounded-full flex items-center justify-center"
              style={{ background: "rgba(239, 68, 68, 0.1)" }}
            >
              <AlertTriangle className="w-8 h-8" style={{ color: "#EF4444" }} />
            </div>

            <h1
              className="text-xl font-medium mb-2"
              style={{ color: "#F5F5F5" }}
            >
              Something went wrong
            </h1>

            <p
              className="text-sm mb-6"
              style={{ color: "rgba(245, 245, 245, 0.6)" }}
            >
              We're sorry, but something unexpected happened. Please try again or go back to the home page.
            </p>

            {this.props.showError && this.state.error && (
              <div
                className="mb-6 p-3 rounded text-left text-xs overflow-auto"
                style={{
                  background: "rgba(0, 0, 0, 0.3)",
                  color: "rgba(245, 245, 245, 0.5)",
                  maxHeight: "150px",
                }}
              >
                {this.state.error.toString()}
              </div>
            )}

            <div className="flex items-center justify-center gap-3">
              <button
                onClick={this.handleRetry}
                className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors"
                style={{
                  background: "rgba(0, 229, 255, 0.1)",
                  border: "1px solid rgba(0, 229, 255, 0.3)",
                  color: "#00E5FF",
                }}
              >
                <RefreshCw className="w-4 h-4" />
                Try Again
              </button>

              <button
                onClick={this.handleGoHome}
                className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors"
                style={{
                  background: "rgba(255, 255, 255, 0.05)",
                  border: "1px solid rgba(255, 255, 255, 0.1)",
                  color: "rgba(245, 245, 245, 0.8)",
                }}
              >
                <Home className="w-4 h-4" />
                Go Home
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;