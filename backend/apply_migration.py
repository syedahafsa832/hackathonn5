#!/usr/bin/env python3
"""
Apply migration to add customer_email, customer_name to orders and create order_items table.
Run: python apply_migration.py
"""
import requests
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# SQL migration commands
sql_commands = """
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name text;
"""

# Use PostgREST to execute SQL via rpc
# Alternative: Use the console or manually run these

# For now, let's try to update via the console if available
# Actually, we need to use the Supabase SQL editor or psql

# Let's try creating the table via REST API (may not work for ALTER TABLE)
# Instead, print instructions

print("=" * 60)
print("MIGRATION REQUIRED")
print("=" * 60)
print("""
Please run the following SQL in your Supabase SQL Editor:

-- 1. Add customer_email and customer_name to orders table
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name text;

-- 2. Create order_items table
CREATE TABLE IF NOT EXISTS order_items (
    id uuid primary key default uuid_generate_v4(),
    order_id uuid references orders(id) on delete cascade,
    shopify_line_item_id bigint,
    title text not null,
    quantity integer default 1,
    price decimal(10,2),
    sku text,
    created_at timestamp with time zone default now()
);

-- 3. Add indexes
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_email ON orders(customer_email);

""")
print("=" * 60)
