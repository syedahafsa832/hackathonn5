"""Ensure the legacy polling scheduler never falls back to any Gmail inbox."""
import asyncio
from unittest.mock import AsyncMock, patch

from src.channels.email_poller import EmailPoller


def test_poller_does_not_use_global_gmail_inbox_when_no_brands_connected():
    poller = EmailPoller()
    with patch(
        "src.services.brand_gmail_service.brand_gmail_service.get_connected_brands",
        return_value=[],
    ), patch.object(poller, "_poll_global_inbox", new=AsyncMock()) as global_poll:
        result = asyncio.run(poller._poll_all_inboxes())

    assert result == 0
    global_poll.assert_not_awaited()
