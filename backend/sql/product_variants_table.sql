-- Product variants table for storing synced Shopify product variants/inventory
-- Run this SQL in your Supabase SQL Editor

CREATE TABLE IF NOT EXISTS product_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shopify_variant_id BIGINT UNIQUE NOT NULL,
    shopify_product_id BIGINT,
    title TEXT,
    variant_title TEXT,
    sku TEXT,
    price NUMERIC(10, 2) DEFAULT 0,
    compare_at_price NUMERIC(10, 2),
    inventory_quantity INTEGER DEFAULT 0,
    inventory_item_id BIGINT,
    option1 TEXT,
    option2 TEXT,
    option3 TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Index for variant lookups
CREATE INDEX IF NOT EXISTS idx_product_variants_sku ON product_variants(sku);
CREATE INDEX IF NOT EXISTS idx_product_variants_product_id ON product_variants(shopify_product_id);
CREATE INDEX IF NOT EXISTS idx_product_variants_inventory ON product_variants(inventory_quantity) WHERE inventory_quantity > 0;

-- Enable RLS
ALTER TABLE product_variants ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access" ON product_variants
    FOR ALL USING (true) WITH CHECK (true);
