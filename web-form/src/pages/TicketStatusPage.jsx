import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import TicketStatus from '../components/TicketStatus';

const TicketStatusPage = () => {
  const { ticketId } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Basic validation that ticketId exists
    if (!ticketId) {
      setError('No ticket ID provided');
      setLoading(false);
      return;
    }

    // Additional validation could be added here if needed
    setLoading(false);
  }, [ticketId]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 py-12">
        <div className="max-w-4xl mx-auto px-4">
          <div className="bg-white rounded-lg shadow-md p-6">
            <div className="flex items-center justify-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mr-3"></div>
              <p>Loading ticket status...</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 py-12">
        <div className="max-w-4xl mx-auto px-4">
          <div className="bg-white rounded-lg shadow-md p-6">
            <div className="text-red-600">
              <h2 className="text-xl font-bold mb-2">Error</h2>
              <p>{error}</p>
              <button
                onClick={() => window.history.back()}
                className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
              >
                Go Back
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-4xl mx-auto px-4">
        <div className="bg-white rounded-lg shadow-md p-6">
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-gray-800">Ticket Status</h1>
            <p className="text-gray-600 mt-1">Track your support ticket progress</p>
          </div>

          <div className="border-t pt-6">
            <TicketStatus ticketId={ticketId} />
          </div>
        </div>
      </div>
    </div>
  );
};

export default TicketStatusPage;