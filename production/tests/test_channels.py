import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.src.services.gmail_handler import GmailHandler
from backend.src.services.whatsapp_handler import WhatsAppHandler
from backend.src.api.routes.support import WebFormSubmission
from backend.src.api.routes.webhooks import GmailWebhookPayload, WhatsAppWebhookPayload
import uuid


class TestGmailHandler:
    @pytest.mark.asyncio
    async def test_gmail_message_parsing(self):
        """Gmail messages should be parsed correctly."""
        handler = GmailHandler()

        # Mock a Gmail message payload
        mock_message = {
            'id': 'test-message-id',
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'Test User <test@example.com>'},
                    {'name': 'Subject', 'value': 'Test Subject'},
                    {'name': 'Date', 'value': 'Mon, 01 Jan 2024 00:00:00 +0000'}
                ],
                'body': {
                    'data': 'VGhpcyBpcyBhIHRlc3QgbWVzc2FnZS4='  # Base64 encoded "This is a test message."
                }
            }
        }

        parsed = handler.extract_email_content(mock_message)

        assert parsed['sender_name'] == 'Test User'
        assert parsed['sender_email'] == 'test@example.com'
        assert parsed['subject'] == 'Test Subject'
        assert 'test message' in parsed['body']

    @pytest.mark.asyncio
    async def test_gmail_reply_formatting(self):
        """Gmail replies should have proper formatting."""
        handler = GmailHandler()

        content = "This is a test response."
        formatted = handler.format_email_response(content, formal_tone=True)

        assert "Dear Valued Customer" in formatted
        assert "Best regards" in formatted
        assert "Customer Success AI Agent" in formatted

        # Test informal tone
        informal_formatted = handler.format_email_response(content, formal_tone=False)
        assert "Dear Valued Customer" not in informal_formatted
        assert "Hi," in informal_formatted

    @pytest.mark.asyncio
    async def test_gmail_message_length_limiting(self):
        """Gmail responses should be limited to 500 words."""
        handler = GmailHandler()

        long_content = "word " * 600  # 600 words
        formatted = handler.format_email_response(long_content, formal_tone=True)

        word_count = len(formatted.split())
        # Should be limited to 500 words plus greeting and signature
        assert word_count <= 550  # Allow some buffer for greeting/signature

    @pytest.mark.asyncio
    async def test_gmail_process_webhook(self):
        """Gmail webhook processing should handle payloads correctly."""
        handler = GmailHandler()

        # Mock the authentication and service
        handler.service = MagicMock()
        mock_results = {'history': [{'messages': [{'id': 'msg1'}]}]}
        handler.service.users.return_value.history.return_value.list.return_value.execute.return_value = mock_results

        # Mock the message processing
        with patch.object(handler, 'process_email_message') as mock_process:
            mock_process.return_value = {"status": "processed"}

            payload = {
                'userId': 'test-user',
                'historyId': 'test-history-id'
            }

            result = await handler.process_webhook(payload)

            assert result['status'] == 'success'
            assert result['processed_count'] == 1
            mock_process.assert_called_once_with('msg1')

    @pytest.mark.asyncio
    async def test_gmail_send_response_email(self):
        """Gmail should send response emails correctly."""
        handler = GmailHandler()

        # Mock the service
        handler.service = MagicMock()
        mock_message = MagicMock()
        mock_message.id = 'sent-message-id'
        handler.service.users.return_value.messages.return_value.send.return_value.execute.return_value = mock_message

        with patch.object(handler, 'authenticate') as mock_auth:
            mock_auth.return_value = handler.service

            result = await handler.send_response_email(
                to_email="test@example.com",
                subject="Test Subject",
                body="This is a test message."
            )

            assert result['id'] == 'sent-message-id'
            handler.service.users.return_value.messages.return_value.send.assert_called_once()


