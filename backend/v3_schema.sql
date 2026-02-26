-- V3 Schema Updates for AI Customer Support Employee

-- 1. Enable Vector Extension (for RAG)
create extension if not exists vector;

-- 2. Products Table
create table if not exists products (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid default '00000000-0000-0000-0000-000000000000',
    shopify_id bigint unique,
    title text not null,
    description text,
    fabric text,
    fit_type text, -- slim, relaxed, cropped
    stretch_level integer check (stretch_level >= 0 and stretch_level <= 3),
    model_height text,
    size_chart jsonb,
    embedding vector(1536), -- For Mistral/OpenAI embeddings
    last_synced timestamp with time zone default now(),
    created_at timestamp with time zone default now()
);

-- 3. Variants Table
create table if not exists variants (
    id uuid primary key default uuid_generate_v4(),
    product_id uuid references products(id) on delete cascade,
    shopify_variant_id bigint unique,
    sku text unique,
    size text,
    price decimal(10,2),
    created_at timestamp with time zone default now()
);

-- 4. Inventory Table
create table if not exists inventory (
    id uuid primary key default uuid_generate_v4(),
    variant_id uuid references variants(id) on delete cascade,
    location_name text, -- e.g., 'Online', 'Soho'
    quantity integer default 0,
    updated_at timestamp with time zone default now(),
    unique(variant_id, location_name)
);

-- 5. Orders Table
create table if not exists orders (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid default '00000000-0000-0000-0000-000000000000',
    shopify_order_id bigint unique,
    customer_id uuid references customers(id),
    order_number text unique,
    status text, -- unfulfilled, partially_fulfilled, fulfilled, refunded
    total_amount decimal(10,2),
    tracking_number text,
    shipping_status text, -- in_transit, delayed, exception, delivered
    last_updated timestamp with time zone default now(),
    created_at timestamp with time zone default now()
);

-- 6. Webhook Events Table (Idempotency)
create table if not exists webhook_events (
    id uuid primary key default uuid_generate_v4(),
    event_id text unique, -- shopify callback id or aftership id
    source text, -- 'shopify', 'aftership'
    payload jsonb,
    processed_at timestamp with time zone default now()
);

-- 7. Vector Similarity Search Function
create or replace function match_products (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  title text,
  description text,
  fabric text,
  fit_type text,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    products.id,
    products.title,
    products.description,
    products.fabric,
    products.fit_type,
    1 - (products.embedding <=> query_embedding) as similarity
  from products
  where 1 - (products.embedding <=> query_embedding) > match_threshold
  order by similarity desc
  limit match_count;
end;
$$;

-- 8. Add Index for Vector Search (HNSW preferred for performance)
create index if not exists idx_products_embedding_hnsw on products using hnsw (embedding vector_cosine_ops);


-- Indexes for performance
create index if not exists idx_products_shopify_id on products(shopify_id);
create index if not exists idx_variants_sku on variants(sku);
create index if not exists idx_orders_customer_id on orders(customer_id);
create index if not exists idx_orders_tracking on orders(tracking_number);
