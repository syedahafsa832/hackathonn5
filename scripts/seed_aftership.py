import os
import requests
import json
import logging
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AFTERSHIP_API_KEY = os.getenv("AFTERSHIP_API_KEY")
BASE_URL = "https://api.aftership.com/tracking/2024-04/trackings"
HEADERS = {
    "as-api-key": AFTERSHIP_API_KEY,
    "Content-Type": "application/json"
}







def create_tracking(tracking_number, carrier="ups"):
    """Create a tracking record in AfterShip."""
    payload = {
        "tracking": {
            "tracking_number": tracking_number,
            "slug": carrier,
            "title": f"Order {tracking_number[-4:]}",
            "emails": ["customer@example.com"]
        }
    }
    resp = requests.post(BASE_URL, headers=HEADERS, json=payload)

    if resp.status_code in [200, 201]:
        logger.info(f"Successfully created tracking: {tracking_number}")
        return resp.json()["data"]["tracking"]
    else:
        logger.error(f"Failed to create tracking {tracking_number}: {resp.text}")
        return None

def simulate_webhook(tracking_data):
    """Simulate a webhook payload delivery to local endpoint."""
    webhook_url = os.getenv("AFTERSHIP_WEBHOOK_URL", "http://localhost:8080/api/webhooks/aftership")
    payload = {
        "msg": tracking_data,
        "event": "tracking_update"
    }
    # In reality, AfterShip sends a more complex signature and structure
    # This mock follows the basic data shape for development
    try:
        resp = requests.post(webhook_url, json=payload)
        logger.info(f"Webhook simulation to {webhook_url}: Status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Could not simulate webhook to {webhook_url}: {e}")

def seed():
    """Seed 10 tracking records in AfterShip."""
    if not AFTERSHIP_API_KEY:
        logger.error("AFTERSHIP_API_KEY not set. Cannot seed AfterShip.")
        return

    carriers = ["ups", "fedex", "dhl"]
    for i in range(1, 11):
        # Generate varied tracking numbers
        tracking_number = f"TRACK{random.randint(100000, 999999)}_{i}"
        carrier = random.choice(carriers)
        tracking = create_tracking(tracking_number, carrier)
        
        if tracking:
            # Log example payload for Objective 6 implementation
            example_payload = {
                "msg": tracking,
                "event": "tracking_update",
                "ts": int(datetime.now().timestamp())
            }
            logger.info(f"Tracking Seeded: {tracking_number}. Example payload saved to logs.")

if __name__ == "__main__":
    seed()
