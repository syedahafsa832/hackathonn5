"""
customers.email is a GLOBAL unique constraint (not email+store_id). The old
get_or_create_customer scoped its pre-check lookup to email+store_id, so a
returning customer already seen under a different store (or a concurrent
request) would miss the pre-check, hit the INSERT, and rely on string-matching
the resulting error to recover -- and never updated stale name/phone on the
existing row. This covers: existing-by-email-alone is found and reused,
name/phone get patched when changed, and the 409-race fallback still works.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio
from src.services.supabase_service import SupabaseService  # noqa: E402


def run(coro):
    # See test_chat_widget_action_staging.py's run() for why: asyncio.get_
    # event_loop() can return an already-closed loop left behind by an
    # earlier @pytest.mark.asyncio test in the full suite, which fails
    # run_until_complete with "Event loop is closed" - not a logic bug in
    # these tests, just this helper never checking loop state before reuse.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_existing_customer_found_by_email_alone_no_insert():
    service = SupabaseService()
    existing_row = {"id": "cust-1", "email": "a@b.com", "name": "A", "phone": None, "store_id": "store-1"}

    with patch("src.services.supabase_service.supabase_select", return_value=[existing_row]), \
         patch("src.services.supabase_service.supabase_insert") as mock_insert, \
         patch("src.services.supabase_service.supabase_update") as mock_update:
        result = run(service.get_or_create_customer(email="a@b.com", store_id="store-2", name="A"))

    mock_insert.assert_not_called()
    mock_update.assert_not_called()  # name unchanged
    assert result["id"] == "cust-1"


def test_existing_customer_name_change_triggers_update_not_store_id():
    service = SupabaseService()
    existing_row = {"id": "cust-1", "email": "a@b.com", "name": "Old Name", "phone": None, "store_id": "store-1"}
    updated = {**existing_row, "name": "New Name"}

    with patch("src.services.supabase_service.supabase_select", return_value=[existing_row]), \
         patch("src.services.supabase_service.supabase_update", return_value=updated) as mock_update:
        result = run(service.get_or_create_customer(email="a@b.com", store_id="store-2", name="New Name"))

    args = mock_update.call_args[0]
    assert args[0] == "customers"
    assert args[1] == {"id": "eq.cust-1"}
    assert args[2] == {"name": "New Name"}  # store_id never included
    assert result["name"] == "New Name"


def test_new_customer_inserts_when_no_existing_row():
    service = SupabaseService()
    inserted = {"id": "cust-new", "email": "new@b.com", "store_id": "store-1"}

    with patch("src.services.supabase_service.supabase_select", return_value=[]), \
         patch("src.services.supabase_service.supabase_insert", return_value=inserted) as mock_insert:
        result = run(service.get_or_create_customer(email="new@b.com", store_id="store-1"))

    mock_insert.assert_called_once()
    assert result["id"] == "cust-new"


def test_concurrent_insert_race_falls_back_to_existing_row():
    service = SupabaseService()
    winner_row = {"id": "cust-winner", "email": "race@b.com", "name": "Race", "phone": None, "store_id": "store-1"}
    select_calls = []

    def fake_select(table, params=None):
        select_calls.append(params)
        return [] if len(select_calls) == 1 else [winner_row]

    def fake_insert(table, data):
        raise Exception("409 Client Error: duplicate key value violates unique constraint \"customers_email_key\"")

    with patch("src.services.supabase_service.supabase_select", side_effect=fake_select), \
         patch("src.services.supabase_service.supabase_insert", side_effect=fake_insert):
        result = run(service.get_or_create_customer(email="race@b.com", store_id="store-2", name="Race"))

    assert result["id"] == "cust-winner"
