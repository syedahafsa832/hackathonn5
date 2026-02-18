#!/usr/bin/env python3
"""
MCP Server for Customer Success AI Agent

This server implements the 5+ tools required for the customer success agent:
- search_knowledge_base
- create_ticket
- get_customer_history
- escalate_to_human
- send_response

The server can be run standalone and provides a foundation for the MCP integration.
"""

import asyncio
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
import uuid

try:
    from mcp.server import Server
    from mcp.types import Tool
except ImportError:
    # Mock the MCP server for local testing if not available
    print("MCP server not available, using mock implementation")

    class Server:
        def __init__(self, name: str):
            self.name = name
            self.tools = {}

        def tool(self, func):
            self.tools[func.__name__] = func
            return func

    class Tool:
        pass

# Initialize server
server = Server("customer-success-agent")

# In-memory storage for demo purposes
tickets_db = {}
customers_db = {}
conversations_db = {}

class KnowledgeBaseService:
    """Service to handle knowledge base operations"""

    def __init__(self):
        self.articles = [
            {
                "id": 1,
                "title": "Password Reset Guide",
                "content": "To reset your password, go to the login page and click 'Forgot Password'. Enter your email address and follow the instructions sent to your inbox. The reset link expires after 24 hours.",
                "category": "account",
                "tags": ["password", "login", "reset", "account", "security"]
            },
            {
                "id": 2,
                "title": "API Authentication Methods",
                "content": "To authenticate with our API, you need an API key. Find your API key in the dashboard under Settings > API Keys. Include it in the Authorization header as 'Bearer YOUR_API_KEY'. For production use, implement proper rate limiting.",
                "category": "technical",
                "tags": ["api", "authentication", "key", "developer", "security"]
            },
            {
                "id": 3,
                "title": "Subscription Management",
                "content": "Manage your subscription in the dashboard under Billing > Subscription. You can upgrade, downgrade, or cancel your plan at any time. Changes take effect at the next billing cycle. Downgrades are prorated.",
                "category": "billing",
                "tags": ["subscription", "billing", "upgrade", "cancel", "payment"]
            },
            {
                "id": 4,
                "title": "Integration Setup Guide",
                "content": "To set up integrations, visit the Integrations page in your dashboard. Select the service you want to connect and follow the OAuth flow. For custom integrations, refer to our API documentation. Webhooks are delivered to your configured endpoint.",
                "category": "technical",
                "tags": ["integration", "api", "oauth", "connect", "webhook"]
            },
            {
                "id": 5,
                "title": "User Management Best Practices",
                "content": "Add team members in Settings > Team. Invite users by email and assign roles (Admin, Member, Viewer). Admins can manage all settings while Members can only access their assigned projects. Roles can be changed at any time.",
                "category": "account",
                "tags": ["team", "users", "roles", "permissions", "collaboration"]
            }
        ]

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Search knowledge base for relevant articles"""
        query_lower = query.lower()
        results = []

        for article in self.articles:
            # Simple scoring algorithm
            score = 0

            # Check title match
            if query_lower in article['title'].lower():
                score += 10

            # Check content match
            if query_lower in article['content'].lower():
                score += 5

            # Check tags match
            for tag in article['tags']:
                if tag in query_lower:
                    score += 2

            if score > 0:
                results.append({
                    **article,
                    'relevance_score': score
                })

        # Sort by relevance and return top_k
        results.sort(key=lambda x: x['relevance_score'], reverse=True)
        return results[:top_k]

# Initialize knowledge base service
kb_service = KnowledgeBaseService()


@server.tool
def search_knowledge_base(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Search the knowledge base for articles relevant to the customer's query.

    Args:
        query: The customer's question or topic to search for
        top_k: Number of results to return (default 3)

    Returns:
        List of matching articles with title, content, and relevance score
    """
    results = kb_service.search(query, top_k)

    # Format results for MCP response
    formatted_results = []
    for result in results:
        formatted_results.append({
            "id": result["id"],
            "title": result["title"],
            "summary": result["content"][:200] + "..." if len(result["content"]) > 200 else result["content"],
            "category": result["category"],
            "relevance_score": result.get("relevance_score", 0)
        })

    return formatted_results


