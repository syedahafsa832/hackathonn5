"""
Regression test for the channel/store_id mislabeling bug.

The dashboard's useConversations hook used to pass its second argument to the
API client under the key "channel", which api.getConversations() silently
dropped (it only reads params.store_id) — so brand/store filtering on the
Conversations page never reached the backend at all, regardless of which
brand was selected in the UI.

The frontend fix (dashboard/src/hooks/useApi.js) is a parameter rename that
can't be exercised by the Python suite directly. What *can* be verified here
is the other half of the contract: that GET /api/tickets's store_id param
(the one the fixed hook now actually sends) genuinely changes which tickets
come back — proving there's a real, working filter on the other end once the
frontend starts sending it correctly.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.middleware.tenant_auth import TenantContext  # noqa: E402
from src.api.routes.tickets import list_tickets, _tickets_cache  # noqa: E402

BRAND_A = "brand-aaaa"
BRAND_B = "brand-bbbb"
FOREIGN_BRAND = "brand-not-owned"

TICKETS_A = [{"id": "ticket-a1", "store_id": BRAND_A, "channel": "email", "messages": []}]
TICKETS_B = [{"id": "ticket-b1", "store_id": BRAND_B, "channel": "email", "messages": []},
             {"id": "ticket-b2", "store_id": BRAND_B, "channel": "email", "messages": []}]

TENANT = TenantContext(tenant_id="tenant-1", email="owner@example.com")


async def _fake_get_tickets(store_id=None, status=None):
    if store_id == BRAND_A:
        return TICKETS_A
    if store_id == BRAND_B:
        return TICKETS_B
    return []


@pytest.fixture(autouse=True)
def _clear_cache():
    _tickets_cache.clear()
    yield
    _tickets_cache.clear()


@pytest.mark.asyncio
async def test_store_id_filter_returns_only_that_brands_tickets():
    """This is the exact behavior that was unreachable while the hook sent
    'channel' instead of 'store_id' — picking a brand in the UI now actually
    changes the result set instead of always returning every brand merged."""
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_A, BRAND_B])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(side_effect=_fake_get_tickets)):
        result_a = await list_tickets(status=None, store_id=BRAND_A, tenant=TENANT)
        _tickets_cache.clear()
        result_b = await list_tickets(status=None, store_id=BRAND_B, tenant=TENANT)

    assert [t["id"] for t in result_a] == ["ticket-a1"]
    assert [t["id"] for t in result_b] == ["ticket-b1", "ticket-b2"]
    # The core assertion: different store_id -> genuinely different result set,
    # not just a request that happens not to error.
    assert result_a != result_b


@pytest.mark.asyncio
async def test_omitting_store_id_merges_all_owned_brands():
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_A, BRAND_B])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(side_effect=_fake_get_tickets)):
        merged = await list_tickets(status=None, store_id=None, tenant=TENANT)

    ids = {t["id"] for t in merged}
    assert ids == {"ticket-a1", "ticket-b1", "ticket-b2"}


@pytest.mark.asyncio
async def test_store_id_not_owned_by_tenant_returns_empty_not_foreign_data():
    with patch("src.api.routes.tickets._get_tenant_brand_ids", new=AsyncMock(return_value=[BRAND_A, BRAND_B])), \
         patch("src.services.supabase_service.supabase_service.get_tickets", new=AsyncMock(side_effect=_fake_get_tickets)):
        result = await list_tickets(status=None, store_id=FOREIGN_BRAND, tenant=TENANT)

    assert result == []
