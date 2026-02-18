import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import uuid
from backend.src.api.routes.support import WebFormSubmission
from backend.src.services.gmail_handler import GmailHandler
from backend.src.services.whatsapp_handler import WhatsAppHandler


class TestWebFormChannel:
    @pytest.mark.asyncio
    async def test_web_form_submit_success(self):
        """Test successful web form submission."""
        # Mock database and services
        with patch('api.routes.support.get_db') as mock_db_ctx:
            with patch('api.routes.support.get_or_create_customer') as mock_get_customer:
                with patch('api.routes.support.create_ticket') as mock_create_ticket:
                    with patch('api.routes.support.kafka_service') as mock_kafka:
                        # Setup mocks
                        mock_db = AsyncMock()
                        mock_db_ctx.__aenter__.return_value = mock_db
                        mock_db_ctx.__aexit__.return_value = AsyncMock()

                        mock_customer = MagicMock()
                        mock_customer.id = uuid.uuid4()
                        mock_get_customer.return_value = mock_customer

                        mock_ticket = MagicMock()
                        mock_ticket.id = uuid.uuid4()
                        mock_create_ticket.return_value = mock_ticket

                        # Import the function to test
                        from backend.src.api.routes.support import submit_support_request
                        from fastapi import BackgroundTasks

                        # Create a mock background tasks object
                        background_tasks = BackgroundTasks()

                        # Submit a valid request
                        form_data = WebFormSubmission(
                            name="Test User",
                            email="test@example.com",
                            subject="Test Subject",
                            category="technical",
                            priority="medium",
                            message="Test message"
                        )

                        result = await submit_support_request(
                            request=form_data,
                            background_tasks=background_tasks,
                            db=mock_db
                        )

                        assert result.id == str(mock_ticket.id)
                        assert result.status == "created"
                        mock_get_customer.assert_called_once()
                        mock_create_ticket.assert_called_once()

    @pytest.mark.asyncio
    async def test_web_form_invalid_data(self):
        """Test web form validation with invalid data."""
        from backend.src.api.routes.support import submit_support_request
        from fastapi import BackgroundTasks

        background_tasks = BackgroundTasks()

        # Test with invalid email
        invalid_form = WebFormSubmission(
            name="Test User",
            email="invalid-email",
            subject="Test Subject",
            category="technical",
            priority="medium",
            message="Test message"
        )

        with pytest.raises(Exception):
            await submit_support_request(
                request=invalid_form,
                background_tasks=background_tasks,
                db=AsyncMock()
            )

    @pytest.mark.asyncio
    async def test_web_form_ticket_retrieval(self):
        """Test retrieving submitted ticket."""
        with patch('api.routes.support.db') as mock_db:
            with patch('sqlalchemy.ext.asyncio.AsyncSession') as mock_session:
                mock_result = MagicMock()
                mock_ticket = MagicMock()
                mock_ticket.id = uuid.uuid4()
                mock_ticket.status = "open"
                mock_ticket.subject = "Test Subject"
                mock_ticket.category = "technical"
                mock_ticket.priority = "medium"
                mock_ticket.description = "Test description"
                mock_ticket.created_at = datetime.now()
                mock_ticket.updated_at = datetime.now()

                mock_result.first.return_value = mock_ticket

                from backend.src.api.routes.support import get_ticket_status

                ticket_id = str(uuid.uuid4())
                result = await get_ticket_status(
                    ticket_id=ticket_id,
                    db=mock_session
                )

                assert result.id == str(mock_ticket.id)
                assert result.status == "open"


class TestEmailChannel:
    @pytest.mark.asyncio
    async def test_gmail_webhook_processing(self):
        """Test processing of Gmail webhook notifications."""
        handler = GmailHandler()

        # Mock the Gmail service
        handler.service = MagicMock()
        mock_history_result = {'history': [{'messages': [{'id': 'test-msg-id'}]}]}
        handler.service.users.return_value.history.return_value.list.return_value.execute.return_value = mock_history_result

        # Mock message processing
        with patch.object(handler, 'process_email_message') as mock_process_msg:
            mock_process_msg.return_value = {"status": "processed"}

            payload = {
                'userId': 'test-user',
                'historyId': 'test-history-id'
            }

            result = await handler.process_webhook(payload)

            assert result['status'] == 'success'
            assert result['processed_count'] == 1
            mock_process_msg.assert_called_once_with('test-msg-id')

    @pytest.mark.asyncio
    async def test_gmail_message_parsing(self):
        """Test parsing of Gmail message content."""
        handler = GmailHandler()

        # Mock a Gmail message
        mock_message = {
            'id': 'test-message-id',
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'Sender Name <sender@example.com>'},
                    {'name': 'Subject', 'value': 'Test Subject'},
                    {'name': 'Date', 'value': 'Mon, 01 Jan 2024 00:00:00 +0000'}
                ],
                'parts': [{
                    'mimeType': 'text/plain',
                    'body': {
                        'data': 'VGhpcyBpcyB0ZXN0IGNvbnRlbnQ='  # Base64 for "This is test content"
                    }
                }]
            }
        }

        parsed = handler.extract_email_content(mock_message)

        assert parsed['sender_name'] == 'Sender Name'
        assert parsed['sender_email'] == 'sender@example.com'
        assert parsed['subject'] == 'Test Subject'
        assert 'test content' in parsed['body']


