# RESOLV — MULTI-TENANT ISOLATION FIX
# Paste this into Claude Code from your project root.

## THE PROBLEM

Resolv has a critical multi-tenant security flaw: when User A connects a Gmail account,
User B (a completely different organization) sees the same Gmail account as connected.

This means:
- Gmail OAuth tokens are not scoped to tenants
- Backend queries are missing organization_id / brand_id filters
- OAuth callback does not bind tokens to the initiating tenant
- Supabase RLS may not be enforcing tenant isolation at the database level
- There may be module-level Gmail client singletons that bleed across tenants

This is a security incident. No new features should be built until this is fixed.

## YOUR TASK

Audit and fix tenant isolation across the entire backend (hack5/).
Work through the checklist below in order.
After each fix, state what you changed and why.
Do not move to the next item until the current one is verified.

---

## STEP 1: AUDIT THE DATA MODEL

Read the database models (SQLAlchemy models or Supabase schema files in hack5/).

Find every table that stores tenant-specific data. Expected tables include:
gmail_integrations (or gmail_credentials, oauth_tokens, or similar)
brands
tickets
actions
send_tasks
knowledge_base / rag_chunks
audit_logs

For each table, check:
A. Does it have an organization_id column? (UUID FK to organizations/tenants table)
B. Does it have a brand_id column where appropriate?
C. Is there a UNIQUE constraint on (brand_id) for gmail_integrations?
   (One Gmail per brand — no shared rows)

Report: list every table, whether organization_id exists, whether it needs it.

---

## STEP 2: ADD MISSING COLUMNS

For any table missing organization_id or brand_id, generate and run the migration:

```sql
-- Example pattern — adapt to actual table names found in Step 1

ALTER TABLE gmail_integrations
  ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id),
  ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id),
  ADD COLUMN IF NOT EXISTS connected_by_user_id UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- Enforce one Gmail per brand
ALTER TABLE gmail_integrations
  DROP CONSTRAINT IF EXISTS unique_gmail_per_brand;
ALTER TABLE gmail_integrations
  ADD CONSTRAINT unique_gmail_per_brand UNIQUE (brand_id);

-- Add oauth_state_tokens table for secure OAuth callback ownership
CREATE TABLE IF NOT EXISTS oauth_state_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_token VARCHAR(64) NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES users(id),
    brand_id UUID NOT NULL REFERENCES brands(id),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    provider VARCHAR(20) NOT NULL DEFAULT 'gmail',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_oauth_state ON oauth_state_tokens(state_token, used, expires_at);
```

After adding columns, backfill organization_id on existing rows:
- Join gmail_integrations to users via connected_by_user_id
- Set organization_id = users.organization_id
- If no user linkage exists, set a placeholder (the first organization in the DB)
- Report how many rows were backfilled

---

## STEP 3: ENABLE SUPABASE RLS

For every tenant-scoped table, enable RLS and add isolation policies.
Run these SQL commands against the Supabase database:

```sql
-- Enable RLS (blocks all access by default until policies are added)
ALTER TABLE gmail_integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE brands ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE actions ENABLE ROW LEVEL SECURITY;

-- Drop any existing overly-permissive policies first
DROP POLICY IF EXISTS "allow_all" ON gmail_integrations;
DROP POLICY IF EXISTS "authenticated_access" ON gmail_integrations;

-- Gmail integrations: only visible to users in the same organization
CREATE POLICY "org_isolation_gmail_integrations"
ON gmail_integrations FOR ALL TO authenticated
USING (
    organization_id IN (
        SELECT organization_id FROM users WHERE id = auth.uid()
    )
);

-- Brands: only visible to users in the same organization
CREATE POLICY "org_isolation_brands"
ON brands FOR ALL TO authenticated
USING (
    organization_id IN (
        SELECT organization_id FROM users WHERE id = auth.uid()
    )
);

-- Tickets: only visible to the brand's organization
CREATE POLICY "org_isolation_tickets"
ON tickets FOR ALL TO authenticated
USING (
    brand_id IN (
        SELECT id FROM brands WHERE organization_id IN (
            SELECT organization_id FROM users WHERE id = auth.uid()
        )
    )
);

-- Actions: same pattern
CREATE POLICY "org_isolation_actions"
ON actions FOR ALL TO authenticated
USING (
    brand_id IN (
        SELECT id FROM brands WHERE organization_id IN (
            SELECT organization_id FROM users WHERE id = auth.uid()
        )
    )
);
```

