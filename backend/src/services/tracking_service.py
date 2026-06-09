"""
Aftership Tracking Service
==========================
Fetches live tracking status for a shipment using the Aftership v4 API.
Called from the agent's order-status tool when a brand has aftership_api_key set.
"""
import logging
from typing import Optional
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

AFTERSHIP_BASE = "https://api.aftership.com/v4"
TIMEOUT = 5.0  # hard cap — never blocks Luna's reply


# ── Carrier slug mapping ──────────────────────────────────────────────────────

_CARRIER_MAP = {
    "tcs":               "tcs-express",
    "tcs express":       "tcs-express",
    "leopards":          "leopards-courier",
    "leopards courier":  "leopards-courier",
    "trax":              "trax",
    "blueex":            "blueex",
    "blue ex":           "blueex",
    "postex":            "postex",
    "m&p":               "mp-courier",
    "mnp":               "mp-courier",
    "m & p":             "mp-courier",
    "speedex":           "speedex-courier",
    "swyft":             "swyft",
    "call courier":      "call-courier",
    "callcourier":       "call-courier",
    "dhl":               "dhl",
    "dhl express":       "dhl",
    "fedex":             "fedex",
    "ups":               "ups",
    "usps":              "usps",
    "pakistan post":     "pakistan-post",
}


def shopify_carrier_to_aftership_slug(shopify_tracking_company: str) -> Optional[str]:
    """Map Shopify's free-text tracking_company to an Aftership carrier slug."""
    if not shopify_tracking_company:
        return None
    needle = shopify_tracking_company.strip().lower()
    for key, slug in _CARRIER_MAP.items():
        if key in needle:
            return slug
    return None


# ── Aftership API call ────────────────────────────────────────────────────────

async def get_tracking_status(
    tracking_number: str,
    carrier_slug: str,
    aftership_api_key: str,
) -> Optional[dict]:
    """
    Returns a plain tracking info dict, or None if unavailable / timed out.
    Never raises — all errors are caught and logged.
    """
    url = f"{AFTERSHIP_BASE}/trackings/{carrier_slug}/{tracking_number}"
    headers = {
        "aftership-api-key": aftership_api_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 404:
                logger.info(f"[Tracking] {carrier_slug}/{tracking_number} not found in Aftership (404)")
                return None
            if res.status_code != 200:
                logger.warning(f"[Tracking] Aftership returned {res.status_code} for {carrier_slug}/{tracking_number}")
                return None

            data = res.json()
            tracking = data.get("data", {}).get("tracking", {})
            checkpoints = tracking.get("checkpoints") or []
            latest = checkpoints[-1] if checkpoints else {}

            return {
                "status":            tracking.get("tag"),             # InTransit, Delivered, etc.
                "status_text":       tracking.get("subtag_message") or tracking.get("tag") or "Unknown",
                "latest_location":   latest.get("location") or latest.get("city") or latest.get("country_name"),
                "latest_message":    latest.get("message"),
                "latest_time":       latest.get("checkpoint_time"),
                "expected_delivery": tracking.get("expected_delivery"),
                "carrier_slug":      tracking.get("slug"),
            }
    except httpx.TimeoutException:
        logger.warning(f"[Tracking] Aftership timed out for {carrier_slug}/{tracking_number}")
        return None
    except Exception as e:
        logger.warning(f"[Tracking] Aftership error for {carrier_slug}/{tracking_number}: {e}")
        return None


# ── Context builder for agent prompt ─────────────────────────────────────────

def build_tracking_context(
    tracking_info: Optional[dict],
    tracking_number: Optional[str],
    tracking_url: Optional[str],
    tracking_company: Optional[str],
) -> str:
    """
    Returns the tracking block to inject into the order context string.
    Priority: live Aftership data → fallback URL → nothing yet.
    """
    if tracking_info:
        status_text = tracking_info.get("status_text") or tracking_info.get("status") or "In transit"
        location    = tracking_info.get("latest_location") or "unknown location"
        message     = tracking_info.get("latest_message") or ""
        last_time   = _fmt_time(tracking_info.get("latest_time"))
        expected    = tracking_info.get("expected_delivery")
        expected_str = _fmt_date(expected) if expected else "not confirmed yet"

        tag = (tracking_info.get("status") or "").lower()

        if tag == "delivered":
            instruction = (
                f"Tell the customer: 'Your order was delivered on {last_time}. "
                "If you didn't receive it, please let me know and I'll help sort it out.'"
            )
        elif tag == "outfordelivery":
            instruction = (
                "Tell the customer: 'Great news — your order is out for delivery today! "
                "The driver should arrive by end of day.'"
            )
        elif tag in ("attemptfail", "exception"):
            instruction = (
                f"Tell the customer: 'There was a delivery issue — {message or status_text}. "
                f"Expected delivery: {expected_str}. I'm sorry for the inconvenience.'"
            )
        else:
            location_part = f"currently at {location}" if location != "unknown location" else "on its way"
            instruction = (
                f"Tell the customer: 'Your order is {location_part}{f', last updated {last_time}' if last_time else ''}. "
                f"Expected delivery: {expected_str}.'"
            )

        return (
            "\nTRACKING STATUS (LIVE FROM AFTERSHIP — USE THIS, DO NOT SHARE RAW URL):\n"
            f"  Current status: {status_text}\n"
            f"  Latest update:  {message or 'No details'}\n"
            f"  Location:       {location}\n"
            f"  Last updated:   {last_time or 'unknown'}\n"
            f"  Expected:       {expected_str}\n"
            f"\n{instruction}\n"
            "IMPORTANT: Do NOT share the raw tracking URL. Do NOT say 'check your email'.\n"
        )

    # No live data — fall back to URL if available
    if tracking_url:
        return (
            "\nTRACKING: Live status unavailable. Tracking URL available.\n"
            f"  You MAY share this link: {tracking_url}\n"
            f"  Carrier: {tracking_company or 'courier'}\n"
            f"  Tracking number: {tracking_number or 'see link'}\n"
            "Say: 'You can track your order here: [link]'\n"
        )

    if tracking_number:
        return (
            f"\nTRACKING: Tracking number {tracking_number} via {tracking_company or 'courier'} "
            "— no tracking URL yet.\n"
            "Tell the customer: 'Your tracking number is [number] via [carrier]. "
            "Tracking usually activates within 24 hours of dispatch.'\n"
        )

    return (
        "\nTRACKING: No tracking information available yet.\n"
        "Tell the customer: 'Tracking information isn't available yet. "
        "It usually appears within 24 hours of shipping.'\n"
    )


def _fmt_time(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d at %I:%M %p")
    except Exception:
        return iso


def _fmt_date(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except Exception:
        return iso