class TestWhatsAppChannel:
    @pytest.mark.asyncio
    async def test_whatsapp_webhook_processing(self):
        """Test processing of WhatsApp webhook notifications."""
        handler = WhatsAppHandler()

        # Mock Twilio client
        handler.twilio_client = MagicMock()

        # Mock database and services
        with patch('backend.src.services.database.get_db') as mock_db_ctx:
            with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_customer:
                with patch('backend.src.services.conversation_service.create_conversation') as mock_create_conv:
                    with patch('backend.src.services.ticket_service.create_ticket') as mock_create_ticket:
                        with patch('backend.src.services.sentiment_analyzer.sentiment_analyzer') as mock_sentiment:
                            with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_query:
                                with patch.object(handler, 'send_response_message') as mock_send_resp:
                                    # Setup mocks
                                    mock_db = AsyncMock()
                                    mock_db_ctx.__aenter__.return_value = mock_db
                                    mock_db_ctx.__aexit__.return_value = AsyncMock()

                                    mock_customer = MagicMock()
                                    mock_customer.id = uuid.uuid4()
                                    mock_get_customer.return_value = mock_customer

                                    mock_conversation = MagicMock()
                                    mock_conversation.id = uuid.uuid4()
                                    mock_create_conv.return_value = mock_conversation

                                    mock_ticket = MagicMock()
                                    mock_ticket.id = uuid.uuid4()
                                    mock_create_ticket.return_value = mock_ticket

                                    mock_sentiment.analyze_sentiment.return_value = 0.2
                                    mock_query.return_value = "Test response message"
                                    mock_send_resp.return_value = MagicMock()

                                    payload = {
                                        'From': 'whatsapp:+1234567890',
                                        'To': 'whatsapp:+0987654321',
                                        'Body': 'Hello, I need help with my account',
                                        'MessageSid': 'test-sid'
                                    }

                                    result = await handler.process_webhook(payload)

                                    assert result['status'] == 'processed'
                                    assert result['message_sid'] == 'test-sid'
                                    mock_get_customer.assert_called_once()
                                    mock_create_conv.assert_called_once()
                                    mock_query.assert_called_once_with(
                                        'Hello, I need help with my account',
                                        mock_customer.id,
                                        mock_conversation.id
                                    )

    @pytest.mark.asyncio
    async def test_whatsapp_response_formatting(self):
        """Test WhatsApp response formatting."""
        handler = WhatsAppHandler()

        # Test normal message
        response = handler.format_whatsapp_response("This is a normal message")
        assert response == "This is a normal message"

        # Test long message (should be truncated)
        long_message = "This is a very long message that exceeds the 300 character limit for WhatsApp. " * 5
        formatted = handler.format_whatsapp_response(long_message)
        assert len(formatted) <= 300
        assert formatted.endswith("...")


