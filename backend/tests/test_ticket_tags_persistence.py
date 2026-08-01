"""
Regression test: create_ticket() was silently dropping the "tags" field.

message_processor.py computes ticket_tags via a fast, synchronous keyword
matcher (_auto_tag_ticket) and passes it into both the early-create payload
and the finalize-update payload. But supabase_service.create_ticket()'s
formatted_ticket dict — the hardcoded whitelist of columns copied onto the
INSERT — never included "tags", so it was discarded on every ticket create
path regardless of what message_processor computed. Confirmed against the
live database: 0 of 49 real tickets had any tags value. This looked like
"tags load slowly" from the UI (they just never appear), not an N+1 query —
there is no separate per-row tag fetch; tags are read straight off the same
ticket row returned by GET /api/tickets.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import pytest

from src.services.supabase_service import supabase_service  # noqa: E402


@pytest.mark.asyncio
async def test_create_ticket_persists_tags():
    captured = {}

    def fake_insert(table, data):
        captured["data"] = data
        return {**data, "id": "ticket-1"}

    with patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        await supabase_service.create_ticket({
            "store_id": "brand-1",
            "customer_email": "c@example.com",
            "subject": "Where is my order",
            "message": "still waiting on shipping",
            "tags": ["shipping"],
        })

    assert captured["data"]["tags"] == ["shipping"]


@pytest.mark.asyncio
async def test_create_ticket_defaults_missing_tags_to_empty_list():
    captured = {}

    def fake_insert(table, data):
        captured["data"] = data
        return {**data, "id": "ticket-2"}

    with patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        await supabase_service.create_ticket({
            "store_id": "brand-1",
            "customer_email": "c@example.com",
        })

    assert captured["data"]["tags"] == []
