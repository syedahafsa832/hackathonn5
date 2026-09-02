"""
Currency bug — the post-approval refund confirmation ("Luna replied ...").

_post_execution_notify() hardcoded the refund confirmation body's currency
as "PKR" regardless of the order's actual Shopify currency — a USD $15.00
refund on order #1001 came back as "PKR 15.00 will be returned...". Fixed
by threading the order's real currency (order.get("currency"), already
present on every Shopify order) through process_refund()'s execution_result
and formatting it with _format_money() — never converted, never inferred
from locale/country, never defaulted to a fixed currency (USD or otherwise).
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.actions_service import actions_service, _format_money  # noqa: E402

BRAND_ROW = {"id": "brand-1", "name": "Test Brand", "gmail_connected": True}


def _action(**overrides):
    a = {
        "id": "action-1", "customer_email": "customer@example.com",
        "customer_name": "Sara", "ticket_id": None, "brand_id": "brand-1",
        "tenant_id": "tenant-1",
    }
    a.update(overrides)
    return a


async def _run_refund_confirmation(execution_result):
    captured = {}

    async def fake_send_email(brand, to, subject, body):
        captured["body"] = body
        return {"success": True}

    with patch("src.services.actions_service.supabase_select", return_value=[BRAND_ROW]), \
         patch("src.services.actions_service.supabase_update"), \
         patch("src.services.brand_gmail_service.brand_gmail_service.send_email", new=fake_send_email):
        await actions_service._post_execution_notify(_action(), "refund", execution_result)

    return captured["body"]


# ── Unit-level: the formatter itself never converts, never invents ─────────

def test_format_money_uses_the_given_currencys_symbol():
    assert _format_money(15.0, "USD") == "$15.00"
    assert _format_money(5.0, "USD") == "$5.00"
    assert _format_money(15.0, "GBP") == "£15.00"


def test_format_money_never_hardcodes_a_default_currency():
    """An unrecognized-but-real ISO code (e.g. a genuinely PKR order) is
    shown as its own code, never silently coerced to USD or any other
    currency this codebase happens to recognize a symbol for."""
    assert _format_money(15.0, "PKR") == "PKR 15.00"
    assert _format_money(15.0, "AED") == "AED 15.00"


def test_format_money_never_invents_a_currency_when_none_is_available():
    assert _format_money(15.0, None) == "15.00"
    assert _format_money(15.0, "") == "15.00"


def test_format_money_never_converts_the_numeric_amount():
    """Same numeric amount must render identically regardless of currency
    code - only the label changes, never the number."""
    for code in ("USD", "GBP", "PKR", None):
        assert f"{15.0:.2f}" in _format_money(15.0, code)


# ── End-to-end: the actual customer-facing confirmation body ───────────────

@pytest.mark.asyncio
async def test_usd_full_refund_confirmation_shows_dollar_amount_not_pkr():
    body = await _run_refund_confirmation({
        "amount": 15.0, "currency": "USD", "order_name": "#1001",
    })
    assert "$15.00" in body
    assert "PKR" not in body


@pytest.mark.asyncio
async def test_usd_partial_refund_confirmation_shows_the_partial_dollar_amount():
    body = await _run_refund_confirmation({
        "amount": 5.0, "currency": "USD", "order_name": "#1001",
    })
    assert "$5.00" in body
    assert "$15.00" not in body
    assert "PKR" not in body


@pytest.mark.asyncio
async def test_gbp_refund_confirmation_uses_gbp_not_usd_or_pkr():
    body = await _run_refund_confirmation({
        "amount": 15.0, "currency": "GBP", "order_name": "#2002",
    })
    assert "£15.00" in body
    assert "$15.00" not in body
    assert "PKR" not in body


@pytest.mark.asyncio
async def test_missing_currency_never_falls_back_to_pkr_or_a_guessed_currency():
    """Authoritative currency truly unavailable (e.g. a legacy/edge Shopify
    response missing the field) - must show the bare amount, never invent
    PKR, USD, or any other currency."""
    body = await _run_refund_confirmation({
        "amount": 15.0, "currency": None, "order_name": "#1001",
    })
    assert "15.00" in body
    assert "PKR" not in body
    assert "$15.00" not in body
