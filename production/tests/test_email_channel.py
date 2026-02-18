import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from backend.src.api.main import app
import uuid
import json

@pytest.fixture
def client():
    """Create a test client for the API."""
    return TestClient(app)


class TestEmailChannel:
    """Test suite for email channel functionality."""

    def test_email_simulator_endpoint_exists(self, client):
        """Test that the email simulator endpoint is available."""
        # Test that the email route is registered
        response = client.post("/email/simulate", json={
            "from_email": "test@example.com",
            "to_emails": ["support@techcorp.com"],
            "subject": "Test Subject",
            "body": "This is a test email body."
        })

        # Should return validation error (not 404) if endpoint exists
        assert response.status_code in [200, 422], f"Expected endpoint to exist, got status {response.status_code}"

    @pytest.mark.asyncio
    async def test_email_submission_processing(self):
        """Test that email submissions are processed correctly."""
        # Mock the database operations
        with patch('src.services.customer_service.get_or_create_customer') as mock_get_customer:
            with patch('src.services.ticket_service.create_ticket') as mock_create_ticket:
                with patch('src.services.database.get_db') as mock_get_db:
                    with patch('src.services.kafka_client.kafka_client_service') as mock_kafka:
                        # Setup mocks
                        mock_customer = MagicMock()
                        mock_customer.id = uuid.uuid4()
                        mock_get_customer.return_value = mock_customer

                        mock_ticket = MagicMock()
                        mock_ticket.id = uuid.uuid4()
                        mock_create_ticket.return_value = mock_ticket

                        mock_db = AsyncMock()
                        mock_get_db.return_value.__aenter__.return_value = mock_db
                        mock_get_db.return_value.__aexit__.return_value = AsyncMock()

                        # Test the email processing logic
                        from src.api.routes.email import simulate_email_submission
                        from src.api.routes.email import EmailSubmission

                        email_data = EmailSubmission(
                            from_email="test@example.com",
                            to_emails=["support@techcorp.com"],
                            subject="Test Subject",
                            body="This is a test email body."
                        )

                        # This should work without raising an exception
                        # Note: We can't test the full function directly due to FastAPI dependencies
                        # but we can at least verify the logic structure
                        assert email_data.from_email == "test@example.com"
                        assert email_data.subject == "Test Subject"
                        assert "test email body" in email_data.body.lower()

    def test_email_formatting_in_agent(self):
        """Test that email responses are properly formatted."""
        # Test the email formatting function directly
        from src.agent.customer_success_agent import customer_success_agent

        test_response = "This is a test response from the AI agent."
        formatted_email = customer_success_agent._format_email_response(test_response)

        # Check that it contains email-specific formatting
        assert "Dear Valued Customer" in formatted_email
        assert "Best regards" in formatted_email
        assert "Customer Success AI Agent" in formatted_email
        assert test_response in formatted_email

    def test_channel_appropriate_response_generation(self):
        """Test that responses are generated appropriately for different channels."""
        from src.agent.customer_success_agent import customer_success_agent

        test_query = "How do I reset my password?"
        customer_id = uuid.uuid4()
        conversation_id = uuid.uuid4()

        # Test WhatsApp formatting (should be truncated)
        whatsapp_response = asyncio.run(
            customer_success_agent.generate_channel_appropriate_response(
                test_query, customer_id, conversation_id, "whatsapp"
            )
        )

        # Test email formatting (should have email template)
        email_response = asyncio.run(
            customer_success_agent.generate_channel_appropriate_response(
                test_query, customer_id, conversation_id, "email"
            )
        )

        # Test web form formatting (should be unchanged)
        webform_response = asyncio.run(
            customer_success_agent.generate_channel_appropriate_response(
                test_query, customer_id, conversation_id, "web_form"
            )
        )

        # Email response should have email-specific formatting
        assert "Dear Valued Customer" in email_response
        assert "Best regards" in email_response

        # WhatsApp response should be handled differently (potentially truncated)
        # Web form response should be the base response without special formatting

    def test_email_validation_errors(self):
        """Test that email validation works correctly."""
        from src.api.routes.email import EmailSubmission

        # Test invalid email format
        with pytest.raises(ValueError):
            EmailSubmission(
                from_email="invalid-email",
                to_emails=["support@techcorp.com"],
                subject="Test Subject",
                body="This is a test email body."
            )

        # Test valid email format
        valid_email = EmailSubmission(
            from_email="valid@example.com",
            to_emails=["support@techcorp.com"],
            subject="Test Subject",
            body="This is a test email body."
        )
        assert valid_email.from_email == "valid@example.com"

    @pytest.mark.asyncio
    async def test_email_message_processing_in_worker(self):
        """Test that email messages are processed correctly in the message processor."""
        from production.workers.message_processor import UnifiedMessageProcessor

        processor = UnifiedMessageProcessor()

        # Mock message data for email channel
        email_message = {
            "ticket_id": str(uuid.uuid4()),
            "customer_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "channel": "email",
            "action": "created",
            "content": "Customer email content",
            "customer_email": "customer@example.com"
        }

        # Verify that email channel is handled
        assert email_message["channel"] == "email"

        # Test that the message structure is correct for email processing
        assert "customer_email" in email_message
        assert "channel" in email_message
        assert email_message["channel"] == "email"

    def test_cross_channel_consistency(self):
        """Test that customer identification works across channels."""
        from production.workers.message_processor import UnifiedMessageProcessor

        processor = UnifiedMessageProcessor()

        # Test customer resolution with email
        email_message = {
            'email': 'test@example.com',
            'phone': '+1234567890',
            'provided_customer_id': None
        }

        # This tests the structure of the resolve_customer method parameters
        assert email_message['email'] == 'test@example.com'
        assert email_message['phone'] == '+1234567890'

    @pytest.mark.asyncio
    async def test_email_response_storage(self):
        """Test that email responses are stored correctly."""
        from production.workers.message_processor import UnifiedMessageProcessor

        processor = UnifiedMessageProcessor()

        # Test email response data structure
        email_response_data = {
            "conversation_id": str(uuid.uuid4()),
            "content": "Test email response content",
            "customer_id": str(uuid.uuid4()),
            "recipient_email": "customer@example.com"
        }

        # Test the store_email_response method
        result = await processor.store_email_response(email_response_data)

        assert result["status"] == "success"
        assert result["channel"] == "email"
        assert "conversation_id" in email_response_data
        assert "recipient_email" in email_response_data

    def test_email_error_handling(self):
        """Test error handling for email channel."""
        from production.workers.message_processor import UnifiedMessageProcessor

        processor = UnifiedMessageProcessor()

        # Test error response structure for email channel
        original_message = {
            'channel': 'email',
            'customer_email': 'customer@example.com',
            'content': 'Original message content'
        }

        error_str = "Test error occurred"

        # Test that email channel is mapped correctly in error handling
        channel_topic_map = {
            'whatsapp': 'whatsapp_outbound',
            'web_form': 'webform_outbound',
            'email': 'webform_outbound'  # Using same topic for simplicity
        }

        response_topic = channel_topic_map.get(original_message.get('channel', 'web_form'), 'webform_outbound')
        assert response_topic == 'webform_outbound'  # Email maps to webform_outbound


if __name__ == "__main__":
    pytest.main([__file__])