Note: The FastAPI backend uses a service role key (bypasses RLS for internal queries).
These RLS policies are a defense-in-depth layer — they protect against direct DB access
and any Python query that accidentally lacks a WHERE clause.
The Python code must ALSO filter by organization_id. Both layers are required.

---

## STEP 4: CREATE THE TENANT CONTEXT DEPENDENCY

In hack5/, create or update the auth dependency that every protected endpoint uses.
File: hack5/src/auth/dependencies.py (or equivalent)

This dependency must:
1. Decode the JWT token
2. Load the user from DB
3. Load the user's organization_id
4. Return a TenantContext object that every endpoint can use

```python
from dataclasses import dataclass
from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

@dataclass
class TenantContext:
    user_id: UUID
    organization_id: UUID
    role: str
    
    def verify_brand_ownership(self, brand_id: UUID, db: Session) -> "Brand":
        """
        Verify a brand belongs to this tenant. Raises 404 if not found or wrong org.
        Never raises 403 — don't confirm the resource exists to the wrong tenant.
        """
        brand = db.query(Brand).filter(
            Brand.id == brand_id,
            Brand.organization_id == self.organization_id
        ).first()
        if not brand:
            raise HTTPException(status_code=404, detail="Brand not found")
        return brand


async def get_tenant_context(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> TenantContext:
    """
    The single auth dependency used by ALL protected endpoints.
    Validates JWT, loads user, returns TenantContext.
    Never returns a context without a valid organization_id.
    """
    try:
        payload = decode_jwt(token)
        user_id = payload.get("user_id") or payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.organization_id:
        raise HTTPException(status_code=401, detail="User has no organization")
    
    return TenantContext(
        user_id=UUID(str(user.id)),
        organization_id=UUID(str(user.organization_id)),
        role=user.role or "member"
    )
```

Inject this into EVERY endpoint in every route file that accesses tenant data:
```python
@router.get("/settings/gmail/status")
async def get_gmail_status(
    brand_id: UUID,
    ctx: TenantContext = Depends(get_tenant_context),  # ← always present
    db: Session = Depends(get_db)
):
    ...
```

---

## STEP 5: FIX THE GMAIL ROUTES

File: hack5/src/api/routes/ — find the Gmail settings routes.

### 5A. GET /settings/gmail/status

Replace the current implementation with:
```python
@router.get("/settings/gmail/status")
async def get_gmail_status(
    brand_id: UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    # Verify brand belongs to this tenant — raises 404 if not
    ctx.verify_brand_ownership(brand_id, db)
    
    # Query ALWAYS scoped to both brand_id AND organization_id
    integration = db.query(GmailIntegration).filter(
        GmailIntegration.brand_id == brand_id,
        GmailIntegration.organization_id == ctx.organization_id,
        GmailIntegration.is_active == True
    ).first()
    
    return {
        "connected": integration is not None,
        "email": integration.gmail_email if integration else None,
        "last_used_at": integration.last_used_at if integration else None
    }
```

### 5B. GET /settings/gmail/connect (initiate OAuth)

```python
import secrets

@router.get("/settings/gmail/connect")
async def initiate_gmail_oauth(
    brand_id: UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    # Verify brand ownership
    ctx.verify_brand_ownership(brand_id, db)
    
    # Create a signed state token that encodes tenant identity
    state_token = secrets.token_urlsafe(32)
    state_record = OAuthStateToken(
        state_token=state_token,
        user_id=ctx.user_id,
        brand_id=brand_id,
        organization_id=ctx.organization_id,
        provider="gmail",
        expires_at=datetime.utcnow() + timedelta(minutes=10)
    )
    db.add(state_record)
    db.commit()
    
    # Build Google OAuth URL with state embedded
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join([
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/userinfo.email"
        ]),
        "access_type": "offline",
        "prompt": "consent",  # forces refresh_token to be returned
        "state": state_token  # ← this is the tenant identity carrier
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return RedirectResponse(auth_url)
```

