"""
Aftership Tracking Service
==========================
Fetches live tracking status for a shipment using the Aftership v4 API.
Called from the agent's order-status tool when a brand has aftership_api_key set.

Production-hardening pass (post Phase 4): a short-TTL in-process cache avoids
re-hitting Aftership for rapid customer follow-ups, one bounded retry recovers
from transient blips, and a failure cooldown protects response latency during
a sustained Aftership outage. All of this is in-process state — deliberately,
since this service runs under a single gunicorn worker (see Dockerfile), so a
plain dict/counter is fully effective without adding Redis or any new infra.
"""
import logging
import time
from collections import Counter
from typing import Optional
from datetime import datetime, timezone

import httpx
import tenacity

logger = logging.getLogger(__name__)

AFTERSHIP_BASE = "https://api.aftership.com/v4"
TIMEOUT = 5.0  # hard cap — never blocks Luna's reply

# ── Short-TTL response cache ──────────────────────────────────────────────────
# Only successful lookups are cached — failures are handled separately by the
# cooldown below, which is deliberately more conservative than a blind cache.
_CACHE_TTL_SECONDS = 30
_cache: dict = {}  # (carrier_slug, tracking_number) -> (expires_at_monotonic, tracking_info)

# ── Failure cooldown (lightweight circuit breaker) ────────────────────────────
# Not a full circuit breaker — there's exactly one call site, no shared
# connection/thread pool to exhaust, and calls are already capped at 5s. This
# exists purely to bound customer-facing latency during a *sustained* Aftership
# outage, since adding a retry (below) would otherwise double that tax on every
# affected message for the duration of the outage.
_FAILURE_THRESHOLD = 3
_COOLDOWN_SECONDS = 60
_consecutive_failures = 0
_cooldown_until = 0.0

# ── In-process metrics ────────────────────────────────────────────────────────
# No metrics stack exists in this deployment — a counter is the smallest thing
# that can answer "what's our success/timeout rate" without new infrastructure.
stats: Counter = Counter()


class _RetryableTrackingError(Exception):
    """Raised for transient Aftership failures (5xx) so tenacity can retry once."""


def get_tracking_stats() -> dict:
    """Snapshot of in-process Aftership call outcomes since last restart."""
    return dict(stats)


def _prune_cache(now: float) -> None:
    expired = [k for k, (expires_at, _) in _cache.items() if expires_at <= now]
    for k in expired:
        del _cache[k]


def _record_failure() -> None:
    global _consecutive_failures, _cooldown_until
    _consecutive_failures += 1
    if _consecutive_failures >= _FAILURE_THRESHOLD:
        _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        logger.warning(
            f"[Tracking] {_consecutive_failures} consecutive Aftership failures — "
            f"pausing live calls for {_COOLDOWN_SECONDS}s"
        )


