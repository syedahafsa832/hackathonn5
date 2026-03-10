import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import SupportPage from "./pages/SupportPage";
import TicketStatusPage from "./pages/TicketStatusPage";
import RevenueCommandCenter from "./pages/RevenueCommandCenter";
import SmartApprovalInbox from "./pages/SmartApprovalInbox";
import SmartInbox from "./pages/SmartInbox";

function App() {
  return (
    <Router>
      <div className="App">
        <Routes>
          <Route path="/" element={<SupportPage />} />
          <Route path="/support/form" element={<SupportPage />} />
          <Route path="/support/ticket/:ticketId" element={<TicketStatusPage />} />
          <Route path="/ticket/:ticketId" element={<TicketStatusPage />} />
          <Route path="/ops/revenue" element={<RevenueCommandCenter />} />
          <Route path="/ops/approvals" element={<SmartApprovalInbox />} />
          <Route path="/ops/decision-hub" element={<SmartInbox />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
