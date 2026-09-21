"""
Careers: applicant -> team member selection handler.

Lives in the existing tResolv backend (no separate deployment) but is fully isolated:
it talks ONLY to the separate Careers Supabase project, never to the product database.

Flow (POST /api/careers/selection, called by the /careers/admin dashboard):
  1. verify the caller's Supabase JWT and that they are an admin (via RLS, with THEIR token)
  2. re-check server-side that the application is really `selected`
  3. find/create the team_members row (unique per application) and the Supabase Auth user
  4. claim the welcome email (idempotent: never sent twice unless `resend` is asked for)
  5. generate a single-use magic-link token and send the branded email via the existing Resend sender

Required env (Careers project): CAREERS_SUPABASE_URL, CAREERS_SUPABASE_ANON_KEY,
CAREERS_SUPABASE_SERVICE_KEY (server-only), CAREERS_PORTAL_URL (e.g. https://www.tresolv.online).
The careers site origin must be listed in CORS_ALLOWED_ORIGINS.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.services import system_email_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/careers", tags=["careers"])

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_CLAIM_TTL = timedelta(minutes=5)      # an in-flight send blocks duplicates for this long
_RESEND_COOLDOWN = timedelta(seconds=60)


def _cfg():
    url = (os.getenv("CAREERS_SUPABASE_URL") or "").rstrip("/")
    anon = os.getenv("CAREERS_SUPABASE_ANON_KEY") or ""
    service = os.getenv("CAREERS_SUPABASE_SERVICE_KEY") or ""
    portal = (os.getenv("CAREERS_PORTAL_URL") or "https://www.tresolv.online").rstrip("/")
    if not (url and anon and service):
        raise HTTPException(503, "careers selection is not configured")
    return url, anon, service, portal


def _svc(anon: str, service: str, **extra) -> dict:
    return {"apikey": service, "Authorization": f"Bearer {service}", "Content-Type": "application/json", **extra}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class SelectionRequest(BaseModel):
    application_id: str
    resend: bool = False


async def _require_admin(client: httpx.AsyncClient, url: str, anon: str, authorization: Optional[str]) -> None:
    """Real authorization: the caller's own Supabase JWT + RLS on admin_users (never a client claim)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "not signed in")
    token = authorization.split(" ", 1)[1].strip()
    who = await client.get(f"{url}/auth/v1/user", headers={"apikey": anon, "Authorization": f"Bearer {token}"})
    if who.status_code != 200 or not who.json().get("id"):
        raise HTTPException(401, "not signed in")
    uid = who.json()["id"]
    row = await client.get(
        f"{url}/rest/v1/admin_users?select=user_id&user_id=eq.{uid}",
        headers={"apikey": anon, "Authorization": f"Bearer {token}"},
    )
    if row.status_code != 200 or not row.json():
        raise HTTPException(403, "not an admin")


def _role_label(slug: str) -> str:
    return (slug or "team member").replace("-", " ").title()


def _welcome_email(name: str, role: str, link: str, portal: str):
    first = (name or "there").strip().split(" ")[0]
    subject = "you're officially on the tResolv team 👀"
    body = (
        f"Hey {first} 👋<br><br><b>You’re officially in.</b><br><br>"
        "We reviewed your application and decided we want you on the tResolv team.<br><br>"
        f"You’ll be joining us as:<br><b>{role}</b><br><br>"
        "Your first step is to get into your private team portal. Inside, you’ll find your onboarding guide, "
        "role information, documents, tasks, and everything you need to get started.<br><br>"
        "Welcome to tResolv :)<br>Hafsa<br>Founder, tResolv"
    )
    foot = (
        "This link is private to your account and works once. Please don’t share it with anyone else. "
        f"If it has expired, go to {portal}/team and enter this email address to get a fresh one."
    )
    html = system_email_service._shell(
        preheader="you're officially on the team", heading="you're in 👀", body_html=body,
        action_label="ENTER YOUR TEAM PORTAL →", action_url=link, footnote=foot,
    )
    text = (
        f"Hey {first},\n\nYou're officially in.\n\nWe reviewed your application and decided we want you on the "
        f"tResolv team.\n\nYou'll be joining us as: {role}\n\nYour first step is to get into your private team portal:\n"
        f"{link}\n\n{foot}\n\nWelcome to tResolv :)\nHafsa\nFounder, tResolv"
    )
    return subject, html, text


