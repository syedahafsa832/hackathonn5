import os
import json
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from src.lib.supabase_client import supabase_set_setting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Scopes required for Gmail
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

@router.get("/health")
async def auth_health():
    return {"status": "auth router active"}

# Production Redirect URI
REDIRECT_URI = "https://hackathonn5-production.up.railway.app/auth/callback"

# Load client config from file or env
CLIENT_CONFIG_PATH = r"C:\Users\Uswer\Downloads\client_secret_909957709543-nl5u456fqt0n9d6kchehbfud0amd14gt.apps.googleusercontent.com.json"

def get_client_config():
    if os.path.exists(CLIENT_CONFIG_PATH):
        with open(CLIENT_CONFIG_PATH, 'r') as f:
            return json.load(f)
    
    # Fallback to env if file missing (helpful for Railway)
    creds_json = os.getenv("GMAIL_CREDENTIALS")
    if creds_json:
        return json.loads(creds_json)
    
    return None

@router.get("/google")
async def auth_google():
    config = get_client_config()
    if not config:
        raise HTTPException(status_code=500, detail="Gmail credentials not configured")
    
    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    return RedirectResponse(authorization_url)

@router.get("/callback")
async def auth_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    
    config = get_client_config()
    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    # Fetch token
    flow.fetch_token(code=code)
    creds = flow.credentials
    
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    
    # Save to Supabase
    supabase_set_setting("GMAIL_TOKEN", token_data)
    
    return {"status": "success", "message": "Gmail token authorized and saved to Supabase securely."}
