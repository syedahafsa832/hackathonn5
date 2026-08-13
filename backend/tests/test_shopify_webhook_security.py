"""
Shopify webhook HMAC verification (deployment-closure security fix).

verify_shopify_webhook() previously returned True ("valid") whenever
SHOPIFY_WEBHOOK_SECRET was unset - accepting any unsigned payload as a
genuine Shopify event in that state, not just in local dev. Now fails
closed: no secret configured means every request is rejected, and a
request missing the HMAC header entirely is rejected rather than crashing
hmac.compare_digest with a TypeError on None.
"""
import base64
import hashlib
import hmac as hmac_module
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

import src.api.routes.webhooks.shopify as shopify_webhook  # noqa: E402


def _sign(secret: str, data: bytes) -> str:
    digest = hmac_module.new(secret.encode("utf-8"), data, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_rejects_when_secret_is_not_configured():
    with patch.object(shopify_webhook, "SHOPIFY_WEBHOOK_SECRET", None):
        assert shopify_webhook.verify_shopify_webhook(b'{"id": 1}', "anything") is False


def test_rejects_missing_hmac_header_even_with_secret_configured():
    with patch.object(shopify_webhook, "SHOPIFY_WEBHOOK_SECRET", "test-secret"):
        assert shopify_webhook.verify_shopify_webhook(b'{"id": 1}', None) is False


def test_rejects_wrong_signature_when_secret_configured():
    with patch.object(shopify_webhook, "SHOPIFY_WEBHOOK_SECRET", "test-secret"):
        assert shopify_webhook.verify_shopify_webhook(b'{"id": 1}', "not-the-real-signature") is False


def test_accepts_correctly_signed_payload():
    data = b'{"id": 1, "title": "Test Product"}'
    secret = "test-secret"
    valid_signature = _sign(secret, data)
    with patch.object(shopify_webhook, "SHOPIFY_WEBHOOK_SECRET", secret):
        assert shopify_webhook.verify_shopify_webhook(data, valid_signature) is True
