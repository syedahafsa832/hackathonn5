import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from backend.src.agent.customer_success_agent import CustomerSuccessAgent, process_customer_query
from backend.src.services.knowledge_base_service import knowledge_base_service
import uuid


class TestAgentBehavior:
    def setup_method(self):
        """Setup test fixtures before each test method."""
        self.agent = CustomerSuccessAgent()
        # Mock the OpenAI client to avoid API calls
        self.agent.openai_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is a test response from the agent."
        self.agent.openai_client.chat.completions.create.return_value = mock_response

    @pytest.mark.asyncio
    async def test_agent_handles_basic_query(self):
        """Agent should handle basic product questions."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        response = await process_customer_query(
            "How do I reset my password?",
            customer_id,
            conversation_id
        )

        assert isinstance(response, str)
        assert len(response) > 0
        assert "test response" in response.lower()
        # Verify OpenAI was called
        self.agent.openai_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_escalates_pricing(self):
        """Agent must escalate pricing questions."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Patch the escalate_to_human function
        with patch('agent.customer_success_agent.escalate_to_human') as mock_escalate:
            mock_escalate.return_value = None

            response = await process_customer_query(
                "What is your enterprise pricing?",
                customer_id,
                conversation_id
            )

            # Should escalate and return escalation message
            assert "connect you with a human agent" in response
            mock_escalate.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_escalates_legal(self):
        """Agent must escalate legal mentions."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        with patch('agent.customer_success_agent.escalate_to_human') as mock_escalate:
            mock_escalate.return_value = None

            response = await process_customer_query(
                "I need to speak to your lawyer about a contract issue",
                customer_id,
                conversation_id
            )

            assert "connect you with a human agent" in response
            mock_escalate.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_channel_email_formatting(self):
        """Email responses should be formal with greeting/signature."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        with patch('agent.customer_success_agent.CustomerSuccessAgent._construct_system_prompt') as mock_construct:
            mock_construct.return_value = "Test system prompt"

            # Test email-specific formatting
            response = await self.agent.generate_channel_appropriate_response(
                "How do I use the API?",
                customer_id,
                conversation_id,
                "email"
            )

            assert "Dear Valued Customer" in response
            assert "Best regards" in response
            # Should be limited to 500 words
            word_count = len(response.split())
            assert word_count <= 500

    @pytest.mark.asyncio
    async def test_agent_channel_whatsapp_formatting(self):
        """WhatsApp responses should be concise (<300 chars)."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Test WhatsApp-specific formatting
        response = await self.agent.generate_channel_appropriate_response(
            "This is a very long message that should be truncated for WhatsApp because WhatsApp has character limits and we need to ensure the message fits within those limits and maintains readability and coherence despite the truncation requirements.",
            customer_id,
            conversation_id,
            "whatsapp"
        )

        # Should be limited to 300 characters
        assert len(response) <= 300

    @pytest.mark.asyncio
    async def test_agent_handles_empty_query(self):
        """Agent should handle empty queries gracefully."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        response = await process_customer_query(
            "",
            customer_id,
            conversation_id
        )

        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_agent_handles_special_characters(self):
        """Agent should handle queries with special characters."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        response = await process_customer_query(
            "What's the API rate limit? Also, can I use @#$%^&*() characters?",
            customer_id,
            conversation_id
        )

        assert isinstance(response, str)
        assert len(response) > 0
        self.agent.openai_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_detects_negative_sentiment(self):
        """Agent should detect and respond to negative sentiment."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Patch escalation to prevent actual escalation during test
        with patch('agent.customer_success_agent.escalate_to_human'):
            response = await process_customer_query(
                "This product is terrible and I hate it!",
                customer_id,
                conversation_id
            )

            # Even if escalated, should return a response
            assert isinstance(response, str)
            assert len(response) > 0

    @pytest.mark.asyncio
    async def test_agent_uses_knowledge_base(self):
        """Agent should incorporate knowledge base results."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Mock the knowledge base search
        with patch('agent.customer_success_agent.search_knowledge_base') as mock_search:
            mock_search.return_value = [
                {
                    "title": "Password Reset Guide",
                    "content": "To reset your password, go to settings...",
                    "category": "account"
                }
            ]

            response = await process_customer_query(
                "How do I reset my password?",
                customer_id,
                conversation_id
            )

            # Knowledge base should have been searched
            mock_search.assert_called_once_with(query="How do I reset my password?", top_k=3)
            assert isinstance(response, str)

    @pytest.mark.asyncio
    async def test_agent_handles_customer_context(self):
        """Agent should incorporate customer context."""
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Mock customer history retrieval
        with patch('agent.customer_success_agent.get_customer_history') as mock_history:
            mock_history.return_value = {
                "customer_id": str(customer_id),
                "name": "Test User",
                "email": "test@example.com",
                "company": "Test Company"
            }

            response = await process_customer_query(
                "Where do I find my API key?",
                customer_id,
                conversation_id
            )

            # Customer history should have been retrieved
            mock_history.assert_called_once_with(customer_id=customer_id)
            assert isinstance(response, str)
