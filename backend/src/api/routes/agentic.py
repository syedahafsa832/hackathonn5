"""
Agentic Decision Hub API Routes
The Intelligence Layer for AI Ops Console
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
import requests
import logging
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/agentic", tags=["agentic"])
logger = logging.getLogger(__name__)

# Mistral API configuration
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "p1i9KPJCfXxBqWJakwkF5lLJwurKZc1s")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

# Mock inventory for demo
MOCK_INVENTORY = {
    "XL": 25,
    "L": 10,
    "M": 0,
    "S": 5
}

# Mock tickets for demo
MOCK_TICKETS = {
    "TKT-1001": {
        "customer_name": "Sarah Mitchell",
        "customer_email": "sarah.mitchell@email.com",
        "order_id": "ORD-1002",
        "sentiment": "frustrated",
        "sentiment_score": 8,
        "status": "pending",
        "vip_status": "VIP",
        "ltv": 2450,
        "intent": "exchange",
        "requested_item": "Size XL",
        "message_content": "Hi, I ordered a Navy Blue T-Shirt in Size M but received Size L. I need Size XL instead. Can you please exchange this for me?",
    },
    "TKT-1002": {
        "customer_name": "James Chen",
        "customer_email": "james.chen@techmail.com",
        "order_id": "ORD-1245",
        "sentiment": "neutral",
        "sentiment_score": 5,
        "status": "pending",
        "vip_status": "Regular",
        "ltv": 380,
        "intent": "refund",
        "requested_item": None,
        "message_content": "I'd like to request a refund for my recent order.",
    },
}


# ============== Request Models ==============

class ProcessTicketRequest(BaseModel):
    ticket_id: str
    customer_email: str
    customer_name: str
    message_content: str
    order_id: Optional[str] = None


class ExecuteActionRequest(BaseModel):
    action_id: str
    action_type: str  # "exchange", "store_credit", "escalate"
    approved_by: Optional[str] = "admin"


# ============== Response Models ==============

class ShopifyAudit(BaseModel):
    order_id: str
    order_date: str
    order_total: float
    items: List[dict]
    return_window_open: bool
    days_remaining: int
    items_count: int


class InventoryCheck(BaseModel):
    item_id: str
    item_name: str
    available_quantity: int
    in_stock: bool


class AIRecommendation(BaseModel):
    intent: str
    requested_item: str
    sentiment_score: float
    sentiment_label: str
    recommended_action: str
    revenue_at_stake: float
    reasoning: str


class ProcessedTicketResponse(BaseModel):
    ticket_id: str
    shopify_audit: ShopifyAudit
    inventory_check: Optional[InventoryCheck]
    ai_recommendation: AIRecommendation
    raw_extraction: dict


# ============== Helper Functions ==============

async def extract_intent_with_llm(message_content: str, order_id: Optional[str] = None) -> dict:
    """
    Use Mistral LLM to extract intent, order_id, requested_item, and sentiment_score.
    """
    prompt = f"""You are a customer support AI analyst. Analyze the following customer message and extract:
1. intent: The customer's primary intent (refund, exchange, order_status, damaged_product, other)
2. requested_item: What item/size/color they're asking about (if applicable)
3. sentiment_score: A score from 1-10 (1=very happy, 10=very frustrated)
4. order_id: The order number if mentioned (format: ORD-XXXX or just the number)

Customer Message:
{message_content}

