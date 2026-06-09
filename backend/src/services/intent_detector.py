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
from dataclasses import dataclass, field
from typing import Optional, Dict

from openai import OpenAI

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
- "cancel" — customer wants to CANCEL an active order they no longer want (NOT restore — they want it stopped)
- "refund" — customer wants money back, to return an item, exchange, item damaged or wrong
- "none" — general question, tracking inquiry, product question (no financial action needed)

Required JSON format:
{{"action_type": "...", "order_id": "...", "raw_address": "...", "confidence": 0.0}}

Rules:
- order_id: order number digits only (e.g. "1006"), or null if not mentioned
- raw_address: new address text verbatim if action_type is address_change, else null
- confidence: 0.0–1.0 reflecting how certain you are

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
_REFUND_FRAGS = ['refund', 'money back', 'return', 'exchange', 'damaged', 'wrong item', 'get my money']
_ADDRESS_FRAGS = ['address', 'new address', 'delivery address', 'shipping address']
_RESHIP_FRAGS = ['not received', 'never received', 'not arrived', 'never arrived', 'missing', 'lost', 'stolen',
                 'not delivered', 'says delivered', "didn't receive", 'didnt receive', 'havent received',
                 "haven't received", 'never got', 'never came']


@dataclass
class IntentResult:
    action_type: str          # "address_change" | "reship" | "cancel" | "refund" | "none"
    order_id: Optional[str]   # order number digits only, or None
    raw_address: Optional[str]
    confidence: float
    source: str = field(default="llm")  # "llm" | "fallback"

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


def _keyword_fallback(message: str) -> IntentResult:
    """Broad fragment matching — short tokens match across any phrasing."""
    m = message.lower()
    order_id = _extract_order_id(message)
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
    if any(f in m for f in _CANCEL_FRAGS):
        return IntentResult("cancel", order_id, None, 0.7, "fallback")
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
                max_retries=1,
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

        try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=120,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=120,
                )

            raw = (response.choices[0].message.content or "").strip()
            data = json.loads(raw)

            action_type = str(data.get("action_type", "none")).lower()
            if action_type not in {"address_change", "reship", "cancel", "refund", "restore_order", "none"}:
                action_type = "none"

            raw_order = data.get("order_id")
            order_id = str(raw_order).strip() if raw_order and str(raw_order).strip().isdigit() else None
            if not order_id:
                order_id = _extract_order_id(message)

            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.8))))
            raw_address = data.get("raw_address") or None

            logger.info(f"[Intent] LLM → {action_type} conf={confidence:.2f} order={order_id}")
            return IntentResult(action_type, order_id, raw_address, confidence, "llm")

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
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=150,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=150,
                )

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