@server.tool
def create_ticket(customer_id: str, issue: str, priority: str = "medium", channel: str = "web_form") -> Dict[str, Any]:
    """
    Create a new support ticket for the customer.

    Args:
        customer_id: Unique identifier for the customer
        issue: Description of the customer's issue
        priority: Priority level (low, medium, high, critical)
        channel: Source channel (email, whatsapp, web_form)

    Returns:
        Dictionary containing ticket ID and creation status
    """
    ticket_id = str(uuid.uuid4())

    # Validate priority
    valid_priorities = ["low", "medium", "high", "critical"]
    if priority not in valid_priorities:
        priority = "medium"

    # Validate channel
    valid_channels = ["email", "whatsapp", "web_form"]
    if channel not in valid_channels:
        channel = "web_form"

    # Create ticket object
    ticket = {
        "id": ticket_id,
        "customer_id": customer_id,
        "issue": issue,
        "priority": priority,
        "channel": channel,
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "assigned_agent": None
    }

    # Store in in-memory database
    tickets_db[ticket_id] = ticket

    return {
        "ticket_id": ticket_id,
        "status": "created",
        "message": f"Ticket {ticket_id} created successfully",
        "estimated_resolution": "within 24 hours" if priority in ["low", "medium"] else "within 4 hours"
    }


@server.tool
def get_customer_history(customer_id: str) -> Dict[str, Any]:
    """
    Retrieve the customer's interaction history including tickets and conversations.

    Args:
        customer_id: Unique identifier for the customer

    Returns:
        Dictionary containing customer profile and interaction history
    """
    # Check if customer exists
    if customer_id not in customers_db:
        return {
            "error": f"Customer with ID {customer_id} not found",
            "customer_id": customer_id
        }

    customer = customers_db[customer_id]

    # Find related tickets
    customer_tickets = [
        ticket for ticket in tickets_db.values()
        if ticket["customer_id"] == customer_id
    ]

    # Find related conversations
    customer_conversations = [
        conv for conv in conversations_db.values()
        if conv["customer_id"] == customer_id
    ]

    return {
        "customer_id": customer_id,
        "profile": {
            "name": customer.get("name", "Unknown"),
            "email": customer.get("email", ""),
            "company": customer.get("company", ""),
            "created_at": customer.get("created_at", ""),
            "tier": customer.get("tier", "standard")
        },
        "interaction_summary": {
            "total_tickets": len(customer_tickets),
            "total_conversations": len(customer_conversations),
            "most_common_category": "technical" if customer_tickets else "none",
            "last_contact_date": max(
                [t.get("created_at", "") for t in customer_tickets] +
                [c.get("created_at", "") for c in customer_conversations],
                default="never"
            )
        },
        "recent_tickets": [
            {
                "id": t["id"],
                "issue": t["issue"][:50] + "..." if len(t["issue"]) > 50 else t["issue"],
                "status": t["status"],
                "priority": t["priority"],
                "created_at": t["created_at"]
            }
            for t in sorted(customer_tickets, key=lambda x: x["created_at"], reverse=True)[:5]
        ]
    }