def _record_success() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(
        (_RetryableTrackingError, httpx.TimeoutException, httpx.TransportError)
    ),
    stop=tenacity.stop_after_attempt(2),  # one retry
    wait=tenacity.wait_fixed(0.3),  # brief pause — this sits on the customer's reply latency
    reraise=True,
)
async def _fetch_tracking(url: str, headers: dict) -> httpx.Response:
    """Single HTTP attempt (retried once by the decorator on transient failures)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.get(url, headers=headers)
    if res.status_code >= 500:
        raise _RetryableTrackingError(f"Aftership returned {res.status_code}")
    return res


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
    cache_key = (carrier_slug, tracking_number)
    now = time.monotonic()

    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        stats["cache_hit"] += 1
        logger.info(f"[Tracking] Cache hit for {carrier_slug}/{tracking_number}")
        return cached[1]

    if now < _cooldown_until:
        stats["skipped_cooldown"] += 1
        logger.warning(
            f"[Tracking] Skipping Aftership call for {carrier_slug}/{tracking_number} — "
            f"in failure cooldown ({int(_cooldown_until - now)}s remaining)"
        )
        return None

    url = f"{AFTERSHIP_BASE}/trackings/{carrier_slug}/{tracking_number}"
    headers = {
        "aftership-api-key": aftership_api_key,
        "Content-Type": "application/json",
    }
    stats["requests"] += 1
    logger.info(f"[Tracking] Requesting Aftership status for {carrier_slug}/{tracking_number}")
    started = time.monotonic()
    try:
        res = await _fetch_tracking(url, headers)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if res.status_code == 404:
            stats["not_found"] += 1
            _record_success()  # a clean 404 answer means Aftership itself is healthy
            logger.info(f"[Tracking] {carrier_slug}/{tracking_number} not found in Aftership (404, {elapsed_ms}ms)")
            return None
        if res.status_code != 200:
            stats["http_error"] += 1
            _record_failure()
            logger.warning(f"[Tracking] Aftership returned {res.status_code} for {carrier_slug}/{tracking_number} ({elapsed_ms}ms)")
            return None

        data = res.json()
        tracking = data.get("data", {}).get("tracking", {})
        checkpoints = tracking.get("checkpoints") or []
        latest = checkpoints[-1] if checkpoints else {}
        recent_checkpoints = [
            {
                "time":    cp.get("checkpoint_time"),
                "message": cp.get("message") or cp.get("tag"),
                "tag":     cp.get("tag"),
            }
            for cp in checkpoints[-3:]
        ]

        logger.info(f"[Tracking] Aftership responded for {carrier_slug}/{tracking_number}: status={tracking.get('tag')} ({elapsed_ms}ms)")

        result = {
            "status":            tracking.get("tag"),             # InTransit, Delivered, etc.
            "status_text":       tracking.get("subtag_message") or tracking.get("tag") or "Unknown",
            "latest_location":   latest.get("location") or latest.get("city") or latest.get("country_name"),
            "latest_message":    latest.get("message"),
            "latest_time":       latest.get("checkpoint_time"),
            "expected_delivery": tracking.get("expected_delivery"),
            "carrier_slug":      tracking.get("slug"),
            "recent_checkpoints": recent_checkpoints,
        }
        stats["success"] += 1
        _record_success()
        now2 = time.monotonic()
        _prune_cache(now2)
        _cache[cache_key] = (now2 + _CACHE_TTL_SECONDS, result)
        return result
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["timeout"] += 1
        _record_failure()
        logger.warning(f"[Tracking] Aftership timed out for {carrier_slug}/{tracking_number} ({elapsed_ms}ms)")
        return None
    except _RetryableTrackingError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["http_error"] += 1
        _record_failure()
        logger.warning(f"[Tracking] Aftership failed after retry for {carrier_slug}/{tracking_number} ({elapsed_ms}ms): {e}")
        return None
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["exception"] += 1
        _record_failure()
        logger.warning(f"[Tracking] Aftership error for {carrier_slug}/{tracking_number} ({elapsed_ms}ms): {e}")
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

        timeline = _fmt_timeline(tracking_info.get("recent_checkpoints"))
        timeline_line = f"  Recent history: {timeline}\n" if timeline else ""

        return (
            "\nTRACKING STATUS (LIVE FROM AFTERSHIP — USE THIS, DO NOT SHARE RAW URL):\n"
            f"  Current status: {status_text}\n"
            f"  Latest update:  {message or 'No details'}\n"
            f"  Location:       {location}\n"
            f"  Last updated:   {last_time or 'unknown'}\n"
            f"{timeline_line}"
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


def _fmt_timeline(recent_checkpoints: Optional[list]) -> Optional[str]:
    """Renders up to the last 3 checkpoints as a short arrow-separated timeline."""
    if not recent_checkpoints:
        return None
    entries = []
    for cp in recent_checkpoints:
        date = _fmt_date(cp.get("time"))
        label = cp.get("message") or cp.get("tag")
        if date and label:
            entries.append(f"{date}: {label}")
    if not entries:
        return None
    return " → ".join(entries)
