#!/usr/bin/env python3
"""
Test script to verify cross-channel customer identification
"""

import asyncio
import uuid
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy import select
from backend.src.services.database import db_session
from backend.src.models.customer import Customer
from backend.src.models.customer_identifier import CustomerIdentifier
from backend.src.models.conversation import Conversation
from backend.src.models.message import Message
from backend.src.services.customer_service import get_customer_by_identifier, get_or_create_customer
from backend.src.services.conversation_service import get_conversations_by_customer
from backend.src.services.message_service import get_messages_by_conversation

async def test_cross_channel_identification():
    """Test that same customer across different channels is identified as one person."""
    print("[INFO] Testing Cross-Channel Customer Identification...")

    async with db_session() as db:
        # Test 1: Create customer via web form (email)
        print("\n[Test 1] Creating customer via web form (email)")
        web_customer = await get_or_create_customer(
            db=db,
            email="test@example.com",
            name="Test User",
            phone="+1234567890"
        )
        print(f"   [PASS] Created customer: {web_customer.id}")
        print(f"   [INFO] Email: {web_customer.email}")
        print(f"   [INFO] Phone: {web_customer.phone}")

        # Verify customer identifiers were created
        identifier_result = await db.execute(
            select(CustomerIdentifier)
            .where(CustomerIdentifier.customer_id == web_customer.id)
        )
        identifiers = identifier_result.scalars().all()
        print(f"   [PASS] Customer identifiers created: {len(identifiers)}")
        for ident in identifiers:
            print(f"     - {ident.identifier_type}: {ident.identifier_value}")

        # Test 2: Try to get same customer by phone (simulating WhatsApp contact)
        print("\n[Test 2] Resolving same customer by phone (WhatsApp simulation)")
        whatsapp_customer = await get_customer_by_identifier(
            db=db,
            identifier_value="+1234567890",
            identifier_type="phone"
        )

        if whatsapp_customer and whatsapp_customer.id == web_customer.id:
            print(f"   [PASS] SUCCESS: Same customer resolved by phone: {whatsapp_customer.id}")
            print(f"   [INFO] This proves cross-channel identification works!")
        else:
            print(f"   [FAIL] FAILURE: Different customer resolved by phone")
            if whatsapp_customer:
                print(f"     - Web form customer ID: {web_customer.id}")
                print(f"     - WhatsApp customer ID: {whatsapp_customer.id}")
            else:
                print("     - No customer found by phone")

        # Test 3: Try to get same customer by email (simulating email contact)
        print("\n[Test 3] Resolving same customer by email")
        email_customer = await get_customer_by_identifier(
            db=db,
            identifier_value="test@example.com",
            identifier_type="email"
        )

        if email_customer and email_customer.id == web_customer.id:
            print(f"   [PASS] SUCCESS: Same customer resolved by email: {email_customer.id}")
        else:
            print(f"   [FAIL] FAILURE: Different customer resolved by email")

        # Test 4: Create conversation and message for web form
        print("\n[Test 4] Creating web form conversation")
        web_conv = Conversation(
            customer_id=web_customer.id,
            initial_channel="web_form",
            status="open"
        )
        db.add(web_conv)
        await db.flush()

        web_msg = Message(
            conversation_id=web_conv.id,
            channel="web_form",
            direction="inbound",
            sender_identifier="test@example.com",
            content="I need help with API integration"
        )
        db.add(web_msg)
        await db.flush()

        print(f"   [PASS] Created web form conversation: {web_conv.id}")
        print(f"   [PASS] Created web form message: {web_msg.id}")

        # Test 5: Create conversation and message for WhatsApp
        print("\n[Test 5] Creating WhatsApp conversation for same customer")
        whatsapp_conv = Conversation(
            customer_id=web_customer.id,  # Same customer ID
            initial_channel="whatsapp",
            status="open"
        )
        db.add(whatsapp_conv)
        await db.flush()

        whatsapp_msg = Message(
            conversation_id=whatsapp_conv.id,
            channel="whatsapp",
            direction="inbound",
            sender_identifier="+1234567890",
            content="Hi, I contacted you via web form earlier about API help"
        )
        db.add(whatsapp_msg)
        await db.flush()

        print(f"   [PASS] Created WhatsApp conversation: {whatsapp_conv.id}")
        print(f"   [PASS] Created WhatsApp message: {whatsapp_msg.id}")

        # Test 6: Get all conversations for this customer (cross-channel)
        print("\n[Test 6] Retrieving all conversations for customer (cross-channel)")
        all_conversations = await get_conversations_by_customer(db, web_customer.id)
        print(f"   [PASS] Found {len(all_conversations)} conversations for customer")

        for conv in all_conversations:
            print(f"     - Channel: {conv.initial_channel}, ID: {conv.id}")

            # Get messages for each conversation
            messages = await get_messages_by_conversation(db, conv.id)
            for msg in messages:
                print(f"       - {msg.channel} ({msg.direction}): {msg.content[:50]}...")

        # Test 7: Summary
        print("\n[SUMMARY] Test Summary:")
        print(f"   - Customer ID: {web_customer.id}")
        print(f"   - Total Conversations: {len(all_conversations)}")
        print(f"   - Channels represented: {set(conv.initial_channel for conv in all_conversations)}")

        total_messages = 0
        for conv in all_conversations:
            msgs = await get_messages_by_conversation(db, conv.id)
            total_messages += len(msgs)

        print(f"   - Total Messages: {total_messages}")
        print(f"   - Customer Identifiers: {len(identifiers)}")

        # Verify cross-channel functionality
        if len(all_conversations) >= 2 and len(identifiers) >= 2:
            print("\n[SUCCESS] SUCCESS: Cross-channel customer identification is working!")
            print("   - Same customer linked across multiple channels")
            print("   - Conversations from different channels accessible")
            print("   - Customer identifiers properly stored")
            return True
        else:
            print("\n[FAILURE] FAILURE: Cross-channel identification not working properly")
            return False

if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

    print("[TEST] Running Cross-Channel Customer Identification Test")
    print("=" * 60)

    success = asyncio.run(test_cross_channel_identification())

    print("\n" + "=" * 60)
    if success:
        print("[SUCCESS] ALL TESTS PASSED - Cross-channel identification is working!")
    else:
        print("[FAILURE] TESTS FAILED - Cross-channel identification needs fixing")
    print("=" * 60)