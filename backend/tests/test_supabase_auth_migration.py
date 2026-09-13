"""
Supabase Auth Migration Tests
==============================
Covers the tenant-resolution logic added when authentication moved to
Supabase Auth (auth.users) as the identity source of truth, while `tenants`
stays the merchant/company account and brand-isolation root.

All Supabase REST calls (supabase_select/insert/update) and all GoTrue
calls (supabase_gotrue.*) are mocked — no live Supabase project required.
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.services.auth_service import AuthService, FoundingCohortFullError  # noqa: E402
from src.services.supabase_gotrue import GoTrueError  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_password_reset_timing_pad():
    """request_password_reset() pads its response time to mask whether the
    email exists (see test_password_reset_request_pads_a_fast_response,
    which tests that behavior directly) — every other test in this file
    doesn't care about timing and shouldn't pay a real ~1s sleep per call."""
    with patch("src.services.auth_service._PASSWORD_RESET_MIN_RESPONSE_SECONDS", 0):
        yield


# ─── 1. New Supabase user with no existing tenant → creates one ────────────

def test_new_supabase_user_creates_a_tenant():
    inserted = {}

    def fake_insert(table, data):
        if table == "tenants":
            inserted["tenant"] = {"id": "tenant-new", **data}
            return inserted["tenant"]
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user(
            "sb-user-1", "new@example.com", "New Co"
        ))

    assert tenant["id"] == "tenant-new"
    assert tenant["supabase_user_id"] == "sb-user-1"
    # Never store a password for a Supabase-Auth-created tenant.
    assert tenant["password_hash"] is None


# ─── 2. Existing supabase_user_id match → returns that tenant, no insert ───

def test_existing_supabase_user_id_maps_straight_to_its_tenant():
    existing = {"id": "tenant-1", "email": "a@b.com", "supabase_user_id": "sb-user-1", "is_active": True}

    def fake_select(table, params=None):
        params = params or {}
        if table == "tenants" and params.get("supabase_user_id") == "eq.sb-user-1":
            return [existing]
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert:
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "a@b.com"))

    assert tenant is existing
    mock_insert.assert_not_called()


# ─── 3. Repeated login with the same Supabase user never creates a second tenant ───

def test_duplicate_login_does_not_create_a_duplicate_tenant():
    store = {}

    def fake_select(table, params=None):
        params = params or {}
        if table != "tenants":
            return []
        rows = list(store.values())
        if "supabase_user_id" in params:
            wanted = params["supabase_user_id"][3:]
            rows = [r for r in rows if r.get("supabase_user_id") == wanted]
        if "email" in params:
            wanted = params["email"][3:]
            rows = [r for r in rows if r.get("email") == wanted]
        if "founding_cohort" in params:
            rows = [r for r in rows if r.get("founding_cohort")]
        return rows

    def fake_insert(table, data):
        if table == "tenants":
            row = {"id": "tenant-1", **data}
            store["tenant-1"] = row
            return row
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        first = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "dup@example.com"))
        second = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-user-1", "dup@example.com"))

    assert first["id"] == second["id"]
    assert len(store) == 1


# ─── 4. Pre-migration tenant (no supabase_user_id yet) links on first Supabase login ───

def test_pre_migration_tenant_links_by_email_on_first_supabase_login():
    """A tenant created before this migration has no supabase_user_id yet.
    Whichever provider (Google or password) they first authenticate with
    through Supabase Auth, they must land on their existing tenant/brands —
    not get a second, empty one — and get backfilled so the next login
    resolves by supabase_user_id directly. (If they later use the *other*
    provider with the same email, Supabase's own identity linking hands back
    this same supabase_user_id, so it resolves straight away — see
    test_existing_supabase_user_id_maps_straight_to_its_tenant.)"""
    existing = {"id": "tenant-1", "email": "both@example.com", "supabase_user_id": None, "is_active": True}
    updated = {}

    def fake_select(table, params=None):
        params = params or {}
        if table != "tenants":
            return []
        if params.get("supabase_user_id") == "eq.sb-id-1":
            return []  # not linked yet — this is the first Supabase login
        if params.get("email") == "eq.both@example.com":
            return [existing]
        return []

    def fake_update(table, match, data):
        updated.update(data)
        return {**existing, **data}

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert, \
         patch("src.services.auth_service.supabase_update", side_effect=fake_update):
        auth_service = AuthService()
        tenant = _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-id-1", "both@example.com"))

    assert tenant["id"] == "tenant-1"
    mock_insert.assert_not_called()
    assert updated["supabase_user_id"] == "sb-id-1"


# ─── 5. Founding cohort cap is enforced for brand-new tenants ──────────────

def test_founding_cohort_cap_blocks_new_tenant_creation():
    def fake_select(table, params=None):
        params = params or {}
        if table == "tenants" and params.get("founding_cohort") == "eq.true":
            return [{"id": f"t{i}"} for i in range(20)]
        return []

    with patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert:
        auth_service = AuthService()
        with pytest.raises(FoundingCohortFullError):
            _run(auth_service.resolve_or_create_tenant_for_supabase_user("sb-new", "new@example.com"))

    mock_insert.assert_not_called()


# ─── 6. register() surfaces "check your email" instead of a session when Supabase requires confirmation ───

def test_register_reports_email_confirmation_required_without_a_session():
    def fake_signup(email, password):
        return {"user": {"id": "sb-unconfirmed", "email": email}, "session": None}

    def fake_insert(table, data):
        if table == "tenants":
            return {"id": "tenant-1", **data}
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_gotrue.sign_up", side_effect=fake_signup), \
         patch("src.services.auth_service.supabase_gotrue.generate_signup_confirmation_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_generic_auth_email", return_value=True) as mock_send, \
         patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        result = _run(auth_service.register("new@example.com", "supersecret123"))

    assert result["success"] is True
    assert result.get("email_confirmation_required") is True
    assert result.get("access_token") is None

    # The confirmation email must not depend on Supabase's own unreliable
    # mailer/Send Email Hook - it's generated and sent directly, the same
    # way the password-reset email already is.
    mock_generate.assert_called_once()
    assert mock_generate.call_args.args[0] == "new@example.com"
    assert mock_generate.call_args.args[1] == "supersecret123"
    assert mock_generate.call_args.kwargs["redirect_to"].endswith("/login")
    mock_send.assert_called_once()
    send_args, send_kwargs = mock_send.call_args
    assert send_args[0] == "new@example.com"
    assert send_kwargs["action_url"] == _FAKE_ACTION_LINK


def test_register_does_not_crash_if_confirmation_link_generation_fails():
    """generate_signup_confirmation_link returning None (e.g. Supabase's
    admin API is unreachable) must not fail the signup itself - the tenant
    is already created at this point; the merchant can still use "resend
    confirmation" or contact support instead of losing the account."""
    def fake_signup(email, password):
        return {"user": {"id": "sb-unconfirmed", "email": email}, "session": None}

    def fake_insert(table, data):
        if table == "tenants":
            return {"id": "tenant-1", **data}
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_gotrue.sign_up", side_effect=fake_signup), \
         patch("src.services.auth_service.supabase_gotrue.generate_signup_confirmation_link", return_value=None), \
         patch("src.services.auth_service.system_email_service.send_generic_auth_email") as mock_send, \
         patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        result = _run(auth_service.register("new@example.com", "supersecret123"))

    assert result["success"] is True
    assert result.get("email_confirmation_required") is True
    mock_send.assert_not_called()  # no link - nothing to send


