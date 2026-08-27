"""
CORS origin allowlist (deployment-closure fix).

Previously allow_origins=["*"] + allow_credentials=True - Starlette's
CORSMiddleware reflects the request's actual Origin header back in that
combination, which functionally trusts every origin with credentials, not
a real restriction. Replaced with an environment-driven allowlist:
FRONTEND_URL (already required to be correct in production for the Shopify
OAuth redirect - reused here rather than a new, separately-configured
value) plus an optional CORS_ALLOWED_ORIGINS for extra origins.

The real production origin (https://app.tresolv.online) and local-dev
origins are always included, regardless of env config - this app's live
CORS config was silently dead code (never wired into main.py, which used
a raw allow_origins=["*"] instead), so a missing/misconfigured env var
must never again be able to either reopen this to a wildcard or lock the
real frontend out. See test_cors_middleware_wiring.py for proof this is
actually applied to the running app, and WIDGET_PATH_PREFIX's own carve-out
for the embeddable chat widget, which must stay open to any origin.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.middleware.cors import _get_allowed_origins, _PRODUCTION_ORIGIN  # noqa: E402

_BASELINE = {_PRODUCTION_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"}


def test_always_includes_the_real_production_origin():
    with patch.dict(os.environ, {}, clear=True):
        origins = _get_allowed_origins()
    assert _PRODUCTION_ORIGIN in origins


def test_production_origin_included_even_when_frontend_url_set_to_something_else():
    """A wrong/stale FRONTEND_URL in Render must never lock the real
    frontend out of its own backend."""
    with patch.dict(os.environ, {"FRONTEND_URL": "https://staging.tresolv.online"}, clear=True):
        origins = _get_allowed_origins()
    assert _PRODUCTION_ORIGIN in origins
    assert "https://staging.tresolv.online" in origins


def test_never_returns_wildcard():
    with patch.dict(os.environ, {}, clear=True):
        origins = _get_allowed_origins()
    assert "*" not in origins


def test_falls_back_to_baseline_only_when_unconfigured():
    with patch.dict(os.environ, {}, clear=True):
        origins = _get_allowed_origins()
    assert set(origins) == _BASELINE


def test_combines_frontend_url_and_extra_origins():
    env = {
        "FRONTEND_URL": "https://dashboard.tresolv.online/",
        "CORS_ALLOWED_ORIGINS": "https://staging.tresolv.online, https://preview.tresolv.online",
    }
    with patch.dict(os.environ, env, clear=True):
        origins = _get_allowed_origins()
    # Trailing slash stripped so it matches the exact Origin header browsers send.
    assert set(origins) == _BASELINE | {
        "https://dashboard.tresolv.online",
        "https://staging.tresolv.online",
        "https://preview.tresolv.online",
    }


def test_blank_extra_origins_entries_are_ignored():
    env = {"FRONTEND_URL": "https://dashboard.tresolv.online", "CORS_ALLOWED_ORIGINS": " , ,"}
    with patch.dict(os.environ, env, clear=True):
        origins = _get_allowed_origins()
    assert set(origins) == _BASELINE | {"https://dashboard.tresolv.online"}
