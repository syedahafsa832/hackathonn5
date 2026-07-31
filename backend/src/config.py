"""
Shared configuration constants.
"""
import os

# Single source of truth for the Shopify Admin API version. Was previously
# hardcoded as "2024-01" independently in 8 files — bumping it required
# editing all of them. Now a one-line change here (or the env var).
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-01")
