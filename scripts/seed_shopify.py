import os
import requests
import json
import logging
import random
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shopify Credentials
SHOPIFY_SHOP_NAME = os.getenv("SHOPIFY_SHOP_NAME")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-01")

if not SHOPIFY_SHOP_NAME or not SHOPIFY_ACCESS_TOKEN:
    logger.error("SHOPIFY_SHOP_NAME or SHOPIFY_ACCESS_TOKEN not set in environment.")
    # Exit or handle gracefully if this is a simulation mode
    
BASE_URL = f"https://{SHOPIFY_SHOP_NAME}.myshopify.com/admin/api/{SHOPIFY_API_VERSION}"
HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json"
}

def create_product(product_data):
    """Create a product in Shopify if it doesn't exist."""
    search_resp = requests.get(f"{BASE_URL}/products.json", headers=HEADERS, params={"title": product_data["title"]})
    if search_resp.status_code == 200 and search_resp.json().get("products"):
        logger.info(f"Product '{product_data['title']}' already exists. Skipping.")
        return search_resp.json()["products"][0]
    
    payload = {"product": product_data}
    resp = requests.post(f"{BASE_URL}/products.json", headers=HEADERS, json=payload)
    if resp.status_code == 201:
        logger.info(f"Successfully created product: {product_data['title']}")
        return resp.json()["product"]
    else:
        logger.error(f"Failed to create product {product_data['title']}: {resp.text}")
        return None

def create_customer(customer_data):
    """Create a customer in Shopify."""
    search_resp = requests.get(f"{BASE_URL}/customers/search.json", headers=HEADERS, params={"query": f"email:{customer_data['email']}"})
    if search_resp.status_code == 200 and search_resp.json().get("customers"):
        logger.info(f"Customer '{customer_data['email']}' already exists. Skipping.")
        return search_resp.json()["customers"][0]
    
    payload = {"customer": customer_data}
    resp = requests.post(f"{BASE_URL}/customers.json", headers=HEADERS, json=payload)
    if resp.status_code == 201:
        logger.info(f"Successfully created customer: {customer_data['email']}")
        return resp.json()["customer"]
    else:
        logger.error(f"Failed to create customer {customer_data['email']}: {resp.text}")
        return None

def create_order(order_data):
    """Create an order in Shopify."""
    # Orders are harder to check for existence by just a title, usually by order_number if provided
    # For seeding, we'll just try to create. 
    payload = {"order": order_data}
    resp = requests.post(f"{BASE_URL}/orders.json", headers=HEADERS, json=payload)
    if resp.status_code == 201:
        logger.info(f"Successfully created order for customer: {order_data.get('email')}")
        return resp.json()["order"]
    else:
        logger.error(f"Failed to create order: {resp.text}")
        return None

def adjust_inventory(variant_id, location_id, quantity):
    """Adjust inventory for a variant at a location."""
    # Find inventory_item_id first
    variant_resp = requests.get(f"{BASE_URL}/variants/{variant_id}.json", headers=HEADERS)
    if variant_resp.status_code != 200:
        return
    inventory_item_id = variant_resp.json()["variant"]["inventory_item_id"]
    
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available_adjustment": quantity
    }
    resp = requests.post(f"{BASE_URL}/inventory_levels/adjust.json", headers=HEADERS, json=payload)
    if resp.status_code == 200:
        logger.info(f"Adjusted inventory for variant {variant_id} by {quantity}")
    else:
        logger.error(f"Failed to adjust inventory: {resp.text}")

def seed():
    """Main seeding logic."""
    # 1. Get Locations
    loc_resp = requests.get(f"{BASE_URL}/locations.json", headers=HEADERS)
    if loc_resp.status_code != 200:
        logger.warning(f"Location fetch failed ({loc_resp.status_code}). Proceeding without specific inventory locations.")
        locations = []
    else:
        locations = loc_resp.json().get("locations", [])
        
    if not locations:
        logger.warning("No locations found (or access denied). Products will be created without starting inventory levels.")
        online_loc = None
        soho_loc = None
    else:
        online_loc = locations[0]["id"]
        soho_loc = locations[1]["id"] if len(locations) > 1 else online_loc
        logger.info(f"Found {len(locations)} location(s). Using primary: {online_loc}")


    
    # 2. Seed 30 Products
    fabrics = ["Organic Cotton", "Pima Cotton", "Merino Wool", "Recycle Polyester", "Silk Blend"]
    fits = ["slim", "relaxed", "cropped", "oversized"]
    
    for i in range(1, 31):
        product_title = f"{random.choice(['Essential', 'Premium', 'Signature'])} Hoodie V{i}"
        product_data = {
            "title": product_title,
            "body_html": f"A high-end hoodie made from {random.choice(fabrics)}. Fit: {random.choice(fits)}.",
            "vendor": "High-End Fashion Brand",
            "product_type": "Hoodie",
            "variants": [
                {"option1": "XS", "price": "120.00", "sku": f"HD-{i}-XS"},
                {"option1": "S", "price": "120.00", "sku": f"HD-{i}-S"},
                {"option1": "M", "price": "120.00", "sku": f"HD-{i}-M"},
                {"option1": "L", "price": "120.00", "sku": f"HD-{i}-L"},
                {"option1": "XL", "price": "120.00", "sku": f"HD-{i}-XL"}
            ],
            "options": [{"name": "Size"}]
        }
        
        # Add metadata/tags for our custom logic later
        product_data["tags"] = f"fabric:{random.choice(fabrics)},fit:{random.choice(fits)},stretch:{random.randint(0,3)},model_height:6'1\""
        
        product = create_product(product_data)
        if product and online_loc:
            for variant in product["variants"]:
                adjust_inventory(variant["id"], online_loc, random.randint(10, 50))
                if soho_loc and soho_loc != online_loc:
                    adjust_inventory(variant["id"], soho_loc, random.randint(5, 20))


    # 3. Seed 15 Customers
    customers = []
    for i in range(1, 16):
        c_data = {
            "first_name": f"Customer{i}",
            "last_name": "Test",
            "email": f"customer{i}@example.com",
            "phone": f"+120255501{i:02d}", # Fixed format
            "verified_email": True,
            "addresses": [{"address1": "123 Fashion St", "city": "New York", "province": "NY", "country": "US", "zip": "10001"}]
        }
        customer = create_customer(c_data)
        if customer:
            customers.append(customer)
    
    # 4. Seed 20 Orders
    import time
    
    # Fetch real variants to use in orders
    all_variants = []
    prod_resp = requests.get(f"{BASE_URL}/products.json", headers=HEADERS, params={"limit": 50})
    if prod_resp.status_code == 200:
        for p in prod_resp.json().get("products", []):
            for v in p.get("variants", []):
                all_variants.append(v["id"])
                
    if not all_variants:
        logger.error("No real variants found for order seeding.")
        return

    for i in range(1, 21):
        if not customers: break
        customer = random.choice(customers)
        v_id = random.choice(all_variants)
        order_data = {
            "email": customer["email"],
            "fulfillment_status": random.choice(["fulfilled", "null", "null", "fulfilled"]),
            "line_items": [{"variant_id": v_id, "quantity": 1}],
            "customer": {"id": customer["id"]},
            "financial_status": random.choice(["paid", "refunded", "paid", "paid"])
        }
        create_order(order_data)
        time.sleep(2)


if __name__ == "__main__":
    seed()
