import requests
import json

# Configuration
API_BASE_URL = "http://127.0.0.1:8000"
SUBMIT_URL = f"{API_BASE_URL}/support/submit"

def test_web_form_submission():
    """Test web form submission endpoint"""
    payload = {
        "name": "Jane Smith",
        "email": "jane.smith@example.com",
        "subject": "Technical Issue with Dashboard",
        "category": "technical",
        "priority": "high",
        "message": "The dashboard is not loading data since this morning. Please check.",
        "company": "Tech Corp"
    }

    print(f"Submitting web form to {SUBMIT_URL}...")
    try:
        response = requests.post(SUBMIT_URL, json=payload)
        print(f"Response Status: {response.status_code}")
        print(f"Response Body: {response.text}")
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        print(f"Ticket ID: {data['id']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_web_form_submission()