@server.tool
def escalate_to_human(ticket_id: str, reason: str) -> Dict[str, Any]:
    """
    Escalate a ticket to a human agent with the specified reason.

    Args:
        ticket_id: The ID of the ticket to escalate
        reason: The reason for escalation

    Returns:
        Dictionary confirming escalation with details
    """
    if ticket_id not in tickets_db:
        return {
            "error": f"Ticket with ID {ticket_id} not found",
            "ticket_id": ticket_id
        }

    ticket = tickets_db[ticket_id]

    # Update ticket status and assign to human
    ticket.update({
        "status": "escalated",
        "assigned_agent": "human_agent_pending",
        "escalation_reason": reason,
        "updated_at": datetime.utcnow().isoformat()
    })

    # In a real implementation, this would notify human agents
    # via email, Slack, or other communication channels

    return {
        "status": "escalated",
        "ticket_id": ticket_id,
        "reason": reason,
        "message": f"Ticket {ticket_id} escalated to human agent",
        "next_steps": "Human agent will contact you within 30 minutes"
    }


@server.tool
def send_response(ticket_id: str, message: str, channel: str = "email") -> Dict[str, Any]:
    """
    Send a response message to the customer through the specified channel.

    Args:
        ticket_id: The ID of the ticket this response relates to
        message: The message content to send
        channel: The communication channel (email, whatsapp, web_form)

    Returns:
        Dictionary confirming delivery status
    """
    # Validate channel
    valid_channels = ["email", "whatsapp", "web_form"]
    if channel not in valid_channels:
        return {
            "error": f"Invalid channel: {channel}. Must be one of {valid_channels}",
            "ticket_id": ticket_id
        }

    # In a real implementation, this would send the actual message
    # through the appropriate channel (email, WhatsApp API, etc.)

    # For demo purposes, just log the response
    response_record = {
        "ticket_id": ticket_id,
        "channel": channel,
        "message": message,
        "sent_at": datetime.utcnow().isoformat(),
        "status": "sent"
    }

    # Store response for tracking
    if "responses" not in tickets_db[ticket_id]:
        tickets_db[ticket_id]["responses"] = []

    tickets_db[ticket_id]["responses"].append(response_record)

    return {
        "status": "sent",
        "ticket_id": ticket_id,
        "channel": channel,
        "message_length": len(message),
        "sent_at": response_record["sent_at"]
    }


def initialize_demo_data():
    """Initialize some demo data for testing"""
    # Create a few demo customers
    customers_db["demo_customer_1"] = {
        "id": "demo_customer_1",
        "name": "John Doe",
        "email": "john@example.com",
        "company": "Acme Corp",
        "created_at": "2024-01-01T00:00:00Z",
        "tier": "premium"
    }

    customers_db["demo_customer_2"] = {
        "id": "demo_customer_2",
        "name": "Jane Smith",
        "email": "jane@example.com",
        "company": "Tech Solutions Inc",
        "created_at": "2024-01-15T00:00:00Z",
        "tier": "standard"
    }

    # Create a few demo conversations
    conversations_db["conv_1"] = {
        "id": "conv_1",
        "customer_id": "demo_customer_1",
        "channel": "email",
        "status": "active",
        "created_at": "2024-01-10T10:00:00Z",
        "topic": "Password reset issue"
    }

    conversations_db["conv_2"] = {
        "id": "conv_2",
        "customer_id": "demo_customer_2",
        "channel": "web_form",
        "status": "closed",
        "created_at": "2024-01-20T14:30:00Z",
        "topic": "API integration question"
    }


async def main():
    """Main function to run the MCP server"""
    print("Starting Customer Success AI Agent MCP Server...")

    # Initialize demo data
    initialize_demo_data()

    print("Demo data initialized:")
    print(f"- {len(customers_db)} customers")
    print(f"- {len(tickets_db)} tickets")
    print(f"- {len(conversations_db)} conversations")
    print(f"- Knowledge base with {len(kb_service.articles)} articles")

    print("\nAvailable tools:")
    for tool_name in ['search_knowledge_base', 'create_ticket', 'get_customer_history', 'escalate_to_human', 'send_response']:
        print(f"- {tool_name}")

    print("\nServer ready!")

    # In a real MCP implementation, we would start the server here
    # For this demo, we'll just keep it running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down server...")


if __name__ == "__main__":
    # Run the server
    asyncio.run(main())
