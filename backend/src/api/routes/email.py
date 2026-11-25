"""Inbound routes for email delivered by Cloudflare Email Routing."""
import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Email"])
_MAX_RAW_EMAIL_BYTES = 25 * 1024 * 1024


@router.post("/email/inbound")
async def receive_forwarded_email(request: Request):
    """Accept raw RFC822 email from the authenticated Cloudflare Worker.

    This endpoint is intentionally not exposed to end users. The Worker holds
    a random shared secret; the backend resolves a brand from the opaque
    envelope recipient address.
    """
    expected_secret = os.getenv("INBOUND_EMAIL_SHARED_SECRET", "")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="Inbound email is not configured")

    authorization = request.headers.get("authorization", "")
    scheme, _, supplied_secret = authorization.partition(" ")
    if scheme.lower() != "bearer" or not supplied_secret or not hmac.compare_digest(
        supplied_secret, expected_secret
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    envelope_to = request.headers.get("x-tresolv-envelope-to", "").strip()
    if not envelope_to:
        raise HTTPException(status_code=400, detail="Missing email recipient")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty email")
    if len(raw) > _MAX_RAW_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Email exceeds the inbound size limit")

    try:
        from src.services.email_forwarding_service import process_forwarded_email

        result = await process_forwarded_email(raw, envelope_to)
    except ValueError as exc:
        logger.info("[InboundEmail] Rejected malformed forwarded email: %s", exc)
        raise HTTPException(status_code=422, detail="Email could not be parsed") from exc
    except Exception:
        logger.exception("[InboundEmail] Failed to process forwarded email for %s", envelope_to)
        # Return a transient server error. The Worker deliberately does not
        # mark a 5xx response as successful delivery.
        raise HTTPException(status_code=503, detail="Temporary inbound processing failure")

    if not result.get("accepted"):
        status = result.get("status")
        if status == "unknown_recipient":
            raise HTTPException(status_code=404, detail="Unknown forwarding address")
        raise HTTPException(status_code=400, detail="Invalid forwarding address")

    return JSONResponse(status_code=202, content=result)