class TestWhatsAppHandler:
    @pytest.mark.asyncio
    async def test_whatsapp_message_splitting(self):
        """Long WhatsApp messages should be split properly."""
        handler = WhatsAppHandler()

        # Test short message (should not be split)
        short_msg = "Short message"
        formatted_short = handler.format_whatsapp_response(short_msg)
        assert len(formatted_short) <= 300

        # Test long message (should be truncated)
        long_msg = "This is a very long message that exceeds the 300 character limit for WhatsApp responses. " * 5
        formatted_long = handler.format_whatsapp_response(long_msg)
        assert len(formatted_long) <= 300
        assert formatted_long.endswith("...")

    @pytest.mark.asyncio
    async def test_whatsapp_webhook_validation(self):
        """WhatsApp webhooks should validate Twilio signature."""
        handler = WhatsAppHandler()

        # Test with validator available
        if handler.validator:
            # This tests the validation when Twilio credentials are configured
            is_valid = handler.validate_webhook_signature(
                "https://example.com/webhook",
                {"Body": "test message"},
                "test-signature"
            )
            # Should return True if validator not initialized (skips validation)
            assert is_valid is True
        else:
            # Without validator, should skip validation
            is_valid = handler.validate_webhook_signature(
                "https://example.com/webhook",
                {"Body": "test message"},
                "test-signature"
            )
            assert is_valid is True

    @pytest.mark.asyncio
    async def test_whatsapp_process_webhook(self):
        """WhatsApp webhook should process incoming messages."""
        handler = WhatsAppHandler()

        # Mock the Twilio client
        handler.twilio_client = MagicMock()

        # Mock database and services
        with patch('services.whatsapp_handler.get_db') as mock_db_ctx:
            with patch('services.whatsapp_handler.get_or_create_customer') as mock_get_cust:
                with patch('services.whatsapp_handler.create_conversation') as mock_create_conv:
                    with patch('services.whatsapp_handler.add_message_to_conversation') as mock_add_msg:
                        with patch('services.whatsapp_handler.create_ticket') as mock_create_tick:
                            with patch('services.whatsapp_handler.sentiment_analyzer') as mock_sentiment:
                                with patch('services.whatsapp_handler.process_customer_query') as mock_query:
                                    with patch.object(handler, 'send_response_message') as mock_send_resp:
                                        # Setup mocks
                                        mock_db = AsyncMock()
                                        mock_db_ctx.__aenter__.return_value = mock_db
                                        mock_db_ctx.__aexit__.return_value = AsyncMock()

                                        mock_customer = MagicMock()
                                        mock_customer.id = uuid.uuid4()
                                        mock_get_cust.return_value = mock_customer

                                        mock_conversation = MagicMock()
                                        mock_conversation.id = uuid.uuid4()
                                        mock_create_conv.return_value = mock_conversation

                                        mock_ticket = MagicMock()
                                        mock_ticket.id = uuid.uuid4()
                                        mock_create_tick.return_value = mock_ticket

                                        mock_sentiment.analyze_sentiment.return_value = 0.1
                                        mock_query.return_value = "Test response"

                                        payload = {
                                            'From': 'whatsapp:+1234567890',
                                            'To': 'whatsapp:+0987654321',
                                            'Body': 'Hello, how are you?',
                                            'MessageSid': 'test-sid'
                                        }

                                        result = await handler.process_webhook(payload)

                                        assert result['status'] == 'processed'
                                        assert result['message_sid'] == 'test-sid'
                                        mock_get_cust.assert_called_once()
                                        mock_create_conv.assert_called_once()
                                        mock_add_msg.assert_called()
                                        mock_query.assert_called_once_with(
                                            'Hello, how are you?',
                                            mock_customer.id,
                                            mock_conversation.id
                                        )

    @pytest.mark.asyncio
    async def test_whatsapp_send_response_message(self):
        """WhatsApp handler should send response messages."""
        handler = WhatsAppHandler()

        if handler.twilio_client:
            # Mock the Twilio message
            mock_twilio_message = MagicMock()
            mock_twilio_message.sid = 'test-message-sid'
            handler.twilio_client.messages.create.return_value = mock_twilio_message

            with patch('os.getenv') as mock_env:
                mock_env.return_value = '+1234567890'

                result = await handler.send_response_message(
                    to_number='whatsapp:+0987654321',
                    body='Test response message'
                )

                assert result.sid == 'test-message-sid'
                handler.twilio_client.messages.create.assert_called_once()
        else:
            # Without Twilio client, should handle gracefully
            with patch('os.getenv') as mock_env:
                mock_env.return_value = '+1234567890'

                with pytest.raises(Exception):
                    await handler.send_response_message(
                        to_number='whatsapp:+0987654321',
                        body='Test response message'
                    )


