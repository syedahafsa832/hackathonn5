"""
Task 3 investigation (documented, not fixed - this is the discovered current
behavior, pending a product decision on whether/when to change it):

There is no explicit "activation" or "go-live" state anywhere in the backend.
Gmail polling (email_poller.py -> brand_gmail_service.get_connected_brands())
starts acting on a brand's real inbox as soon as gmail_connected=true - that
happens at onboarding step 3 of 5 (Onboarding.jsx), before the "Customize
Luna" and "Test AI" steps a merchant might expect to complete first.

Whether the AI actually auto-replies to what it finds is gated by ai_mode
(system_settings table), already fully wired through message_processor.py's
STAGE 4 (manual mode -> requires_human) and STAGE 8 routing. But no
system_settings row is created at brand signup, and get_system_settings()'s
fallback for a store with no row is hardcoded to ai_mode="active" - so a
brand-new store has AI active by default, with zero explicit configuration.

These tests document both halves of that gap directly against the real code,
so the behavior is pinned down (and any future change to it is a deliberate,
visible diff) rather than only described in a report.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.supabase_service import SupabaseService  # noqa: E402
from src.services.brand_gmail_service import BrandGmailService  # noqa: E402


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_new_brand_with_no_settings_row_defaults_to_ai_mode_active():
    """A brand that just connected Gmail, with no system_settings row created
    yet for its store_id and no global default row either, gets ai_mode=active
    from the hardcoded fallback - not a safer default like 'paused'."""
    service = SupabaseService()
    with patch("src.services.supabase_service.supabase_select", return_value=[]):
        settings = run(service.get_system_settings("brand-new-store-id"))
    assert settings["ai_mode"] == "active"


def test_connected_brands_query_has_no_activation_or_onboarding_gate():
    """get_connected_brands() is the sole source feeding the email poller.
    Its Supabase filter is gmail_connected=true only - no onboarding-complete,
    is_active, or go-live field is part of the query."""
    service = BrandGmailService()
    with patch("src.services.brand_gmail_service.supabase_select", return_value=[]) as mock_select:
        service.get_connected_brands()
    args, kwargs = mock_select.call_args
    query_params = args[1] if len(args) > 1 else kwargs.get("params")
    assert query_params == {"gmail_connected": "is.true"}
