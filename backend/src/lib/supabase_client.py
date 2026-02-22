"""
Supabase REST API Client — uses requests directly to avoid supabase-py dependency issues.
"""
import os
import logging
import requests
from typing import Optional, List, Any, Dict
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")

if SUPABASE_URL and SUPABASE_KEY:
    logger.info(f"✓ Supabase REST client configured for {SUPABASE_URL}")
else:
    logger.warning("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set. Supabase is DISABLED.")

def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def _rest_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"

# --- Public API ---

def supabase_select(table: str, params: dict = None) -> list:
    """SELECT from a Supabase table. params are query params like {'status': 'eq.open', 'order': 'created_at.desc'}"""
    try:
        resp = requests.get(_rest_url(table), headers=_headers(), params=params or {})
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Supabase Select Error: {e}")
        return []

def supabase_insert(table: str, data: dict) -> dict:
    """INSERT a row into a Supabase table. Returns the created row."""
    try:
        resp = requests.post(_rest_url(table), headers=_headers(), json=data)
        resp.raise_for_status()
        result = resp.json()
        return result[0] if isinstance(result, list) else result
    except Exception as e:
        logger.error(f"Supabase Insert Error: {e}")
        raise e

def supabase_update(table: str, match: dict, data: dict) -> dict:
    """UPDATE rows matching the filter. match is like {'id': 'eq.some-uuid'}"""
    try:
        resp = requests.patch(_rest_url(table), headers=_headers(), params=match, json=data)
        resp.raise_for_status()
        result = resp.json()
        return result[0] if isinstance(result, list) else result
    except Exception as e:
        logger.error(f"Supabase Update Error: {e}")
        raise e

# --- Settings Helpers ---

def supabase_get_setting(key: str) -> Optional[dict]:
    """Fetch a setting from the settings table."""
    try:
        results = supabase_select("settings", {"key": f"eq.{key}"})
        return results[0]["value"] if results else None
    except Exception as e:
        logger.error(f"Error fetching setting {key}: {e}")
        return None

def supabase_set_setting(key: str, value: dict):
    """Save a setting to the settings table using UPSERT."""
    try:
        data = {"key": key, "value": value, "updated_at": "now()"}
        # Supabase UPSERT via POST with Prefer: resolution=merge-duplicates or simply PATCH if exists
        # Simplest is to check if exists, then insert or update
        existing = supabase_get_setting(key)
        if existing is not None:
            supabase_update("settings", {"key": f"eq.{key}"}, {"value": value})
        else:
            supabase_insert("settings", data)
        logger.info(f"✓ Setting {key} saved to Supabase.")
    except Exception as e:
        logger.error(f"Error saving setting {key}: {e}")

# Keep backward compat
supabase = None
