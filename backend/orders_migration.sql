-- Migration: Add customer_email, customer_name to orders and create order_items table
-- Run this against Supabase database

-- 1. Add customer_email and customer_name columns to orders table
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name text;

-- 2. Create order_items table to store line items
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

-- 3. Add index for faster lookups
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_email ON orders(customer_email);