# ─── 7. login() never leaks account-existence detail for a bad password ────

def test_login_with_wrong_password_returns_generic_error():
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin):
        auth_service = AuthService()
        result = _run(auth_service.login("a@b.com", "wrongpassword"))

    assert result["success"] is False
    assert result["error"] == "Invalid email or password"


# ─── 8. change_password re-verifies the current password via a real Supabase sign-in ───

def test_change_password_rejects_when_current_password_is_wrong():
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin), \
         patch("src.services.auth_service.supabase_auth_service.verify_jwt",
               return_value={"app_metadata": {"provider": "email", "providers": ["email"]}}), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="a@b.com",
            current_password="wrong", new_password="newpassword123",
            access_token="at-1",
        ))

    assert result["success"] is False
    assert "incorrect" in result["error"].lower()
    mock_update.assert_not_called()


def test_change_password_succeeds_when_current_password_verifies():
    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", return_value={"user": {}}), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password", return_value={}) as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="a@b.com",
            current_password="correct", new_password="newpassword123",
            access_token="at-1",
        ))

    assert result["success"] is True
    mock_update.assert_called_once_with("at-1", "newpassword123")


def test_change_password_gives_a_clear_message_for_a_google_only_account():
    """A Google-only account (never set a password) fails
    sign_in_with_password the same generic way a wrong password would
    (Supabase doesn't distinguish, by anti-enumeration design) - but the
    error shown to the user should say why, not just call a nonexistent
    password "incorrect". Detected from the verified access token's own
    app_metadata.providers, since Supabase doesn't expose "has a password"
    any other way."""
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    google_only_payload = {"app_metadata": {"provider": "google", "providers": ["google"]}}

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin), \
         patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=google_only_payload), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="google-user@example.com",
            current_password="anything", new_password="newpassword123",
            access_token="at-google-1",
        ))

    assert result["success"] is False
    assert "google" in result["error"].lower()
    assert "incorrect" not in result["error"].lower()
    mock_update.assert_not_called()


