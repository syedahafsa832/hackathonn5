import os
import requests
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from openai import OpenAI
from src.lib.supabase_client import supabase_select, supabase_insert, supabase_update, supabase_set_setting, supabase_get_setting

logger = logging.getLogger(__name__)

class ShopifySyncService:
    """Service to ingest and normalize Shopify data into Supabase with V3 schema."""

    def __init__(self):
        self.shop_name = os.getenv("SHOPIFY_SHOP_NAME")
        self.access_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = os.getenv("SHOPIFY_API_VERSION", "2024-01")
        self.base_url = f"https://{self.shop_name}.myshopify.com/admin/api/{self.api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }
        
        # AI Client for embeddings (Mistral or OpenAI)
        self.ai_client = OpenAI(
            api_key=os.getenv("MISTRAL_API_KEY"),
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")

    async def sync_all_products(self, store_id: str = "00000000-0000-0000-0000-000000000000"):
        """Pull all products from Shopify and sync to Supabase with embeddings."""
        if not self.shop_name or not self.access_token:
            logger.error("Shopify credentials missing. Sync aborted.")
            return

        logger.info(f"Starting Shopify product sync for {self.shop_name}...")
        url = f"{self.base_url}/products.json"
        params = {"limit": 50}
        
        while url:
            resp = requests.get(url, headers=self.headers, params=params)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch products: {resp.text}")
                break
            
            data = resp.json()
            products = data.get("products", [])
            for p in products:
                await self.sync_single_product(p, store_id)
                # Respect rate limits (Shopify is 2 requests per second for standard)
                await asyncio.sleep(0.5)
            
            # Handle pagination via Link header
            link_header = resp.headers.get("Link")
            if link_header and 'rel="next"' in link_header:
                parts = link_header.split(",")
                for part in parts:
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip("< >")
                        params = {} 
                        break
            else:
                url = None
        
        logger.info("Shopify sync completed.")

    async def sync_single_product(self, shopify_product: Dict[str, Any], store_id: str):
        """Normalize, embed, and upsert a single product and its variants."""
        try:
            # 1. Normalize tags (Metadata extraction)
            tags = shopify_product.get("tags", "")
            tag_dict = {}
            if tags:
                for t in tags.split(","):
                    if ":" in t:
                        k, v = t.split(":", 1)
                        tag_dict[k.strip().lower()] = v.strip()
            
            p_payload = {
                "store_id": store_id,
                "shopify_id": shopify_product["id"],
                "title": shopify_product["title"],
                "description": shopify_product.get("body_html", ""),
                "fabric": tag_dict.get("fabric"),
                "fit_type": tag_dict.get("fit"),
                "stretch_level": int(tag_dict.get("stretch", 0)),
                "model_height": tag_dict.get("model_height"),
                "last_synced": datetime.now(timezone.utc).isoformat()
            }
            
            # 2. Generate Embedding for RAG
            text_to_embed = f"Title: {p_payload['title']}\nDescription: {p_payload['description']}\nFabric: {p_payload['fabric']}\nFit: {p_payload['fit_type']}"
            embedding = await self._get_embedding(text_to_embed)
            if embedding:
                p_payload["embedding"] = embedding
            
            # 3. Upsert Product
            existing = supabase_select("products", {"shopify_id": f"eq.{p_payload['shopify_id']}"})
            if existing:
                p_id = existing[0]["id"]
                supabase_update("products", {"id": f"eq.{p_id}"}, p_payload)
            else:
                res = supabase_insert("products", p_payload)
                p_id = res["id"]
            
            # 4. Sync Variants
            for v in shopify_product.get("variants", []):
                v_payload = {
                    "product_id": p_id,
                    "shopify_variant_id": v["id"],
                    "sku": v["sku"],
                    "size": v["option1"],
                    "price": float(v["price"])
                }
                v_existing = supabase_select("variants", {"shopify_variant_id": f"eq.{v_payload['shopify_variant_id']}"})
                if v_existing:
                    supabase_update("variants", {"id": f"eq.{v_existing[0]['id']}"}, v_payload)
                else:
                    supabase_insert("variants", v_payload)

        except Exception as e:
            logger.error(f"Error syncing product {shopify_product.get('id')}: {e}")

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Call LLM API to get vector embedding."""
        try:
            # Safety: don't embed empty strings
            if not text.strip():
                return None
                
            response = self.ai_client.embeddings.create(
                input=[text],
                model=self.embedding_model
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error for text snippet: {e}")
            return None

shopify_sync_service = ShopifySyncService()
