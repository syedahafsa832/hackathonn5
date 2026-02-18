/**
 * API Client Service for Customer Success Web Form
 */

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

class APIClient {
  constructor(baseURL = API_BASE_URL) {
    this.baseURL = baseURL;
  }

  /**
   * Create a new support ticket
   */
  async createTicket(ticketData) {
    try {
      const response = await fetch(`${this.baseURL}/support/submit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(ticketData),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || data.message || 'Failed to create ticket');
      }

      return data;
    } catch (error) {
      console.error('Error creating ticket:', error);
      throw error;
    }
  }

  /**
   * Get ticket status by ID
   */
  async getTicketStatus(ticketId) {
    try {
      const response = await fetch(`${this.baseURL}/support/ticket/${ticketId}`);

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('Ticket not found');
        }
        throw new Error('Failed to fetch ticket status');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching ticket status:', error);
      throw error;
    }
  }

  /**
   * Search knowledge base
   */
  async searchKnowledgeBase(query, topK = 3) {
    try {
      const response = await fetch(`${this.baseURL}/knowledge-base/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query, top_k: topK }),
      });

      if (!response.ok) {
        throw new Error('Failed to search knowledge base');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error searching knowledge base:', error);
      throw error;
    }
  }

  /**
   * Look up customer by identifier
   */
  async lookupCustomer(identifier, type = 'email') {
    try {
      const response = await fetch(
        `${this.baseURL}/customers/lookup?identifier=${encodeURIComponent(identifier)}&type=${encodeURIComponent(type)}`
      );

      if (!response.ok) {
        if (response.status === 404) {
          return null; // Customer not found
        }
        throw new Error('Failed to lookup customer');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error looking up customer:', error);
      throw error;
    }
  }

  /**
   * Get conversation history
   */
  async getConversationHistory(conversationId) {
    try {
      const response = await fetch(`${this.baseURL}/conversations/${conversationId}`);

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('Conversation not found');
        }
        throw new Error('Failed to fetch conversation history');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching conversation history:', error);
      throw error;
    }
  }

  /**
   * Get channel metrics
   */
  async getChannelMetrics(params = {}) {
    try {
      const queryParams = new URLSearchParams(params);
      const response = await fetch(`${this.baseURL}/metrics/channels?${queryParams}`);

      if (!response.ok) {
        throw new Error('Failed to fetch channel metrics');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching channel metrics:', error);
      throw error;
    }
  }

  /**
   * Health check
   */
  async healthCheck() {
    try {
      const response = await fetch(`${this.baseURL}/health`);

      if (!response.ok) {
        throw new Error('Health check failed');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Health check error:', error);
      throw error;
    }
  }
}

// Export singleton instance
const apiClient = new APIClient();
export default apiClient;

// Export for use in other modules
export { APIClient };