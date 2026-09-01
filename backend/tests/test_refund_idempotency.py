"""
Refund double-execution risk (financial safety).

Confirmed gap: v2_actions.py's approve_action() atomically claims an action
("pending" -> "approved") before calling process_refund() - this DOES stop
two concurrent approve calls from both firing Shopify (see
test_actions_duplicate_execution.py). But it does NOT stop a genuinely
sequential fail-then-retry cycle: if process_refund() raises AFTER Shopify
actually processed the refund on its side (the HTTP response is lost, the
worker crashes/times out before we persist anything), the action is marked
"failed" - and v2_actions.py's own /{action_id}/retry endpoint resets a
"failed" action back to "pending" specifically so it can be re-approved.
Re-approving calls process_refund() again. Nothing in the OLD code checked
whether a refund for this action already existed on Shopify's side before
issuing a second one - a genuine duplicate refund.

Fix: process_refund() accepts an idempotency_key (the caller's own stable
action id). When given, it tags the refund's `note` field with it, and -
before creating a new refund - checks Shopify's own orders/{id}/refunds.json
for an existing refund carrying that tag. If one already succeeded, it's
replayed instead of firing a second Shopify mutation. This is authoritative
against Shopify itself, not just our own local bookkeeping (which is
exactly the record that can be missing in the crash/lost-response case this
exists for).

All Shopify HTTP calls are mocked via ShopifyClient._request - no live
service required. Follows the same harness as
test_shopify_action_verification.py.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import asyncio  # noqa: E402
from src.services.shopify_service import ShopifyClient, ShopifyError  # noqa: E402


def run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _client():
    return ShopifyClient("test-shop.myshopify.com", "shpat_test")


def _order_response(**overrides):
    order = {
        "id": 123456789, "name": "#1001", "total_price": "50.00",
        "cancelled_at": None, "refunds": [], "fulfillment_status": None,
        "gateway": "manual",
    }
    order.update(overrides)
    return {"data": {"orders": [order]}}


# ── 1. No idempotency_key -> behavior is completely unchanged ──────────────

def test_no_idempotency_key_skips_the_existing_refund_lookup_entirely():
    """Callers that don't pass idempotency_key (there are none left after
    this fix, but backward compatibility matters) must see zero behavior
    change - no extra Shopify call, no tag in the note."""
    client = _client()
    order_resp = _order_response()
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}]}}}

    with patch.object(client, "_request", side_effect=[order_resp, txn_resp, refund_resp]) as mock_request:
        result = run(client.process_refund("1001"))

    assert result["success"] is True
    assert mock_request.call_count == 3  # order, transactions, refund - no lookup call
    posted_note = mock_request.call_args_list[2][0][2]["refund"]["note"]
    assert "[tResolv-ref:" not in posted_note


# ── 2. First attempt with a key: tags the note, proceeds normally ──────────

def test_first_attempt_with_idempotency_key_tags_the_note_and_succeeds():
    client = _client()
    order_resp = _order_response()
    existing_refunds_resp = {"data": {"refunds": []}}  # nothing tagged yet
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 999, "transactions": [{"id": 2, "kind": "refund", "status": "success"}]}}}

    with patch.object(client, "_request", side_effect=[order_resp, existing_refunds_resp, txn_resp, refund_resp]) as mock_request:
        result = run(client.process_refund("1001", idempotency_key="action-abc"))

    assert result["success"] is True
    assert result.get("already_processed") is not True
    posted_note = mock_request.call_args_list[3][0][2]["refund"]["note"]
    assert "[tResolv-ref:action-abc]" in posted_note


# ── 3. THE core fix: a retry after a lost-response success replays, never refunds twice ─

def test_retry_after_untracked_success_replays_instead_of_refunding_again():
    """The exact scenario: the first attempt's refund actually succeeded on
    Shopify (a tagged refund with a successful transaction already exists),
    but our own side never learned that. A second process_refund() call
    with the SAME idempotency_key must find it and return it - and must
    NEVER reach the create-a-new-refund POST call."""
    client = _client()
    order_resp = _order_response()
    existing_refunds_resp = {"data": {"refunds": [
        {
            "id": 555,
            "note": "Customer request [tResolv-ref:action-abc]",
            "transactions": [{"id": 2, "kind": "refund", "status": "success"}],
        }
    ]}}

    with patch.object(client, "_request", side_effect=[order_resp, existing_refunds_resp]) as mock_request:
        result = run(client.process_refund("1001", idempotency_key="action-abc"))

    assert result["success"] is True
    assert result["refund_id"] == 555
    assert result["already_processed"] is True
    # Only 2 calls: order lookup + existing-refunds lookup. Never reached
    # the transactions.json / refunds.json POST that would create a new one.
    assert mock_request.call_count == 2


def test_retry_with_a_different_key_is_unaffected_by_another_actions_tag():
    """A tagged refund for a DIFFERENT action_id must not short-circuit this
    one - the tag match must be exact, not "any refund exists"."""
    client = _client()
    order_resp = _order_response()
    existing_refunds_resp = {"data": {"refunds": [
        {"id": 555, "note": "Customer request [tResolv-ref:some-other-action]",
         "transactions": [{"id": 2, "kind": "refund", "status": "success"}]},
    ]}}
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 777, "transactions": [{"id": 3, "kind": "refund", "status": "success"}]}}}

    with patch.object(client, "_request", side_effect=[order_resp, existing_refunds_resp, txn_resp, refund_resp]):
        result = run(client.process_refund("1001", idempotency_key="action-xyz"))

    assert result["success"] is True
    assert result["refund_id"] == 777
    assert result.get("already_processed") is not True


def test_tagged_refund_that_never_confirmed_success_does_not_block_a_fresh_attempt():
    """A prior tagged refund exists but its own transaction never confirmed
    success (e.g. the first attempt itself hit the gateway-failure case) -
    must fall through and let this attempt create a genuine new refund,
    not get stuck replaying a failure forever."""
    client = _client()
    order_resp = _order_response()
    existing_refunds_resp = {"data": {"refunds": [
        {"id": 555, "note": "Customer request [tResolv-ref:action-abc]",
         "transactions": [{"id": 2, "kind": "refund", "status": "failure"}]},
    ]}}
    txn_resp = {"data": {"transactions": [{"id": 1, "kind": "sale", "status": "success"}]}}
    refund_resp = {"data": {"refund": {"id": 888, "transactions": [{"id": 3, "kind": "refund", "status": "success"}]}}}

    with patch.object(client, "_request", side_effect=[order_resp, existing_refunds_resp, txn_resp, refund_resp]):
        result = run(client.process_refund("1001", idempotency_key="action-abc"))

    assert result["success"] is True
    assert result["refund_id"] == 888


# ── 4. Fail-safe: can't confirm prior state -> refuse rather than risk a double refund ─

def test_lookup_failure_blocks_the_attempt_rather_than_risking_a_double_refund():
    """If we can't even check whether a prior tagged refund exists, the
    safe choice is to refuse and surface that to a human - never proceed
    blind to a brand-new Shopify mutation that might duplicate one that
    already succeeded."""
    client = _client()
    order_resp = _order_response()

    with patch.object(client, "_request", side_effect=[order_resp, RuntimeError("network blip")]):
        with pytest.raises(ShopifyError):
            run(client.process_refund("1001", idempotency_key="action-abc"))
