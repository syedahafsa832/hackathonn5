"""
Aftership Tracking Service
==========================
Fetches live tracking status for a shipment using the Aftership v4 API.

Platform-level credential (H1 fix): ONE AFTERSHIP_API_KEY, set once for the
whole tResolv deployment, drives tracking for every connected brand - a
merchant never configures anything. The older per-brand `brands.
aftership_api_key` column (and its /settings/aftership UI) still exists and
still works as a fallback/override when no platform key is set, since
removing a live DB column/endpoint isn't "safe" territory - but it's no
longer required, and the platform key always wins when both are present.
See resolve_aftership_api_key() below - every caller goes through it now
instead of reading brand.aftership_api_key directly.

Production-hardening pass (post Phase 4): a short-TTL in-process cache avoids
re-hitting Aftership for rapid customer follow-ups, one bounded retry recovers
from transient blips, and a failure cooldown protects response latency during
a sustained Aftership outage. All of this is in-process state — deliberately,
since this service runs under a single gunicorn worker (see Dockerfile), so a
plain dict/counter is fully effective without adding Redis or any new infra.
"""
import logging
import os
import time
from collections import Counter
from typing import Optional
from datetime import datetime, timezone

import httpx
import tenacity

logger = logging.getLogger(__name__)

AFTERSHIP_BASE = "https://api.aftership.com/v4"
TIMEOUT = 5.0  # hard cap — never blocks Luna's reply

# ── Platform-level credential ───────────────────────────────────────────────
PLATFORM_AFTERSHIP_API_KEY = os.getenv("AFTERSHIP_API_KEY")


def resolve_aftership_api_key(brand: Optional[dict]) -> Optional[str]:
    """The single key used across every brand. Platform key (one shared
    tResolv-wide credential, no merchant setup required) always wins when
    configured; a brand's own legacy aftership_api_key is used only if no
    platform key exists, so nothing already relying on it breaks. Never
    reads customer input - `brand` is always the brand row already resolved
    via tenant/brand_id, never anything derived from a message."""
    if PLATFORM_AFTERSHIP_API_KEY:
        return PLATFORM_AFTERSHIP_API_KEY
    return (brand or {}).get("aftership_api_key") or None

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


# ── Deterministic status normalization ─────────────────────────────────────
# WISMO requirement: carrier/provider wording must never be classified by the
# LLM - only this fixed mapping decides the normalized state. Message-text
# overrides run first because Aftership's own `tag` enum has no distinct
# value for some real states callers need (e.g. "arrived at a distribution
# center" is still tag=InTransit; only the checkpoint's free-text message
# distinguishes it).
NORMALIZED_STATUSES = frozenset({
    "LABEL_CREATED", "IN_TRANSIT", "AT_DISTRIBUTION_CENTER", "OUT_FOR_DELIVERY",
    "DELIVERED", "DELAYED", "EXCEPTION", "AVAILABLE_FOR_PICKUP", "RETURNED", "UNKNOWN",
})

_TAG_TO_NORMALIZED = {
    "pending":            "LABEL_CREATED",
    "inforeceived":       "LABEL_CREATED",
    "intransit":          "IN_TRANSIT",
    "outfordelivery":     "OUT_FOR_DELIVERY",
    "attemptfail":        "EXCEPTION",
    "delivered":           "DELIVERED",
    "exception":          "EXCEPTION",
    "expired":            "EXCEPTION",
    "availableforpickup": "AVAILABLE_FOR_PICKUP",
}


def normalize_status(tag: Optional[str], message: Optional[str] = None) -> str:
    """Deterministic mapping from Aftership's raw tag + latest checkpoint
    message to the fixed WISMO status enum (NORMALIZED_STATUSES). Never
    calls the LLM - pure string matching, same contract for every caller."""
    msg = (message or "").strip().lower()
    if "distribution center" in msg or "sorting facility" in msg or "sorting center" in msg:
        return "AT_DISTRIBUTION_CENTER"
    if "out for delivery" in msg:
        return "OUT_FOR_DELIVERY"
    if "delay" in msg:
        return "DELAYED"
    if "returned to sender" in msg or "return to sender" in msg:
        return "RETURNED"
    return _TAG_TO_NORMALIZED.get((tag or "").strip().lower(), "UNKNOWN")


