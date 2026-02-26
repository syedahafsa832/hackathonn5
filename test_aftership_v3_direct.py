import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_aftership_connection():
    api_key = os.getenv("AFTERSHIP_API_KEY")
    print(f"Testing AfterShip with API Key: {api_key[:8]}...{api_key[-4:]}")

    if not api_key:
        print("Error: AFTERSHIP_API_KEY not found in .env")
        return

    endpoints = [
        "https://api.aftership.com/v4/trackings",
        "https://api.aftership.com/v4/couriers",
        "https://api.aftership.com/tracking/2024-10/trackings",
        "https://api.aftership.com/tracking/v4/trackings"
    ]
    
    headers_to_try = [
        {"aftership-api-key": api_key, "Content-Type": "application/json"},
        {"as-api-key": api_key, "Content-Type": "application/json"}
    ]

    for url in endpoints:
        for headers in headers_to_try:
            h_key = list(headers.keys())[0]
            print(f"\nTrying: {url} with {h_key}")
            try:
                resp = requests.get(url, headers=headers)
                print(f"Status: {resp.status_code}")
                if resp.status_code == 200:
                    print(f"SUCCESS! {url} is valid.")
                    return
                else:
                    print(f"Error: {resp.text}")
            except Exception as e:
                print(f"Failed: {e}")

if __name__ == "__main__":
    test_aftership_connection()
