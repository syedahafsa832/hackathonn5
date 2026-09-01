import os
import json
import re
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Awaitable

# Set OPENAI_API_KEY for compatibility with Mistral's OpenAI-compatible API
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("MISTRAL_API_KEY", "")

from openai import OpenAI

from ..services.brand_knowledge_service import brand_knowledge_service
from ..services.sentiment_analyzer import sentiment_analyzer
from ..services.size_engine import size_engine
from ..services.tools import v3_tools
from ..services.return_actions_integration import return_actions
from ..lib.supabase_client import supabase_rpc, supabase_update
from ..services.mistral_limiter import call_with_limit
from ..services.ai_provider_manager import ai_provider_manager, AllProvidersFailedError

logger = logging.getLogger(__name__)


# Placeholder/fallback values that mean "we don't actually know this
# customer's name" - never a real name to greet someone by. Single source
# used by _construct_v3_prompt (the one shared prompt builder for every
# channel/response type) so a placeholder can never be handed to the model
# as if it were the customer's real name (root cause of "Dear There").
_UNKNOWN_NAME_PLACEHOLDERS = {"there", "customer", "website visitor", "unknown", "guest", "friend"}


def _known_customer_name(raw_name: Optional[str]) -> Optional[str]:
    """Returns a real customer name, or None if it's missing/a placeholder/
    an email address. Never derives a name from an email address or order
    number - callers only ever pass what was actually verified/provided.

    customer_info["name"] sometimes IS a raw email address (e.g. the Gmail
    poller falls back to the sender's address when the "From" header has no
    display name) - previously that value passed straight through here
    unfiltered, since it isn't one of the fixed placeholder words, letting
    the model be told "Name: someone@example.com" and the post-processing
    greeting below address the customer by their email ("Hey
    someone@example.com,")."""
    if not raw_name:
        return None
    name = str(raw_name).strip()
    if not name or name.lower() in _UNKNOWN_NAME_PLACEHOLDERS:
        return None
    if "@" in name:
        return None
    return name


# Common ways a reply can legitimately open with its own greeting/
# acknowledgement - "Hi", "Hey", "Hello", "Dear {name}", "Good morning",
# "Thanks for reaching out", etc. Matched at the very start of the reply
# (post-formatting), case-insensitively.
_GREETING_OPENER_RE = re.compile(
    r"^\s*(hi|hey|hello|dear|greetings|good\s+(?:morning|afternoon|evening)|thanks?(?:\s+you)?(?:\s+for)?)\b",
    re.IGNORECASE,
)


def _reply_already_has_greeting(reply: str) -> bool:
    """True if the model's own reply already opens with some form of
    greeting, regardless of whether it happens to include the customer's
    name. This is the single source of truth for whether the email
    safety-net greeting below should run at all.

    Root cause of the reported duplicate ("Hey [email],\\n\\nHey!"): the
    previous check asked "does the specific `name` string (which may be an
    email, or the neutral 'there' fallback) appear in the first 30 chars of
    the reply?" - which fails whenever the model's own greeting doesn't
    textually contain that exact string, even though the reply plainly
    already has a greeting. Checking for a greeting-shaped opening instead
    of a name match makes this correct regardless of what `name` resolves
    to, and is the one place that decides whether a greeting is already
    present - the model is the primary/preferred greeting owner; this is
    purely a fallback for a reply with no greeting at all."""
    return bool(_GREETING_OPENER_RE.match(reply or ""))


# A candidate store name the customer typed, e.g. "hasha clothing store
# order #1002" -> "hasha clothing". Deliberately generic (no hardcoded
# brand name) - matches "<Name> store" / "<Name> shop" / "<Name> clothing
# store" phrasing regardless of what name is used. Captures a wide window
# (up to 6 preceding words) because ordinary phrasing before "store" can be
# long ("is the QA Test Tee available in your store") - _detect_store_name_
# mismatch below trims that down to just the trailing name-like words.
_STORE_NAME_MENTION_RE = re.compile(
    r"\b([a-z0-9&'\-]+(?:\s+[a-z0-9&'\-]+){0,5})\s+(?:clothing\s+store|store|shop)\b",
    re.IGNORECASE,
)
_STORE_NAME_STOPWORDS = {"the", "clothing", "store", "shop", "inc", "llc", "co"}
# Ordinary English function words (articles, pronouns, prepositions,
# auxiliary/modal verbs, common fillers) - a closed, standard linguistic
# class, not a business-specific keyword list. A candidate store name must
# not be built ENTIRELY from these, and any leading run of them (e.g. "do
# you have this in your ...", "is the QA Test Tee available in your ...")
# is trimmed off before deciding whether what's left looks like a real name -
# this is what keeps "in your store"/"available in your store" from being
# misread as the customer naming a store called "your", while still
# extracting "hasha" cleanly out of "tell me about hasha clothing store".
_ENGLISH_FUNCTION_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "am", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "can", "could", "will", "would",
    "shall", "should", "may", "might", "must",
    "i", "you", "your", "yours", "we", "our", "ours", "he", "she", "it", "its",
    "they", "them", "their", "this", "that", "these", "those",
    "in", "on", "at", "for", "to", "of", "with", "from", "by", "about",
    "and", "or", "but", "not", "no", "yes", "please", "me", "my", "us",
    "available", "here", "there", "any", "some", "tell", "what", "how",
}


def _normalize_store_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    words = [w for w in name.split() if w not in _STORE_NAME_STOPWORDS]
    return " ".join(words)


def _detect_store_name_mismatch(query: str, real_brand_name: Optional[str]) -> Optional[str]:
    """Deterministic, no LLM call. Returns the store name the customer
    used (verbatim from their message) if it names a store that isn't this
    connected brand, or None if no store name was mentioned or it matches.

    This ONLY changes wording (a brief correction the model is asked to
    open with) - it never influences which brand/tenant/Shopify credentials
    are used elsewhere in this function. A wrong guess here (false
    positive or negative) only affects whether a one-line correction gets
    added to the prompt, never any security-relevant behavior."""
    if not real_brand_name:
        return None
    m = _STORE_NAME_MENTION_RE.search(query)
    if not m:
        return None
    words = m.group(1).strip().split()
    # "your store"/"our store"/"this store"/"the store" are how customers
    # overwhelmingly refer to THIS store generically, never a way of naming
    # a different one - if a possessive/demonstrative/article sits directly
    # against "store"/"shop", there's no real name mention here at all,
    # regardless of whatever unrelated words (a product name, "in stock at")
    # happen to appear earlier in the same sentence.
    if words and words[-1].lower() in {"your", "our", "my", "its", "their", "this", "that", "the", "a", "an"}:
        return None
    # Trim a leading run of ordinary function words - the actual name (if
    # any) is whatever's left immediately before "store"/"shop".
    while words and words[0].lower() in _ENGLISH_FUNCTION_WORDS:
        words.pop(0)
    if not words or all(w.lower() in _ENGLISH_FUNCTION_WORDS for w in words):
        return None
    mentioned = " ".join(words)
    norm_mentioned = _normalize_store_name(mentioned)
    if not norm_mentioned:
        return None
    norm_real = _normalize_store_name(real_brand_name)
    # Match if either name contains the other (handles "Syedahafsa1983" vs
    # "Syedahafsa1983's Clothing Store", or the customer using a shortened
    # form of the real name) - only a genuinely different name counts as a
    # mismatch worth correcting.
    if norm_mentioned in norm_real or norm_real in norm_mentioned:
        return None
    return mentioned


# Kept as a plain (non-f) string, assigned to a local variable before use in
# _construct_v3_prompt's f-string, never inlined directly into an f-string
# {..} expression - Python's f-string grammar (pre-3.12) rejects ANY
# backslash inside the {} part, including escaped quotes in a nested string
# literal, which is exactly what a `\"Dear {name},\"`-style inline string
# would need. This was a real deploy-breaking SyntaxError under the
# production Python version even though it parsed fine locally.
_UNKNOWN_NAME_PROMPT_TEXT = (
    "Not known - do NOT guess or invent one, and never derive one from the "
    "email address or an order number. If the greeting style above calls "
    "for a name (e.g. 'Dear {name},'), use a neutral opening instead - "
    "'Hi there,' or 'Thanks for reaching out,' - and NEVER write "
    "'Dear There' or treat any placeholder word as if it were the "
    "customer's real name."
)


def _format_address(addr: dict) -> str:
    if not addr:
        return "No shipping address"
    parts = [addr.get("name", ""), addr.get("address1", ""), addr.get("city", ""),
             addr.get("province", ""), addr.get("country", "")]
    return ", ".join(p for p in parts if p)


def _build_order_context(order: dict, tracking_context: str = "") -> str:
    """Build an explicit order context block that the LLM cannot ignore."""
    if not order or not order.get("success"):
        return ""

    items = []
    for item in order.get("items", []):
        title = item.get("title", "Unknown item")
        variant = item.get("variant_title", "")
        qty = item.get("quantity", 1)
        price = item.get("price", "")
        item_str = f"{qty}x {title}"
        if variant and variant.lower() not in ("default title", ""):
            item_str += f" ({variant})"
        if price:
            item_str += f" — Rs {price}"
        items.append(item_str)

    order_num = order.get("order_number") or order.get("order_id", "Unknown")
    status = order.get("status", "unfulfilled")
    total = order.get("total_amount", "")
    tracking = order.get("tracking_number", "")
    tracking_url = order.get("tracking_url", "")
    tracking_company = order.get("tracking_company", "")
    shipment_status = order.get("shipment_status")
    shipped_at = order.get("shipped_at")

    financial_status = order.get("financial_status", "")
    cancelled_at = order.get("cancelled_at")

    lines = [
        "=== REAL ORDER DATA FROM SHOPIFY — USE THIS EXACT INFORMATION ===",
        f"Order Number: #{order_num}",
        f"Fulfillment Status: {status}",
        f"Payment Status: {financial_status or 'unknown'}",
    ]
    if cancelled_at:
        lines.append(f"CANCELLED: Yes (cancelled at {cancelled_at})")
    if total:
        lines.append(f"Total: Rs {total}")
    if items:
        lines.append("Items Ordered:")
        for item_line in items:
            lines.append(f"  - {item_line}")

    status_phrases = {
        "in_transit": "in transit",
        "out_for_delivery": "out for delivery today",
        "delivered": "delivered",
        "attempted_delivery": "delivery was attempted but failed",
        "failure": "experiencing a delivery issue",
    }

    def _shipped_day(iso_val):
        if not iso_val:
            return None
        try:
            return datetime.fromisoformat(iso_val.replace("Z", "+00:00")).strftime("%A")
        except Exception:
            return None

    shipments = order.get("fulfillments") or []

    if len(shipments) > 1:
        # Multiple REAL Shopify fulfillments (split shipment, backorder catch-up,
        # multi-warehouse, etc). Each is reported separately — never merged into
        # one fake shipment, and never collapsed to "just the first one" like the
        # single-shipment branch below does for the common case.
        lines.append("")
        lines.append(f"SHIPPING INFO — THIS ORDER HAS {len(shipments)} SEPARATE SHIPMENTS:")
        for i, s in enumerate(shipments, start=1):
            s_tracking = s.get("tracking_number")
            s_company = s.get("tracking_company")
            s_url = s.get("tracking_url")
            s_status = s.get("shipment_status")
            s_day = _shipped_day(s.get("shipped_at"))
            readable = status_phrases.get(s_status, "recently shipped, tracking should update within 24 hours")
            lines.append(f"  Shipment {i}:")
            if s_company:
                lines.append(f"    Carrier: {s_company}")
            if s_tracking:
                lines.append(f"    Tracking Number: {s_tracking}")
            else:
                lines.append("    Tracking Number: not yet available from the carrier")
            if s_url:
                lines.append(f"    Tracking URL: {s_url}")
            if s_day:
                lines.append(f"    Shipped: {s_day}")
            lines.append(f"    Current status: {readable}")
        lines.append("")
        lines.append("IF CUSTOMER ASKS WHERE THEIR ORDER IS:")
        lines.append(f"  This order shipped in {len(shipments)} separate packages — mention EACH shipment distinctly (e.g. 'shipment 1 of 2 is...').")
        lines.append("  Do NOT combine them into a single tracking number or status. Do NOT invent tracking for a shipment that doesn't have one yet.")
        lines.append("  If you cannot clearly explain all shipments, say a team member will confirm the full shipping breakdown rather than guessing.")
    elif tracking_context:
        # Live Aftership data or fallback instructions — injected by caller
        lines.append(tracking_context)
    elif tracking or shipment_status:
        readable_status = status_phrases.get(shipment_status, "recently shipped, tracking should update within 24 hours")

        lines.append("")
        lines.append("SHIPPING INFO:")
        if tracking_company:
            lines.append(f"  Carrier: {tracking_company}")
        if tracking:
            lines.append(f"  Tracking Number: {tracking}")
        if tracking_url:
            lines.append(f"  Tracking URL: {tracking_url}")
        shipped_day = _shipped_day(shipped_at)
        if shipped_day:
            lines.append(f"  Shipped: {shipped_day}")
        lines.append(f"  Current status: {readable_status}")
        lines.append("")
        lines.append("IF CUSTOMER ASKS WHERE THEIR ORDER IS:")
        lines.append("  Answer in plain English using the shipped day + current status above (e.g. 'shipped Tuesday and it's in transit').")
        lines.append("  Do NOT say 'check your email for tracking'. Do NOT paste the raw tracking URL as your main answer.")
        lines.append("  You may offer the tracking URL as a secondary option AFTER the plain-English status.")
    elif status == "unfulfilled":
        lines.append("")
        lines.append("Order has not shipped yet — if customer asks, tell them it's being prepared and hasn't shipped.")

    # Derive what actions are sensible given current order state
    state_notes = []
    if cancelled_at:
        state_notes.append("ORDER IS ALREADY CANCELLED — do not offer to cancel again.")
    if financial_status in ("refunded", "partially_refunded"):
        state_notes.append(f"ORDER IS ALREADY {financial_status.upper()} — do not offer another refund.")
    if status == "fulfilled" and not cancelled_at:
        state_notes.append("ORDER IS FULFILLED (shipped) — cancellation is not possible; address change is not possible.")
    if state_notes:
        lines.append("")
        lines.append("COMMON SENSE RULES FOR THIS ORDER:")
        for note in state_notes:
            lines.append(f"  ⚠ {note}")

    lines.extend([
        "",
        f"CRITICAL: Use ONLY the items listed above. Do NOT invent product names.",
        f"If asked what was ordered, say exactly: {', '.join(items) if items else 'order details unavailable'}",
        "=== END ORDER DATA ===",
    ])
    return "\n".join(lines)


