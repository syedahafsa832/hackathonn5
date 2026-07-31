import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Tickets from './pages/Tickets';
import TicketDetail from './pages/TicketDetail';
import Actions from './pages/Actions';
import Brands from './pages/Brands';
import Settings from './pages/Settings';
import Signup from './pages/Signup';
import Onboarding from './pages/Onboarding';
import QuarantineQueue from './pages/QuarantineQueue';
import Admin from './pages/Admin';
import Profile from './pages/Profile';
import Upgrade from './pages/Upgrade';

function ProtectedRoute({ children }) {
  const token = localStorage.getItem('resolv_token');
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace />;
  return (
    <Layout>
      {/* key resets the boundary when navigating to a different page/ticket
          instead of getting stuck showing a stale error */}
      <ErrorBoundary key={location.pathname}>{children}</ErrorBoundary>
    </Layout>
  );
}

function OnboardingRoute({ children }) {
  const token = localStorage.getItem('resolv_token');
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/tickets" element={<ProtectedRoute><Tickets /></ProtectedRoute>} />
        <Route path="/tickets/:ticket_id" element={<ProtectedRoute><TicketDetail /></ProtectedRoute>} />
        <Route path="/actions" element={<ProtectedRoute><Actions /></ProtectedRoute>} />
        <Route path="/quarantine" element={<ProtectedRoute><QuarantineQueue /></ProtectedRoute>} />
        <Route path="/brands" element={<ProtectedRoute><Brands /></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute><Admin /></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
        <Route path="/upgrade" element={<ProtectedRoute><Upgrade /></ProtectedRoute>} />
        <Route path="/onboarding" element={<OnboardingRoute><Onboarding /></OnboardingRoute>} />
      </Routes>
    </BrowserRouter>
  );
}