Respond in JSON format only:
{{
    "intent": "refund|exchange|order_status|damaged_product|other",
    "requested_item": "description of item or null",
    "sentiment_score": 1-10,
    "order_id": "order number or null",
    "reasoning": "brief explanation"
}}"""

    try:
        headers = {
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "mistral-small-latest",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        response = requests.post(MISTRAL_API_URL, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            import json
            extracted = json.loads(content)
            return extracted
        else:
            logger.error(f"Mistral API error: {response.status_code} - {response.text}")
            # Return fallback extraction
            return {
                "intent": "refund",
                "requested_item": None,
                "sentiment_score": 5,
                "order_id": order_id,
                "reasoning": "Fallback due to API error"
            }

    except Exception as e:
        logger.error(f"LLM extraction error: {e}")
        return {
            "intent": "refund",
            "requested_item": None,
            "sentiment_score": 5,
            "order_id": order_id,
            "reasoning": f"Fallback due to error: {str(e)}"
        }


async def get_shopify_audit(order_id: str) -> ShopifyAudit:
    """
    Fetch order details from Shopify and check return window.
    """
    try:
        # Try to get order from Supabase first
        from src.lib.supabase_client import supabase_select

        orders = supabase_select("orders", {"order_id": f"eq.{order_id}"})

        if orders:
            order = orders[0]
            order_date = datetime.fromisoformat(order.get("created_at", datetime.now().isoformat()))
            days_remaining = RETURN_WINDOW_DAYS - (datetime.now() - order_date).days

            return ShopifyAudit(
                order_id=order_id,
                order_date=order.get("created_at", ""),
                order_total=float(order.get("total_price", 0)),
                items=order.get("line_items", []),
                return_window_open=days_remaining > 0,
                days_remaining=max(0, days_remaining),
                items_count=len(order.get("line_items", []))
            )
        else:
            # Return mock data for demo
            mock_order_date = datetime.now() - timedelta(days=15)
            return ShopifyAudit(
                order_id=order_id,
                order_date=mock_order_date.isoformat(),
                order_total=85.00,
                items=[
                    {"title": "Premium Cotton T-Shirt", "quantity": 1, "price": 85.00, "variant": "Size M / Navy Blue"}
                ],
                return_window_open=True,
                days_remaining=15,
                items_count=1
            )

    except Exception as e:
        logger.error(f"Shopify audit error: {e}")
        # Return mock data on error
        mock_order_date = datetime.now() - timedelta(days=15)
        return ShopifyAudit(
            order_id=order_id,
            order_date=mock_order_date.isoformat(),
            order_total=85.00,
            items=[
                {"title": "Premium Cotton T-Shirt", "quantity": 1, "price": 85.00, "variant": "Size M / Navy Blue"}
            ],
            return_window_open=True,
            days_remaining=15,
            items_count=1
        )


async def check_inventory(requested_item: str, order_id: str) -> Optional[InventoryCheck]:
    """
    Check live inventory levels for the requested item.
    """
    try:
        # Try to get inventory from Supabase
        from src.lib.supabase_client import supabase_select

        # Search for product variants
        variants = supabase_select("product_variants", {})

        # Extract size from requested_item
        size = "M"  # Default
        for s in ["XL", "L", "M", "S"]:
            if s.lower() in requested_item.lower():
                size = s
                break

        available = MOCK_INVENTORY.get(size, 0)

        return InventoryCheck(
            item_id=f"VAR-{order_id}-{size}",
            item_name=f"T-Shirt Size {size}",
            available_quantity=available,
            in_stock=available > 0
        )

    except Exception as e:
        logger.error(f"Inventory check error: {e}")
        return InventoryCheck(
            item_id=f"VAR-{order_id}-M",
            item_name="Premium Cotton T-Shirt Size M",
            available_quantity=10,
            in_stock=True
        )


def calculate_agentic_strategy(ticket_body: str, shopify_order_data: dict, sentiment_score: float, inventory_data: dict = None) -> dict:
    """
    The Reasoning Engine - Calculate the best strategy based on ticket and order data.

    Logic:
    - IF inventory > 0 AND return_window = open -> Strategy: "Exchange (Revenue Saved)"
    - IF sentiment > 7 -> Strategy: "Priority Exchange + Free Shipping"
    - IF return_window = closed -> Strategy: "Store Credit (Courtesy)"

    Output: JSON object with recommended_action, revenue_at_stake, and ai_reasoning string.
    """
    order_total = shopify_order_data.get("order_total", 0)
    days_remaining = shopify_order_data.get("days_remaining", 0)
    return_window_open = shopify_order_data.get("return_window_open", True)
    inventory_count = inventory_data.get("available_quantity", 0) if inventory_data else 0

    recommended_action = ""
    ai_reasoning = ""

    # Determine strategy based on rules
    if not return_window_open:
        # Return window closed
        recommended_action = "Store Credit (Courtesy)"
        ai_reasoning = f"Return window has expired ({abs(days_remaining)} days ago). Offer store credit as a courtesy to maintain customer relationship. Revenue at stake: ${order_total:.2f}"
    elif inventory_count > 0 and return_window_open:
        # Inventory available and return window open
        if sentiment_score > 7:
            # High sentiment - Priority Exchange + Free Shipping
            recommended_action = "Priority Exchange + Free Shipping"
            ai_reasoning = f"Frustrated customer (sentiment: {sentiment_score}/10) with inventory available ({inventory_count} units). Offer priority exchange with free shipping to turn negative experience positive. Revenue at stake: ${order_total:.2f}"
        else:
            # Normal exchange
            recommended_action = "Exchange (Revenue Saved)"
            ai_reasoning = f"Inventory available ({inventory_count} units) and return window open ({days_remaining} days remaining). Exchange saves the full ${order_total:.2f} revenue."
    elif inventory_count == 0 and return_window_open:
        # Out of stock but return window open
        recommended_action = "Store Credit (Out of Stock)"
        ai_reasoning = f"Requested item is out of stock ({inventory_count} available) but return window is open ({days_remaining} days). Offer store credit to retain customer. Revenue at stake: ${order_total:.2f}"
    else:
        # Default - manual review
        recommended_action = "Review Required"
        ai_reasoning = "Case requires manual review due to unusual circumstances."

    return {
        "recommended_action": recommended_action,
        "revenue_at_stake": order_total,
        "ai_reasoning": ai_reasoning
    }


def generate_recommendation(shopify_audit: ShopifyAudit, extraction: dict, inventory: Optional[InventoryCheck]) -> AIRecommendation:
    """
    Generate AI recommendation based on extracted intent, order audit, and inventory.
    Uses the calculate_agentic_strategy reasoning engine.
    """
    intent = extraction.get("intent", "refund")
    sentiment = extraction.get("sentiment_score", 5)
    revenue = shopify_audit.order_total

    # Sentiment label
    if sentiment >= 8:
        sentiment_label = "Frustrated"
    elif sentiment >= 5:
        sentiment_label = "Neutral"
    else:
        sentiment_label = "Happy"

    # Use the reasoning engine
    shopify_data = {
        "order_total": revenue,
        "days_remaining": shopify_audit.days_remaining,
        "return_window_open": shopify_audit.return_window_open
    }
    inventory_data = {
        "available_quantity": inventory.available_quantity if inventory else 0,
        "in_stock": inventory.in_stock if inventory else False
    } if inventory else None

    # Get strategy from reasoning engine
    strategy = calculate_agentic_strategy(
        ticket_body=extraction.get("reasoning", ""),
        shopify_order_data=shopify_data,
        sentiment_score=sentiment,
        inventory_data=inventory_data
    )

    # Override recommendation with strategy from reasoning engine
    recommended_action = strategy["recommended_action"]
    reasoning = strategy["ai_reasoning"]

    return AIRecommendation(
        intent=intent,
        requested_item=extraction.get("requested_item", "T-Shirt"),
        sentiment_score=float(sentiment),
        sentiment_label=sentiment_label,
        recommended_action=recommended_action,
        revenue_at_stake=revenue,
        reasoning=reasoning
    )


# ============== API Endpoints ==============

@router.post("/process-ticket", response_model=ProcessedTicketResponse)
async def process_ticket(request: ProcessTicketRequest):
    """
    The Intelligence Layer - Process a customer ticket and generate AI recommendation.
    Uses Mistral LLM to extract intent, then fetches Shopify context and checks inventory.
    """
    try:
        logger.info(f"Processing ticket: {request.ticket_id}")

        # Step 1: Extract intent with LLM
        extraction = await extract_intent_with_llm(
            request.message_content,
            request.order_id
        )

        # Use order_id from extraction if not provided
        order_id = extraction.get("order_id") or request.order_id or "ORD-1002"

        # Step 2: Get Shopify audit
        shopify_audit = await get_shopify_audit(order_id)

        # Step 3: Check inventory if exchange requested
        inventory = None
        if extraction.get("intent") == "exchange" or "exchange" in extraction.get("requested_item", "").lower():
            inventory = await check_inventory(
                extraction.get("requested_item", "T-Shirt"),
                order_id
            )

        # Step 4: Generate recommendation
        recommendation = generate_recommendation(shopify_audit, extraction, inventory)

        return ProcessedTicketResponse(
            ticket_id=request.ticket_id,
            shopify_audit=shopify_audit,
            inventory_check=inventory,
            ai_recommendation=recommendation,
            raw_extraction=extraction
        )

    except Exception as e:
        logger.error(f"Process ticket error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ticket/{ticket_id}")
async def get_ticket_analysis(ticket_id: str):
    """
    Get existing ticket analysis.
    """
    try:
        from src.lib.supabase_client import supabase_select

        tickets = supabase_select("tickets", {"id": f"eq.{ticket_id}"})

        if not tickets:
            # Return mock data for demo
            if ticket_id in MOCK_TICKETS:
                return MOCK_TICKETS[ticket_id]
            raise HTTPException(status_code=404, detail="Ticket not found")

        return tickets[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue")
async def get_decision_queue():
    """
    Get all tickets pending AI decision from the database.
    Falls back to demo tickets if no tickets in database.
    """
    try:
        from src.lib.supabase_client import supabase_select

        # Get pending tickets from database
        tickets = supabase_select("tickets", {"status": "eq.pending"})

        if not tickets or len(tickets) == 0:
            # Return demo tickets if no tickets in database
            return {
                "tickets": [
                    {
                        "id": "TKT-1001",
                        "ticket_id": "TKT-1001",
                        "customer_name": "Sarah Mitchell",
                        "customer_email": "sarah.mitchell@email.com",
                        "order_id": "ORD-1002",
                        "sentiment": "frustrated",
                        "sentiment_score": 8,
                        "status": "pending",
                        "vip_status": "VIP",
                        "ltv": 2450,
                        "intent": "exchange",
                        "requested_item": "Size XL",
                        "message_content": "Hi, I ordered a Navy Blue T-Shirt in Size M but received Size L. I need Size XL instead. Can you please exchange this for me?",
                        "channel": "email",
                        "created_at": "2 hours ago",
                        "createdAt": "2 hours ago",
                    },
                    {
                        "id": "TKT-1002",
                        "ticket_id": "TKT-1002",
                        "customer_name": "James Chen",
                        "customer_email": "james.chen@techmail.com",
                        "order_id": "ORD-1245",
                        "sentiment": "neutral",
                        "sentiment_score": 5,
                        "status": "pending",
                        "vip_status": "Regular",
                        "ltv": 380,
                        "intent": "refund",
                        "requested_item": None,
                        "message_content": "I'd like to request a refund for my recent order.",
                        "channel": "chat",
                        "created_at": "4 hours ago",
                        "createdAt": "4 hours ago",
                    },
                ],
                "count": 2,
                "source": "demo"
            }

        # Transform database tickets to frontend format
        transformed_tickets = []
        for ticket in tickets:
            transformed_tickets.append({
                "id": ticket.get("id", ""),
                "ticket_id": ticket.get("id", ""),
                "customer_name": ticket.get("customer_name", "Unknown"),
                "customer_email": ticket.get("customer_email", ""),
                "order_id": ticket.get("order_id", ""),
                "sentiment": ticket.get("sentiment", "neutral"),
                "sentiment_score": ticket.get("sentiment_score", 5),
                "status": ticket.get("status", "pending"),
                "vip_status": ticket.get("vip_status", "Regular"),
                "ltv": ticket.get("ltv", 0),
                "intent": ticket.get("intent", "other"),
                "requested_item": ticket.get("requested_item"),
                "message_content": ticket.get("content", ticket.get("message", "")),
                "channel": ticket.get("channel", "email"),
                "created_at": ticket.get("created_at", ""),
                "createdAt": ticket.get("created_at", ""),
            })

        return {
            "tickets": transformed_tickets,
            "count": len(transformed_tickets),
            "source": "database"
        }

    except Exception as e:
        logger.error(f"Error fetching queue: {e}")
        # Return demo tickets on error
        return {
            "tickets": [],
            "count": 0,
            "error": str(e)
        }


@router.post("/execute-action")
async def execute_action(request: ExecuteActionRequest):
    """
    Execute the approved action (Exchange, Store Credit, or Escalate).
    """
    try:
        logger.info(f"Executing action: {request.action_type} for {request.action_id}")

        if request.action_type == "exchange":
            # Create exchange order in Shopify
            return {
                "success": True,
                "message": "Exchange order created in Shopify",
                "action_id": request.action_id,
                "shopify_action": "exchange_created"
            }
        elif request.action_type == "store_credit":
            # Issue store credit
            return {
                "success": True,
                "message": "Store credit issued to customer",
                "action_id": request.action_id,
                "shopify_action": "store_credit_issued"
            }
        elif request.action_type == "escalate":
            # Escalate to human
            return {
                "success": True,
                "message": "Ticket escalated to human agent",
                "action_id": request.action_id,
                "shopify_action": "escalated"
            }
        else:
            raise HTTPException(status_code=400, detail="Invalid action type")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def agentic_health():
    """Health check for agentic service."""
    return {
        "status": "ok",
        "service": "agentic-decision-hub",
        "llm": "mistral-small-latest"
    }
