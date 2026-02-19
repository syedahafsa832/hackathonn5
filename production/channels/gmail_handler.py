import asyncio
import smtplib
import os
import logging
from typing import Dict, Any, List
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import imaplib
import email
from email.header import decode_header
import tenacity

logger = logging.getLogger(__name__)

class GmailHandler:
    def __init__(self):
        self.sender_email = os.getenv("SUPPORT_EMAIL_ADDRESS", "syedahafsa832@gmail.com")
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.email_password = os.getenv("EMAIL_PASSWORD")  # App password for Gmail

        if not self.email_password:
            raise ValueError("EMAIL_PASSWORD environment variable is required for SMTP authentication")

    def format_email_response(self, ai_response: str, customer_email: str, original_subject: str = "") -> Dict[str, str]:
        """Format the AI response into a proper email response."""
        if original_subject and (original_subject.startswith("Re:") or original_subject.startswith("RE:")):
            subject = original_subject
        else:
            subject = f"Re: {original_subject}" if original_subject else "Re: Customer Support Response"

        # Clean up the AI response by removing any existing email formalities
        # to avoid double formatting
        cleaned_response = self._clean_ai_response(ai_response)

        # Create the email body with proper formatting
        email_body = f"""Dear Valued Customer,

Thank you for reaching out to our support team. We have processed your inquiry and here is the response from our AI assistant:

{cleaned_response}

If you have any further questions or need additional assistance, please don't hesitate to reach out.

Best regards,
Customer Success AI Agent
TechCorp Support Team

---
This is an automated response. Please note that our AI assistant handles routine inquiries. Complex issues are escalated to human agents."""

        return {
            'subject': subject,
            'body': email_body,
            'to_email': customer_email
        }

    def _clean_ai_response(self, ai_response: str) -> str:
        """Clean the AI response by removing any existing email formalities."""
        lines = ai_response.split('\n')
        cleaned_lines = []

        for line in lines:
            line_lower = line.lower().strip()

            # Skip lines that are email formalities
            if (line_lower.startswith("dear valued customer") or
                line_lower.startswith("dear customer") or
                line_lower.startswith("hello valued customer") or
                line_lower.startswith("hi valued customer") or
                "best regards," in line_lower or
                "regards," in line_lower or
                "sincerely," in line_lower or
                "thank you" in line_lower and "customer success ai agent" in line_lower or
                "customer success ai agent" in line_lower or
                "---" in line and "automated response" in line_lower or
                "automated response" in line_lower):
                continue

            cleaned_lines.append(line)

        # Join the lines back together and clean up extra whitespace
        cleaned_response = '\n'.join(cleaned_lines).strip()

        # Remove any trailing formalities
        if cleaned_response.lower().endswith("best regards,"):
            cleaned_response = cleaned_response[:-len("best regards,")].rstrip()
        elif cleaned_response.lower().endswith("regards,"):
            cleaned_response = cleaned_response[:-len("regards,")].rstrip()
        elif cleaned_response.lower().endswith("sincerely,"):
            cleaned_response = cleaned_response[:-len("sincerely,")].rstrip()

        return cleaned_response.strip()

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(f"Retrying email send to {retry_state.args[1]}. Attempt {retry_state.attempt_number}")
    )
    async def send_response_email(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        """Send an email response to a customer. Uses Resend (HTTPS) if key is present, otherwise fallback to SMTP."""
        
        # 1. Try Resend (HTTPS) - Best for Railway Trial/Free plans where SMTP is blocked
        resend_key = os.getenv("RESEND_API_KEY")
        if resend_key:
            try:
                import httpx
                logger.info(f"Attempting HTTPS delivery via Resend to {to_email}...")
                
                # Resend's default test email is onboarding@resend.dev
                # In production, they should use their verified domain
                from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {resend_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "from": from_email,
                            "to": to_email,
                            "subject": subject,
                            "text": body
                        }
                    )
                    
                    if response.status_code in [200, 201, 202, 204]:
                        logger.info(f"✓ Email successfully sent via Resend API to {to_email}")
                        return {
                            'status': 'sent',
                            'method': 'resend',
                            'to_email': to_email,
                            'subject': subject
                        }
                    else:
                        logger.error(f"Resend API Error ({response.status_code}): {response.text}")
                        # If Resend fails, we still try SMTP fallback below
            except ImportError:
                logger.error("httpx not installed. Cannot use Resend.")
            except Exception as e:
                logger.error(f"Resend delivery failed: {e}")

        # 2. Fallback to Traditional SMTP
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = to_email
            msg['Subject'] = subject

            # Add body to email
            msg.attach(MIMEText(body, 'plain'))

            # Create SMTP session and send
            # We use a context manager for SMTP to ensure resources are cleaned up
            logger.info(f"Connecting to SMTP server {self.smtp_server}:{self.smtp_port}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.set_debuglevel(1) if logger.level <= logging.DEBUG else server.set_debuglevel(0)
                server.starttls()  # Enable security
                logger.info(f"Logging in as {self.sender_email}...")
                server.login(self.sender_email, self.email_password)
                logger.info(f"Sending mail to {to_email}...")
                server.sendmail(self.sender_email, to_email, msg.as_string())

            logger.info(f"Email sent successfully to {to_email} via SMTP")

            return {
                'status': 'sent',
                'method': 'smtp',
                'to_email': to_email,
                'subject': subject
            }
        except Exception as e:
            logger.error(f"Final error sending email entry to {to_email}: {e}")
            raise # Important to raise so tenacity can retry

    # Alias for local logic compatibility
    async def send_reply(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        return await self.send_response_email(to_email, subject, body)

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(f"Retrying email check. Attempt {retry_state.attempt_number}")
    )
    async def check_new_emails(self, days_back: int = 1) -> List[Dict[str, Any]]:
        """Check for new emails using IMAP with retry logic."""
        try:
            # Connect to server
            mail = imaplib.IMAP4_SSL(self.smtp_server)
            mail.login(self.sender_email, self.email_password)

            # Select mailbox
            mail.select('inbox')

            # Search for emails from the last N days
            since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'SINCE {since_date} UNSEEN')

            email_ids = messages[0].split()
            new_emails = []

            for email_id in email_ids:
                # Fetch the email
                status, msg_data = mail.fetch(email_id, '(RFC822)')

                # Get the email content
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)

                # Decode subject
                subject = email_message["Subject"]
                if subject:
                    decoded_subject = decode_header(subject)[0]
                    if decoded_subject[1]:
                        subject = decoded_subject[0].decode(decoded_subject[1])
                    else:
                        subject = decoded_subject[0]

                # Get sender
                sender = email_message["From"]
                sender_email = email.utils.parseaddr(sender)[1] if sender else "Unknown"
                sender_name = email.utils.parseaddr(sender)[0] if sender else "Unknown"

                # Get date
                date = email_message["Date"]

                # Get body
                body = ""
                if email_message.is_multipart():
                    for part in email_message.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))

                        if content_type == "text/plain" and "attachment" not in content_disposition:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode()
                            break
                else:
                    payload = email_message.get_payload(decode=True)
                    if payload:
                        body = payload.decode()

                email_content = {
                    'id': email_id.decode(),
                    'sender_name': sender_name,
                    'sender_email': sender_email,
                    'subject': subject or "No Subject",
                    'body': body or "",
                    'date': date or "",
                    'thread_id': email_message.get('Thread-Index', ''),
                    'message_id': email_message.get('Message-ID', '')
                }

                new_emails.append(email_content)

            mail.close()
            mail.logout()

            if new_emails:
                logger.info(f"Successfully pulled {len(new_emails)} new emails")
            return new_emails

        except Exception as e:
            logger.error(f"IMAP error encountered: {e}")
            raise

    async def mark_as_read(self, email_ids: List[str]) -> bool:
        """Mark emails as read by setting the SEEN flag."""
        try:
            mail = imaplib.IMAP4_SSL(self.smtp_server)
            mail.login(self.sender_email, self.email_password)
            mail.select('inbox')

            for email_id in email_ids:
                # Mark as read
                mail.store(email_id, '+FLAGS', '\\Seen')

            mail.close()
            mail.logout()

            logger.info(f"Marked {len(email_ids)} emails as read")
            return True
        except Exception as e:
            logger.error(f"Error marking emails as read: {e}")
            return False

    async def process_new_emails(self) -> Dict[str, Any]:
        """Process all new emails and return them for AI processing."""
        new_emails = await self.check_new_emails(days_back=1)

        processed = []
        email_ids_to_mark_read = []

        for email in new_emails:
            # Prepare the email data for processing
            email_for_processing = {
                'id': email['id'],
                'thread_id': email.get('thread_id', ''),
                'sender_email': email['sender_email'],
                'sender_name': email['sender_name'],
                'subject': email['subject'],
                'body': email['body'],
                'received_at': email['date']
            }

            processed.append(email_for_processing)
            email_ids_to_mark_read.append(email['id'])

        # Mark processed emails as read
        if email_ids_to_mark_read:
            await self.mark_as_read(email_ids_to_mark_read)

        return {
            'emails': processed,
            'count': len(processed),
            'marked_as_read': len(email_ids_to_mark_read)
        }


# Singleton instance
gmail_handler = GmailHandler()