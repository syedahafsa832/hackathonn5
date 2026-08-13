"""
CORS origin allowlist (deployment-closure fix).

Previously allow_origins=["*"] + allow_credentials=True - Starlette's
CORSMiddleware reflects the request's actual Origin header back in that
combination, which functionally trusts every origin with credentials, not
a real restriction. Replaced with an environment-driven allowlist:
FRONTEND_URL (already required to be correct in production for the Shopify
OAuth redirect - reused here rather than a new, separately-configured
value) plus an optional CORS_ALLOWED_ORIGINS for extra origins. Falls back
to local-dev-only origins, never a wildcard, when neither is set.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.middleware.cors import _get_allowed_origins  # noqa: E402


def test_uses_frontend_url_when_set():
    with patch.dict(os.environ, {"FRONTEND_URL": "https://dashboard.tresolv.online"}, clear=True):
        origins = _get_allowed_origins()
    assert origins == ["https://dashboard.tresolv.online"]


def test_never_returns_wildcard():
    with patch.dict(os.environ, {}, clear=True):
        origins = _get_allowed_origins()
    assert "*" not in origins


def test_falls_back_to_localhost_only_when_unconfigured():
    with patch.dict(os.environ, {}, clear=True):
        origins = _get_allowed_origins()
    assert set(origins) == {"http://localhost:5173", "http://127.0.0.1:5173"}


def test_combines_frontend_url_and_extra_origins():
    env = {
        "FRONTEND_URL": "https://dashboard.tresolv.online/",
        "CORS_ALLOWED_ORIGINS": "https://staging.tresolv.online, https://preview.tresolv.online",
    }
    with patch.dict(os.environ, env, clear=True):
        origins = _get_allowed_origins()
    # Trailing slash stripped so it matches the exact Origin header browsers send.
    assert set(origins) == {
        "https://dashboard.tresolv.online",
        "https://staging.tresolv.online",
        "https://preview.tresolv.online",
    }


def test_blank_extra_origins_entries_are_ignored():
    env = {"FRONTEND_URL": "https://dashboard.tresolv.online", "CORS_ALLOWED_ORIGINS": " , ,"}
    with patch.dict(os.environ, env, clear=True):
        origins = _get_allowed_origins()
    assert origins == ["https://dashboard.tresolv.online"]