@router.post("/selection")
async def handle_selection(req: SelectionRequest, authorization: Optional[str] = Header(None)):
    url, anon, service, portal = _cfg()
    if not _UUID_RE.match(req.application_id):
        raise HTTPException(400, "invalid application id")

    async with httpx.AsyncClient(timeout=20) as c:
        await _require_admin(c, url, anon, authorization)

        # 2. the application must really be `selected` (checked here, not trusted from the browser)
        r = await c.get(f"{url}/rest/v1/applications?id=eq.{req.application_id}&select=id,full_name,email,role,status",
                        headers=_svc(anon, service))
        apps = r.json() if r.status_code == 200 else []
        if not apps:
            raise HTTPException(404, "application not found")
        app = apps[0]
        if app["status"] != "selected":
            raise HTTPException(409, "application is not selected")

        # 3a. team member row (unique per application)
        async def get_tm():
            g = await c.get(f"{url}/rest/v1/team_members?application_id=eq.{app['id']}&select=*", headers=_svc(anon, service))
            return (g.json() or [None])[0] if g.status_code == 200 else None

        tm = await get_tm()
        if not tm:
            ins = await c.post(f"{url}/rest/v1/team_members", headers=_svc(anon, service, Prefer="return=representation"),
                               json={"application_id": app["id"], "email": app["email"].strip().lower(),
                                     "name": app["full_name"], "role": _role_label(app["role"])})
            tm = (ins.json() or [None])[0] if ins.status_code in (200, 201) else await get_tm()
            if not tm:
                logger.error("[Careers] team member create failed (%s)", ins.status_code)
                raise HTTPException(502, "could not create team member")

        if tm["status"] != "active":
            return {"status": "inactive", "team_member_id": tm["id"]}

        # 4. idempotency
        now = datetime.now(timezone.utc)
        if tm.get("welcome_email_sent_at") and not req.resend:
            return {"status": "already_sent", "team_member_id": tm["id"], "welcome_email_sent_at": tm["welcome_email_sent_at"]}
        ttl = _RESEND_COOLDOWN if req.resend else _CLAIM_TTL
        sent_filter = "" if req.resend else "&welcome_email_sent_at=is.null"
        claim = await c.patch(
            f"{url}/rest/v1/team_members?id=eq.{tm['id']}{sent_filter}"
            f"&or=(welcome_email_claimed_at.is.null,welcome_email_claimed_at.lt.{_iso(now - ttl)})",
            headers=_svc(anon, service, Prefer="return=representation"), json={"welcome_email_claimed_at": _iso(now)})
        if claim.status_code != 200 or not claim.json():
            return {"status": "in_progress", "team_member_id": tm["id"]}

        async def release():
            await c.patch(f"{url}/rest/v1/team_members?id=eq.{tm['id']}", headers=_svc(anon, service),
                          json={"welcome_email_claimed_at": None})

        try:
            # 3b. auth user (create if missing; "already exists" is fine)
            email = tm["email"]
            await c.post(f"{url}/auth/v1/admin/users", headers=_svc(anon, service), json={"email": email, "email_confirm": True})
            # 5. single-use magic-link token (returned to us, never to the browser)
            gl = await c.post(f"{url}/auth/v1/admin/generate_link", headers=_svc(anon, service),
                              json={"type": "magiclink", "email": email})
            body = gl.json() if gl.status_code == 200 else {}
            token_hash = body.get("hashed_token") or (body.get("properties") or {}).get("hashed_token")
            auth_id = body.get("id") or (body.get("user") or {}).get("id")
            if not token_hash or not auth_id:
                logger.error("[Careers] generate_link failed (%s)", gl.status_code)
                await release()
                raise HTTPException(502, "could not create sign-in link")

            if not tm.get("auth_user_id"):
                await c.patch(f"{url}/rest/v1/team_members?id=eq.{tm['id']}&auth_user_id=is.null", headers=_svc(anon, service),
                              json={"auth_user_id": auth_id})
            elif tm["auth_user_id"] != auth_id:
                logger.error("[Careers] auth user mismatch for team member %s", tm["id"])
                await release()
                raise HTTPException(409, "account mismatch")

            link = f"{portal}/team/auth?token_hash={token_hash}"
            subject, html, text = _welcome_email(tm["name"], tm["role"], link, portal)
            if not system_email_service._send(email, subject, html, text):
                await release()
                raise HTTPException(502, "email could not be sent")

            for _ in range(2):  # record the send (retry once: a lost write could mean a duplicate later)
                done = await c.patch(f"{url}/rest/v1/team_members?id=eq.{tm['id']}", headers=_svc(anon, service),
                                     json={"welcome_email_sent_at": _iso(datetime.now(timezone.utc)), "welcome_email_claimed_at": None})
                if done.status_code in (200, 204):
                    break
            return {"status": "sent", "team_member_id": tm["id"]}
        except HTTPException:
            raise
        except Exception:
            logger.exception("[Careers] selection failed")
            await release()
            raise HTTPException(502, "selection failed")
