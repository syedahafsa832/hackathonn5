"""
Proves the CORS fix is actually wired into the running app - not just
correct in isolation. _get_allowed_origins() being right doesn't help if
nothing ever calls add_cors_middleware(app), which is exactly how the
previous config went stale: src/api/middleware/cors.py existed with a
correct, restrictive allowlist, but main.py never imported or called it,
so the live app ran a raw CORSMiddleware(allow_origins=["*"]) instead.

Builds a minimal FastAPI app the same way (add_cors_middleware + a widget
route + a dashboard route) rather than importing the real main.py, which
has heavy side-effecting imports unsuited to a unit test.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.middleware.cors import add_cors_middleware, WIDGET_PATH_PREFIX, _PRODUCTION_ORIGIN  # noqa: E402


def _make_app():
    app = FastAPI()
    add_cors_middleware(app)

    @app.get("/api/v1/auth/me")
    async def dashboard_route():
        return {"ok": True}

    @app.post(f"{WIDGET_PATH_PREFIX}chat")
    async def widget_route():
        return {"ok": True}

    return app


def _preflight(client, path, origin):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET" if "widget" not in path else "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_dashboard_route_allows_the_real_production_origin():
    client = TestClient(_make_app())
    resp = _preflight(client, "/api/v1/auth/me", _PRODUCTION_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _PRODUCTION_ORIGIN


def test_dashboard_route_rejects_an_untrusted_origin():
    client = TestClient(_make_app())
    resp = _preflight(client, "/api/v1/auth/me", "https://evil.example.com")
    # Starlette answers preflight with 400 and omits Access-Control-Allow-Origin
    # for a disallowed origin - the browser blocks the real request either way.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"
    assert resp.headers.get("access-control-allow-origin") != "*"


def test_dashboard_route_never_reflects_wildcard():
    client = TestClient(_make_app())
    resp = _preflight(client, "/api/v1/auth/me", _PRODUCTION_ORIGIN)
    assert resp.headers.get("access-control-allow-origin") != "*"


def test_widget_route_stays_open_to_any_merchant_storefront_origin():
    client = TestClient(_make_app())
    resp = _preflight(client, f"{WIDGET_PATH_PREFIX}chat", "https://some-random-merchant.myshopify.com")
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_actual_request_not_just_preflight_gets_the_header_too():
    """Regression for the 401-response case: an error response must still
    carry the CORS header, or the browser hides even a legitimate 401 body
    from the frontend's error handling."""
    client = TestClient(_make_app())
    resp = client.get("/api/v1/auth/me", headers={"Origin": _PRODUCTION_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == _PRODUCTION_ORIGIN


def test_missing_frontend_url_env_var_does_not_break_the_real_frontend():
    """The literal bug this fix closes: a Render deploy with FRONTEND_URL
    unset or wrong must not lock https://app.tresolv.online out."""
    with patch.dict(os.environ, {}, clear=True):
        client = TestClient(_make_app())
        resp = _preflight(client, "/api/v1/auth/me", _PRODUCTION_ORIGIN)
    assert resp.headers.get("access-control-allow-origin") == _PRODUCTION_ORIGIN
