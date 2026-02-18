import React from "react";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import SupportPage from "./pages/SupportPage";
import TicketStatusPage from "./pages/TicketStatusPage";

function App() {
  return (
    <Router>
      <div className="App">
        <Routes>
          <Route path="/" element={<SupportPage />} />
          <Route path="/support/form" element={<SupportPage />} />
          <Route path="/support/ticket/:ticketId" element={<TicketStatusPage />} />
          <Route path="/ticket/:ticketId" element={<TicketStatusPage />} /> {/* Fallback route */}
        </Routes>
      </div>
    </Router>
  );
}

export default App;
