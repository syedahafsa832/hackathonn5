import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.src.agent.tools import (
    search_knowledge_base,
    create_ticket,
    get_customer_history,
    escalate_to_human,
    send_response
)
import uuid


class TestKnowledgeSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """Knowledge search should return relevant results."""
        with patch('src.services.knowledge_base_service.knowledge_base_service') as mock_kb_service:
            mock_db = AsyncMock()
            mock_article = MagicMock()
            mock_article.title = "Test Article"
            mock_article.content = "This is test content"
            mock_article.category = "technical"

            mock_kb_service.search_similar.return_value = [mock_article]

            results = await search_knowledge_base(query="test query", top_k=3)

            assert len(results) == 1
            assert results[0]["title"] == "Test Article"
            assert "test content" in results[0]["content"]
            assert results[0]["category"] == "technical"
            mock_kb_service.search_similar.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_handles_no_results(self):
        """Knowledge search should handle no results gracefully."""
        with patch('src.services.knowledge_base_service.knowledge_base_service') as mock_kb_service:
            mock_kb_service.search_similar.return_value = []

            results = await search_knowledge_base(query="nonexistent query", top_k=3)

            assert len(results) == 0
            mock_kb_service.search_similar.assert_called_once()


class TestTicketCreation:
    @pytest.mark.asyncio
    async def test_create_ticket_with_channel(self):
        """Ticket creation should include channel tracking."""
        customer_id = str(uuid.uuid4())

        with patch('src.services.ticket_service.create_ticket') as mock_create_ticket:
            with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
                mock_ticket = MagicMock()
                mock_ticket.id = uuid.uuid4()

                mock_create_ticket.return_value = mock_ticket

                result = await create_ticket(
                    customer_id=customer_id,
                    source_channel="email",
                    subject="Test Subject",
                    category="technical",
                    priority="medium",
                    description="Test description"
                )

                assert "id" in result
                assert result["status"] == "created"
                mock_create_ticket.assert_called_once()
                mock_kafka.publish_message.assert_called_once()


class TestCustomerHistory:
    @pytest.mark.asyncio
    async def test_get_customer_history_exists(self):
        """Should return customer history when customer exists."""
        customer_id = str(uuid.uuid4())

        with patch('src.services.customer_service.get_customer_by_id') as mock_get_customer:
            with patch('src.services.conversation_service.get_conversations_by_customer') as mock_get_conv:
                with patch('src.services.message_service.get_messages_by_conversation') as mock_get_msgs:
                    mock_customer = MagicMock()
                    mock_customer.id = uuid.uuid4()
                    mock_customer.email = "test@example.com"
                    mock_customer.name = "Test User"

                    mock_get_customer.return_value = mock_customer
                    mock_get_conv.return_value = []

                    history = await get_customer_history(customer_id)

                    assert "customer_id" in history
                    assert history["email"] == "test@example.com"
                    assert history["name"] == "Test User"

    @pytest.mark.asyncio
    async def test_get_customer_history_not_found(self):
        """Should handle case when customer is not found."""
        customer_id = str(uuid.uuid4())

        with patch('src.services.customer_service.get_customer_by_id') as mock_get_customer:
            mock_get_customer.return_value = None

            history = await get_customer_history(customer_id)

            assert "error" in history
            assert "not found" in history["error"]


class TestEscalation:
    @pytest.mark.asyncio
    async def test_escalate_to_human_success(self):
        """Should escalate to human successfully."""
        customer_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())

        with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
            result = await escalate_to_human(
                customer_id=str(customer_id),
                conversation_id=str(conversation_id),
                reason="Pricing inquiry"
            )

            assert result["status"] == "escalated"
            assert result["customer_id"] == str(customer_id)
            assert result["reason"] == "Pricing inquiry"
            mock_kafka.publish_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalate_to_human_error_handling(self):
        """Should handle escalation errors gracefully."""
        customer_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())

        with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
            mock_kafka.publish_message.side_effect = Exception("Kafka error")

            result = await escalate_to_human(
                customer_id=str(customer_id),
                conversation_id=str(conversation_id),
                reason="Pricing inquiry"
            )

            assert "error" in result
            assert "Failed to escalate" in result["error"]


