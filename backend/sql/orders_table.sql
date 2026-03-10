-- Orders table for storing synced Shopify orders
-- Run this SQL in your Supabase SQL Editor

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shopify_order_id BIGINT UNIQUE NOT NULL,
    order_number INTEGER NOT NULL,
    order_name TEXT,
    email TEXT,
    customer_name TEXT,
    total_price NUMERIC(10, 2) DEFAULT 0,
    subtotal_price NUMERIC(10, 2) DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'USD',
    financial_status VARCHAR(50),
    fulfillment_status VARCHAR(50),
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    line_items JSONB DEFAULT '[]',
    tags TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}'
);

-- Index for order lookups
CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);
CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at DESC);

-- Enable RLS
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access" ON orders
    FOR ALL USING (true) WITH CHECK (true);
