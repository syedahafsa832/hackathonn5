import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _get_allowed_origins() -> list[str]:
    """Origins allowed to make credentialed requests to this API.

    allow_origins=["*"] combined with allow_credentials=True (the previous
    config) is a real gap: Starlette's CORSMiddleware handles that specific
    combination by reflecting back whatever Origin header the request sent,
    which functionally means ANY origin is trusted with credentials - not a
    hardening no-op.

    FRONTEND_URL already has to be set correctly in production for the
    Shopify OAuth redirect to work (shopify_auth.py, brand_gmail.py) - reused
    here as the primary allowed origin rather than inventing a new,
    separately-configured value. CORS_ALLOWED_ORIGINS (comma-separated) is
    for any additional legitimate origins (e.g. a second custom domain).
    With neither set, this fails closed to local-dev-only origins rather
    than falling back to a wildcard.
    """
    origins = set()
    frontend_url = os.getenv("FRONTEND_URL")
    if frontend_url:
        origins.add(frontend_url.rstrip("/"))
    extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
    for o in extra.split(","):
        o = o.strip().rstrip("/")
        if o:
            origins.add(o)
    if not origins:
        origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    return sorted(origins)


def add_cors_middleware(app: FastAPI):
    """
    Add CORS middleware to the FastAPI application
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Allow specific headers if needed
        # expose_headers=["Access-Control-Allow-Origin"]
    )

    return app