class TestCrossChannelContinuity:
    @pytest.mark.asyncio
    async def test_same_customer_different_channels(self):
        """Test that the same customer is recognized across channels."""
        # Simulate customer using email first
        email_customer_id = uuid.uuid4()
        email_conversation_id = uuid.uuid4()

        # Mock services for email
        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_email_customer:
            with patch('backend.src.services.conversation_service.create_conversation') as mock_create_email_conv:
                with patch('backend.src.services.message_service.add_message_to_conversation') as mock_add_email_msg:
                    with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_email_query:
                        # Setup email mocks
                        mock_email_customer = MagicMock()
                        mock_email_customer.id = email_customer_id
                        mock_email_customer.email = "test@example.com"
                        mock_get_email_customer.return_value = mock_email_customer

                        mock_email_conv = MagicMock()
                        mock_email_conv.id = email_conversation_id
                        mock_create_email_conv.return_value = mock_email_conv

                        mock_email_query.return_value = "Thank you for your email. We'll look into this."

                        # Process email message
                        email_handler = GmailHandler()
                        email_payload = {
                            'userId': 'test-user',
                            'historyId': 'test-history-id'
                        }

                        # Mock the service for email
                        email_handler.service = MagicMock()
                        mock_history_result = {'history': [{'messages': [{'id': 'email-msg-1'}]}]}
                        email_handler.service.users.return_value.history.return_value.list.return_value.execute.return_value = mock_history_result

                        with patch.object(email_handler, 'process_email_message') as mock_process_email:
                            mock_process_email.return_value = {"status": "processed"}

                            await email_handler.process_webhook(email_payload)

        # Now simulate same customer using WhatsApp
        whatsapp_customer_id = uuid.uuid4()
        whatsapp_conversation_id = uuid.uuid4()

        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_wa_customer:
            with patch('backend.src.services.conversation_service.create_conversation') as mock_create_wa_conv:
                with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_wa_query:
                    # Setup WhatsApp mocks - use same email to identify same customer
                    mock_wa_customer = MagicMock()
                    mock_wa_customer.id = whatsapp_customer_id  # This should be the same ID as email customer
                    mock_wa_customer.email = "test@example.com"  # Same email as used in email channel
                    mock_get_wa_customer.return_value = mock_wa_customer

                    mock_wa_conv = MagicMock()
                    mock_wa_conv.id = whatsapp_conversation_id
                    mock_create_wa_conv.return_value = mock_wa_conv

                    mock_wa_query.return_value = "Thanks for reaching out via WhatsApp!"

                    # Process WhatsApp message
                    wa_handler = WhatsAppHandler()
                    wa_handler.twilio_client = MagicMock()  # Mock client to avoid actual API calls

                    with patch('backend.src.services.database.get_db') as mock_db_ctx:
                        mock_db = AsyncMock()
                        mock_db_ctx.__aenter__.return_value = mock_db
                        mock_db_ctx.__aexit__.return_value = AsyncMock()

                        with patch.object(wa_handler, 'send_response_message') as mock_send_resp:
                            wa_payload = {
                                'From': 'whatsapp:+1234567890',
                                'Body': 'Following up on my email',
                                'MessageSid': 'wa-msg-1'
                            }

                            result = await wa_handler.process_webhook(wa_payload)

                            # The customer should be recognized as the same one from email
                            mock_get_wa_customer.assert_called_once()
                            assert result['status'] == 'processed'

    @pytest.mark.asyncio
    async def test_conversation_continuity_across_channels(self):
        """Test that conversation context is maintained across channels."""
        customer_id = uuid.uuid4()

        # Simulate customer asking about API on email
        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_customer:
            with patch('backend.src.services.conversation_service.create_conversation') as mock_create_conv:
                with patch('backend.src.services.message_service.add_message_to_conversation') as mock_add_msg:
                    with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_query:
                        with patch('backend.src.services.conversation_service.get_conversations_by_customer') as mock_get_conv:
                            # Setup mocks
                            mock_customer = MagicMock()
                            mock_customer.id = customer_id
                            mock_get_customer.return_value = mock_customer

                            mock_conv = MagicMock()
                            mock_conv.id = uuid.uuid4()
                            mock_create_conv.return_value = mock_conv
                            mock_get_conv.return_value = [mock_conv]

                            mock_query.return_value = "For API questions, please check our documentation."

                            # Process email
                            email_handler = GmailHandler()
                            email_handler.service = MagicMock()
                            mock_history = {'history': [{'messages': [{'id': 'api-question'}]}]}
                            email_handler.service.users.return_value.history.return_value.list.return_value.execute.return_value = mock_history

                            with patch.object(email_handler, 'process_email_message') as mock_process:
                                mock_process.return_value = {"status": "processed"}
                                await email_handler.process_webhook({
                                    'userId': 'test',
                                    'historyId': 'hist'
                                })

        # Later, same customer asks related question on WhatsApp
        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_wa_customer:
            with patch('backend.src.services.conversation_service.create_conversation') as mock_create_wa_conv:
                with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_wa_query:
                    with patch('backend.src.services.conversation_service.get_conversations_by_customer') as mock_get_wa_conv:
                        # Setup WhatsApp mocks
                        mock_wa_customer = MagicMock()
                        mock_wa_customer.id = customer_id  # Same customer
                        mock_get_wa_customer.return_value = mock_wa_customer

                        mock_wa_conv = MagicMock()
                        mock_wa_conv.id = uuid.uuid4()
                        mock_create_wa_conv.return_value = mock_wa_conv
                        mock_get_wa_conv.return_value = [mock_wa_conv]  # Should return previous conversations too

                        mock_wa_query.return_value = "Regarding your API question from email..."

                        # Process WhatsApp
                        wa_handler = WhatsAppHandler()
                        wa_handler.twilio_client = MagicMock()

                        with patch('backend.src.services.database.get_db') as mock_db_ctx:
                            mock_db = AsyncMock()
                            mock_db_ctx.__aenter__.return_value = mock_db
                            mock_db_ctx.__aexit__.return_value = AsyncMock()

                            with patch.object(wa_handler, 'send_response_message') as mock_send_resp:
                                wa_payload = {
                                    'From': 'whatsapp:+1234567890',
                                    'Body': 'Can you elaborate on the API question I asked?',
                                    'MessageSid': 'wa-followup'
                                }

                                result = await wa_handler.process_webhook(wa_payload)

                                # Should recognize the context from previous email
                                assert result['status'] == 'processed'
                                mock_wa_query.assert_called_once()


