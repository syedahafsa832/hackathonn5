import aiohttp
import os
import logging
from typing import Dict, Optional
from fastapi import Request, HTTPException

import tenacity

logger = logging.getLogger(__name__)

class MetaWhatsAppHandler:
    def __init__(self):
        self.access_token = os.getenv('META_WHATSAPP_ACCESS_TOKEN')
        self.phone_number_id = os.getenv('META_WHATSAPP_PHONE_NUMBER_ID')
        self.verify_token = os.getenv('META_VERIFY_TOKEN', 'my_verify_token')
        self.waba_id = os.getenv('META_WHATSAPP_WABA_ID')
        self.api_version = os.getenv('META_API_VERSION', 'v18.0')
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
    
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(5),
        retry=tenacity.retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(f"Retrying Meta WhatsApp message send. Attempt {retry_state.attempt_number}")
    )
    async def send_message(self, to_phone: str, message: str) -> Dict:
        """Send WhatsApp message via Meta Graph API with retry logic"""
        # Ensure phone number is in correct format (no +, no spaces)
        to_phone = to_phone.replace('+', '').replace(' ', '').replace('whatsapp:', '')
        
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"body": message}
        }
        
        logger.info(f"Attempting to send Meta WhatsApp message to {to_phone}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers) as response:
                    result = await response.json()
                    if response.status != 200:
                        logger.error(f"Meta API error (Status {response.status}): {result}")
                        raise Exception(f"Meta API error: {result}")
                    
                    message_id = result.get('messages', [{}])[0].get('id')
                    logger.info(f"Message sent successfully to {to_phone}. ID: {message_id}")
                    return result
            except aiohttp.ClientError as e:
                logger.error(f"Network error sending Meta WhatsApp message: {str(e)}")
                raise
    
    async def verify_webhook(self, request: Request):
        """Verify webhook for Meta (GET request)"""
        params = request.query_params
        mode = params.get('hub.mode')
        token = params.get('hub.verify_token')
        challenge = params.get('hub.challenge')

        # Allow hardcoded fallback for immediate validation if env var is missing/incorrect
        HARDCODED_BACKUP_TOKEN = "my_verify_token_12345"
        
        if mode == 'subscribe' and (token == self.verify_token or token == HARDCODED_BACKUP_TOKEN):
            logger.info(f"Meta webhook verified successfully with challenge: {challenge}")
            # CRITICAL: Return ONLY the challenge string as plain text
            from fastapi.responses import PlainTextResponse
            if challenge:
                return PlainTextResponse(content=str(challenge))
            else:
                logger.warning("Challenge parameter missing in verification request")
                return PlainTextResponse(content="")
        
        logger.warning(f"Meta webhook verification failed. Mode: {mode}, Token Match: {token == self.verify_token or token == HARDCODED_BACKUP_TOKEN}")
        expected = f"{self.verify_token} or {HARDCODED_BACKUP_TOKEN}"
        raise HTTPException(status_code=403, detail=f"Verification failed. Expected token: {expected}")
    
    async def process_webhook(self, webhook_data: Dict) -> Optional[Dict]:
        """Process incoming Meta webhook payload"""
        try:
            if not webhook_data.get('entry'):
                return None
                
            entry = webhook_data['entry'][0]
            if not entry.get('changes'):
                return None
                
            changes = entry['changes'][0]
            value = changes.get('value', {})
            
            messages = value.get('messages', [])
            if not messages:
                return None
            
            message = messages[0]
            contacts = value.get('contacts', [{}])
            contact = contacts[0]
            
            # Extract basic message data formatted for our internal use
            return {
                'channel': 'whatsapp',
                'channel_message_id': message.get('id'),
                'customer_phone': message.get('from'),
                'customer_name': contact.get('profile', {}).get('name', ''),
                'content': message.get('text', {}).get('body', '') if message.get('type') == 'text' else f"[Non-text message: {message.get('type')}]",
                'timestamp': message.get('timestamp')
            }
        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing Meta webhook: {str(e)}")
            return None