def test_change_password_still_says_incorrect_for_a_real_email_account_with_wrong_password():
    """The improved Google-account messaging must not swallow the ordinary
    wrong-password case for accounts that do have a password."""
    def fake_signin(email, password):
        raise GoTrueError("Invalid login credentials", 400)

    email_account_payload = {"app_metadata": {"provider": "email", "providers": ["email"]}}

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_password", side_effect=fake_signin), \
         patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=email_account_payload), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.change_password(
            tenant_id="tenant-1", email="a@b.com",
            current_password="wrong", new_password="newpassword123",
            access_token="at-1",
        ))

    assert result["success"] is False
    assert "incorrect" in result["error"].lower()
    mock_update.assert_not_called()


# ─── 9. Password reset never reveals whether the email is registered ───────
#
# request_password_reset() generates the recovery link via Supabase's Admin
# API (generate_recovery_link) and emails it directly with
# system_email_service — not via the Send Email Hook, which was tied to
# Supabase's own hard 5-second webhook timeout and failed unpredictably in
# production regardless of whether the SMTP send itself would have
# succeeded.

_FAKE_ACTION_LINK = "https://project-ref.supabase.co/auth/v1/verify?token=abc&type=recovery&redirect_to=https://app.tresolv.online/reset-password"


def test_password_reset_request_always_reports_success():
    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_password_reset_email", return_value=True) as mock_send:
        auth_service = AuthService()
        result = _run(auth_service.request_password_reset("reset-generic-response@example.com"))

    assert result["success"] is True
    mock_generate.assert_called_once()
    called_email, called_kwargs = mock_generate.call_args[0][0], mock_generate.call_args[1]
    assert called_email == "reset-generic-response@example.com"
    # Must always pass an explicit redirect_to — never rely on Supabase's
    # dashboard-configured default Site URL, which could be pointed at the
    # wrong environment.
    assert called_kwargs["redirect_to"].endswith("/reset-password")
    mock_send.assert_called_once_with("reset-generic-response@example.com", _FAKE_ACTION_LINK)


def test_password_reset_request_uses_frontend_url_env_var_for_the_redirect():
    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_password_reset_email", return_value=True), \
         patch("src.services.auth_service.FRONTEND_URL", "https://app.tresolv.online"):
        auth_service = AuthService()
        _run(auth_service.request_password_reset("reset-frontend-url@example.com"))

    assert mock_generate.call_args[1]["redirect_to"] == "https://app.tresolv.online/reset-password"


def test_password_reset_request_falls_back_to_localhost_in_dev_never_hardcodes_production():
    """FRONTEND_URL unset (the local-dev default) must produce a localhost
    reset link, not silently point at production — and the reverse must
    never happen either: production always sets FRONTEND_URL explicitly,
    it never inherits a hardcoded dev value from this code."""
    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_password_reset_email", return_value=True), \
         patch("src.services.auth_service.FRONTEND_URL", "http://localhost:5173"):
        auth_service = AuthService()
        _run(auth_service.request_password_reset("reset-dev-default@example.com"))

    redirect_to = mock_generate.call_args[1]["redirect_to"]
    assert redirect_to == "http://localhost:5173/reset-password"
    assert "tresolv.online" not in redirect_to


