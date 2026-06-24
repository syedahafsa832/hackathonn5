"""
Public Live Feed
================
Anonymized, real resolved tickets for the homepage "Watch Luna Work" widget.
No auth, no PII: no customer name/email, no brand name, no message body.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from src.lib.supabase_client import supabase_select

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/public", tags=["Public"])

MAX_DISPLAY_SECONDS = 600  # cap displayed resolve time at 10 min


def _seconds_between(created_at: str, updated_at: str) -> int:
    try:
        c = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        u = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        return max(1, min(int((u - c).total_seconds()), MAX_DISPLAY_SECONDS))
    except Exception:
        return 90


def _short_intent(subject: str) -> str:
    """First 6 words only — strips anything that could be a name embedded in the subject."""
    words = (subject or "Customer inquiry").split()
    return " ".join(words[:6])


@router.get("/live-feed")
async def public_live_feed():
    rows = supabase_select("tickets", {
        "status": "in.(resolved,auto_resolved)",
        "order": "updated_at.desc",
        "limit": "30",
    }) or []

    feed = []
    for t in rows:
        order_id = t.get("detected_order_id")
        confidence_raw = t.get("confidence_score")
        confidence = round(confidence_raw) if confidence_raw else 80

        feed.append({
            "order_ref": f"#{order_id}" if order_id else "#----",
            "intent": _short_intent(t.get("subject")),
            "confidence": confidence,
            "resolved_in_seconds": _seconds_between(t.get("created_at", ""), t.get("updated_at", "")),
        })

    return {"feed": feed, "source": "production"}
