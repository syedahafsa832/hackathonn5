"""
Rate limiting on authentication endpoints (security audit finding A3).

/register, /login, /refresh, /change-password had no rate limiting at all,
despite being the primary brute-force/credential-stuffing surface. Reuses
the same slowapi Limiter already used elsewhere in this codebase (main.py),
extracted to src/lib/rate_limiter.py so route modules can share the one
instance without a circular import.

Only /login and /register are exercised here (the two unauthenticated,
most-exposed endpoints) - /refresh and /change-password use the same
@limiter.limit(...) mechanism and are covered by code review, not
duplicated here to keep this file focused.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes.saas_auth import router as saas_auth_router  # noqa: E402
from src.lib.rate_limiter import limiter  # noqa: E402

app = FastAPI()
app.include_router(saas_auth_router, prefix="/api/v1")
client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def test_login_is_rate_limited_after_repeated_attempts():
    with patch(
        "src.services.auth_service.auth_service.login",
        new=AsyncMock(return_value={"success": False, "error": "Invalid email or password"}),
    ):
        statuses = []
        for _ in range(15):
            resp = client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"})
            statuses.append(resp.status_code)

    assert 429 in statuses, f"Expected a 429 among repeated login attempts, got: {statuses}"
    # The limit only kicks in after the configured threshold - earlier
    # attempts should still get the real (401) response, not be blocked
    # immediately, so legitimate users retrying a typo aren't punished.
    assert statuses[0] == 401


def test_register_is_rate_limited_after_repeated_attempts():
    with patch(
        "src.services.auth_service.auth_service.register",
        new=AsyncMock(return_value={"success": False, "error": "Email already registered"}),
    ):
        statuses = []
        for _ in range(15):
            resp = client.post("/api/v1/auth/register", json={"email": "a@b.com", "password": "supersecret123"})
            statuses.append(resp.status_code)

    assert 429 in statuses, f"Expected a 429 among repeated register attempts, got: {statuses}"
