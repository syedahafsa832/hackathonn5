import logging
from typing import Dict, Any, Optional, List
from src.lib.supabase_client import supabase
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class SupabaseService:
    """Service for interacting with Supabase tables."""

    async def get_or_create_customer(self, email: str, name: str = None, phone: str = None) -> Dict[str, Any]:
        """Find a customer by email or create a new one."""
        try:
            # Check if customer exists
            response = supabase.table("customers").select("*").eq("email", email).execute()
            
            if response.data:
                return response.data[0]
            
            # Create new customer
            new_customer = {
                "email": email,
                "name": name or email.split("@")[0],
                "phone": phone,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            create_response = supabase.table("customers").insert(new_customer).execute()
            return create_response.data[0]
        except Exception as e:
            logger.error(f"Supabase error in get_or_create_customer: {e}")
            return {"email": email, "name": name or "Customer"}

    async def create_ticket(self, ticket_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new ticket into the tickets table."""
        try:
            # Ensure required fields for Supabase insert
            formatted_ticket = {
                "customer_name": ticket_data.get("customer_name"),
                "customer_email": ticket_data.get("customer_email"),
                "subject": ticket_data.get("subject"),
                "message": ticket_data.get("message"),
                "ai_reply": ticket_data.get("ai_reply"),
                "intent": ticket_data.get("intent"),
                "sentiment": ticket_data.get("sentiment"),
                "risk_level": ticket_data.get("risk_level"),
                "confidence_score": ticket_data.get("confidence_score"),
                "escalate": ticket_data.get("escalate", False),
                "escalation_reason": ticket_data.get("escalation_reason"),
                "status": ticket_data.get("status", "open"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            response = supabase.table("tickets").insert(formatted_ticket).execute()
            return response.data[0]
        except Exception as e:
            logger.error(f"Supabase error in create_ticket: {e}")
            raise e

    async def get_tickets(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch tickets with optional status filtering."""
        query = supabase.table("tickets").select("*")
        if status:
            query = query.eq("status", status)
        
        response = query.order("created_at", desc=True).execute()
        return response.data

    async def get_ticket_by_id(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single ticket by ID."""
        response = supabase.table("tickets").select("*").eq("id", ticket_id).execute()
        return response.data[0] if response.data else None

    async def update_ticket(self, ticket_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a ticket record."""
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        response = supabase.table("tickets").update(updates).eq("id", ticket_id).execute()
        return response.data[0]

supabase_service = SupabaseService()
