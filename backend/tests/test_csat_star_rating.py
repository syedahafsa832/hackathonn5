"""
Post-ticket CSAT email — tap-a-star rating (replaces the old YES/NO survey
in email_poller.py, which was send-only and never recorded a reply
anywhere). Each star links to GET /widget/feedback/rate with a signed
token; tapping records the rating immediately into the same chat_feedback
table the chat widget's thumbs rating writes to. An optional follow-up
comment (POST /widget/feedback/comment) attaches to that same row.

Covers: valid rating recorded (insert), re-rating updates the same row
(idempotent — no duplicates), tampered/invalid tokens rejected, optional
comment requires a real prior rating and a token that matches it, and the
email body itself contains real tap-a-star copy (no fabricated YES/NO
survey, no invented customer data).
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import v2_chat_widget  # noqa: E402
from src.api.routes.v2_chat_widget import sign_star_rating, star_rating_email_url  # noqa: E402
from src.channels.email_poller import EmailPoller  # noqa: E402

app = FastAPI()
app.include_router(v2_chat_widget.router, prefix="/api/v2")
client = TestClient(app)

TICKET_ROW = {"id": "ticket-1", "brand_id": "brand-1", "channel": "email"}


def setup_function():
    v2_chat_widget._rate_buckets.clear()


# ── GET /widget/feedback/rate ────────────────────────────────────────────

def test_valid_star_rating_creates_a_chat_feedback_row():
    captured = {}

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_ROW]
        if table == "chat_feedback":
            return []  # no existing row -> insert path
        return []

    def fake_insert(table, data):
        captured["table"] = table
        captured["data"] = data
        return data

    token = sign_star_rating("ticket-1", 5)
    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=fake_insert), \
         patch("src.api.routes.v2_chat_widget.supabase_update") as mock_update:
        resp = client.get(f"/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=5&token={token}")

    assert resp.status_code == 200
    assert "⭐⭐⭐⭐⭐" in resp.text
    assert "How did we do" in resp.text
    mock_update.assert_not_called()
    assert captured["table"] == "chat_feedback"
    assert captured["data"]["rating_stars"] == 5
    assert captured["data"]["rating"] == "positive"
    assert captured["data"]["ticket_id"] == "ticket-1"
    assert captured["data"]["brand_id"] == "brand-1"


def test_low_star_rating_maps_to_negative():
    def fake_select(table, params=None):
        return [TICKET_ROW] if table == "tickets" else []

    captured = {}
    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert", side_effect=lambda t, d: captured.update(data=d)), \
         patch("src.api.routes.v2_chat_widget.supabase_update"):
        token = sign_star_rating("ticket-1", 2)
        resp = client.get(f"/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=2&token={token}")

    assert resp.status_code == 200
    assert captured["data"]["rating"] == "negative"
    assert captured["data"]["rating_stars"] == 2


def test_reclicking_a_star_updates_the_existing_row_not_a_new_one():
    existing_row = {"id": "fb-1", "ticket_id": "ticket-1", "rating_stars": 3}

    def fake_select(table, params=None):
        if table == "tickets":
            return [TICKET_ROW]
        if table == "chat_feedback":
            return [existing_row]
        return []

    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_insert") as mock_insert, \
         patch("src.api.routes.v2_chat_widget.supabase_update") as mock_update:
        token = sign_star_rating("ticket-1", 4)
        resp = client.get(f"/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=4&token={token}")

    assert resp.status_code == 200
    mock_insert.assert_not_called()
    mock_update.assert_called_once()
    args, _ = mock_update.call_args
    assert args[0] == "chat_feedback"
    assert args[1] == {"id": "eq.fb-1"}
    assert args[2] == {"rating": "positive", "rating_stars": 4}


def test_tampered_token_is_rejected():
    with patch("src.api.routes.v2_chat_widget.supabase_select") as mock_select, \
         patch("src.api.routes.v2_chat_widget.supabase_insert") as mock_insert:
        resp = client.get("/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=5&token=not-a-real-token")

    assert resp.status_code == 400
    mock_select.assert_not_called()
    mock_insert.assert_not_called()


def test_token_for_a_different_ticket_is_rejected():
    # A token signed for a different ticket_id must not validate here -
    # otherwise one customer's rating link could rate a different ticket.
    token = sign_star_rating("some-other-ticket", 5)
    with patch("src.api.routes.v2_chat_widget.supabase_select") as mock_select:
        resp = client.get(f"/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=5&token={token}")

    assert resp.status_code == 400
    mock_select.assert_not_called()


def test_out_of_range_stars_rejected():
    token = sign_star_rating("ticket-1", 6)
    resp = client.get(f"/api/v2/widget/feedback/rate?ticket_id=ticket-1&stars=6&token={token}")
    assert resp.status_code == 400


# ── POST /widget/feedback/comment ────────────────────────────────────────

def test_comment_attaches_to_the_existing_rated_row():
    existing_row = {"id": "fb-1", "ticket_id": "ticket-1", "rating_stars": 5}

    def fake_select(table, params=None):
        return [existing_row] if table == "chat_feedback" else []

    with patch("src.api.routes.v2_chat_widget.supabase_select", side_effect=fake_select), \
         patch("src.api.routes.v2_chat_widget.supabase_update") as mock_update:
        token = sign_star_rating("ticket-1", 5)
        resp = client.post("/api/v2/widget/feedback/comment", data={
            "ticket_id": "ticket-1", "token": token, "comment": "Luna fixed it in seconds!",
        })

    assert resp.status_code == 200
    assert "Feedback received" in resp.text
    mock_update.assert_called_once_with("chat_feedback", {"id": "eq.fb-1"}, {"feedback_text": "Luna fixed it in seconds!"})


def test_comment_without_a_prior_rating_is_rejected():
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[]), \
         patch("src.api.routes.v2_chat_widget.supabase_update") as mock_update:
        resp = client.post("/api/v2/widget/feedback/comment", data={
            "ticket_id": "ticket-1", "token": "whatever", "comment": "hi",
        })

    assert resp.status_code == 404
    mock_update.assert_not_called()


def test_comment_with_token_not_matching_the_stored_rating_is_rejected():
    existing_row = {"id": "fb-1", "ticket_id": "ticket-1", "rating_stars": 5}
    with patch("src.api.routes.v2_chat_widget.supabase_select", return_value=[existing_row]), \
         patch("src.api.routes.v2_chat_widget.supabase_update") as mock_update:
        wrong_token = sign_star_rating("ticket-1", 3)  # token for 3 stars, row has 5
        resp = client.post("/api/v2/widget/feedback/comment", data={
            "ticket_id": "ticket-1", "token": wrong_token, "comment": "hi",
        })

    assert resp.status_code == 400
    mock_update.assert_not_called()


# ── Token signing ─────────────────────────────────────────────────────────

def test_sign_star_rating_differs_per_ticket_and_stars():
    a = sign_star_rating("ticket-1", 5)
    b = sign_star_rating("ticket-2", 5)
    c = sign_star_rating("ticket-1", 4)
    assert len({a, b, c}) == 3


def test_star_rating_email_url_contains_correct_stars_and_valid_token():
    url = star_rating_email_url("ticket-1", 4)
    assert "stars=4" in url
    assert "ticket_id=ticket-1" in url
    token = url.split("token=")[1]
    assert token == sign_star_rating("ticket-1", 4)


# ── Email body content ───────────────────────────────────────────────────

def test_csat_email_has_five_distinct_star_links_and_no_old_yes_no_copy():
    plain, html = EmailPoller._build_csat_email("ticket-1", "Acme Co")

    assert "YES" not in plain and "NO" not in plain
    assert "Tap a star" in plain
    assert "How did we do" in plain
    for n in range(1, 6):
        assert f"stars={n}&" in plain or f"stars={n}\n" in plain or f"stars={n}" in plain
    # Five distinct URLs (one per star count), not one generic link repeated.
    urls_in_html = [line for line in html.split('href="')[1:]]
    star_urls = {u.split('"')[0] for u in urls_in_html}
    assert len(star_urls) == 5
    assert "Acme Co" in plain