### 5C. GET /settings/gmail/callback (OAuth callback)

```python
@router.get("/settings/gmail/callback")
async def gmail_oauth_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db)
    # NOTE: No TenantContext here — user is not authenticated in callback
    # The state token IS the proof of identity
):
    frontend_base = settings.FRONTEND_URL + "/settings"
    
    if error or not code or not state:
        return RedirectResponse(f"{frontend_base}?tab=email&error=oauth_failed")
    
    # Look up state token — this proves which tenant initiated the OAuth
    state_record = db.query(OAuthStateToken).filter(
        OAuthStateToken.state_token == state,
        OAuthStateToken.provider == "gmail",
        OAuthStateToken.used == False,
        OAuthStateToken.expires_at > datetime.utcnow()
    ).first()
    
    if not state_record:
        # State missing, expired, or already used (replay attack)
        return RedirectResponse(f"{frontend_base}?tab=email&error=invalid_state")
    
    # Immediately mark as used to prevent replay attacks
    state_record.used = True
    db.commit()
    
    # Exchange authorization code for tokens
    try:
        token_response = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code"
        })
        token_data = token_response.json()
        
        if "error" in token_data:
            raise ValueError(token_data["error"])
        
        refresh_token = token_data.get("refresh_token")
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        
    except Exception as e:
        logging.error(f"Token exchange failed: {e}")
        return RedirectResponse(f"{frontend_base}?tab=email&error=token_exchange_failed")
    
    # Get the Gmail email address to display to the user
    try:
        profile_resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        gmail_email = profile_resp.json().get("email")
    except Exception:
        gmail_email = "unknown@gmail.com"
    
    # Upsert credential — ALWAYS scoped to the brand from state_record
    existing = db.query(GmailIntegration).filter(
        GmailIntegration.brand_id == state_record.brand_id
    ).first()
    
    if existing:
        existing.gmail_email = gmail_email
        existing.access_token_encrypted = encrypt(access_token)
        if refresh_token:  # Google only returns refresh_token on first consent
            existing.refresh_token_encrypted = encrypt(refresh_token)
        existing.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        existing.organization_id = state_record.organization_id
        existing.connected_by_user_id = state_record.user_id
        existing.connected_at = datetime.utcnow()
        existing.is_active = True
    else:
        if not refresh_token:
            # Cannot create a new integration without a refresh token
            return RedirectResponse(f"{frontend_base}?tab=email&error=no_refresh_token")
        
        integration = GmailIntegration(
            brand_id=state_record.brand_id,
            organization_id=state_record.organization_id,
            gmail_email=gmail_email,
            refresh_token_encrypted=encrypt(refresh_token),
            access_token_encrypted=encrypt(access_token),
            token_expiry=datetime.utcnow() + timedelta(seconds=expires_in),
            connected_by_user_id=state_record.user_id,
            is_active=True
        )
        db.add(integration)
    
    db.commit()
    return RedirectResponse(f"{frontend_base}?tab=email&gmail_connected=true")
```

### 5D. DELETE /settings/gmail/disconnect

```python
@router.delete("/settings/gmail/disconnect")
async def disconnect_gmail(
    brand_id: UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    ctx.verify_brand_ownership(brand_id, db)
    
    integration = db.query(GmailIntegration).filter(
        GmailIntegration.brand_id == brand_id,
        GmailIntegration.organization_id == ctx.organization_id
    ).first()
    
    if integration:
        integration.is_active = False
        integration.access_token_encrypted = None  # clear cached access token
        db.commit()
    
    return {"disconnected": True}
```

---

## STEP 6: REMOVE ALL GMAIL CLIENT SINGLETONS