# Shared with the dashboard (TicketDetail.jsx checks this prefix) and the Test
# Luna onboarding endpoint — every configured AI model (all Mistral keys, all
# Groq keys) is out of quota/rate-limited. Worded for a non-technical store
# owner reading it in the Escalations list, not a dev. Module-level (not a
# class attribute) so it resolves correctly even when tests call
# _get_provider_failure_response with a bare MagicMock() as self.
PROVIDER_OUTAGE_REASON = "AI reply limit reached — every connected AI model is temporarily out of quota. This resolves on its own once quota resets; reply manually for now."

# Deliberately generic — must read naturally whether the original message
# was about an order, a refund, a product question, or anything else. Never
# a specific claim ("I've flagged this") beyond what escalate=True on the
# same response already guarantees is true. Module-level for the same
# bare-MagicMock-as-self reason as PROVIDER_OUTAGE_REASON above.
PROVIDER_OUTAGE_CUSTOMER_MESSAGE = (
    "Hi! Thanks for reaching out. We've got your message and want to make sure "
    "we take care of this properly. Our team is reviewing it now and will get "
    "back to you as soon as possible. \U0001F49B"
)

# Rule 1 backstop (safety-non-negotiable): refunds/cancellations/address changes
# are only ever *staged* for merchant approval by return_actions_integration.py -
# this pipeline never executes them synchronously. The system prompt already
# instructs the model to never claim these are done (see ACTION RULES in
# _construct_v3_prompt), but that is a prompt instruction, not a guarantee.
# This is the code-level check for when the model ignores it anyway.
_UNCONFIRMED_ACTION_INTENTS = {"refund_request", "return_request", "exchange_request", "cancellation_request", "address_change"}
_UNCONFIRMED_ACTION_DETECTED = {"refund", "return", "exchange", "cancel_order", "change_address"}
_FALSE_SUCCESS_RE = re.compile(
    r"\b(has been|have been|is|was)\s+(processed|approved|completed|confirmed|issued|refunded|cancell?ed|updated)\b"
    r"|\b(successfully|already)\s+(processed|approved|completed|refunded|cancell?ed|updated)\b",
    re.IGNORECASE,
)

# GLOBAL no-em-dash rule: no AI-generated customer-facing reply may contain
# an em dash, in any channel (email/chat/widget/draft). Applied once, right
# after the model's reply_body is extracted from the JSON response — before
# this module's own greeting/signature text is appended (that text uses a
# plain hyphen, never an em dash - see the "- {agent_name}" sign-offs below),
# so this single call point covers the entire final reply regardless of
# channel. Never touches the customer's own message or past conversation
# history - only this function's own generated output.
_EM_DASH = "—"


def _strip_em_dash(text: str) -> str:
    if not text or _EM_DASH not in text:
        return text
    # A spaced em dash is almost always a parenthetical aside in conversational
    # text ("all good here — just hanging out") - a comma reads naturally there.
    text = re.sub(r"\s+" + _EM_DASH + r"\s+", ", ", text)
    # Any remaining em dash (unspaced, e.g. word—word, or spaced on only one
    # side) - a plain hyphen is the closest like-for-like substitute.
    text = text.replace(_EM_DASH, "-")
    return text


def _enforce_no_unconfirmed_action_success(structured: Dict[str, Any]) -> Dict[str, Any]:
    """If the model's reply claims a refund/cancellation/address-change already
    happened, that claim is always false in this pipeline (nothing sensitive is
    executed synchronously here - see module docstring above). Overrides the
    reply with an honest "sent for confirmation" message and forces escalation
    rather than letting a false success claim reach the customer."""
    is_action_reply = (
        structured.get("intent") in _UNCONFIRMED_ACTION_INTENTS
        or structured.get("action_detected") in _UNCONFIRMED_ACTION_DETECTED
    )
    if is_action_reply and _FALSE_SUCCESS_RE.search(structured.get("reply_body") or ""):
        logger.warning(
            f"[Agent] Reply claimed unconfirmed action success (intent={structured.get('intent')}, "
            f"action_detected={structured.get('action_detected')}) — overriding"
        )
        structured["reply_body"] = (
            "I've received your request and sent it to our team to confirm before anything "
            "changes on the order. You'll get an update as soon as it's handled."
        )
        structured["escalate"] = True
        structured["status"] = "escalated"
        structured["escalation_reason"] = "AI reply claimed an unconfirmed action was completed - routed to human review."
    return structured


# Rule backstop: a customer explicitly asking for a human must actually get
# one, not an LLM deciding it can still "help" and continuing to auto-
# resolve. The prompt has no dedicated instruction for this, so - same
# reasoning as the false-success backstop above - this is the code-level
# guarantee for when the model doesn't honor an explicit request anyway.
_HUMAN_HANDOFF_FRAGS = [
    "talk to a human", "speak to a human", "talk to a person", "speak to a person",
    "talk with a human", "speak with a human", "talk with a person", "speak with a person",
    "talk to someone", "speak to someone", "real person", "actual person",
    "human agent", "live agent", "human representative", "human support",
    "connect me with a human", "connect me to a human", "get me a human",
    "talk to a representative", "speak to a representative", "customer service rep",
]


def _enforce_human_handoff_request(structured: Dict[str, Any], query: str) -> Dict[str, Any]:
    """If the customer explicitly asked for a human, honor that regardless
    of what the model decided - never let it keep "helpfully" auto-
    resolving instead of handing off.

    Checks only the CURRENT message, not the whole query string: chat's
    query embeds prior conversation history as "Customer: ..." lines ahead
    of the live message (see v2_chat_widget.py's full_query), so scanning
    the entire string would keep re-triggering forever off a request from
    several turns ago that's already been handled. The live message is
    always the text after the last "Customer:" marker; email's query has
    no such marker at all, so this is a no-op split there (whole message
    used, exactly as before)."""
    q = (query or "").lower()
    current_turn = q.rsplit("customer:", 1)[-1]
    if any(frag in current_turn for frag in _HUMAN_HANDOFF_FRAGS) and not structured.get("escalate"):
        logger.info("[Agent] Customer explicitly requested a human — forcing escalation")
        structured["reply_body"] = (
            "Of course — I'm connecting you with a member of our team now. "
            "They'll follow up with you shortly."
        )
        structured["escalate"] = True
        structured["status"] = "escalated"
        structured["escalation_reason"] = "Customer explicitly requested a human agent."
    return structured


