import os
import json
import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime
import tenacity

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import base64
from email.mime.text import MIMEText

from src.lib.supabase_client import supabase_get_setting

logger = logging.getLogger(__name__)

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

class GmailHandler:
    def __init__(self):
        self.creds = None
        self.service = None
        self._initialize_credentials()

    def _initialize_credentials(self):
        """Initialize credentials from environment variables or Supabase."""
        # 0. Try to load from Supabase Settings Table (Primary Source of Truth for Production)
        token_json = None
        token_data = supabase_get_setting("GMAIL_TOKEN")
        if token_data:
            try:
                self.creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logger.info("Successfully loaded Gmail credentials from Supabase.")
            except Exception as e:
                logger.error(f"Failed to load Gmail token from Supabase: {e}")

        # 1. Fallback to GMAIL_TOKEN env var or token.json file if not found in Supabase
        if not self.creds:
            token_json = os.getenv("GMAIL_TOKEN")
        
        # Fallback to local token.json if env var is missing
        if not token_json and os.path.exists("token.json"):
            try:
                with open("token.json", "r") as f:
                    token_json = f.read()
                logger.info("Found token.json file, using as backup.")
            except Exception as e:
                logger.error(f"Failed to read token.json: {e}")

        if token_json:
            try:
                token_data = json.loads(token_json)
                self.creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                logger.info("Successfully loaded Gmail credentials.")
            except Exception as e:
                logger.error(f"Failed to parse Gmail token: {e}")

        # 2. If no valid creds, try to use GMAIL_CREDENTIALS for refresh or new auth
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                    logger.info("✓ Gmail token refreshed successfully.")
                except Exception as e:
                    logger.error(f"Failed to refresh Gmail token: {e}")
                    self.creds = None

            if not self.creds:
                creds_json = os.getenv("GMAIL_CREDENTIALS")
                if creds_json:
                    try:
                        creds_data = json.loads(creds_json)
                        flow = InstalledAppFlow.from_client_config(creds_data, SCOPES)
                        flow.redirect_uri = 'https://hackathonn5-production.up.railway.app/auth/callback'
                        
                        # We no longer exchange GMAIL_TOKEN_CODE here to avoid invalid_grant errors.
                        # Instead, the user must visit /auth/google to generate a permanent token in Supabase.
                        auth_url, _ = flow.authorization_url(prompt='consent')
                        logger.warning("\n" + "="*60)
                        logger.warning("GMAIL AUTHORIZATION REQUIRED")
                        logger.warning(f"Please visit: https://hackathonn5-production.up.railway.app/auth/google")
                        logger.warning("="*60 + "\n")
                    except Exception as e:
                        logger.error(f"Error preparing Gmail flow: {e}")
        
        if self.creds:
            self.service = build('gmail', 'v1', credentials=self.creds)

    async def process_new_emails(self) -> Dict[str, Any]:
        """Bridge method for EmailPoller compatibility. Fetches unread emails and marks them as read."""
        emails = await self.get_new_emails()
        
        processed_emails = []
        for email in emails:
            # Mark as read in Gmail
            try:
                self.service.users().messages().batchModify(
                    userId='me',
                    body={
                        'ids': [email['id']],
                        'removeLabelIds': ['UNREAD']
                    }
                ).execute()
            except Exception as e:
                logger.error(f"Error marking email {email['id']} as read: {e}")

            # Extract name and email from "Name <email@example.com>" format
            sender_raw = email['sender_email']
            sender_name = sender_raw
            sender_email = sender_raw
            
            if '<' in sender_raw and '>' in sender_raw:
                sender_name = sender_raw.split('<')[0].strip()
                import re
                match = re.search(r'<(.*)>', sender_raw)
                if match:
                    sender_email = match.group(1)

            processed_emails.append({
                'id': email['id'],
                'sender_name': sender_name,
                'sender_email': sender_email,
                'subject': email['subject'],
                'body': email['body']
            })

        return {
            'count': len(processed_emails),
            'emails': processed_emails
        }

    def format_email_response(self, ai_response: str, customer_email: str, original_subject: str = "") -> Dict[str, str]:
        """Format the AI response into a proper email response."""
        if original_subject and (original_subject.startswith("Re:") or original_subject.startswith("RE:")):
            subject = original_subject
        else:
            subject = f"Re: {original_subject}" if original_subject else "Re: Customer Support Response"

        # Clean up the AI response
        cleaned_response = self._clean_ai_response(ai_response)

        email_body = f"""{cleaned_response}

Best regards,

Luna
Customer Success Team
TechCorp
"""

        return {
            'subject': subject,
            'body': email_body,
            'to_email': customer_email
        }

    def _clean_ai_response(self, ai_response: str) -> str:
        """Clean the AI response by removing any existing email formalities."""
        return ai_response.strip()

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(Exception)
    )
    async def send_response_email(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email using Gmail API."""
        if not self.service:
            self._initialize_credentials()
            if not self.service:
                logger.error("Cannot send email: Gmail service not initialized.")
                return {'status': 'error', 'error': 'Service not initialized'}

        try:
            message = MIMEText(body)
            message['to'] = to_email
            message['subject'] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Execute the send
            sent_message = self.service.users().messages().send(userId='me', body={'raw': raw}).execute()
            
            logger.info(f"✓ Email successfully sent via Gmail API to {to_email}. ID: {sent_message.get('id')}")
            return {
                'status': 'sent',
                'id': sent_message.get('id'),
                'to_email': to_email
            }
        except HttpError as error:
            logger.error(f"An error occurred sending Gmail: {error}")
            raise

    async def send_reply(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        return await self.send_response_email(to_email, subject, body)

    async def get_new_emails(self, max_results=10) -> List[Dict]:
        """Fetch unread messages from Inbox."""
        if not self.service:
            self._initialize_credentials()
            if not self.service:
                logger.warning("Gmail Poller: Service not initialized. Check GMAIL_CREDENTIALS / GMAIL_TOKEN_CODE.")
                return []
        try:
            # ONLY fetch unread emails from the PRIMARY category (ignoring Promotions, Social, etc.)
            results = self.service.users().messages().list(userId='me', q='is:unread category:primary').execute()
            messages = results.get('messages', [])
            
            email_data = []
            for msg in messages[:max_results]:
                try:
                    full_msg = self.service.users().messages().get(userId='me', id=msg['id']).execute()
                    # Parse headers
                    headers = full_msg['payload']['headers']
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "No Subject")
                    sender = next((h['value'] for h in headers if h['name'] == 'From'), "Unknown Sender")
                    
                    email_data.append({
                        'id': msg['id'],
                        'subject': subject,
                        'sender_email': sender,
                        'body': full_msg.get('snippet', '')
                    })
                except Exception as e:
                    logger.error(f"Error fetching individual email {msg['id']}: {e}")
            return email_data
        except Exception as e:
            logger.error(f"Error fetching unread emails from Gmail: {e}")
            return []

# Singleton instance
gmail_handler = GmailHandler()