def test_password_reset_request_pads_a_fast_response():
    """A registered email does strictly more work (an extra Resend call)
    than an unregistered one, which without padding would leak account
    existence through response TIME even though the response BODY is
    already identical either way. Overrides the autouse fixture above to
    actually exercise the padding for this one test.

    Asserts on the asyncio.sleep call itself rather than measuring real
    wall-clock elapsed time — asyncio.sleep(N) can return a few ms before
    N under real scheduling (observed ~0.046s for a 0.05s target), which
    made a wall-clock `elapsed >= target` assertion genuinely flaky
    without the code itself being wrong."""
    with patch("src.services.auth_service._PASSWORD_RESET_MIN_RESPONSE_SECONDS", 0.05), \
         patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=None), \
         patch("src.services.auth_service.system_email_service.send_password_reset_email") as mock_send, \
         patch("src.services.auth_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        auth_service = AuthService()
        _run(auth_service.request_password_reset("reset-timing-pad@example.com"))

    mock_send.assert_not_called()  # unregistered-email path — nothing to actually send
    mock_sleep.assert_called_once()  # but the response was still padded up to the floor
    assert 0 < mock_sleep.call_args[0][0] <= 0.05


def test_password_reset_request_normalizes_email_case_and_whitespace():
    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_password_reset_email", return_value=True):
        auth_service = AuthService()
        _run(auth_service.request_password_reset("  Reset-Case@Example.com  "))

    assert mock_generate.call_args[0][0] == "reset-case@example.com"


def test_password_reset_request_is_cooled_down_per_email():
    """A second request for the same email within the cooldown window must
    not re-trigger Supabase — and must return the exact same generic
    response either way, so a client can't tell the difference."""
    from src.services import auth_service as auth_service_module
    email = "reset-cooldown@example.com"
    auth_service_module._last_password_reset_request.pop(email, None)

    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=_FAKE_ACTION_LINK) as mock_generate, \
         patch("src.services.auth_service.system_email_service.send_password_reset_email", return_value=True):
        auth_service = AuthService()
        first = _run(auth_service.request_password_reset(email))
        second = _run(auth_service.request_password_reset(email))

    assert first == second
    assert first["success"] is True
    mock_generate.assert_called_once()  # the second call was cooled down, not sent again

    auth_service_module._last_password_reset_request.pop(email, None)


def test_password_reset_request_does_not_email_when_link_generation_fails():
    """If generate_recovery_link fails (including the anti-enumeration case
    of "no such account"), no email attempt should happen — but the
    response must still be the identical generic success message, so a
    client can't distinguish a real send from a silently skipped one."""
    with patch("src.services.auth_service.supabase_gotrue.generate_recovery_link", return_value=None), \
         patch("src.services.auth_service.system_email_service.send_password_reset_email") as mock_send:
        auth_service = AuthService()
        result = _run(auth_service.request_password_reset("reset-no-account@example.com"))

    assert result["success"] is True
    mock_send.assert_not_called()


# ─── 10. Reset-password confirm — success, and invalid/expired token ───────

_RECOVERY_JWT_PAYLOAD = {"amr": [{"method": "otp", "timestamp": 1700000000}]}
_PASSWORD_LOGIN_JWT_PAYLOAD = {"amr": [{"method": "password", "timestamp": 1700000000}]}


def test_confirm_password_reset_succeeds_with_a_valid_recovery_token():
    with patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=_RECOVERY_JWT_PAYLOAD), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password", return_value={}) as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.confirm_password_reset("valid-recovery-token", "newpassword123"))

    assert result["success"] is True
    mock_update.assert_called_once_with("valid-recovery-token", "newpassword123")


def test_confirm_password_reset_rejects_an_invalid_or_expired_token():
    """verify_jwt returns None for a token whose signature is bad or whose
    exp has passed — must fail the same generic way, before ever reaching
    Supabase's own update-password call."""
    with patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=None), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.confirm_password_reset("expired-token", "newpassword123"))

    assert result["success"] is False
    assert "invalid or expired" in result["error"].lower()
    mock_update.assert_not_called()


def test_confirm_password_reset_rejects_a_token_not_from_the_recovery_flow():
    """Security fix: an ordinary logged-in session's access token (amr=
    password) must NOT be usable to reset the password without knowing the
    current one — that would silently bypass change_password()'s
    current-password check. Only a token whose amr shows it came from the
    recovery-link flow (amr=otp — this app has no magic-link login, so otp
    is unambiguous) may reach Supabase's update-password call."""
    with patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=_PASSWORD_LOGIN_JWT_PAYLOAD), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.confirm_password_reset("a-normal-login-session-token", "newpassword123"))

    assert result["success"] is False
    assert "invalid or expired" in result["error"].lower()
    mock_update.assert_not_called()


