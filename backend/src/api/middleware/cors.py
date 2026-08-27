import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# The live production dashboard origin. Always included regardless of how
# FRONTEND_URL/CORS_ALLOWED_ORIGINS are set in the deployment environment,
# so a missing/incorrect env var in Render can never silently reopen this
# to a wildcard or lock the real frontend out - see the CORS outage this
# fixed (browser preflight failures against backend.tresolv.online for
# /api/v1/auth/me, /api/brands, /api/v1/settings/*, /api/v1/actions/pending,
# /api/v1/quarantine, /api/tickets).
_PRODUCTION_ORIGIN = "https://app.tresolv.online"

# Endpoints the embeddable chat widget calls from arbitrary, unknown-in-
# advance merchant Shopify storefronts - these must stay open to any
# origin (no cookies/credentials involved either way), so they're excluded
# from the strict dashboard allowlist below rather than forcing a single
# fixed origin onto every route.
WIDGET_PATH_PREFIX = "/api/v2/widget/"


def _get_allowed_origins() -> list[str]:
    """Origins allowed to reach the dashboard/admin API (everything except
    the public widget routes - see WIDGET_PATH_PREFIX).

    allow_origins=["*"] combined with allow_credentials=True is a real gap:
    Starlette's CORSMiddleware handles that specific combination by
    reflecting back whatever Origin header the request sent, which
    functionally means ANY origin is trusted with credentials - not a
    hardening no-op. This app authenticates via an explicit
    `Authorization: Bearer <token>` header only (no cookies), so
    allow_credentials stays False regardless.

    FRONTEND_URL already has to be set correctly in production for the
    Shopify OAuth redirect to work (shopify_auth.py, brand_gmail.py) - reused
    here as an additional allowed origin. CORS_ALLOWED_ORIGINS
    (comma-separated) is for any other legitimate origins (staging/preview
    domains). The real production origin and local-dev origins are always
    included so a missing env var here can never reopen this to "*" or
    lock out the actual frontend.
    """
    origins = {_PRODUCTION_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"}
    frontend_url = os.getenv("FRONTEND_URL")
    if frontend_url:
        origins.add(frontend_url.rstrip("/"))
    extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
    for o in extra.split(","):
        o = o.strip().rstrip("/")
        if o:
            origins.add(o)
    return sorted(origins)


class _PathAwareCORSMiddleware:
    """Starlette's CORSMiddleware only supports one origin policy for the
    whole app. This dispatches each request to one of two pre-built
    CORSMiddleware instances by path, reusing its exact (already-correct)
    preflight/simple-response handling rather than reimplementing it:
    - WIDGET_PATH_PREFIX routes: Access-Control-Allow-Origin: * (unchanged
      behavior - must stay callable from any merchant storefront).
    - every other route (the dashboard/admin API): restricted to
      _get_allowed_origins(), never a wildcard.
    """

    def __init__(self, app):
        self._widget_cors = CORSMiddleware(
            app, allow_origins=["*"], allow_credentials=False,
            allow_methods=["*"], allow_headers=["*"],
        )
        self._dashboard_cors = CORSMiddleware(
            app, allow_origins=_get_allowed_origins(), allow_credentials=False,
            allow_methods=["*"], allow_headers=["*"],
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(WIDGET_PATH_PREFIX):
            await self._widget_cors(scope, receive, send)
        else:
            await self._dashboard_cors(scope, receive, send)


def add_cors_middleware(app: FastAPI):
    """Add CORS middleware to the FastAPI application."""
    app.add_middleware(_PathAwareCORSMiddleware)
    return app