# ── Failure-reason classification ───────────────────────────────────────────
# get_tracking_status() keeps returning None on any failure (its existing,
# widely-relied-on contract - see test_aftership_tracking.py) so this is
# purely additive: the caller can read WHY the most recent call returned
# None without get_tracking_status()'s own signature changing at all. Single
# gunicorn worker (see Dockerfile) makes plain module state safe here, same
# as the existing cache/cooldown state above.
NO_TRACKING_NUMBER = "NO_TRACKING_NUMBER"
CARRIER_UNKNOWN = "CARRIER_UNKNOWN"
TRACKING_NOT_FOUND = "TRACKING_NOT_FOUND"
TRACKING_PROVIDER_TIMEOUT = "TRACKING_PROVIDER_TIMEOUT"
TRACKING_PROVIDER_RATE_LIMIT = "TRACKING_PROVIDER_RATE_LIMIT"
TRACKING_PROVIDER_ERROR = "TRACKING_PROVIDER_ERROR"
TRACKING_PROVIDER_UNAVAILABLE = "TRACKING_PROVIDER_UNAVAILABLE"  # cooldown engaged
TRACKING_DATA_AVAILABLE = "TRACKING_DATA_AVAILABLE"

_last_failure_reason: Optional[str] = None


def get_last_failure_reason() -> Optional[str]:
    """Classification of the most recent get_tracking_status() outcome - a
    provider outage (TRACKING_PROVIDER_*) must never be reported to a
    customer as a shipment delay. Call immediately after get_tracking_status()
    returns None to find out why."""
    return _last_failure_reason


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
    Never raises — all errors are caught and logged. See
    get_last_failure_reason() for why a None was returned.
    """
    global _last_failure_reason
    cache_key = (carrier_slug, tracking_number)
    now = time.monotonic()

    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        stats["cache_hit"] += 1
        _last_failure_reason = None
        logger.info(f"[Tracking] Cache hit for {carrier_slug}/{tracking_number}")
        return cached[1]

    if now < _cooldown_until:
        stats["skipped_cooldown"] += 1
        _last_failure_reason = TRACKING_PROVIDER_UNAVAILABLE
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
            _last_failure_reason = TRACKING_NOT_FOUND
            logger.info(f"[Tracking] {carrier_slug}/{tracking_number} not found in Aftership (404, {elapsed_ms}ms)")
            return None
        if res.status_code == 429:
            stats["http_error"] += 1
            _record_failure()
            _last_failure_reason = TRACKING_PROVIDER_RATE_LIMIT
            logger.warning(f"[Tracking] Aftership rate-limited us for {carrier_slug}/{tracking_number} ({elapsed_ms}ms)")
            return None
        if res.status_code != 200:
            stats["http_error"] += 1
            _record_failure()
            _last_failure_reason = TRACKING_PROVIDER_ERROR
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

        latest_message = latest.get("message")
        normalized = normalize_status(tracking.get("tag"), latest_message)
        result = {
            "status":            tracking.get("tag"),             # InTransit, Delivered, etc. (raw carrier_status)
            "status_text":       tracking.get("subtag_message") or tracking.get("tag") or "Unknown",
            "latest_location":   latest.get("location") or latest.get("city") or latest.get("country_name"),
            "latest_message":    latest_message,
            "latest_time":       latest.get("checkpoint_time"),
            "expected_delivery": tracking.get("expected_delivery"),
            "carrier_slug":      tracking.get("slug"),
            "recent_checkpoints": recent_checkpoints,
            # Structured, deterministic fields (WISMO requirement) - additive,
            # existing keys above are untouched for backward compatibility.
            "carrier_status":         tracking.get("tag"),
            "normalized_status":      normalized,
            "status_description":     latest_message or tracking.get("subtag_message") or tracking.get("tag"),
            "latest_event":           latest_message,
            "latest_event_location":  latest.get("location") or latest.get("city") or latest.get("country_name"),
            "latest_event_timestamp": latest.get("checkpoint_time"),
            "events":                 recent_checkpoints,
            "is_delivered":           normalized == "DELIVERED",
            "is_out_for_delivery":    normalized == "OUT_FOR_DELIVERY",
            "is_delayed":             normalized == "DELAYED",
            "is_exception":           normalized == "EXCEPTION",
            "provider":               "aftership",
            "last_updated_at":        datetime.now(timezone.utc).isoformat(),
        }
        stats["success"] += 1
        _record_success()
        _last_failure_reason = None
        now2 = time.monotonic()
        _prune_cache(now2)
        _cache[cache_key] = (now2 + _CACHE_TTL_SECONDS, result)
        return result
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["timeout"] += 1
        _record_failure()
        _last_failure_reason = TRACKING_PROVIDER_TIMEOUT
        logger.warning(f"[Tracking] Aftership timed out for {carrier_slug}/{tracking_number} ({elapsed_ms}ms)")
        return None
    except _RetryableTrackingError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["http_error"] += 1
        _record_failure()
        _last_failure_reason = TRACKING_PROVIDER_ERROR
        logger.warning(f"[Tracking] Aftership failed after retry for {carrier_slug}/{tracking_number} ({elapsed_ms}ms): {e}")
        return None
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stats["exception"] += 1
        _record_failure()
        _last_failure_reason = TRACKING_PROVIDER_ERROR
        logger.warning(f"[Tracking] Aftership error for {carrier_slug}/{tracking_number} ({elapsed_ms}ms): {e}")
        return None


# ── Context builder for agent prompt ─────────────────────────────────────────

def build_tracking_context(
    tracking_info: Optional[dict],
    tracking_number: Optional[str],
    tracking_url: Optional[str],
    tracking_company: Optional[str],
    failure_reason: Optional[str] = None,
) -> str:
    """
    Returns the tracking block to inject into the order context string.
    Priority: live Aftership data → real tracking number/URL with no live
    status → nothing at all.

    failure_reason: accepted for callers that pass it (logging/future use)
    but no longer changes which branch fires below - ANY case with a real
    tracking_number and no tracking_info gets the same hard
    no-status/no-ETA-fabrication instruction, regardless of the specific
    reason live data is missing (timeout, rate limit, provider error, or a
    genuine "not found"). Narrowing this to only certain reasons was the
    actual bug: a "not found" case fell through to a branch with no
    fabrication guardrail at all, and the LLM padded the reply with an
    invented ETA ("it should arrive in a couple of days") - confirmed live.
    """
    # Any case where we have a real tracking number/URL from Shopify but NO
    # live status from Aftership - whatever the exact reason (timeout, rate
    # limit, provider error, cooldown, or a genuine "not found" because
    # nothing was ever registered with Aftership) - must get the SAME hard
    # no-fabrication instruction. This used to only fire for the narrow
    # TRACKING_PROVIDER_* reasons, which meant a plain "not found" (get_
    # tracking_status returning None with no _provider_failure_reasons
    # match) silently fell through to the plain URL-sharing branch below,
    # which had no prohibition on padding the reply with an invented ETA -
    # confirmed live: "It should arrive in a couple of days" with zero
    # tracking evidence behind it. Any tracking_number/URL present here is
    # still 100% real (from Shopify) and safe to share; only the STATUS/
    # ETA claims must be blocked.
    if not tracking_info and tracking_number:
        return (
            "\nTRACKING: No live carrier status available for this tracking number right now "
            "(the tracking link below is real Shopify data and safe to share; the STATUS is not known).\n"
            f"  Tracking number: {tracking_number} via {tracking_company or 'courier'}\n"
            + (f"  Tracking link: {tracking_url}\n" if tracking_url else "")
            + "Tell the customer: 'I found the tracking information for your order, but the "
            "carrier isn't returning a current tracking update right now."
            + (" You can check the tracking link directly here: [link].'" if tracking_url else "'")
            + "\nHARD RULES: Do NOT say the shipment is delayed, in transit, out for delivery, or "
            "delivered - the live status is simply unknown, not confirmed as any of those. Do NOT "
            "invent or estimate a delivery date/ETA (e.g. never say 'a couple of days' or similar) - "
            "no ETA exists here to share. Only share the tracking link/number above, nothing else.\n"
        )

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

    # Reachable only for the edge case of a URL with no tracking_number at
    # all (any real tracking_number already returned above, with or without
    # a URL) - kept for defensive completeness, not the normal path.
    if tracking_url:
        return (
            "\nTRACKING: Live status unavailable. Tracking URL available.\n"
            f"  You MAY share this link: {tracking_url}\n"
            f"  Carrier: {tracking_company or 'courier'}\n"
            "Say: 'You can track your order here: [link]'\n"
            "Do NOT invent a status or ETA - none is known.\n"
        )

    return (
        "\nTRACKING: No tracking information available yet.\n"
        "Tell the customer: 'Tracking information isn't available yet. "
        "It usually appears within 24 hours of shipping.'\n"
    )


def build_shipment_context(shipments: list) -> str:
    """Multi-fulfillment WISMO context: an order can ship in more than one
    package, each with its own carrier/tracking/status - never assume one
    order equals one shipment. `shipments` is a list of dicts, one per
    Shopify fulfillment:
      {tracking_number, tracking_url, tracking_company, tracking_info, failure_reason}
    (tracking_info/failure_reason as returned by get_tracking_status() /
    get_last_failure_reason() for that shipment - either may be None).

    0 shipments -> same "no tracking yet" message as build_tracking_context.
    1 shipment  -> delegates to build_tracking_context for byte-identical
                   output to the pre-existing single-shipment behavior.
    2+ shipments -> a short per-shipment summary, so Luna can accurately say
                   e.g. "shipped in two packages - one delivered, one still
                   in transit" instead of only ever describing the first.
    """
    if not shipments:
        return build_tracking_context(None, None, None, None)
    if len(shipments) == 1:
        s = shipments[0]
        return build_tracking_context(
            s.get("tracking_info"), s.get("tracking_number"), s.get("tracking_url"),
            s.get("tracking_company"), s.get("failure_reason"),
        )

    lines = [f"\nTRACKING: This order shipped in {len(shipments)} separate packages.\n"]
    for i, s in enumerate(shipments, 1):
        info = s.get("tracking_info")
        carrier = s.get("tracking_company") or "courier"
        tn = s.get("tracking_number")
        if info:
            normalized = info.get("normalized_status", "UNKNOWN")
            desc = info.get("status_description") or info.get("status_text") or normalized
            expected = info.get("expected_delivery")
            expected_str = f", expected {_fmt_date(expected)}" if expected else ""
            lines.append(f"  Package {i} ({carrier}, tracking {tn}): {normalized} — {desc}{expected_str}\n")
        elif s.get("failure_reason"):
            lines.append(f"  Package {i} ({carrier}, tracking {tn}): carrier tracking temporarily unavailable\n")
        elif tn:
            lines.append(f"  Package {i} ({carrier}, tracking {tn}): status not yet available\n")
        else:
            lines.append(f"  Package {i}: no tracking number yet\n")

    lines.append(
        "\nSummarize all packages accurately and briefly (e.g. \"your order shipped in two "
        "packages — one has been delivered, the other is still in transit\"). Do NOT describe "
        "only one package as if it were the whole order. Do NOT invent a status for any package "
        "not listed above. Do NOT share raw tracking URLs.\n"
    )
    return "".join(lines)


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