def _enforce_no_ambiguous_product_claim(structured: Dict[str, Any], inventory_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """If the live Shopify product lookup couldn't resolve to a single
    product (ambiguous=True — e.g. "Essential Hoodie V1" title-matching
    "Essential Hoodie V10"-"V19" too before the word-boundary fix, or any
    other genuinely ambiguous name), the system prompt already says "do not
    guess" - but confirmed live, the model can still generate a specific
    price/availability/variant claim anyway. Unlike that prompt instruction,
    this always overrides the reply with a clarification built from the
    tool's own verified matches list — it never inspects or trusts the
    model's text, so it works even when the model ignores the instruction.

    needs_clarification=True is the other case handled here: a color/variant
    follow-up ("do you have it in black?") whose product-anchor couldn't be
    resolved from this conversation's own history either (see
    _resolve_recent_product_anchor) — no matches to list, just an honest
    "which product?" ask, forced the same non-negotiable way."""
    if not inventory_result:
        return structured

    if inventory_result.get("needs_clarification"):
        clarification = inventory_result.get("message") or (
            "Which product are you asking about? Let me know the item name and I'll check that for you."
        )
        logger.info("[Agent] Overriding reply for an unresolved variant follow-up — model must not guess which product the customer means")
        structured["reply_body"] = clarification
        return structured

    if not inventory_result.get("ambiguous"):
        return structured

    matches = inventory_result.get("matches") or []
    if matches:
        listed = ", ".join(matches[:8])
        clarification = (
            f"I found a few products matching that — {listed}. "
            "Could you let me know which one you mean so I can check it for you?"
        )
    else:
        clarification = (
            "I found a few products matching that name — could you tell me "
            "the exact one you mean so I can check it for you?"
        )

    logger.info("[Agent] Overriding reply for ambiguous product lookup — model must not guess which product the customer meant")
    structured["reply_body"] = clarification
    return structured


def _enforce_no_ungrounded_recommendation(structured: Dict[str, Any], recommendation_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Same philosophy as _enforce_no_ambiguous_product_claim, applied to
    recommendations: the model must never present a recommended product,
    invent a complementary/"bought together" pairing, or claim a confident
    match when the live lookup explicitly said it doesn't have one. Whenever
    the tool result is anything other than a genuine success with real
    candidates, the reply is unconditionally replaced with the tool's own
    honest message — never trusting the model's text."""
    if not recommendation_result:
        return structured

    # Genuine success with real candidates: trust the model to summarize the
    # verified list already placed in tool_context (same approach as a
    # single exact inventory match) — nothing to override here.
    if recommendation_result.get("success") and recommendation_result.get("recommendations"):
        return structured

    message = recommendation_result.get("message")
    if not message:
        return structured

    logger.info("[Agent] Overriding reply for ungrounded recommendation result (ambiguous/no-candidates/no-pairing-data/failure) — model must not invent a recommendation")
    structured["reply_body"] = message
    return structured


def _enforce_no_escalation_for_safe_identity_verification_response(
    structured: Dict[str, Any], needs_identity_verification: bool,
) -> Dict[str, Any]:
    """A Shopify ownership-mismatch response (order found, but the
    conversation's verified email doesn't match the order) is a complete,
    safe, self-contained reply — Luna correctly withheld protected order
    details and told the customer what's needed next. The tool_context
    instruction for this case tells the model it's "a statement, followed
    by escalation" so it states this plainly instead of looping the
    customer through a clarifying question — but the model reliably also
    sets escalate=True from that wording, which downstream routing
    (message_processor.py's _decide_ticket_routing) then reads as "a human
    must act", producing a ticket that shows "Escalated: Needs Your
    Attention" even though the reply was already generated AND already
    sent — confirmed live: a customer received Luna's response while the
    merchant dashboard simultaneously showed the AI had failed.

    Nothing here is actually waiting on the merchant — the ball is in the
    CUSTOMER's court to verify their identity, not the merchant's to
    review a pending action. Only overrides when a real reply exists
    (never manufactures a "handled" state for a failed generation) and
    risk_level isn't independently "high" for some other reason (never
    weakens a genuine escalation signal)."""
    if (
        needs_identity_verification
        and structured.get("reply_body")
        and structured.get("risk_level") != "high"
    ):
        structured["escalate"] = False
        if structured.get("status") == "escalated":
            structured["status"] = "auto_resolved"
    return structured


# Reused by both the recommendation-anchor and variant-followup resolvers
# below — the same "does this look like a real product name, not a
# pronoun" filter the current-message anchor extraction already applies.
_ANCHOR_PRONOUN_STOPWORDS = {"this", "that", "it", "this one", "that one", "the one", "same one", "one"}

# The same three anchor-style patterns used for the CURRENT message (see
# the recommendation-intent block below), reused unmodified so a candidate
# found in history is held to the identical bar as one found live.
_HISTORY_ANCHOR_PATTERNS = (
    r"(?:goes well with|wear with|wear it with|pair(?:s)? with|pair it with|buy with|buy alongside)\s+(?:the |a |an )?(.+?)\s*\??$",
    r"(?:similar to|anything like|something like|alternatives? to)\s+(?:the |a |an )?(.+?)\s*\??$",
    r"other\s+(.+?)(?:\s+do you have|\s+available|\s+in stock)?\s*\??$",
    # Same question phrasing the inventory-gate extractor uses on a
    # CUSTOMER's message ("do you have the Essential Hoodie in stock?") —
    # product name comes AFTER "do you have"/"is".
    r"do you have\s+(?:the |a |an )?(.+?)\s+(?:in stock|available)\b",
    # Luna's own prior AFFIRMATIVE replies confirm a product the other way
    # around — product name BEFORE "is" ("Yes, the Essential Hoodie is in
    # stock.", "The Winter Parka is also available."). An optional leading
    # "yes," and article are stripped the same way the anchor patterns above
    # already strip "the/a/an" from the customer-phrased patterns.
    r"^(?:yes,?\s+)?(?:the |a |an )?(.+?)\s+is\s+(?:currently\s+)?(?:also\s+)?(?:in stock|available)\b",
)

# The same category-word fallback used elsewhere in this file (inventory
# fallback, discovery category match) — deliberately small and known-narrow;
# a miss here just means no history match is found, never a guess.
_HISTORY_PRODUCT_MENTION_RE = re.compile(
    r'\b(\w+\s+)?(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)(\s+v\d+)?\b'
)


def _resolve_recent_product_anchor(query_text: str) -> Optional[str]:
    """When the CURRENT message is a pronoun-only follow-up ("show me that
    one", "do you have it in black") with no product name of its own, look
    backward through this conversation's chat history (already embedded in
    `query_text` by v2_chat_widget.py's _build_history_context — the
    "[CHAT HISTORY ...]...[END CHAT HISTORY]" block) for the most recently
    mentioned real-looking product name, most-recent-first.

    Never invents a product: this only extracts a name-shaped candidate
    from text that was actually said in this conversation (by the customer
    or by Luna's own prior, already-grounded replies). The candidate is
    still resolved through the same live Shopify title search
    (find_products_by_title, via get_inventory_status/
    get_product_recommendations) before ever reaching a reply — a wrong
    guess here still can't produce a fabricated result, only an honest
    "couldn't find that" or a real match. Returns None (never a pronoun,
    never a guess) when no history exists or nothing candidate-shaped is
    found in it, so the caller can fall back to asking the customer
    directly instead of guessing.
    """
    if not query_text or "[CHAT HISTORY" not in query_text:
        return None

    start = query_text.find("[CHAT HISTORY")
    end = query_text.find("[END CHAT HISTORY]")
    history_block = query_text[start:end] if end > start else query_text[start:]

    lines = [ln.strip() for ln in history_block.split("\n") if ln.strip()]
    for line in reversed(lines):  # most recent turn first
        # Strip the "Customer: "/"<agent name>: " role prefix _build_history_
        # context() prepends to every line — otherwise the ^-anchored
        # declarative pattern below would swallow the role label itself into
        # the candidate (e.g. "luna: yes, the essential hoodie"). Agent name
        # is merchant-configurable, so this strips ANY leading "word: "
        # rather than hardcoding "luna".
        line_lower = re.sub(r'^\w+:\s*', '', line.lower())

        for pattern in _HISTORY_ANCHOR_PATTERNS:
            m = re.search(pattern, line_lower)
            if m:
                candidate = m.group(1).strip(" ?.!")
                if len(candidate) >= 2 and candidate not in _ANCHOR_PRONOUN_STOPWORDS:
                    return candidate

        mention = _HISTORY_PRODUCT_MENTION_RE.search(line_lower)
        if mention:
            prefix = (mention.group(1) or "").strip()
            prefix_stopwords = {
                "does", "do", "is", "are", "was", "were", "can", "could",
                "will", "would", "should", "the", "a", "an", "this", "that",
                "your", "our", "my", "have", "has", "had", "luna", "customer",
            }
            if prefix in prefix_stopwords:
                prefix = ""
            candidate = " ".join(g for g in (prefix, mention.group(2), (mention.group(3) or "").strip()) if g)
            if candidate:
                return candidate

    return None


class CustomerSuccessAgent:
    """
    V3 Customer Success Agent (Luna) for Aurelio & Finch.
    Uses pgvector RAG, deterministic sizing, and live Shopify/AfterShip tools.
    """

    def __init__(self):
        # Chat completions go through ai_provider_manager, which owns key/model
        # selection and failover across MISTRAL_API_KEY_PRIMARY + fallback keys.
        logger.info(
            f"Initializing V3 Agent — AI providers configured: {ai_provider_manager.has_providers}"
        )

    async def process_customer_query(self, query: str, customer_info: Dict[str, Any], tenant_id: Optional[str] = None, store_id: Optional[str] = None, ticket_id: Optional[str] = None, on_progress: Optional[Callable[[str, str], Awaitable[None]]] = None) -> Dict[str, Any]:
        """
        V3 Orchestration:
        1. RAG Retrieval (Policies, Brand, Product Info) - tenant-specific if tenant_id provided
        2. Sizing Check (if applicable)
        3. Tool Calls (Order/Shipping/Inventory) - REAL TIME
        4. Structured Response Generation
        5. Confidence & Escalation Enforcement

        on_progress(stage, label), when provided, is called synchronously at
        each real dispatch point below - immediately before the tool call or
        model call it describes actually runs. It never decides which stage
        happened; it only reports the branch this function already took, so
        the caller can surface accurate activity status to the customer
        without inventing progress that isn't real. Best-effort: a broken
        callback must never break query processing.
        """
        async def _emit(stage: str, label: str) -> None:
            if not on_progress:
                return
            try:
                await on_progress(stage, label)
            except Exception:
                logger.debug("[Agent] on_progress callback failed (non-blocking)", exc_info=True)

        try:
            # channel is the real, already-set signal (v2_chat_widget.py sets
            # customer_info["channel"]="chat") - not a magic string sniffed
            # out of the customer's own message text, which used to leak
            # "[CHAT MODE — reply in 1-3 short sentences...]" into anything
            # that persists `query` verbatim (actions.original_message,
            # shown to merchants on the Escalations page).
            _is_chat = customer_info.get("channel") == "chat"
            # No "received" emit here — message_processor.py already logs its
            # own "message_received" ticket_events row earlier (right after
            # ticket intake, before this function even runs) for every email
            # ticket. Emitting a second one here duplicated it verbatim in
            # the Activity timeline (see test_no_duplicate_activity_events.py).
            await _emit("thinking", "Analyzing request…")

            # 2. Sizing Engine - Get actual recommendation if we have measurements
            sizing_context = ""
            if any(k in query.lower() for k in ["size", "fit", "small", "medium", "large", "xl"]):
                height = customer_info.get("height")
                weight = customer_info.get("weight")
                fit_preference = customer_info.get("fit_preference", "true")

                if height and weight:
                    # Get actual size recommendation from size engine
                    try:
                        from src.services.size_engine import size_engine
                        product_data = {
                            "fit_type": "tailored",
                            "stretch_level": 1
                        }
                        user_profile = {
                            "height": height,
                            "weight": weight,
                            "fit_preference": fit_preference
                        }
                        size_result = size_engine.recommend_size(user_profile, product_data)

                        if size_result.get("success"):
                            size = size_result.get("recommended_size")
                            confidence = size_result.get("confidence", 0)
                            reasoning = size_result.get("reasoning", "")

                            confidence_text = "pretty confident" if confidence > 0.85 else "fairly sure"
                            sizing_context = f"\nBased on measurements ({height}cm, {weight}kg), I'm {confidence_text} they'd take a **{size}**."
                        else:
                            sizing_context = "\nNeed a bit more info to pin down the perfect size."
                    except Exception as e:
                        logger.error(f"Sizing engine error: {e}")
                        sizing_context = ""
                else:
                    sizing_context = "\nNeed height and weight to give a proper recommendation."

            # 3. REAL TIME TOOL CALLS - Get live data from Shopify & AfterShip
            tool_results = {}
            query_lower = query.lower()

            # Resolve brand-specific Shopify + Aftership credentials
            _brand_name = "our store"
            _agent_name = "Luna"
            _email_signature = None
            # Default OFF: without this explicit merchant opt-in, an AI-
            # provider outage produces no customer-facing text at all (see
            # _get_provider_failure_response) - never Luna's old "I've
            # flagged this for my team" claim, which promised a real
            # escalation whether or not this specific message actually got
            # one routed to a human in time.
            _provider_outage_fallback_enabled = False
            from src.services.reply_style_service import build_style_prompt_block
            _style_block = build_style_prompt_block(None)
            _brand_shopify_domain = None
            _brand_shopify_token = None
            _brand_aftership_key = None
            _default_store = "00000000-0000-0000-0000-000000000000"
            if store_id and store_id != _default_store:
                try:
                    from src.lib.supabase_client import supabase_select as _sel
                    from src.services.shopify_service import decrypt_token as _dec
                    _b = _sel("brands", {"id": f"eq.{store_id}"})
                    if _b:
                        _brand_name = _b[0].get("name") or _b[0].get("brand_name") or "our store"
                        _agent_name = _b[0].get("agent_name") or "Luna"
                        _email_signature = _b[0].get("email_signature") or None
                        _provider_outage_fallback_enabled = bool(_b[0].get("provider_outage_fallback_enabled"))
                        try:
                            from src.services.reply_style_service import get_active_style, get_uploaded_example_snippets
                            await _emit("style_check", "Checking reply style…")
                            _active_style = get_active_style(_b[0])
                            # Uploaded Examples are independent seed data (see reply_style_service.py) —
                            # they must reach the live prompt on their own, not only via the learned-
                            # profile pipeline, which gates on approved-reply volume unrelated to examples.
                            _examples = get_uploaded_example_snippets(_b[0]["id"]) if _active_style is not None else None
                            _style_block = build_style_prompt_block(_active_style, _examples)
                        except Exception as _style_err:
                            logger.warning(f"[Agent] Reply Style resolution failed: {_style_err}")
                        if _b[0].get("shopify_connected") or _b[0].get("shopify_access_token"):
                            _brand_shopify_domain = _b[0].get("shopify_domain")
                            _raw = _b[0].get("shopify_access_token") or ""
                            _brand_shopify_token = _dec(_raw) if _raw else None
                        from src.services.tracking_service import resolve_aftership_api_key
                        _brand_aftership_key = resolve_aftership_api_key(_b[0])
                        logger.info(f"[Agent] Brand found: name={_brand_name}, domain={_brand_shopify_domain}, aftership={'set' if _brand_aftership_key else 'not set'}")
                except Exception as _se:
                    logger.warning(f"[Agent] Brand lookup failed (non-blocking): {_se}")

            # Only meaningful once we actually resolved a real brand name -
            # "our store" is just the unresolved-brand placeholder above, not
            # a real name to compare the customer's phrasing against.
            _store_name_mismatch = _detect_store_name_mismatch(query, _brand_name) if _brand_name != "our store" else None

            # Generic catalog question ("what products do you sell?", "what
            # do you have available?") - detected here, before RAG, so a
            # purely structured question never pays for (or depends on) an
            # embedding call at all. Reuses live Shopify data
            # (v3_tools.list_catalog -> ShopifyClient.list_active_products,
            # the same call find_products_by_title already uses) instead of
            # RAG. A question naming an actual product won't match this
            # narrow phrasing, so specific-product routing further below is
            # unaffected.
            _catalog_kw = [
                "what products do you sell", "what do you sell", "what products are available",
                "what products do you have", "what do you have available", "what do you carry",
                "what items do you sell", "what's in your store", "what is in your store",
                "show me your products", "list your products", "what products do you offer",
            ]
            _is_catalog_query = any(kw in query.lower() for kw in _catalog_kw)
            if _is_catalog_query:
                await _emit("product_lookup", "Checking the catalog…")
                tool_results["catalog"] = await v3_tools.list_catalog(
                    shop_domain=_brand_shopify_domain,
                    access_token=_brand_shopify_token,
                )
                if tool_results["catalog"].get("success"):
                    await _emit("product_found", "Shopify catalog found")

            # RAG retrieval is deferred until after every Shopify/order/
            # inventory/action tool has had a chance to run (see "1. RAG
            # Retrieval" further below, right before response generation) -
            # none of those tools need rag_context, so running RAG first was
            # only ever paying for (and depending on) an embedding call that
            # a purely structured question never needed at all. tool_results
            # populated below is what that later step uses to decide.

            # Order number / email extraction must only ever look at the
            # customer's own new top-level text, never a quoted earlier
            # message in the same email thread ("On ... wrote:" / "> " quote
            # lines) - a quote commonly repeats an old order number, email,
            # or even an unrelated digit string (e.g. "Aug 23, 2026" in the
            # quote header itself), any of which could otherwise be
            # mistaken for a fresh, current one.
            _quote_marker = re.search(r'^On .{0,80}wrote:|^>', query, re.MULTILINE)
            _new_reply_text = query[:_quote_marker.start()] if _quote_marker else query

            # Check for order status inquiry
            if any(kw in query_lower for kw in ["order", "shipped", "tracking", "delivered", "when will", "what did i order"]):
                # Try to extract order number from query
                order_match = re.search(r'#?(\d{3,6})', _new_reply_text)
                if order_match:
                    order_id = order_match.group(1)
                    await _emit("order_lookup", f"Finding order #{order_id}…")
                    tool_results["order_status"] = await v3_tools.get_order_status(
                        order_id,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                        # Ownership check: a bare order number must never surface another
                        # customer's order data. customer_info["email"] is "" for an
                        # unverified chat-widget visitor, which always fails the check.
                        customer_email=customer_info.get("email") or "",
                    )
                    if tool_results["order_status"].get("success"):
                        await _emit("order_found", "Shopify order found")

                # Also try to look up by customer email if provided in query
                email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', _new_reply_text)
                customer_email = None
                if email_match:
                    customer_email = email_match.group(0)
                elif customer_info.get("email"):
                    # Use customer's email from their info
                    customer_email = customer_info.get("email")

                if customer_email:
                    if "order_status" not in tool_results:
                        await _emit("order_lookup", "Looking up your order…")
                    tool_results["orders_by_email"] = await v3_tools.get_orders_by_email(
                        customer_email,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )

            # Identity-verification follow-up: this message names no order
            # number of its own, but Luna's own prior reply on this ticket
            # asked the customer to confirm the email used on a specific
            # order (needs_email_verification, persisted on that outbound
            # message - see STAGE 10 in message_processor.py). Re-run the
            # SAME get_order_status lookup with the order number already
            # known from this conversation and the newly supplied email -
            # never re-ask for the order number, and never treat a bare
            # email as sufficient without that prior request having
            # genuinely happened. Deterministic (regex), no LLM call.
            if "order_status" not in tool_results and ticket_id:
                _verify_email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', _new_reply_text)
                if _verify_email_match:
                    try:
                        from src.lib.supabase_client import supabase_select as _sel2
                        _t_rows = _sel2("tickets", {"id": f"eq.{ticket_id}"})
                        _t = _t_rows[0] if _t_rows else {}
                        _last_outbound = next(
                            (m for m in reversed(_t.get("messages") or []) if m.get("direction") in ("outbound", "draft")),
                            None,
                        )
                        _pending_order_id = _t.get("detected_order_id")
                        if _last_outbound and _last_outbound.get("needs_email_verification") and _pending_order_id:
                            await _emit("order_lookup", f"Finding order #{_pending_order_id}…")
                            tool_results["order_status"] = await v3_tools.get_order_status(
                                _pending_order_id,
                                shop_domain=_brand_shopify_domain,
                                access_token=_brand_shopify_token,
                                customer_email=_verify_email_match.group(0),
                            )
                            if tool_results["order_status"].get("success"):
                                await _emit("order_found", "Shopify order found")
                    except Exception as _pve:
                        logger.warning(f"[Agent] Pending identity-verification lookup failed (non-blocking): {_pve}")

            # Recommendation intent is checked first so a message like "Do you
            # have anything like the Galactic Space Boots?" is recognized as
            # a recommendation question, not ALSO fired through the plain
            # inventory trigger below (both keyword sets can match the same
            # message, e.g. "do you have" + "anything like" — found live:
            # without this, that phrasing triggered a second, wasted, lower-
            # quality Shopify lookup for "anything like the galactic space
            # boots" as if it were a literal product name).
            _complementary_kw = ["goes well with", "wear with", "wear it with", "pair with", "pairs with", "pair it with", "buy with", "buy alongside"]
            _similar_kw = ["similar", "something like", "anything like", "alternative", "recommend", "what other", "other hoodies", "other shirts", "other options"]
            # Bare pronoun references to a previously-discussed product
            # ("show me that one", "what about this one?") — these carry no
            # keyword like "similar"/"recommend" at all, so without this list
            # they never even reached the recommendation-intent gate, let
            # alone the anchor extraction below. None of the 3 anchor
            # patterns extract anything from phrasing this bare, so these
            # always fall through to _resolve_recent_product_anchor()'s
            # history lookup — never a literal Shopify search for "that one".
            _pronoun_followup_kw = ["that one", "this one", "the other one"]
            _is_recommendation_query = any(kw in query_lower for kw in _complementary_kw + _similar_kw + _pronoun_followup_kw)

            # Discovery queries — "I need a hoodie for winter, what would you
            # suggest" — have no specific anchor product to be "similar to",
            # so they don't match _similar_kw's patterns (which all require an
            # anchor after the keyword) and previously fell through to a plain
            # LLM response with no live product data at all, which is exactly
            # how the model ended up inventing material/fit details for
            # products it never actually looked up. Handled as its own
            # category-based path (discover_products_by_category), not routed
            # through the anchor-based recommendation flow above.
            _discovery_kw = ["what would you suggest", "any suggestions", "what do you suggest", "what should i get",
                              "what should i buy", "looking for a", "looking for something", "need something for",
                              "help me choose", "help me pick", "what would you recommend"]
            _is_discovery_query = (not _is_recommendation_query) and any(kw in query_lower for kw in _discovery_kw)

            # Color/variant follow-ups — "do you have it in black?", "what
            # about another color?", "what about a smaller size?", "same one
            # in blue". The customer isn't naming a new product; they're
            # asking about a different variant of whatever was already
            # discussed. Checked before the plain inventory gate below so
            # "this in another color" is never captured as if it were a
            # literal product name (previously: it was — get_inventory_status
            # would honestly report "couldn't find 'this in another color'",
            # which is truthful but useless, since the real product was never
            # looked up at all).
            _variant_followup_kw = [
                "another color", "different color", "other color", "another colour", "different colour", "other colour",
                "smaller size", "bigger size", "larger size", "different size", "another size", "other size",
                "in black", "in white", "in red", "in blue", "in green", "in yellow", "in pink", "in purple",
                "in grey", "in gray", "in brown", "in navy", "in beige", "in orange",
            ]
            _is_variant_followup_query = (
                not _is_recommendation_query and not _is_discovery_query
                and any(kw in query_lower for kw in _variant_followup_kw)
            )
            if _is_variant_followup_query:
                variant_anchor = _resolve_recent_product_anchor(query)
                if variant_anchor:
                    await _emit("product_lookup", "Checking that item…")
                    tool_results["inventory"] = await v3_tools.get_inventory_status(
                        variant_anchor,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )
                    if tool_results["inventory"].get("success"):
                        await _emit("product_found", "Shopify product found")
                else:
                    # No product identified anywhere in this conversation to
                    # resolve "it"/"this one" against — ask, don't guess.
                    tool_results["inventory"] = {
                        "success": False,
                        "needs_clarification": True,
                        "message": "Which product are you asking about? Let me know the item name and I'll check that for you.",
                    }

            # Check for inventory/product/price inquiry. The keyword gate below
            # is what keeps unrelated messages from ever reaching Shopify — a
            # product search only runs when both the gate AND one of the
            # extraction patterns below actually match. Extraction used to
            # depend on a tiny hardcoded category-word list (hoodie/jacket/
            # pants/...), which missed any merchant-specific product name and
            # every price question. These patterns pull the product name out
            # of the phrasing itself instead, so "Essential Crewneck" or "how
            # much is the Winter Parka?" reach the live tool the same as
            # "hoodie" always did. A miss here just means no live lookup runs
            # (falls back to normal RAG/LLM handling) — never a guess.
            if not _is_recommendation_query and not _is_discovery_query and not _is_variant_followup_query and any(kw in query_lower for kw in ["in stock", "available", "inventory", "do you have", "how much", "price", "cost", "tell me about", "describe", "sizes", "size does", "colors", "colours", "what sizes", "what colors", "what colours"]):
                product = None
                for pattern in (
                    # Trigger-word variants first — non-greedy up to the FIRST
                    # occurrence of "in stock"/"available", not end-of-string,
                    # so trailing qualifiers ("...available in size M?") don't
                    # get swallowed into the product name. Found live: without
                    # this, "Is the Essential Hoodie V1 available in size M?"
                    # extracted the entire trailing clause instead of just the
                    # product name.
                    r"do you have\s+(?:the |a |an )?(.+?)\s+(?:in stock|available)\b",
                    r"do you have\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"is\s+(?:the |a |an )?(.+?)\s+(?:in stock|available)\b",
                    r"how much (?:is|does)\s+(?:the |a |an )?(.+?)(?:\s+cost)?\s*\??$",
                    r"what(?:'s| is) the price of\s+(?:the |a |an )?(.+?)\s*\??$",
                    # Product-detail requests ("tell me about the Premium Hoodie
                    # V23", "describe the Essential Hoodie") — previously
                    # matched nothing, so this class of question got only
                    # whatever RAG happened to retrieve (frozen at last import,
                    # no live price/availability, no signal to the model that
                    # it might be stale) instead of a live lookup.
                    r"tell me (?:more )?about\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"describe\s+(?:the |a |an )?(.+?)\s*\??$",
                    # Variant questions ("what sizes does the QA Test Tee
                    # come in?", "what colors does it come in?") - a named
                    # product's own variant options, not a followup on one
                    # already discussed (that's _variant_followup_kw above).
                    r"what (?:sizes|colors|colours) (?:does|do)\s+(?:the |a |an )?(.+?)\s+come(?:s)? in\b",
                    r"(?:sizes|colors|colours) (?:does|do)\s+(?:the |a |an )?(.+?)\s+come(?:s)? in\b",
                ):
                    m = re.search(pattern, query_lower)
                    if m:
                        candidate = m.group(1).strip(" ?.!")
                        if len(candidate) >= 2:
                            product = candidate
                            break
                if not product:
                    # Fallback for phrasing the patterns above don't cover.
                    fallback_match = re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', query_lower)
                    if fallback_match:
                        product = fallback_match.group(1)
                if product:
                    await _emit("product_lookup", "Finding product…")
                    tool_results["inventory"] = await v3_tools.get_inventory_status(
                        product,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )
                    if tool_results["inventory"].get("success"):
                        await _emit("product_found", "Shopify product found")

            # "what is X" is deliberately NOT in the keyword gate above — it's
            # also the single most common phrasing for policy/account
            # questions ("what is your return policy", "what is my order
            # status"), which would otherwise get a wasted, wrong Shopify
            # lookup and an honest-but-unhelpful "couldn't find that" instead
            # of their real answer from RAG/order lookup. Only fires here when
            # the extracted phrase itself looks product-like — contains a
            # digit (e.g. "V23") or one of the known category words — narrow
            # enough to catch "what is the Premium Hoodie V23" without
            # catching ordinary policy questions.
            if (
                "inventory" not in tool_results
                and not _is_recommendation_query and not _is_discovery_query
                and "what is" in query_lower
            ):
                m = re.search(r"what is\s+(?:the |a |an )?(.+?)\s*\??$", query_lower)
                if m:
                    candidate = m.group(1).strip(" ?.!")
                    looks_product_like = bool(re.search(r'\d', candidate)) or bool(
                        re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', candidate)
                    )
                    if len(candidate) >= 2 and looks_product_like:
                        await _emit("product_lookup", "Finding product…")
                        tool_results["inventory"] = await v3_tools.get_inventory_status(
                            candidate,
                            shop_domain=_brand_shopify_domain,
                            access_token=_brand_shopify_token,
                        )
                        if tool_results["inventory"].get("success"):
                            await _emit("product_found", "Shopify product found")

            # General product-mention fallback — catches product-specific
            # questions in any phrasing the more specific triggers above
            # miss: "Is Premium Hoodie V23 soft and durable?", "What
            # material is Premium Hoodie V23?", "How does it fit?", "Does
            # Hoodie V23 come in cotton?", "Is Hoodie V23 good for winter?".
            # Enumerating every possible question phrasing doesn't scale;
            # this instead looks for a product-name-shaped mention anywhere
            # in the message (a category word, optionally with one
            # preceding qualifier and/or a version suffix) and always
            # attempts a live lookup when one is found. Reuses the exact
            # same get_inventory_status()/find_products_by_title() search
            # and its existing exact/ambiguous handling — this only decides
            # WHETHER to look something up, never WHAT the answer is.
            if "inventory" not in tool_results and not _is_recommendation_query and not _is_discovery_query:
                _mention = re.search(
                    r'\b(\w+\s+)?(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)(\s+v\d+)?\b',
                    query_lower,
                )
                if _mention:
                    # The preceding-word group is meant to catch a real
                    # product qualifier ("Premium"/"Essential"/"Signature"
                    # Hoodie) — filter out common question/verb words that
                    # happen to sit immediately before the category word
                    # ("Does Hoodie V23...", "Is Hoodie V23...") so they
                    # don't get folded into the search term and cause an
                    # honest but wrong "not found" (find_products_by_title
                    # requires the whole captured phrase to appear in the
                    # title).
                    _prefix = (_mention.group(1) or "").strip()
                    _prefix_stopwords = {
                        "does", "do", "is", "are", "was", "were", "can", "could",
                        "will", "would", "should", "the", "a", "an", "this", "that",
                        "your", "our", "my", "have", "has", "had",
                    }
                    if _prefix in _prefix_stopwords:
                        _prefix = ""
                    _candidate = " ".join(g for g in (_prefix, _mention.group(2), (_mention.group(3) or "").strip()) if g)
                    if _candidate:
                        await _emit("product_lookup", "Finding product…")
                        tool_results["inventory"] = await v3_tools.get_inventory_status(
                            _candidate,
                            shop_domain=_brand_shopify_domain,
                            access_token=_brand_shopify_token,
                        )
                        if tool_results["inventory"].get("success"):
                            await _emit("product_found", "Shopify product found")

            # Check for a product-recommendation request ("show me something
            # similar", "what else do you have", "what goes with this").
            # Distinguishes "similar" (deterministic type/tag/vendor scoring —
            # real data) from "complementary" (no real pairing data exists
            # anywhere in this architecture — handled honestly inside
            # get_product_recommendations, never faked as same-type results).
            # "this"/"that" alone (no product actually named in the CURRENT
            # message) falls back to _resolve_recent_product_anchor(), which
            # looks backward through this conversation's own history for the
            # most recently mentioned real product — still never a guess,
            # since whatever it finds still has to resolve through the same
            # live Shopify title search below. Only when history has nothing
            # resolvable either does this ask the customer directly, instead
            # of silently doing nothing (which previously left the reply
            # fully ungrounded — no tool call, no guard, nothing stopping the
            # model from inventing an answer).
            if _is_recommendation_query:
                rec_intent = "complementary" if any(kw in query_lower for kw in _complementary_kw) else "similar"
                anchor = None
                for pattern in (
                    r"(?:goes well with|wear with|wear it with|pair(?:s)? with|pair it with|buy with|buy alongside)\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"(?:similar to|anything like|something like|alternatives? to)\s+(?:the |a |an )?(.+?)\s*\??$",
                    r"other\s+(.+?)(?:\s+do you have|\s+available|\s+in stock)?\s*\??$",
                ):
                    m = re.search(pattern, query_lower)
                    if m:
                        candidate = m.group(1).strip(" ?.!")
                        # A bare pronoun isn't a product name from the current
                        # message alone — fall through to history resolution
                        # below rather than searching Shopify for "this".
                        if len(candidate) >= 2 and candidate not in _ANCHOR_PRONOUN_STOPWORDS:
                            anchor = candidate
                            break
                if not anchor:
                    anchor = _resolve_recent_product_anchor(query)
                if anchor:
                    await _emit("product_search", "Finding products…")
                    tool_results["recommendations"] = await v3_tools.get_product_recommendations(
                        anchor,
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                        intent=rec_intent,
                    )
                else:
                    tool_results["recommendations"] = {
                        "success": False,
                        "message": "Which product would you like recommendations for? Let me know the item and I'll take a look.",
                    }

            # Discovery query — "I need a hoodie for winter, what would you
            # suggest" — no anchor product to compare against, so pull a
            # plain category word out of the message instead (same small,
            # deterministic category list the inventory-trigger fallback
            # already uses) and hand it to discover_products_by_category().
            # No category word found just means no live lookup runs, same
            # fallback-to-normal-handling rule as every other trigger here.
            elif _is_discovery_query:
                _category_match = re.search(r'(hoodie|jacket|pants|shirt|tshirt|coat|dress|skirt)', query_lower)
                if _category_match:
                    await _emit("product_search", "Finding products…")
                    tool_results["recommendations"] = await v3_tools.discover_products_by_category(
                        _category_match.group(1),
                        shop_domain=_brand_shopify_domain,
                        access_token=_brand_shopify_token,
                    )

            # 3b. Aftership live tracking — one lookup per real Shopify
            # fulfillment (an order can ship in multiple packages, each with
            # its own tracking number/carrier/status — never assume one
            # order equals one shipment). Falls back to the single
            # tracking_number/company fields when the fulfillments array is
            # empty, for any caller/legacy shape that never populated it.
            if "order_status" in tool_results and tool_results["order_status"].get("success"):
                _order = tool_results["order_status"]
                _order_fulfillments = _order.get("fulfillments") or []
                if not _order_fulfillments and _order.get("tracking_number"):
                    _order_fulfillments = [{
                        "tracking_number": _order.get("tracking_number"),
                        "tracking_url": _order.get("tracking_url"),
                        "tracking_company": _order.get("tracking_company"),
                    }]
                _shipments_with_numbers = [f for f in _order_fulfillments if f.get("tracking_number")]
                if _shipments_with_numbers and _brand_aftership_key:
                    from src.services.tracking_service import (
                        get_tracking_status,
                        get_last_failure_reason,
                        shopify_carrier_to_aftership_slug,
                    )
                    await _emit("shipping_lookup", "Checking shipping status…")
                    _shipments = []
                    _any_live_data = False
                    _any_provider_failure = False
                    for _f in _shipments_with_numbers:
                        _tn = _f.get("tracking_number")
                        _tc = _f.get("tracking_company") or ""
                        _slug = shopify_carrier_to_aftership_slug(_tc)
                        _shipment_entry = {
                            "tracking_number": _tn,
                            "tracking_url": _f.get("tracking_url"),
                            "tracking_company": _tc,
                            "tracking_info": None,
                            "failure_reason": None,
                        }
                        if _slug:
                            try:
                                _info = await get_tracking_status(_tn, _slug, _brand_aftership_key)
                                _shipment_entry["tracking_info"] = _info
                                if _info:
                                    _any_live_data = True
                                else:
                                    _reason = get_last_failure_reason()
                                    _shipment_entry["failure_reason"] = _reason
                                    if _reason and _reason != "TRACKING_NOT_FOUND":
                                        _any_provider_failure = True
                                logger.info(f"[Agent] Aftership tracking fetched for {_tn}: status={(_info or {}).get('status')}")
                            except Exception as _te:
                                logger.warning(f"[Agent] Aftership call failed for {_tn} (non-blocking): {_te}")
                                _shipment_entry["failure_reason"] = "TRACKING_PROVIDER_ERROR"
                                _any_provider_failure = True
                        else:
                            logger.info(f"[Agent] Carrier '{_tc}' not in Aftership map for {_tn} — skipping live tracking")
                        _shipments.append(_shipment_entry)

                    tool_results["shipments"] = _shipments
                    # Backward-compatible single mirror — first shipment's result.
                    tool_results["tracking_info"] = _shipments[0]["tracking_info"] if _shipments else None
                    # Only ever emit a success event when a lookup genuinely
                    # returned live data — never on a provider failure, so
                    # this stays truthful, not a fake "success" animation.
                    if _any_live_data:
                        await _emit("tracking_retrieved", "Tracking information retrieved")
                    elif _any_provider_failure:
                        await _emit("tracking_unavailable", "Carrier tracking temporarily unavailable")

            # 4. Build tool context for the AI (explicit Shopify data — AI must use this verbatim)
            tool_context = ""
            if _store_name_mismatch:
                # Wording-only: the customer named the wrong store, which has
                # zero bearing on order/identity security below - the actual
                # Shopify lookup already ran (or will run) against THIS
                # connected brand's credentials regardless of what name the
                # customer used. This must never be conflated with identity
                # verification (a completely separate concern, driven by
                # order/email ownership, not by what the customer called the
                # store).
                tool_context += (
                    f"BRAND NAME CORRECTION: The customer referred to this store as \"{_store_name_mismatch}\", "
                    f"but this store is actually \"{_brand_name}\". Open your reply with one brief, friendly "
                    f"correction (e.g. \"Sorry, we're {_brand_name}, not {_store_name_mismatch}.\"), then continue "
                    "normally with the rest of your answer. This is a harmless mix-up, not a security concern - "
                    "do NOT treat it as identity verification failing, do NOT ask the customer to re-confirm "
                    "anything because of it, and do NOT mention it again on a later turn unless they name the "
                    "wrong store again.\n"
                )
            # Set when this reply asks the customer to confirm their order's
            # email - persisted onto the outbound message (see STAGE 10 in
            # message_processor.py) so the customer's next message can be
            # recognized as an identity-verification follow-up without an
            # LLM call, per _pending_order_id below.
            _needs_identity_verification = False
            if tool_results:
                if "order_status" in tool_results:
                    order = tool_results["order_status"]
                    if order.get("success"):
                        # Build Aftership tracking block (or URL fallback) —
                        # build_shipment_context handles both the single- and
                        # multi-fulfillment case; falls back to the
                        # single-shipment fields when no per-fulfillment
                        # lookups ran at all (e.g. no aftership key configured).
                        try:
                            from src.services.tracking_service import build_shipment_context, build_tracking_context
                            _shipments_for_ctx = tool_results.get("shipments")
                            if _shipments_for_ctx is not None:
                                _tracking_ctx = build_shipment_context(_shipments_for_ctx)
                            else:
                                _tracking_ctx = build_tracking_context(
                                    tracking_info=tool_results.get("tracking_info"),
                                    tracking_number=order.get("tracking_number"),
                                    tracking_url=order.get("tracking_url"),
                                    tracking_company=order.get("tracking_company"),
                                )
                        except Exception as _tce:
                            logger.warning(f"[Agent] Tracking context build failed (non-blocking): {_tce}")
                            _tracking_ctx = ""
                        order_block = _build_order_context(order, tracking_context=_tracking_ctx)
                        tool_context += order_block + "\n"
                        logger.info(f"[Agent] Order context built:\n{order_block}")

                        # Deterministic cancellation-window check: a plain
                        # question like "can I cancel? it was placed
                        # yesterday" never reaches return_actions_integration.py's
                        # action-staging flow at all if it isn't classified
                        # as an action request - but the model still needs
                        # the real answer, not a guess from the customer's
                        # own wording. Only checked for cancel-shaped
                        # questions about a still-active order; never
                        # fabricates a policy or a result.
                        if "cancel" in query_lower and not order.get("cancelled_at"):
                            try:
                                from src.services.actions_manager import actions_manager
                                _cancel_policy_text = await actions_manager.get_custom_policy_text(store_id)
                                _window_check = actions_manager.evaluate_cancellation_window(
                                    _cancel_policy_text, order.get("created_at")
                                )
                                if _window_check:
                                    if _window_check["eligible"]:
                                        tool_context += (
                                            f"CANCELLATION WINDOW CHECK: This order was placed {_window_check['elapsed_hours']:.1f} "
                                            f"hours ago, within the store's {_window_check['window_hours']:.0f}-hour cancellation window. "
                                            "State plainly that it's still within the window.\n"
                                        )
                                    else:
                                        tool_context += (
                                            f"CANCELLATION WINDOW CHECK: This order was placed {_window_check['elapsed_hours']:.1f} "
                                            f"hours ago, which is OUTSIDE the store's {_window_check['window_hours']:.0f}-hour "
                                            "cancellation window. State plainly that it can no longer be cancelled. Do NOT say it "
                                            "'might still' be within the window - the timestamps prove it isn't.\n"
                                        )
                            except Exception as _wce:
                                logger.warning(f"[Agent] Cancellation window check failed (non-blocking): {_wce}")
                    elif order.get("ownership_mismatch"):
                        # A real order was found in Shopify, but the identity
                        # on this conversation doesn't match it - never
                        # weaken that check or disclose the order's details,
                        # but don't lie and claim the lookup itself failed
                        # either (it didn't).
                        mentioned_num = order.get("order_number", "")
                        tool_context += f"ORDER IDENTITY UNVERIFIED: You found order #{mentioned_num} in Shopify, but the email this customer is contacting you from is different from the email used on that order.\n"
                        tool_context += (
                            "Do NOT reveal any details about this order (status, items, cancellation, refund, tracking). "
                            "Do NOT say 'the email on file' or imply the customer did anything wrong - a different contact "
                            "email is completely normal (lost access, ordered for someone else, used another address). "
                            "This comparison already used the email this conversation is verified as coming from - if the "
                            "customer already stated an email in their message (e.g. 'the email I used was X'), that has "
                            "ALREADY been checked and doesn't match, so do NOT ask them to confirm/repeat/resend any email. "
                            "Instead, state plainly that you found the order but the identity doesn't match, and that this "
                            "needs to go to the team for verification before any change can be made - e.g. 'I found order "
                            f"#{mentioned_num}, but the email you're contacting us from doesn't match the one on that order. "
                            "I need our team to verify ownership before I can make any changes.' Do NOT ask a clarifying "
                            "question expecting a new answer here - this is a statement, followed by escalation.\n"
                        )
                        _needs_identity_verification = True
                    elif order.get("error"):
                        mentioned_num = order.get("order_number", "")
                        tool_context += f"ORDER LOOKUP FAILED: Could not retrieve order #{mentioned_num} from Shopify.\n"
                        tool_context += "Do NOT invent product names or pretend to know the order. Tell the customer you're unable to pull up their order right now and ask them to reply with their email address or order confirmation number so a team member can follow up.\n"

                if "orders_by_email" in tool_results:
                    orders = tool_results["orders_by_email"]
                    if orders.get("success") and orders.get("orders"):
                        order_list = [f"#{o.get('order_number')} ({o.get('status')})" for o in orders.get("orders", [])]
                        if len(order_list) > 1:
                            tool_context += f"Customer has multiple orders: {', '.join(order_list)}. Ask which order they mean before answering — do not guess.\n"
                        else:
                            tool_context += f"Customer's orders: {', '.join(order_list)}\n"
                    elif orders.get("error"):
                        tool_context += "ORDER LOOKUP BY EMAIL FAILED: Do not invent or guess order details. Ask the customer for their order number, or let them know a team member will follow up.\n"

                if "catalog" in tool_results:
                    cat = tool_results["catalog"]
                    if cat.get("success"):
                        titles = cat.get("titles") or []
                        shown = ", ".join(titles[:20])
                        more_note = f" (+{len(titles) - 20} more)" if len(titles) > 20 else ""
                        tool_context += (
                            f"LIVE SHOPIFY CATALOG ({cat.get('count')} active products): {shown}{more_note}\n"
                            "This is the real, current product list from Shopify. Answer directly from it - "
                            "do NOT say you don't have access to the product list. Do NOT invent, omit, or "
                            "describe any product not listed here.\n"
                        )
                    else:
                        tool_context += f"CATALOG LOOKUP: {cat.get('message', 'Could not load the product catalog')}. Do NOT invent a product list — use this message as-is or offer to have a team member help.\n"

                if "inventory" in tool_results:
                    inv = tool_results["inventory"]
                    if inv.get("ambiguous"):
                        tool_context += f"{inv.get('message')} Ask the customer to clarify which product before answering — do not guess or pick one.\n"
                    elif inv.get("success"):
                        tool_context += f"{inv.get('message', 'Available')}\n"
                        # Only ever included when Shopify actually returned it —
                        # never construct or guess a link/image the customer wasn't
                        # given by the live lookup.
                        if inv.get("product_url"):
                            tool_context += f"Product link: {inv['product_url']}\n"
                        if inv.get("image_url"):
                            tool_context += f"Product image: {inv['image_url']}\n"
                        if inv.get("price") is not None:
                            tool_context += f"LIVE Shopify price: {inv['price']}\n"
                        if inv.get("description"):
                            tool_context += f"LIVE Shopify description: {inv['description']}\n"
                        variant_opts = [v.get("options") for v in (inv.get("variants") or []) if v.get("options")]
                        if variant_opts:
                            opt_desc = "; ".join(", ".join(f"{k} {v}" for k, v in opts.items()) for opts in variant_opts)
                            tool_context += f"Available options: {opt_desc}\n"
                        # Explicit source hierarchy for this exact, identified
                        # product: this live Shopify data is authoritative for
                        # price/description/attributes — it overrides anything
                        # KNOWLEDGE BASE below says about the same product if
                        # the two ever disagree (e.g. an older RAG-imported
                        # price). Any attribute the customer asks about that
                        # isn't in this data or in KNOWLEDGE BASE must be
                        # answered as unverified — never inferred from what
                        # the material/category "usually" means (Organic
                        # Cotton does NOT imply soft, breathable, durable, or
                        # warm unless that word is actually written above).
                        tool_context += (
                            "This live Shopify data is the AUTHORITATIVE source for this product's price, "
                            "description, and attributes — it overrides KNOWLEDGE BASE below if they conflict. "
                            "Only state material/fit/quality/softness/durability/warmth/popularity claims that "
                            "are explicitly written above. If the customer asks about an attribute not shown "
                            "here (e.g. softness, durability, warmth) and it isn't written above, say you don't "
                            "have verified information about it rather than guessing or inferring it from the "
                            "material name.\n"
                        )
                    else:
                        tool_context += f"INVENTORY LOOKUP: {inv.get('message', 'Could not verify inventory')}. Do NOT guess stock levels — use this message as-is or offer to have a team member confirm.\n"

                if "recommendations" in tool_results:
                    rec = tool_results["recommendations"]
                    if rec.get("ambiguous"):
                        tool_context += f"{rec.get('message')} Ask the customer to clarify which product before recommending anything — do not guess.\n"
                    elif rec.get("no_pairing_data"):
                        tool_context += f"{rec.get('message')} Do NOT invent a complementary/pairing suggestion — say this honestly, exactly as given.\n"
                    elif rec.get("no_candidates"):
                        tool_context += f"{rec.get('message')} Do NOT invent a similar product — there genuinely isn't a confident match.\n"
                    elif rec.get("needs_clarification"):
                        tool_context += (
                            f"TOO MANY MATCHES for '{rec.get('category')}' ({rec.get('match_count')} products) — "
                            f"do not list them. Ask the customer this exact clarifying question instead: {rec.get('message')}\n"
                        )
                    elif rec.get("success") and rec.get("recommendations"):
                        tool_context += f"{rec.get('message')}\n"
                        for r in rec["recommendations"]:
                            line = f"  - {r['title']}"
                            if r.get("price"):
                                line += f" (${r['price']})"
                            line += f" — {'in stock' if r.get('available') else 'currently out of stock'}"
                            if r.get("matching_reason"):
                                line += f" — why it's suggested: {r['matching_reason']}"
                            if r.get("description"):
                                line += f" — description: {r['description']}"
                            if r.get("product_url"):
                                line += f" — link: {r['product_url']}"
                            tool_context += line + "\n"
                        tool_context += (
                            "Only mention the products listed above with their actual reason/availability/link "
                            "exactly as given — do not invent additional recommendations, prices, or links. "
                            "CRITICAL: only state material, warmth, fit, fabric, or other physical/quality attributes "
                            "that are explicitly written in the \"description\" field above for that exact product — "
                            "if a product has no description field, do NOT invent one (no \"soft\", \"breathable\", "
                            "\"relaxed fit\", \"Merino wool\", etc. unless that exact word appears in its description). "
                            "Never claim \"customers also bought\" style behavioral claims not present here.\n"
                        )
                    else:
                        tool_context += f"RECOMMENDATION LOOKUP: {rec.get('message', 'Could not look up recommendations')}. Do NOT invent a recommendation — use this message as-is or offer to have a team member help.\n"

            # 4. Return/Exchange Action Layer — runs identically for chat and
            # Gmail. Chat used to skip this entirely (relying on the main
            # structured-response "intent" field the model self-reports),
            # which meant chat customers could be told a refund/cancel/
            # address-change request was "sent to our team" when no action
            # row was ever created. Real eligibility check + staging must run
            # on every channel — this is the only source of truth for
            # whether an action actually exists.
            action_context = ""
            action_taken = None
            from src.services.intent_detector import intent_detector as _intent_detector, NO_ACTION as _NO_ACTION
            # A detected catalog question ("what products do you sell?") can
            # never plausibly be a return/refund/cancel/exchange/address-change
            # request - skip the classification call entirely rather than pay
            # for (and wait on) an LLM call whose answer is already known, same
            # reasoning already applied to the RAG skip above. This is on the
            # critical path of every request, so on a currently-degraded
            # Mistral key this alone can cost up to this client's full timeout
            # before falling back to keyword matching.
            # Durable pending-action lookup: a real actions-table row for
            # this ticket, not a guess. Its EXISTENCE is what tells detect()
            # below whether there's anything for a short reply to possibly
            # be confirming; the model then judges what the customer's own
            # wording means given that fact using its normal language
            # understanding, not a fixed phrase list. Only used when
            # exactly one is active - two pending actions for two different
            # orders is genuine ambiguity ("multiple orders" safety
            # requirement), never resolved by guessing "the latest one".
            _pending_action_context = None
            if ticket_id and not _is_catalog_query:
                try:
                    _pending_actions = await return_actions.find_pending_actions_for_ticket(ticket_id)
                    if len(_pending_actions) == 1:
                        _pa = _pending_actions[0]
                        _pending_action_context = {
                            "action_type": _pa.get("action_type"),
                            "order_number": _pa.get("order_number") or _pa.get("order_id"),
                            "status": _pa.get("status"),
                        }
                except Exception as _pae:
                    logger.warning(f"[Agent] Pending-action lookup failed (continuing without it): {_pae}")

            _intent_result = _NO_ACTION if _is_catalog_query else await _intent_detector.detect(
                query, pending_action_context=_pending_action_context
            )

            if _intent_result.has_action and not _intent_result.order_id and ticket_id:
                # Conversation-history rule: an order number the customer
                # already gave in an EARLIER message of this same ticket
                # must not be re-asked for just because their current
                # message (e.g. "the new address should be X") doesn't
                # repeat it. detect() only sees this one message - reuse the
                # same ticket.detected_order_id field STAGE 1.6/1.8 in
                # message_processor.py already tracks and preserves across
                # turns for exactly this reason, instead of asking again.
                # Deterministic lookup, not an LLM guess - never invents an
                # order number that was never actually given.
                try:
                    from src.lib.supabase_client import supabase_select as _sel3
                    _t_rows2 = _sel3("tickets", {"id": f"eq.{ticket_id}"})
                    _prior_order_id = _t_rows2[0].get("detected_order_id") if _t_rows2 else None
                    if _prior_order_id:
                        _intent_result.order_id = str(_prior_order_id)
                        logger.info(f"[ReturnActions] Reused order #{_prior_order_id} from conversation history for {_intent_result.action_type}")
                except Exception as _oe:
                    logger.warning(f"[Agent] Could not backfill order_id from conversation history (continuing without it): {_oe}")
            if _intent_result.has_action:
                logger.info(f"[ReturnActions] Intent detected: {_intent_result.action_type} (order={_intent_result.order_id}, source={_intent_result.source})")
                action_result = await return_actions.handle_return_intent(
                    query=query,
                    customer_info=customer_info,
                    existing_tool_results=tool_results,
                    tenant_id=tenant_id,
                    brand_id=store_id,
                    ticket_id=ticket_id,
                    intent_result=_intent_result,
                    on_progress=on_progress,
                )
                action_context = action_result.get("action_context", "")
                logger.info(f"[ReturnActions] Action context: {action_context[:200] if action_context else 'EMPTY'}")
                tool_results["return_action"] = action_result
                action_taken = action_result.get("staged")
            else:
                logger.info(f"[ReturnActions] No action intent (source={_intent_result.source})")

            # 1. RAG Retrieval - brand_knowledge_service.get_brand_context() is scoped
            # correctly by brand_id via match_brand_rag_chunks. rag_engine.get_relevant_context's
            # tenant-scoped path calls match_tenant_rag_chunks, which references a
            # tenant_id column migration 006 dropped from rag_chunks - that RPC always
            # errors, is silently swallowed, and falls through to an UNSCOPED
            # cross-tenant search every single time. Do not switch back to rag_engine
            # here without first fixing that RPC/table mismatch.
            # Runs only when nothing else already answered this question:
            # skipped for a detected catalog question (Shopify is already the
            # authoritative structured answer), and skipped whenever any
            # Shopify/order/inventory/recommendation tool above already
            # produced a result (tool_results non-empty) - product, price,
            # inventory, variant, order-status, and order-action questions
            # never depend on an embedding call at all. Deterministic action
            # decisions (cancel/refund/exchange eligibility) have their own,
            # separate policy-evidence lookup in return_actions_integration.py
            # via actions_manager.get_custom_policy_text() - completely
            # independent of this rag_context, so skipping it here never
            # weakens those decisions.
            _needs_rag = bool(store_id) and not _is_catalog_query and not tool_results
            if _needs_rag:
                await _emit("kb_check", "Checking knowledge base…")
            # Isolation: a 429/timeout/exception anywhere in retrieval must
            # never take down the whole reply - get_brand_context() already
            # swallows internally and returns "" on any failure, but this
            # call site catches defensively too so a future change there
            # can't reopen an unhandled-exception path through the agent.
            try:
                rag_context = await brand_knowledge_service.get_brand_context(store_id, query) if _needs_rag else ""
            except Exception as _rag_err:
                logger.error(f"[Agent] RAG retrieval raised unexpectedly (isolated, continuing without it): {_rag_err}")
                rag_context = ""
            logger.info(f"[Agent] RAG context retrieved: {len(rag_context)} chars")

            # 5. Response Generation
            # Only announce a policy check when no live Shopify tool ran and we
            # actually have knowledge-base content to ground the answer in —
            # otherwise this is just "Preparing your answer…" like any other
            # query with nothing brand-specific to check.
            if not tool_results and rag_context:
                await _emit("policy_check", "Checking our policies…")
            await _emit("preparing", "Preparing your answer…")
            system_prompt = self._construct_v3_prompt(customer_info, rag_context, sizing_context, tool_context, action_context, brand_name=_brand_name, agent_name=_agent_name, style_block=_style_block)

            # Defensive check - ensure at least one AI provider is configured
            if not ai_provider_manager.has_providers:
                logger.error("No AI providers configured - API key(s) may be missing")
                return self._get_fallback_response("No AI providers configured", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            same_prompt_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Customer: {query}"}
            ]
            try:
                # Same prompt/context/temperature on every attempt — failover only
                # changes which API key (and its paired model) serves the request.
                response, provider_label, _model, _ai_usage = await ai_provider_manager.create_chat_completion(
                    messages=same_prompt_messages,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
            except AllProvidersFailedError as api_error:
                logger.error(f"[Agent] All AI providers failed: {api_error}")
                return self._get_provider_failure_response(
                    brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature,
                    send_customer_fallback=_provider_outage_fallback_enabled,
                    provider_attempts=api_error.attempts,
                )

            raw_content = response.choices[0].message.content
            if not raw_content:
                logger.error("Empty response from API")
                return self._get_fallback_response("Empty API response", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            try:
                # Clean up response - remove markdown code blocks if present
                clean_content = raw_content.strip()
                if clean_content.startswith("```json"):
                    clean_content = clean_content[7:]
                if clean_content.startswith("```"):
                    clean_content = clean_content[3:]
                if clean_content.endswith("```"):
                    clean_content = clean_content[:-3]
                clean_content = clean_content.strip()

                structured = json.loads(clean_content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}. Raw content: {raw_content[:500]}")
                return self._get_fallback_response(f"JSON parse error: {str(e)}", brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

            # 4. Confidence Calculation - Be more lenient
            sentiment = sentiment_analyzer.analyze_sentiment_detailed(query)

            # Start higher and be less aggressive with penalties
            confidence = 0.80
            if not rag_context: confidence -= 0.15  # Reduced penalty
            if sentiment["label"] == "negative": confidence -= 0.10  # Reduced penalty

            # Boost for standard, low-risk intents
            if structured.get("intent") in ["order_status_inquiry", "shipping_inquiry", "sizing_inquiry", "product_inquiry"] and structured.get("risk_level") == "low":
                confidence += 0.10

            # Ensure minimum confidence of 30% if we got a valid response
            confidence_out_of_100 = int(max(0.30, min(1, confidence)) * 100)
            structured["confidence_score"] = confidence_out_of_100

            # 5. Escalation Thresholds (more lenient)
            if confidence_out_of_100 < 30:
                logger.warning(f"Low confidence: {confidence_out_of_100}%. Still sending response.")
                structured["status"] = "auto_resolved"  # Send anyway
            elif confidence_out_of_100 < 70 or structured.get("risk_level") == "high":
                structured["status"] = "escalated"
                structured["escalate"] = True
            else:
                structured["status"] = "auto_resolved"

            # 5b. Safety backstop: never let a false "action completed" claim through
            structured = _enforce_no_unconfirmed_action_success(structured)

            # 5b2. Safety backstop: an explicit "let me talk to a human"
            # request must always actually escalate, regardless of what the
            # model decided.
            structured = _enforce_human_handoff_request(structured, query)

            # 5c. Safety backstop: never let a specific product/price/
            # availability claim through when the live Shopify lookup
            # couldn't resolve to exactly one product.
            structured = _enforce_no_ambiguous_product_claim(structured, tool_results.get("inventory"))

            # 5d. Safety backstop: never let the model present a recommended
            # product or invented pairing when the live lookup didn't
            # actually produce a confident, grounded candidate.
            structured = _enforce_no_ungrounded_recommendation(structured, tool_results.get("recommendations"))

            # 5e. Safety backstop: a safe identity-verification response
            # (order found, email doesn't match, protected details withheld)
            # is a complete, self-contained reply — not a request for
            # merchant action. See the function's own docstring for the
            # exact contradiction this closes.
            structured = _enforce_no_escalation_for_safe_identity_verification_response(
                structured, _needs_identity_verification
            )

            # 6. Signature Enforcement - Make it natural, not robotic. Falls
            # back to the neutral idiom "there" ("Hey there,") - never a
            # placeholder treated as a real name - when none is known.
            name = (_known_customer_name(customer_info.get("name")) or "there").split()[0]
            reply = _strip_em_dash(structured.get("reply_body", ""))
            structured["reply_body"] = reply

            # Post-process: ensure each sentence is on its own line for readability
            # Split on sentence endings and add newlines
            import re as regex_module
            sentences = regex_module.split(r'([.!?])\s+', reply)
            if len(sentences) > 1:
                formatted_parts = []
                for i in range(0, len(sentences)-1, 2):
                    sent = sentences[i].strip()
                    punct = sentences[i+1] if i+1 < len(sentences) else ''
                    if sent:
                        formatted_parts.append(sent + punct)
                reply = '\n'.join(formatted_parts)

            # For email: add a greeting, but only if the model's own reply
            # doesn't already open with one — Reply Style presets like
            # Professional/Premium instruct the model to write "Dear {name},"
            # style openings, and _reply_already_has_greeting() recognizes
            # any greeting-shaped opening (not just one containing this
            # specific `name`), so a generic "Hey!"/"Hello!" the model wrote
            # on its own is never duplicated regardless of what `name`
            # resolved to. Chat skips this entirely — widget is already
            # mid-conversation.
            if not _is_chat:
                if reply and not _reply_already_has_greeting(reply):
                    structured["reply_body"] = f"Hey {name},\n\n{reply}"

            # Merchant-set signature wins verbatim; otherwise fall back to the
            # generated "- {agent_name}\n{brand_name}" sign-off (plain hyphen,
            # never an em dash - see the GLOBAL no-em-dash rule above).
            if _email_signature:
                if _email_signature not in structured["reply_body"]:
                    structured["reply_body"] += f"\n\n{_email_signature}"
            elif _agent_name not in structured["reply_body"]:
                structured["reply_body"] += f"\n\n- {_agent_name}\n{_brand_name}"

            # Attach order_data for widget card display
            _os = tool_results.get("order_status", {})
            if _os.get("success"):
                _fs = _os.get("status") or "pending"
                if _os.get("cancelled_at"):
                    _widget_status = "cancelled"
                elif _fs == "fulfilled":
                    _widget_status = "fulfilled"
                elif _fs in ("partial", "unfulfilled"):
                    _widget_status = "processing"
                else:
                    _widget_status = "pending"
                _fin = _os.get("financial_status") or ""
                _payment_status = "refunded" if "refund" in _fin else ("paid" if _fin == "paid" else "pending")
                structured["order_data"] = {
                    "orderNumber": str(_os.get("order_number", "")),
                    "items": [
                        {"name": i.get("title", ""), "quantity": i.get("quantity", 1), "price": i.get("price", "")}
                        for i in _os.get("items", [])
                    ],
                    "status": _widget_status,
                    "paymentStatus": _payment_status,
                    "cancelledAt": _os.get("cancelled_at"),
                }

            # The real staging outcome (or None if no action was created) —
            # callers must use this, not the "intent"/"status" fields above,
            # to decide whether to tell the customer an action was staged.
            structured["action_taken"] = action_taken

            structured["model_used"] = _model
            # Real per-call usage from the provider layer (tokens may be None
            # if the provider's response didn't include a usage block — never
            # fabricated). Callers that persist conversation records (e.g. the
            # email worker's _log_conversation) can read this to populate
            # ai_conversations.tokens_used/latency_ms instead of leaving them
            # NULL. Only covers this one model call, not intent_detector's or
            # email_guardian's separate (uninstrumented) calls.
            structured["ai_usage"] = _ai_usage
            # Only the real, model-generated path sets this — both
            # _get_provider_failure_response (all providers exhausted) and
            # _get_fallback_response (empty response / JSON parse error /
            # any other exception) return the same canned "having trouble"
            # text without it, so callers gating AI-reply quota on this flag
            # never charge a customer's trial/plan quota for a failed call.
            structured["ai_reply_generated"] = True
            if _needs_identity_verification:
                structured["needs_identity_verification"] = True
            # No "draft_ready" emit here — message_processor.py already logs
            # its own "draft_ready"/"Draft ready" event right after this call
            # returns, and a further "needs_review"/"Draft ready for your
            # team to review" event once it knows the ticket won't be
            # auto-sent. Emitting a third, overlapping event here duplicated
            # both in the Activity timeline (see test_no_duplicate_activity_events.py).
            return structured

        except Exception as e:
            import traceback
            logger.error(f"V3 Agent Error: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return self._get_fallback_response(str(e), brand_name=_brand_name, agent_name=_agent_name, email_signature=_email_signature)

    def _construct_v3_prompt(self, customer_info: Dict[str, Any], rag_context: str, sizing_context: str, tool_context: str = "", action_context: str = "", brand_name: str = "our store", agent_name: str = "Luna", style_block: str = "") -> str:
        # Computed as a plain local variable, not inlined into the f-string
        # below - see _UNKNOWN_NAME_PROMPT_TEXT's comment for why.
        _customer_name_line = _known_customer_name(customer_info.get('name')) or _UNKNOWN_NAME_PROMPT_TEXT
        # Chat-specific formatting used to be smuggled into the customer's
        # own message text as a "[CHAT MODE ...]" prefix - that string then
        # got persisted verbatim wherever the raw query is stored (e.g.
        # actions.original_message, shown to merchants on the Escalations
        # page as if the customer had typed it). Lives in the prompt itself
        # now, driven by the already-set customer_info["channel"], never
        # mixed into message content.
        _chat_formatting_rule = (
            "\n- This is a live chat - reply in 1 to 3 short sentences, conversational tone, no bullet points"
            if customer_info.get("channel") == "chat" else ""
        )
        order_critical = (
            "\n⚠ LIVE DATA FROM SHOPIFY — USE ONLY THESE DETAILS:\n"
            "• Reference ONLY the product names, quantities, and totals listed below.\n"
            "• Do NOT invent or assume any product names, prices, or details not listed here.\n"
        ) if tool_context.strip() else "\n(No order data fetched — if the customer asks about an order, ask for their order number.)\n"
        return f"""
        You are {agent_name}, the AI customer support employee for {brand_name}. NOT a corporate bot — sound like a real person.

        REPLY STYLE (wording and tone only — this never changes facts, actions, or policy):
        {style_block}

        RULES:
        - Never use words like "algorithm", "system", "deterministic", "variant"
        - Always sound human and friendly
        - NEVER refer to products not listed in ORDER INFO below
        - NEVER say "let me check", "I'll look into it", "give me a moment", or anything that implies you will do something later — respond fully RIGHT NOW based only on what is in ORDER INFO
        - If ORDER INFO shows a lookup failure, apologize you can't see the order and ask the customer for their order confirmation email or contact details so someone can follow up

        TRACKING RULES (CRITICAL — follow exactly):
        - If ORDER INFO gives you a shipped day + status, answer in plain English using both — e.g. "Your order shipped Tuesday and it's in transit, should arrive in a couple days."
        - Do NOT say "check your email for tracking." Do NOT paste the raw tracking URL as your main answer.
        - If a tracking URL is available, you MAY offer it as a secondary option AFTER the plain-English status: "You can also track it here: [url]"
        - If status is unknown, say it was recently shipped and tracking should update within 24 hours.
        - If the order hasn't shipped yet, say it's being prepared and hasn't shipped.

        FORMATTING RULES:
        - NEVER use em dashes (—) or en dashes (–) anywhere in your response
        - NEVER use hyphens to join or separate clauses in a sentence
        - Use a comma or start a new sentence instead of a dash
        - WRONG: "I'd love to help—could you share your order number?"
        - RIGHT: "I'd love to help! Could you share your order number?"{_chat_formatting_rule}

        KNOWLEDGE BASE (authoritative ONLY for claims explicitly present in the text below —
        do NOT invent material, fit, texture, quality, popularity, durability, price,
        availability, or marketing claims that aren't written here. If this section is empty,
        that does NOT mean the store has no such policy — it may just mean we couldn't
        confirm it right now. NEVER tell a customer the store "doesn't have" a return,
        refund, shipping, or other policy just because this section is empty. Instead say
        you don't have that specific detail confirmed and you'll get them a confirmed
        answer / have the team follow up):
        {rag_context}

        SIZING:
        {sizing_context}

        ORDER INFO:{order_critical}
        {tool_context}

        RETURN/EXCHANGE STATUS:
        {action_context}

        CUSTOMER:
        Name: {_customer_name_line}
        Email: {customer_info.get('email')}
        History: {customer_info.get('history', 'New customer')}

        ACTION RULES (IMPORTANT - DO NOT AUTO-CONFIRM):
        1. For refunds, returns, exchanges, cancellations, or address changes - NEVER say it's done
        2. Only say "I've prepared your request and sent it to our team for confirmation. You'll receive an
           update shortly!" when RETURN/EXCHANGE STATUS below actually shows "ACTION STAGED FOR APPROVAL" -
           that phrasing means a real request was just created. If RETURN/EXCHANGE STATUS is empty, or no
           order was found, or something the customer needs to supply (order number, the actual new address,
           etc.) is still missing, NOTHING has been submitted yet - do NOT claim it was "prepared" or "sent
           to our team". Instead plainly ask for whatever is genuinely still missing.
        3. NEVER use words like "processed", "approved", "completed", "done", "exchanged"
        4. If (and only if) a request genuinely was staged per rule 2, describe it as "being reviewed" or
           "sent for confirmation" - never as done.
        5. If not eligible - be honest and offer alternatives
        6. NEVER invent a specific policy detail - a time window ("within 2 hours of ordering"),
           a cutoff, a fee, a percentage, a return/exchange window, a restocking fee, or any other
           concrete rule - unless that exact detail appears in KNOWLEDGE BASE or RETURN/EXCHANGE
           STATUS below. If asked how a policy works and no grounded detail is available, say
           you'll need to confirm the specifics rather than guessing a number.
        7. For an exchange: RETURN/EXCHANGE STATUS below already reflects LIVE Shopify stock/price -
           never say a size/color/product is available or unavailable except exactly as stated there.
           Never invent a replacement item, variant, or price difference that isn't given to you.
        8. If RETURN/EXCHANGE STATUS says a request is already pending, approved, or completed - do
           NOT say a new request was sent. Reflect the real, current status truthfully instead.
        9. Only greet the customer by name if CUSTOMER Name above gives you a real one. If it says
           "Not known", use a neutral opening instead - never write "Dear There" or greet them by
           any placeholder word as if it were their real name.

        COMMON SENSE — READ ORDER STATUS BEFORE RESPONDING:
        - If ORDER DATA says "CANCELLED" — do NOT offer cancellation. Tell them plainly it's already cancelled and can't be cancelled again, using the real order data (not a one-line brush-off) so they know their request was actually handled.
        - If ORDER DATA says "refunded" or "partially_refunded" — do NOT offer a refund. Acknowledge it is refunded already.
        - If ORDER DATA says "fulfilled" (shipped) AND a tracking URL is present — share that URL directly in your reply. Never say "check your email".
        - If ORDER DATA says "fulfilled" (shipped) — do NOT offer cancellation or address change. Offer reship/refund if relevant.
        - Never suggest an action that the order state makes impossible.

        RESPONSE (JSON only):
        {{
            "intent": "what they want (refund_request|return_request|exchange_request|cancellation_request|address_change|order_status_inquiry|shipping_inquiry|sizing_inquiry|product_inquiry|general_inquiry)",
            "sentiment": "positive|neutral|negative",
            "risk_level": "low|medium|high",
            "escalate": false,
            "action_detected": "refund|return|exchange|cancel_order|change_address|none",
            "confidence_score": 80,
            "reply_body": "your friendly response - NEVER confirm actions are done, only say they're being reviewed",
            "suggested_actions": []
        }}

        Include "confidence_score" as an integer 0-100 reflecting how certain you are the reply fully resolves the issue.
        95-100: definitive answer. 80-94: quite sure. 60-79: mostly sure. Below 60: escalate.
        """

    def _get_fallback_response(self, error: str, brand_name: str = "", agent_name: str = "Luna", email_signature: str = None) -> Dict[str, Any]:
        logger.error(f"Using fallback response due to error: {error}")
        sign_off = email_signature or (f"- {agent_name}\n{brand_name}" if brand_name else f"- {agent_name}")
        return {
            "intent": "general_inquiry",
            "sentiment": "neutral",
            "risk_level": "medium",
            "confidence_score": 40,
            "escalate": True,
            "escalation_reason": f"System error: {error}",
            "reply_body": f"Hey there!\n\nThanks for reaching out. I'm having a bit of trouble processing your message right now, but I've flagged this for my team to take a look.\n\nWe'll follow up once it's reviewed.\n\n{sign_off}",
            "status": "escalated"
        }

    def _get_provider_failure_response(
        self, brand_name: str = "", agent_name: str = "Luna", email_signature: str = None,
        send_customer_fallback: bool = False, provider_attempts: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Every configured AI provider (all Mistral keys, all Groq fallback keys)
        failed for this request — distinct from _get_fallback_response so the
        escalation card reads as a known, temporary quota problem, not a generic
        "system error". provider_outage=True lets callers (Test Luna) detect this
        specific case without string-matching escalation_reason.

        Never auto-sends Luna's own wording during an outage — there is no
        real generated reply to send, only a canned placeholder, and
        claiming "I've flagged this for my team" is not something this
        function alone can guarantee actually happened for this message.
        The customer's message is still saved and the ticket still
        escalates exactly as before (both handled by the callers of this
        function, unchanged); only whether a customer-facing reply_body is
        produced at all is gated here, on the brand's own explicit
        provider_outage_fallback_enabled opt-in (default off). When off,
        reply_body is empty — every existing caller (email routing, chat
        widget) already treats an empty/falsy reply_body as "nothing to
        send", which is exactly the desired behavior: wait for a human."""
        reply_body = ""
        if send_customer_fallback:
            sign_off = email_signature or (f"- {agent_name}\n{brand_name}" if brand_name else f"- {agent_name}")
            reply_body = f"{PROVIDER_OUTAGE_CUSTOMER_MESSAGE}\n\n{sign_off}"
        return {
            "intent": "general_inquiry",
            "sentiment": "neutral",
            "risk_level": "medium",
            "confidence_score": 40,
            "escalate": True,
            "provider_outage": True,
            # Per-provider {"label", "reason"} entries from AllProvidersFailedError —
            # lets a caller (message_processor.py's provider-outage retry queue)
            # classify rate-limit/quota/timeout as retryable vs. an unclassified
            # reason (e.g. a bad key/model) as fast-fail, without re-deriving
            # that from raw exception text itself.
            "provider_attempts": provider_attempts or [],
            "escalation_reason": PROVIDER_OUTAGE_REASON,
            "reply_body": reply_body,
            "ai_reply_generated": False,
            "status": "escalated",
        }

    async def generate_channel_appropriate_response(self, query: str, customer_info: Dict[str, Any], channel: str, tenant_id: Optional[str] = None, store_id: Optional[str] = None, ticket_id: Optional[str] = None, on_progress: Optional[Callable[[str, str], Awaitable[None]]] = None) -> Dict[str, Any]:
        # on_progress was previously dropped here — process_customer_query's
        # real dispatch-point emissions (order lookup, eligibility check,
        # policy verified, ...) already existed and were already forwarded
        # into return_actions_integration, but only the chat widget's
        # streaming path ever passed a callback through this method. The
        # email/webform pipeline (message_processor.py) is the other caller
        # of this method and now passes one too, to persist real events.
        return await self.process_customer_query(query, customer_info, tenant_id=tenant_id, store_id=store_id, ticket_id=ticket_id, on_progress=on_progress)

customer_success_agent = CustomerSuccessAgent()
