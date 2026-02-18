from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
import hashlib
import hmac
import os
import uuid
import logging

from src.services.whatsapp_handler import WhatsAppHandler
from src.services.database import get_db
from src.services.kafka_client import kafka_client_service as kafka_service

# Initialize global handler instance for the router
whatsapp_handler = WhatsAppHandler()

router = APIRouter()

# Logger
logger = logging.getLogger(__name__)

# Pydantic models for webhook payloads (Generic or Meta-specific if needed)
class WhatsAppMessage(BaseModel):
    id: str
    from_number: str
    text: str
    timestamp: str

@router.get("/whatsapp")
async def whatsapp_verification(request: Request):
    """
    Handle Meta WhatsApp webhook verification (challenge-response)
    """
    return await whatsapp_handler.meta_handler.verify_webhook(request)

@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Handle incoming WhatsApp webhook notifications from Meta
    """
    try:
        # Meta sends JSON payload
        payload = await request.json()
        
        # Process the WhatsApp webhook via the handler
        result = await whatsapp_handler.process_webhook(payload)

        # Publish to Kafka if it's a valid message
        if result and result.get("status") == "processed":
            background_tasks.add_task(
                kafka_service.send_to_topic,
                "whatsapp_inbound",
                {
                    "channel": "whatsapp",
                    "customer_phone": result.get("from"),
                    "content": result.get("body"),
                    "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                    "webhook_type": "whatsapp",
                    "customer_name": result.get("customer_name", "")
                }
            )

        return {"status": "success"}

    except Exception as e:
        logger.error(f"WhatsApp webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"WhatsApp webhook processing failed: {str(e)}")


@router.post("/whatsapp/status")
async def whatsapp_status_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Handle WhatsApp message status updates from Meta
    """
    try:
        # Meta status updates are also JSON
        payload = await request.json()
        
        # Log and process status (simplified for production)
        logger.info(f"Meta WhatsApp status update: {payload}")

        # Publish status update to Kafka
        background_tasks.add_task(
            kafka_service.send_to_topic,
            "fte.whatsapp.status",
            {
                "payload": payload,
                "processed_at": __import__('datetime').datetime.utcnow().isoformat()
            }
        )

        return {"status": "success"}

    except Exception as e:
        logger.error(f"WhatsApp status webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"WhatsApp status webhook processing failed: {str(e)}")

    except Exception as e:
        logger.error(f"WhatsApp status webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"WhatsApp status webhook processing failed: {str(e)}")


@router.post("/generic")
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Generic webhook endpoint for any other integrations
    """
    try:
        # Get JSON payload
        payload = await request.json()

        # Log the generic webhook
        logger.info(f"Generic webhook received: {payload}")

        # Process asynchronously
        background_tasks.add_task(
            kafka_service.send_to_topic,
            "fte.generic.incoming",
            {
                "payload": payload,
                "source": "generic",
                "processed_at": __import__('datetime').datetime.utcnow().isoformat()
            }
        )

        return {"status": "received", "payload_keys": list(payload.keys())}

    except Exception as e:
        logger.error(f"Generic webhook error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Generic webhook processing failed: {str(e)}")
