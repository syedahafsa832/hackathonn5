"""Inbound email processing for the Cloudflare forwarding channel.

The normal Gmail polling channel remains untouched. This service receives the
raw RFC 5322 message from the Cloudflare Worker, resolves the brand by its
opaque recipient token, applies the existing filter + Guardian stages, and
hands the normalized message to the existing UnifiedMessageProcessor.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from html.parser import HTMLParser
from typing import Any, Dict, Optional

from src.lib.supabase_client import supabase_select, supabase_update
from src.workers.message_processor import message_processor

_TOKEN_RE = re.compile(r"^[a-f0-9]{40}$")
_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def _header(message, name: str) -> str:
    values = message.get_all(name, [])
    return " ".join(str(value).strip() for value in values if value is not None).strip()


def _message_id_tokens(value: str) -> list[str]:
    if not value:
        return []
    tokens = _MESSAGE_ID_RE.findall(value)
    if tokens:
        return tokens
    # Tolerate non-conforming providers that omit angle brackets.
    return [token.strip() for token in value.split() if token.strip()]


def _find_attached_original(message):
    """If a human forwarded an email as an attachment, process the attached
    RFC822 message rather than treating the forwarding employee as the sender."""
    try:
        for part in message.walk():
            if part.get_content_type() != "message/rfc822":
                continue
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                candidate = payload[0]
                if candidate.get("From"):
                    return candidate
    except Exception:
        pass
    return message


def _extract_body(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type().lower()
        if content_type not in ("text/plain", "text/html"):
            continue
        try:
            value = part.get_content()
            if isinstance(value, bytes):
                value = value.decode(part.get_content_charset() or "utf-8", errors="replace")
            if not isinstance(value, str):
                continue
            if content_type == "text/plain":
                plain_parts.append(value)
            else:
                html_parts.append(value)
        except Exception:
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    value = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    (plain_parts if content_type == "text/plain" else html_parts).append(value)
            except Exception:
                continue

    plain = "\n\n".join(part.strip() for part in plain_parts if part.strip()).strip()
    if plain:
        return plain

    if html_parts:
        extractor = _HTMLTextExtractor()
        extractor.feed("\n".join(html_parts))
        return html.unescape(extractor.text()).strip()

    return ""


def parse_forwarded_email(raw: bytes) -> Dict[str, Any]:
    """Parse a forwarded RFC822 message, preserving standard threading headers."""
    if not raw:
        raise ValueError("Empty email message")

    outer = BytesParser(policy=policy.default).parsebytes(raw)
    message = _find_attached_original(outer)

    from_header = _header(message, "From")
    sender_name, sender_email = parseaddr(from_header)
    sender_email = sender_email.strip().lower()
    if not sender_email or "@" not in sender_email:
        raise ValueError("Email does not contain a valid original sender")

    subject = _header(message, "Subject") or "Customer support request"
    body = _extract_body(message)
    if not body:
        # If we found an attached original, try the outer forwarded text as a
        # fallback. Attachment-only support is intentionally not claimed yet.
        body = _extract_body(outer)
    if not body:
        raise ValueError("Email has no readable text body")

    message_id = _header(message, "Message-ID")
    in_reply_to = _header(message, "In-Reply-To")
    references = _header(message, "References")
    reference_ids = _message_id_tokens(references)
    in_reply_to_ids = _message_id_tokens(in_reply_to)

    received_at = None
    date_header = _header(message, "Date")
    if date_header:
        try:
            parsed_date = parsedate_to_datetime(date_header)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            received_at = parsed_date.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            received_at = None

    headers: Dict[str, str] = {}
    for name, value in message.items():
        key = str(name).lower()
        rendered = str(value)
        headers[key] = f"{headers[key]}\n{rendered}" if key in headers else rendered

    return {
        "sender_name": sender_name.strip() or sender_email.split("@", 1)[0],
        "sender_email": sender_email,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to_ids[-1] if in_reply_to_ids else None,
        "references": reference_ids,
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
        "headers": headers,
    }


def forwarding_message_ids(brand_id: str, parsed: Dict[str, Any], raw: bytes) -> tuple[str, str]:
    """Return stable per-brand message and thread IDs for ticket dedup.

    These are internal tResolv identifiers, not Gmail's private thread IDs.
    """
    original_id = parsed.get("message_id") or hashlib.sha256(raw).hexdigest()
    message_digest = hashlib.sha256(f"{brand_id}\0{original_id}".encode()).hexdigest()[:48]
    message_id = f"fwd_{message_digest}"

    refs = parsed.get("references") or []
    root_id = refs[0] if refs else (parsed.get("in_reply_to") or parsed.get("message_id"))
    if root_id:
        thread_source = root_id
    else:
        # Missing RFC Message-ID headers cannot be safely correlated with a
        # later reply. Use this unique message as its own thread rather than
        # guessing based on subject lines.
        thread_source = f"unthreaded:{message_id}"

    thread_digest = hashlib.sha256(f"{brand_id}\0{thread_source}".encode()).hexdigest()[:48]
    return message_id, f"fwd_thread_{thread_digest}"


def _recipient_token(recipient: str) -> Optional[str]:
    """Extract a per-brand token from Cloudflare plus-addressing.

    One exact Cloudflare route handles support@inbound-domain. With
    subaddressing enabled, support+<token>@inbound-domain reaches that same
    route while Cloudflare preserves the plus suffix in message.to.
    """
    if not recipient:
        return None
    address = recipient.strip().strip("<>").lower()
    if "@" not in address:
        return None
    local, recipient_domain = address.rsplit("@", 1)
    expected_domain = os.getenv("INBOUND_EMAIL_DOMAIN", "inbound.tresolv.online").strip().lower()
    if recipient_domain != expected_domain:
        return None
    expected_local = os.getenv("INBOUND_EMAIL_LOCAL_PART", "support").strip().lower()
    if "+" not in local:
        return None
    base_local, token = local.rsplit("+", 1)
    if base_local != expected_local or not _TOKEN_RE.fullmatch(token):
        return None
    return token


def _google_forwarding_confirmation_code(parsed: Dict[str, Any]) -> Optional[str]:
    """Recognize only the forwarding-address verification email from Google."""
    sender = (parsed.get("sender_email") or "").lower()
    subject = (parsed.get("subject") or "").lower()
    if sender not in {"forwarding-noreply@google.com", "mail-noreply@google.com"}:
        return None
    if "forwarding confirmation" not in subject:
        return None
    match = re.search(r"\b(\d{8,10})\b", parsed.get("body") or "")
    return match.group(1) if match else None

async def process_forwarded_email(raw: bytes, envelope_to: str) -> Dict[str, Any]:
    """Resolve a Cloudflare catch-all address and run the standard email pipeline."""
    token = _recipient_token(envelope_to)
    if not token:
        return {"accepted": False, "status": "invalid_recipient"}

    brands = await asyncio.to_thread(
        supabase_select,
        "brands",
        {
            "inbound_email_token": f"eq.{token}",
            "inbound_email_enabled": "eq.true",
            "email_ingestion_mode": "eq.forwarding",
            "limit": "1",
        },
    )
    if not brands:
        return {"accepted": False, "status": "unknown_recipient"}

    brand = brands[0]
    brand_id = brand["id"]
    parsed = parse_forwarded_email(raw)
    if not parsed.get("message_id"):
        # Some senders omit Message-ID. Mint a stable internal RFC-style id
        # from the raw message so the first reply can carry a thread root.
        fallback_id = hashlib.sha256(f"{brand_id}\\0{raw.hex()}".encode()).hexdigest()[:40]
        parsed["message_id"] = f"<{fallback_id}@inbound.tresolv.online>"

    # Gmail requires the new forwarding address to be verified. Capture only
    # its known forwarding-confirmation email and never create a customer ticket.
    verification_code = _google_forwarding_confirmation_code(parsed)
    if verification_code:
        await asyncio.to_thread(
            supabase_update,
            "brands",
            {"id": f"eq.{brand_id}"},
            {
                "inbound_email_verification_code": verification_code,
                "inbound_email_verification_code_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"accepted": True, "status": "verification_code_captured"}

    gmail_message_id, gmail_thread_id = forwarding_message_ids(brand_id, parsed, raw)

    # A duplicate delivery should not trigger a second AI response.
    existing = await asyncio.to_thread(
        supabase_select,
        "tickets",
        {
            "store_id": f"eq.{brand_id}",
            "processed_gmail_message_ids": f"cs.{{{gmail_message_id}}}",
            "select": "id",
            "limit": "1",
        },
    )
    if existing:
        return {"accepted": True, "status": "duplicate", "ticket_id": existing[0].get("id")}

    email_context = {
        "id": gmail_message_id,
        "sender_email": parsed["sender_email"],
        "sender_name": parsed["sender_name"],
        "subject": parsed["subject"],
        "body": parsed["body"],
        "headers": parsed["headers"],
        "label_ids": [],
        "brand_support_email": (brand.get("support_email") or "").lower(),
    }

    from src.services.email_filter_service import email_filter_service
    from src.services.email_guardian_service import email_guardian_service
    filter_result = await asyncio.to_thread(email_filter_service.evaluate, email_context, brand_id)
    await asyncio.to_thread(
        email_filter_service.log_decision,
        brand_id,
        parsed["sender_email"],
        gmail_thread_id,
        filter_result,
        gmail_message_id,
    )
    if filter_result.decision == "blocked":
        return {"accepted": True, "status": "filtered", "reason": filter_result.reason}

    guardian_result = await email_guardian_service.evaluate(
        email_context,
        brand_id,
        brand_name=brand.get("name") or "the store",
        agent_name=brand.get("agent_name") or "Luna",
    )
    await asyncio.to_thread(
        email_guardian_service.log_guardian_decision,
        brand_id,
        parsed["sender_email"],
        gmail_thread_id,
        guardian_result,
        gmail_message_id,
    )
    if guardian_result.decision in ("blocked", "quarantined"):
        return {
            "accepted": True,
            "status": guardian_result.decision,
            "reason": guardian_result.reason,
        }

    # Never auto-send until this brand has completed the send-only Gmail OAuth
    # connection. Incoming messages can still become human-review tickets.
    auto_reply_enabled = bool(
        guardian_result.auto_reply_enabled
        and brand.get("gmail_connected")
        and brand.get("gmail_scope_mode") == "send_only"
        and brand.get("inbound_email_verified_at")
    )

    payload = {
        "channel": "email",
        "content": parsed["body"],
        "customer_email": parsed["sender_email"],
        "customer_name": parsed["sender_name"],
        "subject": parsed["subject"],
        "timestamp": parsed["received_at"],
        "received_at": parsed["received_at"],
        "store_id": brand_id,
        "brand_name": brand.get("name", ""),
        # Internal identifiers used by the existing ticket dedup/thread path.
        # They are deliberately not real Gmail thread IDs.
        "gmail_thread_id": gmail_thread_id,
        "gmail_message_id": gmail_message_id,
        "auto_reply_enabled": auto_reply_enabled,
        "email_message_id": parsed.get("message_id"),
        "email_in_reply_to": parsed.get("in_reply_to"),
        "email_references": parsed.get("references") or [],
        "email_ingestion_mode": "forwarding",
    }

    result = await message_processor.process_message("email_incoming", payload)
    if isinstance(result, dict) and result.get("status") == "error":
        raise RuntimeError(result.get("error") or "Unified message processor failed")

    return {
        "accepted": True,
        "status": (result or {}).get("status", "processed"),
        "ticket_id": (result or {}).get("ticket_id"),
    }
