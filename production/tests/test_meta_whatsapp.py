import requests
import json
import time

# Configuration
API_BASE_URL = "http://127.0.0.1:8000"
WEBHOOK_URL = f"{API_BASE_URL}/webhooks/whatsapp"

def test_whatsapp_webhook():
    """Simulate an incoming WhatsApp message from Meta"""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "823259887084979",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "951885271332030"
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": "1234567890"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "1234567890",
                                    "id": "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjM0NUI2Nzg5MDEyMzQ1Njc4OQA=",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": "Hello, I need help with my account."},
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    print(f"Sending Meta WhatsApp payload to {WEBHOOK_URL}...")
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        print(f"Response Status: {response.status_code}")
        print(f"Response Body: {response.text}")
        assert response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_whatsapp_webhook()
