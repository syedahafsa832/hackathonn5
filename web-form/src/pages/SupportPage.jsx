import React, { useState } from 'react';
import SupportForm from '../components/SupportForm';
import TicketStatus from '../components/TicketStatus';

const SupportPage = () => {
  const [currentView, setCurrentView] = useState('form'); // 'form' or 'status'
  const [ticketId, setTicketId] = useState('');

  const handleFormSubmit = (ticketId) => {
    setTicketId(ticketId);
    setCurrentView('status');
  };

  return (
    <div className="min-h-screen bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-10">
          <h1 className="text-3xl font-extrabold text-gray-900 sm:text-4xl">
            Customer Support
          </h1>
          <p className="mt-3 text-lg text-gray-500">
            Contact our team for assistance with your questions or issues
          </p>
        </div>

        <div className="bg-white shadow-xl rounded-lg overflow-hidden">
          {/* Navigation tabs */}
          <div className="border-b border-gray-200">
            <nav className="-mb-px flex space-x-8 px-6">
              <button
                onClick={() => setCurrentView('form')}
                className={`${
                  currentView === 'form'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
              >
                Submit Request
              </button>
              <button
                onClick={() => setCurrentView('status')}
                className={`${
                  currentView === 'status'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm`}
              >
                Check Status
              </button>
            </nav>
          </div>

          <div className="p-6">
            {currentView === 'form' ? (
              <SupportForm onSubmit={handleFormSubmit} />
            ) : (
              <div>
                <h2 className="text-xl font-semibold mb-4">Check Ticket Status</h2>
                <TicketStatus ticketId={ticketId} />

                <div className="mt-6">
                  <button
                    onClick={() => setCurrentView('form')}
                    className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                  >
                    Submit Another Request
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="mt-8 text-center text-sm text-gray-500">
          <p>Need immediate assistance? Our team typically responds within 2 minutes during business hours.</p>
        </div>
      </div>
    </div>
  );
};

export default SupportPage;