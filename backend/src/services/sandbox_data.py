"""
Luna Sandbox - static sample-store fixtures.

Single, isolated source of truth for the sandbox. Everything here is fake
sample data for "Northstar Apparel" - a fictional store. Nothing in this
module reads from, writes to, or references any real tenant, Shopify store,
Gmail inbox, or customer, and it deliberately imports nothing that could
(no database client, no Shopify/Gmail service). See sandbox_service.py for
the deterministic scenario engine that consumes it.

The sandbox has its own fixed "store clock" (SANDBOX_NOW) so eligibility
(e.g. the 30-day return window) is fully deterministic regardless of when a
demo is run.
"""
from datetime import datetime, timezone

STORE = {
    "name": "Northstar Apparel",
    "domain": "northstar-apparel.sample",
    "currency": "USD",
    "label": "Sample store",
}

SANDBOX_NOTICE = (
    "Sandbox mode: Northstar Apparel is a sample store with sample customers and orders. "
    "No real Shopify store, inbox, or customer is connected, and nothing here can change or email anyone real."
)

SANDBOX_NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)

# --- Policies (also the rules the deterministic eligibility checks read) -----

POLICIES = {
    "cancellation": {
        "id": "kb_cancellation",
        "title": "Order Cancellation Policy",
        "text": (
            "Orders can be cancelled any time before they are fulfilled (packed and handed to the carrier). "
            "Once an order has shipped it can no longer be cancelled; the customer can return it for a refund instead. "
            "Cancelled orders are refunded to the original payment method within 5 to 7 business days."
        ),
        "cancellable_fulfillment_statuses": ["unfulfilled"],
    },
    "returns": {
        "id": "kb_returns",
        "title": "Returns & Refunds Policy",
        "text": (
            "Items can be returned within 30 days of delivery if they are unworn, unwashed, and have the original tags attached. "
            "Final sale items cannot be returned. Approved refunds go back to the original payment method within 5 to 7 business days "
            "after we receive the item."
        ),
        "return_window_days": 30,
    },
    "shipping": {
        "id": "kb_shipping",
        "title": "Shipping Policy",
        "text": (
            "Orders are packed within 1 business day. Standard shipping takes 3 to 6 business days and is free on orders over $75."
        ),
    },
}

# --- Customers / orders ---------------------------------------------------------

CUSTOMERS = {
    "maya.chen@example.com": {"name": "Maya Chen", "email": "maya.chen@example.com"},
    "jordan.ellis@example.com": {"name": "Jordan Ellis", "email": "jordan.ellis@example.com"},
}

ORDERS = {
    "1048": {
        "order_number": "1048",
        "customer_email": "maya.chen@example.com",
        "created_at": "2026-09-18T09:12:00+00:00",
        "financial_status": "paid",
        "fulfillment_status": "unfulfilled",
        "delivered_at": None,
        "cancelled_at": None,
        "currency": "USD",
        "total": 84.00,
        "line_items": [
            {"title": "Relaxed Linen Shirt", "variant": "L", "quantity": 1, "price": 84.00, "final_sale": False},
        ],
    },
    "1051": {
        "order_number": "1051",
        "customer_email": "jordan.ellis@example.com",
        "created_at": "2026-09-02T13:40:00+00:00",
        "financial_status": "paid",
        "fulfillment_status": "fulfilled",
        "delivered_at": "2026-09-08T16:20:00+00:00",
        "cancelled_at": None,
        "currency": "USD",
        "total": 62.00,
        "line_items": [
            {"title": "Cotton Crew Sweatshirt", "variant": "M", "quantity": 1, "price": 62.00, "final_sale": False},
        ],
    },
}

# --- Catalog ----------------------------------------------------------------------

PRODUCTS = {
    "black maxi dress": {
        "title": "Black Maxi Dress",
        "price": 96.00,
        "currency": "USD",
        "variants": [
            {"size": "XS", "inventory": 0},
            {"size": "S", "inventory": 0},
            {"size": "M", "inventory": 6},
            {"size": "L", "inventory": 3},
            {"size": "XL", "inventory": 2},
        ],
    },
    "relaxed linen shirt": {
        "title": "Relaxed Linen Shirt",
        "price": 84.00,
        "currency": "USD",
        "variants": [
            {"size": "S", "inventory": 4},
            {"size": "M", "inventory": 7},
            {"size": "L", "inventory": 0},
        ],
    },
    "cotton crew sweatshirt": {
        "title": "Cotton Crew Sweatshirt",
        "price": 62.00,
        "currency": "USD",
        "variants": [
            {"size": "S", "inventory": 9},
            {"size": "M", "inventory": 12},
            {"size": "L", "inventory": 5},
        ],
    },
}

SIZE_WORDS = {
    "xs": "XS", "extra small": "XS",
    "s": "S", "small": "S",
    "m": "M", "medium": "M",
    "l": "L", "large": "L",
    "xl": "XL", "extra large": "XL",
}

# --- Knowledge base chunks (what "Luna found the relevant source" searches) ------

KB_SOURCES = [
    {"source_id": POLICIES["returns"]["id"], "title": POLICIES["returns"]["title"], "text": POLICIES["returns"]["text"]},
    {"source_id": POLICIES["cancellation"]["id"], "title": POLICIES["cancellation"]["title"], "text": POLICIES["cancellation"]["text"]},
    {"source_id": POLICIES["shipping"]["id"], "title": POLICIES["shipping"]["title"], "text": POLICIES["shipping"]["text"]},
]

# --- Canonical scenarios: the customer's opening message -------------------------

SCENARIOS = [
    {
        "id": "cancel",
        "title": "Cancel an order",
        "cta": "Cancel an order",
        "summary": "Read the order, check policy, stage a cancellation for your approval.",
        "default": True,
        "subject": "Cancel order #1048",
        "customer_email": "maya.chen@example.com",
        "message": "hey, can you cancel order #1048? i ordered the wrong size",
    },
    {
        "id": "refund",
        "title": "Request a refund",
        "cta": "Request a refund",
        "summary": "Check the order and refund policy, stage a refund for your approval.",
        "default": False,
        "subject": "Refund for order #1051",
        "customer_email": "jordan.ellis@example.com",
        "message": "Hi, I'd like a refund for order #1051. The sweatshirt doesn't fit the way I hoped.",
    },
    {
        "id": "returns",
        "title": "Ask about returns",
        "cta": "Ask about returns",
        "summary": "Find the right policy in the knowledge base and answer from it.",
        "default": False,
        "subject": "Return window question",
        "customer_email": "maya.chen@example.com",
        "message": "How long do I have to return an item?",
    },
    {
        "id": "product",
        "title": "Ask about a product",
        "cta": "Ask about a product",
        "summary": "Check the sample catalog and inventory for size availability.",
        "default": False,
        "subject": "Black Maxi Dress in Medium?",
        "customer_email": "maya.chen@example.com",
        "message": "Is the Black Maxi Dress available in Medium?",
    },
]
