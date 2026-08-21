"""
"Dear There" bug — the post-approval confirmation email path.

actions_service._post_execution_notify() builds the "Your cancellation/
refund/exchange has been processed" email sent after a human approves an
action. It had its own, separate email-derivation bug: falling back to
`customer_email.split("@")[0]).capitalize()` when no name was on file, e.g.
"customer10@example.com" -> "Customer10" - exactly the forbidden pattern.
It also didn't guard against placeholder names like "Website Visitor"
leaking straight into the greeting ("Hey Website visitor,"). Fixed to fall
back to the same neutral "Hey there," idiom used everywhere else when no
real name is known.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_service import actions_service  # noqa: E402

BRAND_ROW = {"id": "brand-1", "name": "Test Brand", "gmail_connected": True}


def _action(customer_name=None, **overrides):
    a = {
        "id": "action-1", "customer_email": "customer10@example.com",
        "customer_name": customer_name, "ticket_id": None, "brand_id": "brand-1",
        "tenant_id": "tenant-1",
    }
    a.update(overrides)
    return a


@pytest.mark.asyncio
async def test_confirmation_email_never_derives_greeting_from_email_address():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(customer_name=None), "cancel_order", {"order_name": "#1013"},
        )

    assert "Hey there," in captured["body"]
    assert "customer10" not in captured["body"].lower()
    assert "Customer10" not in captured["body"]


@pytest.mark.asyncio
async def test_confirmation_email_never_greets_by_a_placeholder_name():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(customer_name="Website Visitor"), "cancel_order", {"order_name": "#1013"},
        )

    assert "Hey there," in captured["body"]
    assert "Website visitor" not in captured["body"]
    assert "Website Visitor" not in captured["body"]


@pytest.mark.asyncio
async def test_confirmation_email_uses_a_real_known_name_when_given():
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(
            _action(customer_name="bushra"), "cancel_order", {"order_name": "#1013"},
        )

    assert "Hey Bushra," in captured["body"]
