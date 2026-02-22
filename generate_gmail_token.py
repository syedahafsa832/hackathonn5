"""
Run this script ONCE locally to generate a Gmail token.
It will open a browser for you to authorize, then print the token JSON.
Copy that JSON and set it as GMAIL_TOKEN in Railway environment variables.

Usage:
    python generate_gmail_token.py

Requirements:
    pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Path to your downloaded client secret JSON file
CLIENT_SECRET_FILE = r'C:\Users\Uswer\Downloads\client_secret_909957709543-nl5u456fqt0n9d6kchehbfud0amd14gt.apps.googleusercontent.com.json'

def main():
    # We use a fixed port 8080 to make it easier to add to Google Console
    PORT = 8080
    print("\n" + "="*60)
    print("GMAIL TOKEN GENERATOR")
    print("="*60)
    print(f"IMPORTANT: You must add http://localhost:{PORT}/ to your")
    print("Authorized Redirect URIs in the Google Cloud Console.")
    print("1. Go to: https://console.cloud.google.com/apis/credentials")
    print("2. Edit your OAuth 2.0 Client ID (Web Client)")
    print(f"3. Add 'http://localhost:{PORT}/' to Authorized redirect URIs")
    print("4. Save and THEN run this script again.")
    print("="*60 + "\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        # Using a fixed port helps with redirect_uri_mismatch
        creds = flow.run_local_server(port=PORT)
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nIf you still get redirect_uri_mismatch, make sure the URL in the error")
        print(f"matches http://localhost:{PORT}/ exactly (including the trailing slash).")
        return

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }

    token_json = json.dumps(token_data)
    
    print("\n" + "="*60)
    print("SUCCESS! Copy the JSON below and set it as GMAIL_TOKEN in Railway:")
    print("="*60)
    print(token_json)
    print("="*60)
    
    # Also save to file as backup
    with open("gmail_token_output.json", "w") as f:
        f.write(token_json)
    print("\nAlso saved to: gmail_token_output.json")

if __name__ == "__main__":
    main()
