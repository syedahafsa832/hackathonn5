import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import SupportPage from "./pages/SupportPage";
import TicketStatusPage from "./pages/TicketStatusPage";
import RevenueCommandCenter from "./pages/RevenueCommandCenter";
import SmartApprovalInbox from "./pages/SmartApprovalInbox";
import SmartInbox from "./pages/SmartInbox";
import Inbox from "./pages/Inbox";
import History from "./pages/History";
import Settings from "./pages/Settings";
import KnowledgeBase from "./pages/KnowledgeBase";
import ErrorBoundary from "./components/ErrorBoundary";

function App() {
  return (
    <ErrorBoundary>
      <Router>
        <div className="App container">
          <Routes>
            <Route path="/" element={<SupportPage />} />
            <Route path="/support/form" element={<SupportPage />} />
            <Route path="/support/ticket/:ticketId" element={<TicketStatusPage />} />
            <Route path="/ticket/:ticketId" element={<TicketStatusPage />} />
            <Route path="/ops/revenue" element={<RevenueCommandCenter />} />
            <Route path="/ops/approvals" element={<SmartApprovalInbox />} />
            <Route path="/ops/decision-hub" element={<SmartInbox />} />
            <Route path="/inbox" element={<Inbox />} />
            <Route path="/history" element={<History />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/knowledge" element={<KnowledgeBase />} />
          </Routes>
        </div>
      </Router>
    </ErrorBoundary>
  );
}

export default App;
