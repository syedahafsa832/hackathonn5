"""
Send Queue Status cross-tenant leak (item 8 of the bug-fix pass).

get_gmail_queue_status() queried send_tasks/send_log with only a status/
sent_at filter — no brand_id or tenant_id — even though both tables have a
brand_id column. Every tenant's Settings page was showing platform-wide
counts across every brand, not their own. Currently masked in production
because send_tasks/send_log are empty (nothing in the codebase writes to
them yet — a separate, larger finding reported alongside this fix rather
than "fixed" here, since building an actual async send queue is a new
feature, not a bug fix).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.saas_settings import get_gmail_queue_status  # noqa: E402
from src.api.middleware.tenant_auth import TenantContext  # noqa: E402

TENANT = TenantContext(tenant_id="tenant-1", email="owner@example.com")
BRAND = {"id": "brand-1", "tenant_id": "tenant-1"}


@pytest.mark.asyncio
async def test_every_send_tasks_and_send_log_query_is_scoped_to_the_tenants_brand():
    captured_params = []

    def fake_select(table, params=None):
        captured_params.append((table, params or {}))
        return []

    with patch("src.api.routes.saas_settings._get_tenant_brand_async", new=AsyncMock(return_value=BRAND)), \
         patch("src.api.routes.saas_settings.supabase_select", side_effect=fake_select):
        await get_gmail_queue_status(tenant=TENANT)

    send_table_calls = [(t, p) for t, p in captured_params if t in ("send_tasks", "send_log")]
    assert len(send_table_calls) == 4  # queued, failed, sent_24h, hourly
    for table, params in send_table_calls:
        assert params.get("brand_id") == "eq.brand-1", f"{table} query missing brand_id scope: {params}"


@pytest.mark.asyncio
async def test_no_brand_returns_zeroed_stats_without_querying_send_tables():
    with patch("src.api.routes.saas_settings._get_tenant_brand_async", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.saas_settings.supabase_select") as mock_select:
        result = await get_gmail_queue_status(tenant=TENANT)

    assert result == {"queued_count": 0, "sent_24h": 0, "failed_count": 0, "hourly_count": 0, "last_polled_at": None}
    mock_select.assert_not_called()
