import os
import logging
import httpx
import uuid
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional

from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update

logger = logging.getLogger(__name__)

# Note: We use no prefix for the router to match the required /new-install exactly in main.py registration
router = APIRouter(tags=["shopify_auth"])

CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
SCOPES = os.getenv("SHOPIFY_SCOPES", "read_products,write_products")
REDIRECT_URI = "https://hackathonn5-production.up.railway.app/new-install"

# Temporary in-memory state store for CSRF protection
# In a multi-worker production app, use Redis or Supabase for this.
OAUTH_STATES = {}

@router.get("/install")
async def shopify_install(shop: str = Query(...)):
    """
    Step 1: Redirect merchant to Shopify for authorization.
    """
    if not shop:
        raise HTTPException(status_code=400, detail="Missing shop parameter")
    
    # Clean shop domain
    shop = shop.replace("https://", "").replace("http://", "").rstrip("/")
    if not shop.endswith(".myshopify.com"):
        shop = f"{shop}.myshopify.com"
        
    state = str(uuid.uuid4())
    OAUTH_STATES[state] = True # Store state for validation
    
    auth_url = (
        f"https://{shop}/admin/oauth/authorize?"
        f"client_id={CLIENT_ID}&"
        f"scope={SCOPES}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"state={state}"
    )
    
    logger.info(f"Initiating Shopify install for {shop}")
    return RedirectResponse(auth_url)

@router.get("/new-install")
async def shopify_callback(
    code: Optional[str] = Query(None),
    shop: Optional[str] = Query(None),
    state: Optional[str] = Query(None)
):
    """
    Step 2: Handle callback from Shopify, exchange code for permanent access token.
    """
    if not shop:
        return JSONResponse(status_code=400, content={"error": "Missing 'shop' parameter. Please start from the /install endpoint."})
    
    logger.info(f"Received Shopify callback for shop: {shop}")
    
    if not code:
        return JSONResponse(status_code=400, content={"error": "Missing 'code' parameter. Shopify authorization failed or was cancelled."})
    
    if not state:
        return JSONResponse(status_code=400, content={"error": "Missing 'state' parameter. This is required for security (CSRF)."})
        
    # 0. Validate State (CSRF Protection)
    if state not in OAUTH_STATES:
        logger.error(f"Invalid state parameter: {state}")
        raise HTTPException(status_code=403, detail="Invalid state parameter (CSRF Validation Failed)")
    
    # Remove state after use
    del OAUTH_STATES[state]

    # 1. Exchange code for access token
    token_url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, json=payload)
            
        if response.status_code != 200:
            error_data = response.json()
            logger.error(f"Shopify token exchange failed: {error_data}")
            return JSONResponse(
                status_code=401, 
                content={"error": "Token exchange failed", "details": error_data}
            )
            
        token_data = response.json()
        access_token = token_data.get("access_token")
        granted_scopes = token_data.get("scope", "").split(",")
        
        if not access_token:
            return JSONResponse(status_code=500, content={"error": "Access token not received from Shopify"})
            
        # 2. Save/Update in Supabase
        # We check if shop already exists to avoid duplicates
        existing_shops = supabase_select("shops", {"shop_domain": f"eq.{shop}"})
        
        shop_payload = {
            "shop_domain": shop,
            "access_token": access_token,
            "scopes": granted_scopes,
            "updated_at": "now()"
        }
        
        if existing_shops:
            logger.info(f"Updating existing shop token for {shop}")
            supabase_update("shops", {"shop_domain": f"eq.{shop}"}, shop_payload)
        else:
            logger.info(f"Registering new shop: {shop}")
            supabase_insert("shops", shop_payload)
            
        logger.info(f"✓ Shopify OAuth successful for {shop}")
        
        return {
            "status": "success",
            "message": f"Luna AI is successfully installed on {shop}",
            "shop": shop
        }
        
    except Exception as e:
        logger.error(f"Error during Shopify OAuth: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
