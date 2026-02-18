import React, { useState, useEffect } from 'react';
import apiClient from '../services/apiClient';

const TicketStatus = ({ ticketId: initialTicketId }) => {
  const [ticketId, setTicketId] = useState(initialTicketId || '');
  const [ticketData, setTicketData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Auto-fetch if ticketId is provided
  useEffect(() => {
    if (initialTicketId) {
      setTicketId(initialTicketId);
      fetchTicketStatus(initialTicketId);
    }
  }, [initialTicketId]);

  const fetchTicketStatus = async (id) => {
    if (!id || !id.trim()) {
      setError('Please enter a ticket ID');
      return;
    }

    setLoading(true);
    setError(null);
    setTicketData(null);

    try {
      const data = await apiClient.getTicketStatus(id.trim());
      setTicketData(data);
    } catch (err) {
      console.error('Error fetching ticket:', err);
      setError(err.message || 'An error occurred while fetching ticket status.');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    fetchTicketStatus(ticketId);
  };

  const getStatusColor = (status) => {
    const colors = {
      'open': 'bg-blue-100 text-blue-800',
      'in_progress': 'bg-yellow-100 text-yellow-800',
      'resolved': 'bg-green-100 text-green-800',
      'closed': 'bg-gray-100 text-gray-800',
    };
    return colors[status] || 'bg-gray-100 text-gray-800';
  };

  const getPriorityColor = (priority) => {
    const colors = {
      'low': 'text-green-600',
      'medium': 'text-yellow-600',
      'high': 'text-orange-600',
      'critical': 'text-red-600',
    };
    return colors[priority] || 'text-gray-600';
  };

  return (
    <div className="space-y-6">
      {/* Search Form */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="ticketId" className="block text-sm font-medium text-gray-700 mb-2">
            Enter Ticket ID
          </label>
          <div className="flex space-x-2">
            <input
              type="text"
              id="ticketId"
              value={ticketId}
              onChange={(e) => setTicketId(e.target.value)}
              placeholder="e.g., 1747f78a-6d12-42e0-b2cf-1bedb8f52197"
              className="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="submit"
              disabled={loading}
              className={`px-6 py-2 rounded-md text-white font-medium focus:outline-none focus:ring-2 focus:ring-offset-2 ${
                loading
                  ? 'bg-gray-400 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-500'
              }`}
            >
              {loading ? 'Checking...' : 'Check Status'}
            </button>
          </div>
        </div>
      </form>

      {/* Error Message */}
      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-md">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {/* Ticket Details */}
      {ticketData && (
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
          <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900">Ticket Details</h3>
          </div>
          
          <div className="px-6 py-4 space-y-4">
            {/* Status and Priority */}
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm text-gray-500">Status:</span>
                <span className={`ml-2 inline-flex px-3 py-1 text-xs font-semibold rounded-full ${getStatusColor(ticketData.status)}`}>
                  {ticketData.status.replace('_', ' ').toUpperCase()}
                </span>
              </div>
              <div>
                <span className="text-sm text-gray-500">Priority:</span>
                <span className={`ml-2 text-sm font-semibold ${getPriorityColor(ticketData.priority)}`}>
                  {ticketData.priority.toUpperCase()}
                </span>
              </div>
            </div>

            {/* Ticket ID */}
            <div>
              <p className="text-sm text-gray-500">Ticket ID</p>
              <p className="text-sm font-mono text-gray-900 break-all">{ticketData.id}</p>
            </div>

            {/* Subject */}
            <div>
              <p className="text-sm text-gray-500">Subject</p>
              <p className="text-base font-medium text-gray-900">{ticketData.subject}</p>
            </div>

            {/* Category */}
            <div>
              <p className="text-sm text-gray-500">Category</p>
              <p className="text-sm text-gray-900 capitalize">{ticketData.category}</p>
            </div>

            {/* Description */}
            <div>
              <p className="text-sm text-gray-500">Description</p>
              <p className="text-sm text-gray-900 whitespace-pre-wrap">{ticketData.description}</p>
            </div>

            {/* Timestamps */}
            <div className="grid grid-cols-2 gap-4 pt-4 border-t border-gray-200">
              <div>
                <p className="text-sm text-gray-500">Created</p>
                <p className="text-sm text-gray-900">
                  {new Date(ticketData.created_at).toLocaleString()}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-500">Last Updated</p>
                <p className="text-sm text-gray-900">
                  {new Date(ticketData.updated_at).toLocaleString()}
                </p>
              </div>
            </div>

            {/* Resolution Notes */}
            {ticketData.resolution_notes && (
              <div className="pt-4 border-t border-gray-200">
                <p className="text-sm text-gray-500">Resolution Notes</p>
                <p className="text-sm text-gray-900 whitespace-pre-wrap">{ticketData.resolution_notes}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* No ticket selected message */}
      {!ticketData && !error && !loading && (
        <div className="text-center py-8 text-gray-500">
          <p>No ticket selected or invalid ticket ID.</p>
          <p className="text-sm mt-2">Enter a ticket ID above to check its status.</p>
        </div>
      )}
    </div>
  );
};

export default TicketStatus;