Search the entire hack5/ codebase for these patterns and eliminate them:

Search for:
- `_gmail_credentials = None` (module-level variable)
- `_gmail_client = None`
- `gmail_service = None`
- Any function that initializes a Gmail client without a brand_id parameter
- Any `global` keyword in gmail-related files

Replace any singleton pattern with a factory function that takes `brand_id` as a required
parameter and fetches credentials from DB every time (or from a short-lived cache keyed
by brand_id, NOT a single global):

```python
# WRONG — singleton
_client = None
def get_gmail():
    global _client
    if not _client:
        _client = load_gmail()
    return _client

# CORRECT — factory, always scoped to brand
async def get_gmail_client_for_brand(brand_id: UUID, db: Session) -> GmailClient:
    integration = db.query(GmailIntegration).filter(
        GmailIntegration.brand_id == brand_id,
        GmailIntegration.is_active == True
    ).first()
    if not integration:
        raise GmailNotConnectedError(brand_id)
    access_token = await maybe_refresh_token(integration, db)
    return GmailClient(access_token=access_token, brand_id=brand_id)
```

---

## STEP 7: FIX THE BACKGROUND WORKER

The worker polls Gmail for all connected brands. Find the worker file (worker.py or similar).

Ensure the worker:
1. Fetches ONLY brands with is_active=True Gmail integrations
2. Creates a fresh, isolated Gmail client per brand (never reuses across brands)
3. Wraps each brand's processing in an independent try/except
4. Never stores a brand's credentials in a variable that persists across loop iterations
5. Scopes all ticket creation to the brand's organization_id

```python
async def poll_all_brands(db: Session):
    """
    Correctly isolated multi-tenant polling loop.
    Each brand is fully independent. One failure never affects others.
    """
    integrations = db.query(GmailIntegration).filter(
        GmailIntegration.is_active == True
    ).all()
    
    for integration in integrations:
        try:
            # Get brand (includes organization_id for scoping downstream)
            brand = db.query(Brand).filter(
                Brand.id == integration.brand_id
            ).first()
            if not brand:
                continue
            
            # Each brand gets its own isolated client
            client = await get_gmail_client_for_brand(brand.id, db)
            
            # Poll and process — all operations scoped to brand.id and brand.organization_id
            await poll_and_process_brand(brand, client, db)
            
        except GmailNotConnectedError:
            integration.is_active = False
            db.commit()
        except Exception as e:
            # Log with brand context but NEVER let one brand's failure affect others
            logging.error(f"Poll failed for brand {integration.brand_id}: {e}", exc_info=True)
            continue
        finally:
            # Explicitly clear any brand-local variables
            pass


async def poll_and_process_brand(brand, client: GmailClient, db: Session):
    """All ticket creation here must include brand.organization_id"""
    messages = await client.fetch_unread_messages()
    
    for message in messages:
        existing_ticket = db.query(Ticket).filter(
            Ticket.gmail_thread_id == message.thread_id,
            Ticket.brand_id == brand.id,           # ← scoped to brand
            Ticket.organization_id == brand.organization_id  # ← scoped to org
        ).first()
        
        if existing_ticket:
            await add_message_to_ticket(existing_ticket, message, db)
        else:
            ticket = Ticket(
                brand_id=brand.id,
                organization_id=brand.organization_id,  # ← ALWAYS set this
                gmail_thread_id=message.thread_id,
                customer_email=message.from_email,
                subject=message.subject,
                channel="email",
                status="open"
            )
            db.add(ticket)
            db.commit()
```

---

## STEP 8: AUDIT ALL OTHER ROUTES FOR THE SAME BUG

Search hack5/src/api/routes/ for every endpoint that queries tenant data.

For each endpoint, verify it:
A. Has `ctx: TenantContext = Depends(get_tenant_context)` in the signature
B. Includes `organization_id == ctx.organization_id` in every DB query that touches
   brands, tickets, actions, or any tenant-specific table
C. Calls `ctx.verify_brand_ownership(brand_id, db)` before using any brand_id
   parameter received from the client

