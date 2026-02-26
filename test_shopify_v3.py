import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

# Load .env first
load_dotenv()

# Add backend to path
sys.path.insert(0, os.path.join(os.getcwd(), 'backend'))

# Mock or Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_sync():
    print("Starting Shopify V3 Integration Test...")
    
    # 1. Check Credentials
    shop_name = os.getenv("SHOPIFY_SHOP_NAME")
    access_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
    
    print(f"Shop: {shop_name}")
    print(f"Token: {access_token[:8]}...{access_token[-4:]}")
    
    if not shop_name or not access_token:
        print("Error: SHOPIFY_SHOP_NAME or SHOPIFY_ACCESS_TOKEN not set in .env")
        return

    # 2. Import and Run Sync
    try:
        from src.services.shopify_sync import shopify_sync_service
        from src.lib.supabase_client import supabase_select
        
        print("\n--- Testing Product Sync ---")
        # Sync a small batch
        await shopify_sync_service.sync_all_products()
        
        # 3. Verify in Supabase
        products = supabase_select("products", {"limit": 1})
        if products:
            print(f"Success! Found {len(products)} product(s) in Supabase.")
            print(f"Latest Product: {products[0].get('title')}")
        else:
            print("Sync ran but no products found in 'products' table. Check Shopify store content.")
            
    except Exception as e:
        print(f"Sync Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_sync())
