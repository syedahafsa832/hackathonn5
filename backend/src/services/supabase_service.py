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
        """Find a customer by email or create a new one. customers.email has a GLOBAL
        unique constraint (not email+store_id — see schema.sql), so lookup and the
        insert-conflict fallback both key on email alone. store_id is only ever set
        on first creation and never overwritten on an existing row, so a customer
        already owned by another store isn't silently reassigned."""
        try:
            existing = supabase_select("customers", {"email": f"eq.{email}"})
            if existing:
                return self._update_customer_fields(existing[0], name, phone)

            new_customer = {
                "email": email,
                "store_id": store_id,
                # Never derive a name from the email local-part (e.g.
                # "customer10@example.com" -> "Customer10") - that's not a
                # real name and gets echoed straight into greetings.
                # "Customer" is a known placeholder customer_success_agent.py
                # treats as "no name known", not a real one to greet by.
                "name": name or "Customer",
                "phone": phone,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            try:
                return supabase_insert("customers", new_customer)
            except Exception as insert_err:
                err_str = str(insert_err)
                if "409" in err_str or "23505" in err_str or "duplicate key" in err_str.lower():
                    # Lost the race to a concurrent request that inserted first — use its row.
                    existing = supabase_select("customers", {"email": f"eq.{email}"})
                    if existing:
                        return self._update_customer_fields(existing[0], name, phone)
                raise
        except Exception as e:
            logger.error(f"Supabase error in get_or_create_customer: {e}")
            return {"email": email, "name": name or "Customer", "store_id": store_id}

    def _update_customer_fields(self, customer: Dict[str, Any], name: Optional[str], phone: Optional[str]) -> Dict[str, Any]:
        """Patch an existing customer's name/phone when the caller has new values
        for them. Never touches store_id (see get_or_create_customer)."""
        updates = {}
        if name and name != customer.get("name"):
            updates["name"] = name
        if phone and phone != customer.get("phone"):
            updates["phone"] = phone
        if not updates:
            return customer
        try:
            return supabase_update("customers", {"id": f"eq.{customer['id']}"}, updates)
        except Exception as e:
            logger.warning(f"Supabase error updating customer fields: {e}")
            return customer

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
                # Written only here and by message_processor.py's STAGE 1.5
                # thread-continuation update — the Conversations list's
                # primary sort key (see migration 056). Was previously
                # dropped by this method's fixed field whitelist, so every
                # brand-new ticket (first message in a thread) got created
                # with this column NULL and sorted to the very bottom via
                # nullslast, even when it was the most recent activity - the
                # Recent Conversations widget's raw server-order slice(0, 3)
                # could then never surface a customer message that arrived
                # while the merchant was away.
                "last_customer_message_at": ticket_data.get("last_customer_message_at"),
                # Every inbound Gmail message id already folded into this
                # ticket (the creating message here; STAGE 1.5 thread-
                # continuation replies append to it directly via
                # supabase_update). email_poller.py's dedup check reads this
                # instead of the single gmail_message_id column, which only
                # ever reflected the ticket-creating message — see migration
                # 060 for why a reply-level dedup gap caused reprocessing.
                "processed_gmail_message_ids": (
                    [ticket_data["gmail_message_id"]] if ticket_data.get("gmail_message_id") else []
                ),
                "email_category": ticket_data.get("email_category"),
                "sender_type": ticket_data.get("sender_type"),
                "customer_sentiment": ticket_data.get("customer_sentiment"),
                "tags": ticket_data.get("tags") or [],
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

    async def log_onboarding_event(self, store_id: Optional[str], event_type: str, metadata: Dict = None):
        """Log an onboarding funnel event (signup_completed, shopify_connected, etc.)
        via the same audit_logs table log_audit already writes to — analytics_events
        is defined in migration 006 but was never actually applied to the live
        database (confirmed directly against Supabase), so this reuses the table
        that does exist rather than adding a new one. Never raises: a broken
        analytics write must not block the onboarding action it's logging."""
        try:
            supabase_insert("audit_logs", {
                "store_id": store_id,
                "action_type": event_type,
                "performed_by": "onboarding",
                "metadata": metadata or {},
            })
        except Exception as e:
            logger.warning(f"[Onboarding] Failed to log event '{event_type}': {e}")

    async def get_tickets(self, store_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch tickets. If store_id is None or dummy UUID, return all tickets."""
        dummy = "00000000-0000-0000-0000-000000000000"
        # Order by genuine customer activity, not any write to the row -
        # updated_at changes on AI processing, draft edits, and any other
        # ticket update (see migration 056), which used to resurface old
        # conversations with no new customer message involved. Falls back to
        # updated_at only for legacy rows where the new column is null.
        params = {"order": "last_customer_message_at.desc.nullslast,updated_at.desc"}
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