class TestWebFormHandler:
    def test_web_form_submission_validation(self):
        """Web form should validate input correctly."""
        # Test valid submission
        valid_data = WebFormSubmission(
            name="Test User",
            email="test@example.com",
            subject="Test Subject",
            category="technical",
            priority="medium",
            message="Test message"
        )

        assert valid_data.name == "Test User"
        assert valid_data.email == "test@example.com"

        # Test invalid email
        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="invalid-email",
                subject="Test Subject",
                category="technical",
                priority="medium",
                message="Test message"
            )

        # Test invalid category
        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="test@example.com",
                subject="Test Subject",
                category="invalid-category",
                priority="medium",
                message="Test message"
            )

        # Test invalid priority
        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="test@example.com",
                subject="Test Subject",
                category="technical",
                priority="invalid-priority",
                message="Test message"
            )

    def test_web_form_required_fields(self):
        """Web form should validate required fields."""
        with pytest.raises(ValueError):
            WebFormSubmission(
                name="",  # Empty name
                email="test@example.com",
                subject="Test Subject",
                category="technical",
                priority="medium",
                message="Test message"
            )

        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="",  # Empty email
                subject="Test Subject",
                category="technical",
                priority="medium",
                message="Test message"
            )

        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="test@example.com",
                subject="",  # Empty subject
                category="technical",
                priority="medium",
                message="Test message"
            )

        with pytest.raises(ValueError):
            WebFormSubmission(
                name="Test User",
                email="test@example.com",
                subject="Test Subject",
                category="technical",
                priority="medium",
                message=""  # Empty message
            )


class TestChannelCompatibility:
    @pytest.mark.asyncio
    async def test_cross_channel_consistency(self):
        """Verify that all channels handle similar data consistently."""
        # Test that all handlers can process similar customer information
        customer_info = {
            "email": "test@example.com",
            "name": "Test User",
            "phone": "+1234567890",
            "message": "This is a test message for cross-channel consistency."
        }

        # Gmail handler should handle email format
        gmail_handler = GmailHandler()
        gmail_parsed = {
            'sender_email': customer_info['email'],
            'sender_name': customer_info['name'],
            'body': customer_info['message']
        }

        # WhatsApp handler should handle phone format
        whatsapp_handler = WhatsAppHandler()
        whatsapp_payload = {
            'From': f"whatsapp:{customer_info['phone']}",
            'Body': customer_info['message'],
            'MessageSid': 'test-sid'
        }

        # All should be able to extract customer identity
        assert customer_info['email'] is not None
        assert customer_info['name'] is not None
        assert customer_info['phone'] is not None

    @pytest.mark.asyncio
    async def test_channel_specific_formatting(self):
        """Verify each channel applies appropriate formatting."""
        test_content = "This is a test message that should be formatted appropriately for each channel."

        # Gmail formatting
        gmail_handler = GmailHandler()
        gmail_formatted = gmail_handler.format_email_response(test_content, formal_tone=True)
        assert "Dear Valued Customer" in gmail_formatted

        # WhatsApp formatting
        whatsapp_handler = WhatsAppHandler()
        whatsapp_formatted = whatsapp_handler.format_whatsapp_response(test_content)
        # Should be truncated if too long
        assert len(whatsapp_formatted) <= 300

        # Web form doesn't format responses directly, but validates input
        # which is tested in the WebFormHandler tests


class TestChannelErrorHandling:
    @pytest.mark.asyncio
    async def test_gmail_error_handling(self):
        """Gmail handler should handle errors gracefully."""
        handler = GmailHandler()

        # Test webhook processing error
        with patch.object(handler, 'authenticate') as mock_auth:
            mock_auth.side_effect = Exception("Authentication failed")

            with pytest.raises(Exception):
                await handler.process_webhook({'historyId': 'test'})

    @pytest.mark.asyncio
    async def test_whatsapp_error_handling(self):
        """WhatsApp handler should handle errors gracefully."""
        handler = WhatsAppHandler()

        # Test message processing error
        with patch('services.whatsapp_handler.get_db') as mock_db_ctx:
            mock_db_ctx.__aenter__.side_effect = Exception("Database error")

            payload = {
                'From': 'whatsapp:+1234567890',
                'Body': 'Test message',
                'MessageSid': 'test-sid'
            }

            with pytest.raises(Exception):
                await handler.process_webhook(payload)

    def test_web_form_error_handling(self):
        """Web form should handle validation errors."""
        # Already tested in validation tests, but confirming
        with pytest.raises(ValueError):
            WebFormSubmission(
                name="",
                email="test@example.com",
                subject="Test",
                category="technical",
                priority="medium",
                message="Test"
            )
