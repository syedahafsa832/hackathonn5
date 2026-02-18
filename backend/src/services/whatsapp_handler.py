import logging
from typing import Dict, Any, Optional
from .meta_whatsapp_handler import MetaWhatsAppHandler

logger = logging.getLogger(__name__)

class WhatsAppHandler:
    def __init__(self):
        self.meta_handler = MetaWhatsAppHandler()

    async def process_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming WhatsApp webhook notification (Meta format)
        """
        # Note: If the payload is from the /webhooks/whatsapp endpoint, 
        # it might already be the JSON from Meta.
        # If it's from Twilio (legacy), it would be form data.
        # We assume Meta JSON here for production.
        
        result = await self.meta_handler.process_webhook(payload)
        if not result:
            return {"status": "skipped", "reason": "Not a message event"}
            
        return {
            "status": "processed",
            "from": result.get('customer_phone'),
            "body": result.get('content'),
            "message_id": result.get('channel_message_id')
        }

    async def send_response_message(self, to_number: str, body: str):
        """
        Send a response message via Meta WhatsApp API
        """
        return await self.meta_handler.send_message(to_number, body)
