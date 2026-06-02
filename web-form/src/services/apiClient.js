/**
 * API Client Service for Customer Success Web Form
 * Supports both legacy endpoints and v2 brand-scoped endpoints
 */

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

class APIClient {
  constructor(baseURL = API_BASE_URL) {
    this.baseURL = baseURL;
    this.authToken = null;
    this.brandId = null;
  }

  /**
   * Set authentication token for v2 API requests
   */
  setAuthToken(token) {
    this.authToken = token;
  }

  /**
   * Set current brand ID for brand-scoped requests
   */
  setBrandId(brandId) {
    this.brandId = brandId;
  }

  /**
   * Get headers for authenticated requests
   */
  _getAuthHeaders() {
    const headers = {
      'Content-Type': 'application/json',
    };
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`;
    }
    return headers;
  }

  /**
   * Helper for authenticated fetch requests
   */
  async _authFetch(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...this._getAuthHeaders(),
        ...options.headers,
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || error.message || `Request failed: ${response.status}`);
    }

    return response.json();
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
   * Get latest ticket by email
   */
  async getTicketByEmail(email) {
    try {
      const response = await fetch(`${this.baseURL}/support/ticket-by-email?email=${encodeURIComponent(email)}`);

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('No tickets found');
        }
        throw new Error('Failed to fetch ticket');
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching ticket by email:', error);
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

  // ==================== V2 API: Tickets ====================

  /**
   * List tickets for a brand (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} params - Query parameters (status, page, per_page)
   */
  async getTickets(brandId, params = {}) {
    const queryParams = new URLSearchParams(params);
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/tickets?${queryParams}`
    );
  }

  /**
   * Get a single ticket (v2)
   * @param {string} brandId - Brand ID
   * @param {string} ticketId - Ticket ID
   */
  async getTicket(brandId, ticketId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/tickets/${ticketId}`
    );
  }

  /**
   * Update ticket status (v2)
   * @param {string} brandId - Brand ID
   * @param {string} ticketId - Ticket ID
   * @param {Object} updates - Fields to update (status, assigned_to, etc.)
   */
  async updateTicket(brandId, ticketId, updates) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/tickets/${ticketId}`,
      {
        method: 'PATCH',
        body: JSON.stringify(updates),
      }
    );
  }

  // ==================== V2 API: Actions ====================

  /**
   * List actions for a brand (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} params - Query parameters (status, page, per_page)
   */
  async getActions(brandId, params = {}) {
    const queryParams = new URLSearchParams(params);
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions?${queryParams}`
    );
  }

  /**
   * Get pending actions (v2)
   * @param {string} brandId - Brand ID
   */
  async getPendingActions(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions/pending`
    );
  }

  /**
   * Get a single action (v2)
   * @param {string} brandId - Brand ID
   * @param {string} actionId - Action ID
   */
  async getAction(brandId, actionId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions/${actionId}`
    );
  }

  /**
   * Approve an action (v2)
   * @param {string} brandId - Brand ID
   * @param {string} actionId - Action ID
   */
  async approveAction(brandId, actionId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions/${actionId}/approve`,
      { method: 'POST' }
    );
  }

  /**
   * Reject an action (v2)
   * @param {string} brandId - Brand ID
   * @param {string} actionId - Action ID
   * @param {string} reason - Rejection reason
   */
  async rejectAction(brandId, actionId, reason) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions/${actionId}/reject`,
      {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }
    );
  }

  /**
   * Get action logs (v2)
   * @param {string} brandId - Brand ID
   * @param {string} actionId - Action ID
   */
  async getActionLogs(brandId, actionId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/actions/${actionId}/logs`
    );
  }

  // ==================== V2 API: Knowledge Base ====================

  /**
   * List knowledge base sources for a brand (v2)
   * @param {string} brandId - Brand ID
   */
  async getKnowledgeSources(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/sources`
    );
  }

  /**
   * Get a single knowledge source (v2)
   * @param {string} brandId - Brand ID
   * @param {string} sourceId - Source ID
   */
  async getKnowledgeSource(brandId, sourceId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/sources/${sourceId}`
    );
  }

  /**
   * Upload text content to knowledge base (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} data - { name, content, metadata }
   */
  async uploadKnowledge(brandId, data) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/upload`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  }

  /**
   * Delete a knowledge source (v2)
   * @param {string} brandId - Brand ID
   * @param {string} sourceId - Source ID
   */
  async deleteKnowledgeSource(brandId, sourceId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/sources/${sourceId}`,
      { method: 'DELETE' }
    );
  }

  /**
   * Search knowledge base (v2)
   * @param {string} brandId - Brand ID
   * @param {string} query - Search query
   * @param {number} topK - Number of results
   */
  async searchBrandKnowledge(brandId, query, topK = 5) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/search`,
      {
        method: 'POST',
        body: JSON.stringify({ query, top_k: topK }),
      }
    );
  }

  /**
   * Get knowledge base stats (v2)
   * @param {string} brandId - Brand ID
   */
  async getKnowledgeStats(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/knowledge/stats`
    );
  }

  // ==================== V2 API: Brands & Settings ====================

  /**
   * List brands for current user (v2)
   */
  async getBrands() {
    return this._authFetch(`${this.baseURL}/api/v2/brands`);
  }

  /**
   * Get a single brand (v2)
   * @param {string} brandId - Brand ID
   */
  async getBrand(brandId) {
    return this._authFetch(`${this.baseURL}/api/v2/brands/${brandId}`);
  }

  /**
   * Update brand settings (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} settings - Settings to update
   */
  async updateBrandSettings(brandId, settings) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}`,
      {
        method: 'PATCH',
        body: JSON.stringify(settings),
      }
    );
  }

  /**
   * Connect Shopify store (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} credentials - { shop_domain, access_token }
   */
  async connectShopify(brandId, credentials) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/shopify/connect`,
      {
        method: 'POST',
        body: JSON.stringify(credentials),
      }
    );
  }

  /**
   * Test Shopify connection (v2)
   * @param {string} brandId - Brand ID
   */
  async testShopifyConnection(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/shopify/test`
    );
  }

  /**
   * Disconnect Shopify store (v2)
   * @param {string} brandId - Brand ID
   */
  async disconnectShopify(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/shopify/disconnect`,
      { method: 'POST' }
    );
  }

  // ==================== V2 API: History & Audit ====================

  /**
   * Get brand history/audit logs (v2)
   * @param {string} brandId - Brand ID
   * @param {Object} params - Query parameters (event_type, page, per_page)
   */
  async getHistory(brandId, params = {}) {
    const queryParams = new URLSearchParams(params);
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/history?${queryParams}`
    );
  }

  /**
   * Get brand stats (v2)
   * @param {string} brandId - Brand ID
   */
  async getBrandStats(brandId) {
    return this._authFetch(
      `${this.baseURL}/api/v2/brands/${brandId}/stats`
    );
  }
}

// Export singleton instance
const apiClient = new APIClient();
export default apiClient;

// Export for use in other modules
export { APIClient };