Files to audit:
- v2_tickets.py (or tickets.py)
- v2_actions.py (or actions.py)
- v2_brands.py (or brands.py)
- settings routes
- knowledge base routes
- any other route touching tenant data

For each missing check: add it. Do not skip any file.

---

## STEP 9: FIX FRONTEND STATE (prevent stale data showing)

In ai-ops-console/src/, find where Gmail connection state is stored.

Ensure:
A. Gmail status is fetched from the API on every Settings page mount
   (not from localStorage, not from a global context initialized once at login)
B. On logout: clear ALL state — do not use React context or Zustand store
   that persists between user sessions in the same browser tab
C. The Settings page calls GET /api/v1/settings/gmail/status?brand_id={id}
   with the CURRENT brand_id on every render — never uses cached state

```javascript
// Correct pattern — always fetch on mount, never use stale state
useEffect(() => {
    const fetchGmailStatus = async () => {
        setLoading(true)
        try {
            const res = await api.get(`/settings/gmail/status?brand_id=${currentBrandId}`)
            setGmailStatus(res.data)
        } catch (err) {
            setGmailStatus({ connected: false, email: null })
        } finally {
            setLoading(false)
        }
    }
    fetchGmailStatus()
}, [currentBrandId])  // ← re-fetch if brand changes
```

On logout (in useAuth.js or equivalent):
```javascript
const logout = () => {
    localStorage.removeItem('resolv_token')
    // Clear ALL cached state — React state resets on full page reload
    window.location.href = '/login'  // hard reload, not router.push
}
```

---

## STEP 10: VERIFY END-TO-END

After all fixes, run this exact test sequence manually:

TEST A — Cross-tenant isolation:
1. Register Organization A, log in as User A
2. Connect Gmail account gmail_a@gmail.com to Brand A
3. Log out of Organization A
4. Register Organization B, log in as User B  
5. GET /api/v1/settings/gmail/status?brand_id={brand_b_id} with User B's token
6. EXPECTED: { connected: false, email: null }
7. FAIL if: { connected: true, email: "gmail_a@gmail.com" }

TEST B — Brand ownership check:
1. Log in as User B
2. Try GET /api/v1/settings/gmail/status?brand_id={brand_a_id} (User A's brand)
3. EXPECTED: 404 Not Found
4. FAIL if: 200 with any data (even empty)

TEST C — OAuth callback security:
1. Initiate Gmail OAuth as User A → get state token S1
2. Try to use state token S1 in a second callback request
3. EXPECTED: redirect with error=invalid_state (used=true blocks replay)
4. FAIL if: second callback succeeds

TEST D — Worker isolation:
1. Connect Gmail A to Brand A, Gmail B to Brand B
2. Run the worker for 2 minutes
3. Check that tickets created from Gmail A emails have brand_id=Brand_A and org_id=Org_A
4. Check that tickets created from Gmail B emails have brand_id=Brand_B and org_id=Org_B
5. FAIL if any ticket has mismatched brand/org

---

## WHAT NOT TO DO

- Do not add `organization_id` as an optional/nullable field — it must be NOT NULL after backfill
- Do not skip RLS on any table "for now" — enable it on every tenant-scoped table before finishing
- Do not use a workaround that checks organization in the frontend only — backend must enforce it
- Do not cache Gmail client objects across requests at the module level
- Do not store decrypted tokens in memory longer than the duration of one request
- Do not move to implementing new features until TEST A, B, C, and D all pass

---

## ENCRYPTION HELPER (add if not already present)

hack5/src/utils/crypto.py:

```python
from cryptography.fernet import Fernet
from functools import lru_cache
import os

@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable not set")
    return Fernet(key.encode())

def encrypt(plaintext: str) -> str:
    if not plaintext:
        return None
    return get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return None
    return get_fernet().decrypt(ciphertext.encode()).decode()
```

Add to .env:
```
ENCRYPTION_KEY=<run: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

NEVER commit this key. Add to .gitignore. Store in environment secrets.