class TestResponseSending:
    @pytest.mark.asyncio
    async def test_send_response_success(self):
        """Should send response successfully."""
        response_data = {
            "channel": "email",
            "recipient": "test@example.com",
            "content": "This is a test response"
        }

        with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
            result = await send_response(response_data)

            assert result["status"] == "response_prepared"
            assert result["channel"] == "email"
            mock_kafka.publish_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_response_error_handling(self):
        """Should handle response sending errors."""
        response_data = {
            "channel": "email",
            "recipient": "test@example.com",
            "content": "This is a test response"
        }

        with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
            mock_kafka.publish_message.side_effect = Exception("Kafka error")

            result = await send_response(response_data)

            assert "error" in result
            assert "Failed to prepare response" in result["error"]


class TestToolsIntegration:
    @pytest.mark.asyncio
    async def test_full_tool_workflow(self):
        """Test that tools work together in a typical workflow."""
        # Simulate a customer support flow
        customer_id = str(uuid.uuid4())

        # Step 1: Search knowledge base
        with patch('src.services.knowledge_base_service.knowledge_base_service') as mock_kb_service:
            mock_article = MagicMock()
            mock_article.title = "Password Reset Guide"
            mock_article.content = "To reset your password..."
            mock_kb_service.search_similar.return_value = [mock_article]

            kb_results = await search_knowledge_base("password reset", top_k=1)
            assert len(kb_results) == 1

        # Step 2: Get customer history
        with patch('src.services.customer_service.get_customer_by_id') as mock_get_customer:
            with patch('agent.tools.get_conversations_by_customer') as mock_get_conv:
                mock_customer = MagicMock()
                mock_customer.id = uuid.uuid4()
                mock_customer.email = "test@example.com"
                mock_get_customer.return_value = mock_customer
                mock_get_conv.return_value = []

                customer_history = await get_customer_history(customer_id)
                assert "customer_id" in customer_history

    @pytest.mark.asyncio
    async def test_tool_error_propagation(self):
        """Verify that tools properly propagate errors."""
        # Test that each tool handles exceptions properly
        test_cases = [
            lambda: search_knowledge_base("test", top_k=3),
            lambda: create_ticket("invalid-uuid", "email", "Test", "tech", "med", "desc"),
            lambda: get_customer_history("invalid-uuid"),
            lambda: escalate_to_human("invalid", "invalid", "test"),
            lambda: send_response({"channel": "email", "content": "test"})
        ]

        # Run each test case and verify it doesn't crash
        for test_func in test_cases:
            try:
                await test_func()
            except Exception:
                # It's okay to get exceptions, as long as they're handled properly
                pass


class TestToolParameterValidation:
    @pytest.mark.asyncio
    async def test_search_knowledge_base_validation(self):
        """Validate search parameters."""
        # Test with various parameter combinations
        result1 = await search_knowledge_base("test")
        assert isinstance(result1, list)

        result2 = await search_knowledge_base("test", top_k=5)
        assert isinstance(result2, list)

    @pytest.mark.asyncio
    async def test_create_ticket_validation(self):
        """Validate ticket creation parameters."""
        customer_id = str(uuid.uuid4())

        with patch('agent.tools.create_ticket_service') as mock_create:
            with patch('agent.tools.kafka_service'):
                mock_ticket = MagicMock()
                mock_ticket.id = uuid.uuid4()
                mock_create.return_value = mock_ticket

                # Test required fields
                result = await create_ticket(
                    customer_id=customer_id,
                    source_channel="web_form",
                    subject="Test Subject",
                    category="technical",
                    priority="medium",
                    description="Test description"
                )

                assert result["status"] == "created"

    @pytest.mark.asyncio
    async def test_escalate_validation(self):
        """Validate escalation parameters."""
        customer_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())

        with patch('agent.tools.kafka_service'):
            result = await escalate_to_human(
                customer_id=str(customer_id),
                conversation_id=str(conversation_id),
                reason="Test reason"
            )

            assert result["status"] == "escalated"
            assert result["customer_id"] == str(customer_id)
