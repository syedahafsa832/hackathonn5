"""
AI Intent Detector
==================
Replaces all static keyword/regex action detection with a single Mistral LLM call.
Detects action type, order ID, and address text from any customer message phrasing.
Fail-open: keyword fallback fires if LLM is unavailable.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Dict

from openai import OpenAI

from src.services.mistral_limiter import call_with_limit

logger = logging.getLogger(__name__)

ADDRESS_PARSE_PROMPT = """Parse this address text into structured fields. Respond with valid JSON only.

Required JSON format:
{{"address1": "...", "address2": "...", "city": "...", "province": "...", "zip": "...", "country": "..."}}

Rules:
- address1: street number + street name (e.g. "123 Main St")
- address2: apartment/suite/unit if present, else ""
- city: city name
- province: state/province abbreviation (e.g. "NY", "CA") or full name
- zip: postal/zip code
- country: 2-letter ISO code (e.g. "US", "GB", "PK") — infer from context, default "US"
- Use "" for any field that cannot be determined

Address text:
{address_text}"""

INTENT_PROMPT = """Classify this customer support message. Respond with valid JSON only — no explanation.

action_type options:
- "restore_order" — customer accidentally cancelled their order and wants it back/restored/reactivated. Key phrases: "mistakenly canceled", "accidentally canceled", "please get it back", "restore my order", "undo my cancellation", "cancel was a mistake", "reactivate". IMPORTANT: classify as restore_order even if the message contains the word "cancel" — if the customer wants to UN-cancel, it is restore_order NOT cancel.
- "address_change" — customer wants to change or update their delivery/shipping address
- "reship" — package not received, lost, stolen, missing, marked delivered but not arrived
- "cancel" — customer wants to CANCEL an active order they no longer want, before it ships (NOT restore — they want it stopped; NOT a return, which is for an item already received)
- "return" — customer wants to send an item they already RECEIVED back for a refund. Key phrases: "return this", "send it back", "want to return", "return my order", "return item X", "how do I return", "changed my mind" (about a received item), "don't want this anymore" (about a received item). Does NOT include wanting a different size/color/product instead — that is "exchange".
- "exchange" — customer wants to swap a received item for a DIFFERENT size, color, or product (not just their money back). Key phrases: "exchange this", "wrong size", "doesn't fit", "need a bigger/smaller one", "can I swap this", "can I get another size/color", "I ordered the wrong size", "change this from M to L", "same item in another size". If the customer names or implies a specific replacement (a size, a color, a different product), it is "exchange", not "return".
- "refund" — customer wants money back for a reason OTHER than sending a physical item back (e.g. billed incorrectly, price adjustment, item damaged/wrong and no replacement wanted). If the customer talks about sending something back, use "return" instead. If the customer wants a different size/color/product, use "exchange" instead.
- "none" — general question, tracking inquiry, product question, or a POLICY question about how returns/exchanges/refunds/cancellations work in general (e.g. "what is your return policy", "what is your cancellation policy", "how long do I have to return something", "do you offer exchanges") — asking ABOUT a policy is never an action request, even if it uses the words return/exchange/refund/cancel. A past-tense statement or question about a cancellation that already happened ("why was my order cancelled?", "my order was cancelled") or a hypothetical ("what happens if I cancel?") is also "none" — the customer is not asking to cancel anything right now. Only classify as return/exchange/refund/cancel when the customer is asking to DO something to their own order right now.

Required JSON format:
{{"action_type": "...", "order_id": "...", "raw_address": "...", "exchange_target": "...", "confidence": 0.0}}

Rules:
- order_id: order number digits only (e.g. "1006"), or null if not mentioned
- raw_address: new address text verbatim if action_type is address_change, else null
- exchange_target: ONLY when action_type is "exchange" — the replacement the customer described, verbatim or lightly normalized (e.g. "size L", "black", "the hoodie in blue", "a different product"). null otherwise or if not yet stated.
- confidence: 0.0–1.0 reflecting how certain you are

