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
        from supabase.lib.client_options import ClientOptions
        options = ClientOptions(
            postgrest_client_timeout=10,
            storage_client_timeout=10,
        )
        supabase: Client = create_client(url, key, options=options)
        logger.info("✓ Supabase client initialized.")
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        # Fallback: try without options
        try:
            from supabase import create_client, Client
            supabase: Client = create_client(url, key)
            logger.info("✓ Supabase client initialized (fallback).")
        except Exception as e2:
            logger.error(f"Supabase fallback also failed: {e2}")
else:
    logger.warning("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set. Supabase is DISABLED.")

def get_supabase():
    return supabase
