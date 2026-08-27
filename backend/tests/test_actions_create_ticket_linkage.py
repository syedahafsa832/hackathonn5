"""
POST /api/v1/actions/create silently dropped `ticket_id` - Pydantic ignores
unknown fields by default, and CreateActionRequest never declared it, even
though TicketDetail.jsx's Order Context "Reship"/"Update Address" buttons
already sent it. Without a ticket_id, a manually-staged action's approval
can never resolve/append-to the originating ticket the way an AI-staged
action already does (see actions_service._post_execution_notify, which
looks the ticket up by action["ticket_id"]). Fixed by declaring the field
and threading it through to actions_service.create_action().
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")

from src.api.routes.saas_actions import create_action, CreateActionRequest  # noqa: E402
from src.api.middleware.tenant_auth import TenantContext  # noqa: E402


@pytest.mark.asyncio
async def test_manually_staged_action_ticket_id_reaches_actions_service():
    tenant = TenantContext(tenant_id="tenant-1", email="merchant@example.com")
    request = CreateActionRequest(
        action_type="reship",
        customer_email="customer@example.com",
        order_id="1234",
        ticket_id="ticket-789",
    )

    with patch(
        "src.api.routes.saas_actions.actions_service.create_action",
        new=AsyncMock(return_value={"success": True, "action_id": "a1", "status": "pending"}),
    ) as mock_create:
        await create_action(request, tenant)

    assert mock_create.await_args.kwargs["ticket_id"] == "ticket-789"


@pytest.mark.asyncio
async def test_ticket_id_is_optional_and_defaults_to_none():
    tenant = TenantContext(tenant_id="tenant-1", email="merchant@example.com")
    request = CreateActionRequest(action_type="refund", customer_email="c@example.com", order_id="1")

    with patch(
        "src.api.routes.saas_actions.actions_service.create_action",
        new=AsyncMock(return_value={"success": True, "action_id": "a1", "status": "pending"}),
    ) as mock_create:
        await create_action(request, tenant)

    assert mock_create.await_args.kwargs["ticket_id"] is None