def test_confirm_password_reset_rejects_when_supabase_itself_rejects_the_token():
    """A token that passes our own signature/amr checks can still be
    rejected by Supabase at the point of use (e.g. the recovery link was
    already consumed once) — must still fail safely with the same generic
    message, not leak Supabase's internal error detail."""
    def fake_update(token, new_password):
        raise GoTrueError("Token has expired or is invalid", 401)

    with patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=_RECOVERY_JWT_PAYLOAD), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password", side_effect=fake_update):
        auth_service = AuthService()
        result = _run(auth_service.confirm_password_reset("already-used-token", "newpassword123"))

    assert result["success"] is False
    assert "invalid or expired" in result["error"].lower()


def test_confirm_password_reset_rejects_a_too_short_password():
    with patch("src.services.auth_service.supabase_auth_service.verify_jwt", return_value=_RECOVERY_JWT_PAYLOAD), \
         patch("src.services.auth_service.supabase_gotrue.update_user_password") as mock_update:
        auth_service = AuthService()
        result = _run(auth_service.confirm_password_reset("valid-recovery-token", "short"))

    assert result["success"] is False
    mock_update.assert_not_called()  # never sent to Supabase — rejected before the network call


# ─── 11. Google sign-in — new tenant, and mapping an existing one ──────────

def test_google_auth_creates_a_tenant_for_a_first_time_google_user():
    def fake_signin_id_token(credential, provider):
        return {
            "access_token": "at-1", "refresh_token": "rt-1", "token_type": "bearer", "expires_in": 3600,
            "user": {"id": "sb-google-1", "email": "newgoogle@example.com", "user_metadata": {"full_name": "Jamie Lee"}},
        }

    inserted = {}

    def fake_insert(table, data):
        if table == "tenants":
            inserted["tenant"] = {"id": "tenant-google-new", **data}
            return inserted["tenant"]
        return {"id": "brand-1", **data}

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_id_token", side_effect=fake_signin_id_token), \
         patch("src.services.auth_service.supabase_select", return_value=[]), \
         patch("src.services.auth_service.supabase_insert", side_effect=fake_insert), \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        result = _run(auth_service.google_auth("fake-google-id-token"))

    assert result["success"] is True
    assert result["access_token"] == "at-1"
    assert result["tenant_id"] == "tenant-google-new"
    assert inserted["tenant"]["supabase_user_id"] == "sb-google-1"


def test_google_auth_maps_to_the_existing_tenant_on_repeat_sign_in():
    existing = {"id": "tenant-1", "email": "returning@example.com", "supabase_user_id": "sb-google-2", "is_active": True}

    def fake_signin_id_token(credential, provider):
        return {
            "access_token": "at-2", "refresh_token": "rt-2", "token_type": "bearer", "expires_in": 3600,
            "user": {"id": "sb-google-2", "email": "returning@example.com", "user_metadata": {}},
        }

    def fake_select(table, params=None):
        params = params or {}
        if table == "tenants" and params.get("supabase_user_id") == "eq.sb-google-2":
            return [existing]
        return []

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_id_token", side_effect=fake_signin_id_token), \
         patch("src.services.auth_service.supabase_select", side_effect=fake_select), \
         patch("src.services.auth_service.supabase_insert") as mock_insert, \
         patch("src.services.auth_service.supabase_update", return_value={}):
        auth_service = AuthService()
        result = _run(auth_service.google_auth("fake-google-id-token"))

    assert result["success"] is True
    assert result["tenant_id"] == "tenant-1"
    mock_insert.assert_not_called()  # no duplicate tenant created for a returning Google user


def test_google_auth_failure_returns_generic_error():
    def fake_signin_id_token(credential, provider):
        raise GoTrueError("Invalid Google token", 401)

    with patch("src.services.auth_service.supabase_gotrue.sign_in_with_id_token", side_effect=fake_signin_id_token):
        auth_service = AuthService()
        result = _run(auth_service.google_auth("bad-token"))

    assert result["success"] is False
    assert result["error"] == "Google sign-in failed"
