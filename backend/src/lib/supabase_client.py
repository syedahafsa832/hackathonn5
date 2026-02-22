import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

url: str = os.environ.get("SUPABASE_URL", "")
key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")

supabase = None

if url and key:
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(url, key)
        logger.info("✓ Supabase client initialized.")
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
else:
    logger.warning("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set. Supabase is DISABLED.")

def get_supabase():
    return supabase
