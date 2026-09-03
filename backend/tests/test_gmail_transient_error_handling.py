"""
Production incident: repeated Gmail 502s on batchModify (the "mark as read"
call inside brand_gmail_service.py's _get_new_emails_sync):

    ERROR:src.services.brand_gmail_service:[BrandGmail] Error reading
    message 1a0677793a424d0f
    googleapiclient.errors.HttpError: <HttpError 502 when requesting
    .../batchModify returned "Bad Gateway">

Root cause: the message content fetch (messages().get()) and the mark-as-
read call (messages().batchModify()) shared ONE try/except per message. A
transient 502 on batchModify — which runs AFTER the message was already
successfully fetched and fully parsed — raised, was caught by that same
try/except, and skipped the emails.append() for a message that had, moments
earlier in the very same iteration, been retrieved successfully. The
message was silently dropped from that poll's results.

batchModify is also not actually load-bearing for correctness in the
normal (steady-state) polling mode: once last_polled_at is set, the Gmail
search query is `after:{date}`, not `is:unread` — dedup against a repeat
message is handled entirely by email_poller.py's own gmail_message_id
check against the tickets table, never by Gmail's read/unread flag. Only
the one-time `is:unread` fallback (a brand's very first poll, before
last_polled_at exists) actually depends on messages being marked read.

Fix: batchModify now runs in its own try/except, AFTER the message is
already appended to the results — its failure is logged but never drops
the message. It also gets one bounded retry (tenacity, transient 429/5xx
only, never 401/403) since most such 502s are momentary.

Separately: get_new_emails() now returns a FetchedEmails (list subclass)
that also carries `.fetch_failures` — messages Gmail's search matched that
genuinely could NOT be retrieved (the messages().get() call itself failing,
not a batchModify-only failure). email_poller.py folds this into its
"fetched=X processed=Y failures=Z" summary log line so a real Gmail-level
failure is no longer invisible as "failures=0".
"""
import os
import sys
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.brand_gmail_service import BrandGmailService, FetchedEmails  # noqa: E402


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"error body")


def _message_response(msg_id="m1"):
    return {
        "threadId": "t1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "customer@example.com"}],
            "body": {},
        },
        "labelIds": [],
        "internalDate": "1798580580000",
        "snippet": "hi",
    }


def _fake_service(get_return, batch_modify_side_effect=None, list_return=None):
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_return or {"messages": [{"id": "m1"}]}
    )
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = get_return
    bm = svc.users.return_value.messages.return_value.batchModify.return_value.execute
    if batch_modify_side_effect is not None:
        bm.side_effect = batch_modify_side_effect
    else:
        bm.return_value = {}
    return svc


def _service():
    return BrandGmailService.__new__(BrandGmailService)


# ── 1. A transient 502 on batchModify must NOT drop the message ────────────

def test_transient_502_on_batch_modify_does_not_drop_an_already_fetched_message():
    svc = _fake_service(
        _message_response(),
        batch_modify_side_effect=[_http_error(502), _http_error(502), {}],  # fails twice, succeeds on retry
    )
    service = _service()
    with patch.object(service, "_build_service", return_value=svc), \
         patch.object(service, "_decode_body", return_value="hi"), \
         patch("time.sleep"):  # skip real backoff delay
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    assert len(emails) == 1
    assert emails[0]["id"] == "m1"
    assert emails.fetch_failures == 0


def test_batch_modify_failing_every_retry_still_returns_the_message():
    """Even if the retry itself is exhausted, the message the content of
    which was already successfully retrieved must still be returned - a
    cosmetic mark-as-read failure is never a reason to lose a real message."""
    svc = _fake_service(
        _message_response(),
        batch_modify_side_effect=_http_error(502),  # every attempt fails
    )
    service = _service()
    with patch.object(service, "_build_service", return_value=svc), \
         patch.object(service, "_decode_body", return_value="hi"), \
         patch("time.sleep"):
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    assert len(emails) == 1
    assert emails[0]["id"] == "m1"
    assert emails.fetch_failures == 0


# ── 2. A permanent auth error must never be retried ─────────────────────────

def test_batch_modify_permanent_403_is_not_retried():
    svc = _fake_service(_message_response(), batch_modify_side_effect=_http_error(403))
    service = _service()
    with patch.object(service, "_build_service", return_value=svc), \
         patch.object(service, "_decode_body", return_value="hi"):
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    # Message still returned (batchModify failure is non-fatal either way)...
    assert len(emails) == 1
    # ...but only ONE attempt was made - no retry for a permanent error.
    call_count = svc.users.return_value.messages.return_value.batchModify.return_value.execute.call_count
    assert call_count == 1


# ── 3. A genuine content-fetch failure (not batchModify) counts as a
#      real fetch failure, and does not affect other messages in the batch ──

def test_genuine_message_get_failure_is_counted_and_does_not_affect_other_messages():
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "bad-1"}, {"id": "good-1"}],
    }

    def _get_side_effect(userId, id):
        mock = MagicMock()
        if id == "bad-1":
            mock.execute.side_effect = _http_error(502)
        else:
            mock.execute.return_value = _message_response(id)
        return mock

    svc.users.return_value.messages.return_value.get.side_effect = _get_side_effect
    svc.users.return_value.messages.return_value.batchModify.return_value.execute.return_value = {}

    service = _service()
    with patch.object(service, "_build_service", return_value=svc), \
         patch.object(service, "_decode_body", return_value="hi"):
        emails = service._get_new_emails_sync({"id": "brand-1", "name": "Test"}, max_results=5)

    assert len(emails) == 1
    assert emails[0]["id"] == "good-1"
    assert emails.fetch_failures == 1


# ── 4. FetchedEmails is a plain list everywhere existing callers rely on it ─

def test_fetched_emails_behaves_as_a_plain_list_for_existing_callers():
    fe = FetchedEmails()
    fe.append({"id": "a"})
    fe.append({"id": "b"})
    assert len(fe) == 2
    assert [e["id"] for e in fe] == ["a", "b"]
    assert fe.fetch_failures == 0  # class-level default, no failures recorded


def test_a_bare_list_from_an_existing_mock_reports_zero_fetch_failures_via_getattr():
    """Every existing test mocking get_new_emails with return_value=[...]
    (a plain list) must keep working unchanged - getattr's default (0)
    is what email_poller.py's summary log falls back to for those."""
    bare_list = [{"id": "a"}]
    assert getattr(bare_list, "fetch_failures", 0) == 0