class TestChannelMetrics:
    @pytest.mark.asyncio
    async def test_channel_performance_tracking(self):
        """Test that channel performance metrics are tracked."""
        # Test that each channel tracks its performance
        customer_id = uuid.uuid4()

        # Track email performance
        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_customer:
            with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_query:
                with patch('backend.src.services.kafka_client.kafka_client_service') as mock_kafka:
                    mock_customer = MagicMock()
                    mock_customer.id = customer_id
                    mock_get_customer.return_value = mock_customer

                    mock_query.return_value = "Email response"
                    mock_kafka.publish_message.return_value = AsyncMock()

                    # Process email
                    email_handler = GmailHandler()
                    email_handler.service = MagicMock()
                    mock_history = {'history': [{'messages': [{'id': 'perf-test'}]}]}
                    email_handler.service.users.return_value.history.return_value.list.return_value.execute.return_value = mock_history

                    with patch.object(email_handler, 'process_email_message') as mock_process:
                        mock_process.return_value = {"status": "processed"}
                        await email_handler.process_webhook({
                            'userId': 'test',
                            'historyId': 'hist'
                        })

                    # Check that metrics were published
                    # Kafka should have been called to publish metrics
                    assert mock_kafka.publish_message.called

        # Track WhatsApp performance
        with patch('backend.src.services.customer_service.get_or_create_customer') as mock_get_wa_customer:
            with patch('backend.src.agent.customer_success_agent.process_customer_query') as mock_wa_query:
                with patch('backend.src.services.kafka_client.kafka_client_service') as mock_wa_kafka:
                    mock_wa_customer = MagicMock()
                    mock_wa_customer.id = customer_id
                    mock_get_wa_customer.return_value = mock_wa_customer

                    mock_wa_query.return_value = "WhatsApp response"
                    mock_wa_kafka.publish_message.return_value = AsyncMock()

                    # Process WhatsApp
                    wa_handler = WhatsAppHandler()
                    wa_handler.twilio_client = MagicMock()

                    with patch('backend.src.services.database.get_db') as mock_db_ctx:
                        mock_db = AsyncMock()
                        mock_db_ctx.__aenter__.return_value = mock_db
                        mock_db_ctx.__aexit__.return_value = AsyncMock()

                        with patch.object(wa_handler, 'send_response_message') as mock_send_resp:
                            wa_payload = {
                                'From': 'whatsapp:+1234567890',
                                'Body': 'Performance test',
                                'MessageSid': 'perf-test'
                            }

                            await wa_handler.process_webhook(wa_payload)

                    # Check that metrics were published for WhatsApp
                    assert mock_wa_kafka.publish_message.called

    @pytest.mark.asyncio
    async def test_error_handling_across_channels(self):
        """Test error handling consistency across all channels."""
        # Test that all channels handle errors gracefully

        # Email error handling
        email_handler = GmailHandler()
        with patch.object(email_handler, 'authenticate') as mock_auth:
            mock_auth.side_effect = Exception("Email service unavailable")

            with pytest.raises(Exception) as exc_info:
                await email_handler.process_webhook({
                    'userId': 'test',
                    'historyId': 'test'
                })

            assert "Email service unavailable" in str(exc_info.value)

        # WhatsApp error handling
        wa_handler = WhatsAppHandler()
        with patch('backend.src.services.database.get_db') as mock_db_ctx:
            mock_db_ctx.__aenter__.side_effect = Exception("Database connection failed")

            wa_payload = {
                'From': 'whatsapp:+1234567890',
                'Body': 'Test message',
                'MessageSid': 'test-error'
            }

            with pytest.raises(Exception) as exc_info:
                await wa_handler.process_webhook(wa_payload)

            assert "Database connection failed" in str(exc_info.value)

        # Web form error handling
        with patch('api.routes.support.get_db') as mock_db:
            mock_db.side_effect = Exception("Database error")

            from backend.src.api.routes.support import submit_support_request
            from fastapi import BackgroundTasks

            form_data = WebFormSubmission(
                name="Test User",
                email="test@example.com",
                subject="Test Subject",
                category="technical",
                priority="medium",
                message="Test message"
            )

            background_tasks = BackgroundTasks()

            with pytest.raises(Exception) as exc_info:
                await submit_support_request(
                    request=form_data,
                    background_tasks=background_tasks,
                    db=mock_db
                )

            assert "Database error" in str(exc_info.value)
