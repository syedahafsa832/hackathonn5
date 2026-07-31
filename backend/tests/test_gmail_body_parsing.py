"""
Gmail Full-Body Decoding Tests (C1 fix)
=========================================
Covers two things:
1. BrandGmailService._decode_body — the MIME decoder already used by the
   per-brand Gmail path (plain/html/nested-multipart).
2. GmailHandler.get_new_emails() — the legacy "global inbox fallback" path
   used by email_poller.py whenever no brand has per-brand Gmail OAuth
   connected. Before this fix it returned Gmail's short `snippet` field
   unconditionally; it must now return the full decoded body and only fall
   back to the snippet if decoding genuinely fails.

No live Gmail/Supabase calls — the Gmail API client is mocked.
"""
import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)                       # for "src.*"
sys.path.insert(0, os.path.dirname(_BACKEND_DIR))      # for "production.*" (repo root, sibling of backend/)
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.brand_gmail_service import BrandGmailService  # noqa: E402

LONG_BODY = (
    "Hi, I ordered product X two weeks ago and still have not received any "
    "shipping confirmation. My order number is 4521. Could someone please "
    "check on this for me? This message is intentionally longer than a "
    "typical Gmail snippet preview so the test can prove full-body decoding "
    "actually happened instead of truncating."
)
assert len(LONG_BODY) > 200  # must be longer than any realistic Gmail snippet


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# ─── 1. BrandGmailService._decode_body (the decoder itself) ─────────────────

def test_decode_body_single_part_plain_text():
    payload = {"body": {"data": _b64(LONG_BODY)}}
    assert BrandGmailService._decode_body(payload) == LONG_BODY


def test_decode_body_multipart_prefers_plain_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64(LONG_BODY)}},
            {"mimeType": "text/html", "body": {"data": _b64(f"<p>{LONG_BODY}</p>")}},
        ],
    }
    assert BrandGmailService._decode_body(payload) == LONG_BODY


def test_decode_body_falls_back_to_html_if_no_plain_part():
    html = f"<p>{LONG_BODY}</p>"
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [{"mimeType": "text/html", "body": {"data": _b64(html)}}],
    }
    assert BrandGmailService._decode_body(payload) == html


def test_decode_body_handles_nested_multipart_mixed_with_attachment():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": _b64(LONG_BODY)}}],
            },
            {"mimeType": "image/png", "body": {"attachmentId": "abc123"}},
        ],
    }
    assert BrandGmailService._decode_body(payload) == LONG_BODY


def test_decode_body_returns_empty_string_when_no_data_present():
    payload = {"body": {}}
    assert BrandGmailService._decode_body(payload) == ""


# ─── 2. GmailHandler.get_new_emails() — the legacy fallback path ────────────

def _fake_gmail_service(list_response, get_response):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = list_response
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = get_response
    return service


def _make_handler(service):
    with patch.object(
        __import__("production.channels.gmail_handler", fromlist=["GmailHandler"]).GmailHandler,
        "_initialize_credentials",
        lambda self: None,
    ):
        from production.channels.gmail_handler import GmailHandler
        handler = GmailHandler()
    handler.service = service
    return handler


@pytest.mark.asyncio
async def test_get_new_emails_returns_full_body_not_truncated_snippet():
    full_msg = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Where is my order?"},
                {"name": "From", "value": "Jane Doe <jane@example.com>"},
            ],
            "body": {"data": _b64(LONG_BODY)},
        },
        "snippet": LONG_BODY[:100] + "...",  # what Gmail's short preview looks like
    }
    service = _fake_gmail_service(
        list_response={"messages": [{"id": "msg1"}]},
        get_response=full_msg,
    )
    handler = _make_handler(service)

    emails = await handler.get_new_emails()

    assert len(emails) == 1
    assert emails[0]["body"] == LONG_BODY
    assert emails[0]["body"] != full_msg["snippet"]
    assert len(emails[0]["body"]) > 200


@pytest.mark.asyncio
async def test_get_new_emails_falls_back_to_snippet_only_if_decode_genuinely_fails():
    full_msg = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Hi"},
                {"name": "From", "value": "jane@example.com"},
            ],
            "body": {},  # no data, no parts — genuinely nothing to decode
        },
        "snippet": "short preview text",
    }
    service = _fake_gmail_service(
        list_response={"messages": [{"id": "msg1"}]},
        get_response=full_msg,
    )
    handler = _make_handler(service)

    emails = await handler.get_new_emails()

    assert emails[0]["body"] == "short preview text"
