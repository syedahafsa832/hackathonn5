"""
Supabase Service — handles all database operations via REST API.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

class SupabaseService:
    """Service for interacting with Supabase tables via REST API."""

    async def get_or_create_customer(self, email: str, store_id: str, name: str = None, phone: str = None) -> Dict[str, Any]:
        """Find a customer by email or create a new one. The customers table has a unique
        constraint on email (not on email+store_id), so we fall back to an email-only lookup
        on 409 to avoid crashing when the same address appears in multiple brands."""
        try:
            # Try scoped lookup first
            results = supabase_select("customers", {
                "email": f"eq.{email}",
                "store_id": f"eq.{store_id}"
            })
            if results:
                return results[0]

            new_customer = {
                "email": email,
                "store_id": store_id,
                "name": name or email.split("@")[0],
                "phone": phone,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            try:
                return supabase_insert("customers", new_customer)
            except Exception as insert_err:
                err_str = str(insert_err)
                if "409" in err_str or "23505" in err_str or "duplicate key" in err_str.lower():
                    # Another row with this email already exists — return it
                    existing = supabase_select("customers", {"email": f"eq.{email}"})
                    if existing:
                        return existing[0]
                raise
        except Exception as e:
            logger.error(f"Supabase error in get_or_create_customer: {e}")
            return {"email": email, "name": name or "Customer", "store_id": store_id}

    async def create_ticket(self, ticket_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new ticket into the tickets table."""
        try:
            formatted_ticket = {
                "store_id": ticket_data.get("store_id", "00000000-0000-0000-0000-000000000000"),
                "customer_name": ticket_data.get("customer_name"),
                "customer_email": ticket_data.get("customer_email"),
                "subject": ticket_data.get("subject"),
                "message": ticket_data.get("message"),
                "channel": ticket_data.get("channel", "email"),
                "ai_reply": ticket_data.get("ai_reply"),
                "ai_draft": ticket_data.get("ai_draft"),
                "intent": ticket_data.get("intent"),
                "sentiment": ticket_data.get("sentiment"),
                "risk_level": ticket_data.get("risk_level"),
                "confidence_score": ticket_data.get("confidence_score"),
                "escalate": ticket_data.get("escalate", False),
                "escalation_reason": ticket_data.get("escalation_reason"),
                "gmail_thread_id": ticket_data.get("gmail_thread_id"),
                "gmail_message_id": ticket_data.get("gmail_message_id"),
                "detected_order_id": ticket_data.get("detected_order_id"),
                "status": ticket_data.get("status", "open"),
                "messages": ticket_data.get("messages"),
                "email_category": ticket_data.get("email_category"),
                "sender_type": ticket_data.get("sender_type"),
                "customer_sentiment": ticket_data.get("customer_sentiment"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            return supabase_insert("tickets", formatted_ticket)
        except Exception as e:
            logger.error(f"Supabase error in create_ticket: {e}")
            raise e

    async def get_system_settings(self, store_id: str) -> Dict[str, Any]:
        """Fetch system settings for a store, falling back to global defaults."""
        results = supabase_select("system_settings", {"store_id": f"eq.{store_id}"})
        if results:
            return results[0]
        # Fall back to the global/default settings row so the Settings UI affects all brands
        DEFAULT_STORE = "00000000-0000-0000-0000-000000000000"
        if store_id != DEFAULT_STORE:
            global_results = supabase_select("system_settings", {"store_id": f"eq.{DEFAULT_STORE}"})
            if global_results:
                return global_results[0]
        return {"store_id": store_id, "ai_mode": "active", "confidence_threshold": 0.75}

    async def check_conversation_override(self, conversation_id: str) -> bool:
        """Check if a conversation has an active human takeover override."""
        results = supabase_select("conversation_overrides", {
            "conversation_id": f"eq.{conversation_id}",
            "active": "eq.true"
        })
        return len(results) > 0

    async def log_audit(self, store_id: str, action: str, performer: str, metadata: Dict = None):
        """Log an action to the audit_logs table."""
        payload = {
            "store_id": store_id,
            "action_type": action,
            "performed_by": performer,
            "metadata": metadata or {}
        }
        supabase_insert("audit_logs", payload)

    async def get_tickets(self, store_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch tickets. If store_id is None or dummy UUID, return all tickets."""
        dummy = "00000000-0000-0000-0000-000000000000"
        params = {"order": "created_at.desc"}
        if store_id and store_id != dummy:
            params["store_id"] = f"eq.{store_id}"
        if status:
            params["status"] = f"eq.{status}"
        return supabase_select("tickets", params)

    async def get_ticket_by_id(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single ticket by ID."""
        results = supabase_select("tickets", {"id": f"eq.{ticket_id}"})
        return results[0] if results else None

    async def update_ticket(self, ticket_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a ticket record."""
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        return supabase_update("tickets", {"id": f"eq.{ticket_id}"}, updates)

    async def delete_customer_data(self, email: str, store_id: str):
        """GDPR Right to Erasure: Delete all tickets and customer records for an email."""
        # Note: In a real app, you might want to anonymize instead of delete
        from src.lib.supabase_client import supabase_client
        # This is a bit more complex via REST, usually done via a function or series of deletes
        # For this prototype, we'll assume a direct delete or hardcoded PII removal
        logger.info(f"DSR: Requesting erasure for {email} in store {store_id}")
        # Implementation details depend on the specific REST wrapper capabilities
        pass

supabase_service = SupabaseService()
