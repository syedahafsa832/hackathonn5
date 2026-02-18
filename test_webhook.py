#!/usr/bin/env python3
"""
Test script to verify the WhatsApp webhook verification endpoint
"""
import requests
import sys

def test_webhook_verification():
    """Test the webhook verification endpoint"""
    base_url = "http://localhost:8000"
    webhook_url = f"{base_url}/webhooks/whatsapp"

    # Test parameters that Meta would send
    params = {
        'hub.mode': 'subscribe',
        'hub.verify_token': 'my_verify_token_12345',  # Same as in .env
        'hub.challenge': 'TEST_CHALLENGE_12345'
    }

    print(f"Testing webhook verification at: {webhook_url}")
    print(f"Parameters: {params}")

    try:
        response = requests.get(webhook_url, params=params)
        print(f"Response Status: {response.status_code}")
        print(f"Response Text: {response.text}")

        if response.status_code == 200 and response.text == "TEST_CHALLENGE_12345":
            print("\n✅ SUCCESS: Webhook verification is working correctly!")
            print("The endpoint returned the correct challenge string.")
            return True
        else:
            print(f"\n❌ FAILURE: Expected 'TEST_CHALLENGE_12345', got '{response.text}'")
            return False

    except requests.exceptions.ConnectionError:
        print("\n❌ ERROR: Cannot connect to the API. Is the backend service running?")
        print("Make sure to start the backend service with: docker-compose up -d backend")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        return False

if __name__ == "__main__":
    success = test_webhook_verification()
    sys.exit(0 if success else 1)