Examples:
- "I want to know your return policy" -> {{"action_type": "none", ...}}
- "How long do I have to return things?" -> {{"action_type": "none", ...}}
- "I want to return order #1234" -> {{"action_type": "return", "order_id": "1234", ...}}
- "Can I exchange this for a size L?" -> {{"action_type": "exchange", "exchange_target": "size L", ...}}
- "Can I exchange this?" (no size/color/product mentioned yet) -> {{"action_type": "exchange", "exchange_target": null, ...}}
- "I actually want the black one in L" (replying to an earlier exchange conversation) -> {{"action_type": "exchange", "exchange_target": "black, size L", ...}}

Customer message:
{message}"""

# Short fragment fallback — intentionally broad so any phrasing is caught
# restore_order must come BEFORE cancel — "mistakenly canceled" matches both
_RESTORE_FRAGS = [
    'get it back', 'restore', 'reactivate', 'un-cancel', 'undo cancel',
    'cancel my cancellation', 'mistakenly canceled', 'mistakenly cancelled',
    'accidentally canceled', 'accidentally cancelled',
    'please bring it back', 'get my order back', 'undo', 'reverse the cancel',
    "i didn't mean to cancel", "cancel was a mistake", "didn't mean to cancel",
    'bring it back', 'please activate', 'get it active',
]
_CANCEL_FRAGS = ['cancel', 'no longer want', 'changed my mind', "don't want", 'dont want', 'stop my order', 'stop the order']
# Exchange must be checked BEFORE return/refund — "wrong size" or "swap"
# phrasing also often contains words that would otherwise match the broader
# return/refund fragment lists (e.g. "send back the wrong size for a L").
_EXCHANGE_FRAGS = [
    'exchange', 'wrong size', "doesn't fit", 'doesnt fit', 'too small', 'too big', 'too large',
    'different size', 'other size', 'another size', 'another color', 'another colour',
    'need a bigger', 'need a smaller', 'swap this', 'swap it', 'can i swap',
    'in a different size', 'in another size', 'change this from', 'instead of this size',
]
_RETURN_FRAGS = ['return', 'send it back', 'send this back', 'sent it back', 'ship it back', 'no longer want it']
_REFUND_FRAGS = ['refund', 'money back', 'damaged', 'wrong item', 'get my money']
_ADDRESS_FRAGS = ['address', 'new address', 'delivery address', 'shipping address']
_RESHIP_FRAGS = ['not received', 'never received', 'not arrived', 'never arrived', 'missing', 'lost', 'stolen',
                 'not delivered', 'says delivered', "didn't receive", 'didnt receive', 'havent received',
                 "haven't received", 'never got', 'never came']
# Policy questions must never fall through to an action fragment match just
# because they share a word (e.g. "what is your return policy" contains
# "return"). Checked first in the fallback — if present, always "none".
_POLICY_QUESTION_FRAGS = [
    'return policy', 'exchange policy', 'refund policy', 'how long do i have',
    'what is your policy', "what's your policy", 'how does return', 'how does exchange',
    'how do returns work', 'how do exchanges work',
    # Cancellation-specific: 'cancel' is a single broad fragment below (so
    # "cancel #1012" reliably matches every real phrasing), which means a
    # policy question, a past-tense status statement, or a hypothetical
    # about cancelling would otherwise ALSO match it and get misrouted into
    # the mutation workflow just because they share that word. These are
    # checked first, same as the policy phrases above — if present, always
    # "none", never a cancel action.
    'cancellation policy', 'cancel policy', 'cancelation policy',
    'why was my order cancel', 'why is my order cancel', 'why did you cancel',
    'why was it cancel', 'my order was cancel', 'my order got cancel',
    'order has been cancel', 'order got cancel', 'what happens if i cancel',
    'what if i cancel',
]


@dataclass
class IntentResult:
    action_type: str          # "address_change" | "reship" | "cancel" | "return" | "exchange" | "refund" | "restore_order" | "none"
    order_id: Optional[str]   # order number digits only, or None
    raw_address: Optional[str]
    confidence: float
    source: str = field(default="llm")  # "llm" | "fallback"
    # Only meaningful when action_type == "exchange" — the replacement the
    # customer described (a size, color, or different product), verbatim or
    # lightly normalized. None when action_type != "exchange", or when it is
    # but the customer hasn't said what they want instead yet.
    exchange_target: Optional[str] = field(default=None)
    # Real usage from this LLM call (same shape as ai_provider_manager's
    # usage dict: prompt_tokens/completion_tokens/total_tokens - None, never
    # 0, if the response had no usage block - plus latency_ms). None when
    # source="fallback" (no LLM call was made) or the call raised before a
    # response existed. Not persisted to ai_conversations today - that table
    # logs one row per customer-facing message, and this is a separate,
    # internal classification call - logged instead via the existing
    # "[Intent] LLM -> ..." line so operators can grep it without a schema
    # change.
    usage: Optional[dict] = field(default=None)

    @property
    def has_action(self) -> bool:
        return self.action_type != "none"


NO_ACTION = IntentResult(action_type="none", order_id=None, raw_address=None, confidence=1.0, source="llm")


def _extract_order_id(text: str) -> Optional[str]:
    m = re.search(r'(?:order\s*#?\s*|#)(\d{3,8})', text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{4,6})\b', text)
    return m.group(1) if m else None


# Extracted size/color words — used only to fill exchange_target in the
# keyword fallback (the LLM path extracts this via the prompt itself).
_SIZE_WORDS = ['xs', 'extra small', 'small', 'medium', 'large', 'extra large', 'xl', 'xxl', 'x-large',
               'x-small', 's', 'm', 'l']
_COLOR_WORDS = ['black', 'white', 'red', 'blue', 'green', 'yellow', 'pink', 'purple', 'orange',
                'grey', 'gray', 'brown', 'navy', 'beige', 'maroon', 'teal']


def _extract_exchange_target_fallback(message: str) -> Optional[str]:
    m = message.lower()
    found = [w for w in _SIZE_WORDS + _COLOR_WORDS if re.search(r'\b' + re.escape(w) + r'\b', m)]
    return ", ".join(found) if found else None


def _keyword_fallback(message: str) -> IntentResult:
    """Broad fragment matching — short tokens match across any phrasing."""
    m = message.lower()
    order_id = _extract_order_id(message)

    # Policy questions ("what is your return policy") must never be
    # misread as an action just because they share a word with one —
    # checked before any action fragment.
    if any(f in m for f in _POLICY_QUESTION_FRAGS):
        return IntentResult("none", order_id, None, 0.85, "fallback")

    if any(f in m for f in _ADDRESS_FRAGS):
        addr_match = re.search(
            r'(?:to|at|address[:\s]+|change to|update to)\s+(.{10,120})',
            message, re.IGNORECASE
        )
        raw_addr = addr_match.group(1).strip() if addr_match else None
        return IntentResult("address_change", order_id, raw_addr, 0.7, "fallback")
    if any(f in m for f in _RESHIP_FRAGS):
        return IntentResult("reship", order_id, None, 0.7, "fallback")
    # Check restore BEFORE cancel — "mistakenly canceled" must map to restore_order
    if any(f in m for f in _RESTORE_FRAGS):
        return IntentResult("restore_order", order_id, None, 0.8, "fallback")
    # Exchange BEFORE cancel/return/refund — "wrong size" etc. must not be
    # absorbed by the broader cancel/return fragment lists.
    if any(f in m for f in _EXCHANGE_FRAGS):
        target = _extract_exchange_target_fallback(message)
        return IntentResult("exchange", order_id, None, 0.7, "fallback", exchange_target=target)
    if any(f in m for f in _CANCEL_FRAGS):
        return IntentResult("cancel", order_id, None, 0.7, "fallback")
    if any(f in m for f in _RETURN_FRAGS):
        return IntentResult("return", order_id, None, 0.7, "fallback")
    if any(f in m for f in _REFUND_FRAGS):
        return IntentResult("refund", order_id, None, 0.7, "fallback")
    return IntentResult("none", order_id, None, 0.9, "fallback")


class IntentDetector:
    """Singleton LLM-based intent detector for customer action requests."""

    def __init__(self):
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> Optional[OpenAI]:
        if self._client is None:
            api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                return None
            self._client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1"),
                # max_retries=0: same rationale as ai_provider_manager.py's
                # chat/embedding clients - this detector has no key rotation
                # of its own (single MISTRAL_API_KEY/OPENAI_API_KEY), so an
                # SDK-internal retry on a rate-limited/slow key just doubles
                # this call's wait (up to 2x8s) for the same key, on every
                # single customer query (detect() runs unconditionally in
                # process_customer_query) - directly observed live as
                # "Retrying request to /chat/completions" pushing a request
                # past the frontend's 35s timeout even after the chat/
                # embedding clients were already fixed to max_retries=0.
                # _keyword_fallback() below already covers the failure path.
                max_retries=0,
                timeout=8.0,
            )
        return self._client

    async def detect(self, message: str) -> IntentResult:
        """Detect action intent. Falls back to keyword matching if LLM unavailable."""
        client = self._get_client()
        if not client:
            logger.warning("[Intent] No LLM client — using keyword fallback")
            return _keyword_fallback(message)

        prompt = INTENT_PROMPT.format(message=message[:1500])
        model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
        call_start = time.monotonic()

        try:
            try:
                response = await call_with_limit(lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=160,
                    response_format={"type": "json_object"},
                ))
            except Exception:
                response = await call_with_limit(lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=160,
                ))

            raw_usage = getattr(response, "usage", None)
            usage = {
                "prompt_tokens": getattr(raw_usage, "prompt_tokens", None) if raw_usage else None,
                "completion_tokens": getattr(raw_usage, "completion_tokens", None) if raw_usage else None,
                "total_tokens": getattr(raw_usage, "total_tokens", None) if raw_usage else None,
                "latency_ms": round((time.monotonic() - call_start) * 1000),
                "model": model,
            }

            raw = (response.choices[0].message.content or "").strip()
            data = json.loads(raw)

            action_type = str(data.get("action_type", "none")).lower()
            if action_type not in {"address_change", "reship", "cancel", "return", "exchange", "refund", "restore_order", "none"}:
                action_type = "none"

            raw_order = data.get("order_id")
            order_id = str(raw_order).strip() if raw_order and str(raw_order).strip().isdigit() else None
            if not order_id:
                order_id = _extract_order_id(message)

            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.8))))
            raw_address = data.get("raw_address") or None
            exchange_target = data.get("exchange_target") or None
            if exchange_target is not None:
                exchange_target = str(exchange_target).strip()[:200] or None

            logger.info(
                f"[Intent] LLM → {action_type} conf={confidence:.2f} order={order_id} "
                f"exchange_target={exchange_target!r} tokens={usage['total_tokens']} latency_ms={usage['latency_ms']}"
            )
            return IntentResult(
                action_type, order_id, raw_address, confidence, "llm",
                exchange_target=exchange_target, usage=usage,
            )

        except Exception as e:
            if getattr(e, 'status_code', None) == 429 or "429" in str(e):
                logger.warning("[Intent] Rate limited — keyword fallback")
            else:
                logger.warning(f"[Intent] LLM failed ({e}) — keyword fallback")
            return _keyword_fallback(message)

    async def parse_address(self, raw_address: str) -> Optional[Dict[str, str]]:
        """Parse a raw address string into structured Shopify address fields.
        Returns None if LLM unavailable or parsing fails."""
        client = self._get_client()
        if not client:
            return None

        prompt = ADDRESS_PARSE_PROMPT.format(address_text=raw_address[:500])
        model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")

        try:
            try:
                response = await call_with_limit(lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=150,
                    response_format={"type": "json_object"},
                ))
            except Exception:
                response = await call_with_limit(lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=150,
                ))

            raw = (response.choices[0].message.content or "").strip()
            data = json.loads(raw)

            # Require at least address1 + city to consider it parsed
            if not data.get("address1") or not data.get("city"):
                logger.warning(f"[Intent] Address parse incomplete: {data}")
                return None

            structured = {
                "address1": str(data.get("address1", "")).strip(),
                "address2": str(data.get("address2", "")).strip(),
                "city": str(data.get("city", "")).strip(),
                "province": str(data.get("province", "")).strip(),
                "zip": str(data.get("zip", "")).strip(),
                "country": str(data.get("country", "US")).strip() or "US",
            }
            logger.info(f"[Intent] Address parsed: {structured}")
            return structured

        except Exception as e:
            logger.warning(f"[Intent] Address parse failed ({e})")
            return None


# Module-level singleton
intent_detector = IntentDetector()
