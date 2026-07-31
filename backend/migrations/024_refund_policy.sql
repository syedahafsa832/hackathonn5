-- Merchant-configurable refund policy fields.
-- return_policy_days already exists on brands (migration 002) but is not
-- yet read by any eligibility logic — that gap is closed in a later phase,
-- not this migration. This migration only adds storage.

ALTER TABLE brands ADD COLUMN IF NOT EXISTS exclude_digital_products BOOLEAN DEFAULT false;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS refund_notes TEXT;
ALTER TABLE brands ADD COLUMN IF NOT EXISTS final_sale_tags TEXT[] DEFAULT ARRAY['final sale', 'non-returnable', 'no returns', 'all sales final'];

CREATE TABLE IF NOT EXISTS refund_policy_excluded_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    shopify_product_id BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, shopify_product_id)
);
CREATE INDEX IF NOT EXISTS idx_refund_policy_excluded_products_brand ON refund_policy_excluded_products(brand_id);

CREATE TABLE IF NOT EXISTS refund_policy_excluded_collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    shopify_collection_id BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, shopify_collection_id)
);
CREATE INDEX IF NOT EXISTS idx_refund_policy_excluded_collections_brand ON refund_policy_excluded_collections(brand_id);

-- Atomic full-replace for the two exclusion lists, called via supabase_rpc()
-- from the API layer instead of a separate delete-then-insert-loop, so a
-- failure partway through an insert can never leave the list half-written:
-- a Postgres function body runs in a single transaction and rolls back
-- entirely on error. Returns boolean (not void) specifically so the caller
-- can distinguish success from failure: the shared supabase_rpc() helper
-- returns None on both an exception AND a successful void call, so a void
-- function would be indistinguishable from a failed one from the Python side.
CREATE OR REPLACE FUNCTION replace_refund_policy_excluded_products(p_brand_id UUID, p_product_ids BIGINT[])
RETURNS boolean AS $$
BEGIN
    DELETE FROM refund_policy_excluded_products WHERE brand_id = p_brand_id;
    IF array_length(p_product_ids, 1) > 0 THEN
        INSERT INTO refund_policy_excluded_products (brand_id, shopify_product_id)
        SELECT p_brand_id, unnest(p_product_ids);
    END IF;
    RETURN true;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION replace_refund_policy_excluded_collections(p_brand_id UUID, p_collection_ids BIGINT[])
RETURNS boolean AS $$
BEGIN
    DELETE FROM refund_policy_excluded_collections WHERE brand_id = p_brand_id;
    IF array_length(p_collection_ids, 1) > 0 THEN
        INSERT INTO refund_policy_excluded_collections (brand_id, shopify_collection_id)
        SELECT p_brand_id, unnest(p_collection_ids);
    END IF;
    RETURN true;
END;
$$ LANGUAGE plpgsql;
