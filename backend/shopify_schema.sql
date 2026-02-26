-- Migration to add Shopify Shops table
create table if not exists shops (
    id uuid primary key default uuid_generate_v4(),
    shop_domain text unique not null,
    access_token text not null,
    scopes text[],
    installed_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

-- Index for domain lookups
create index if not exists idx_shops_domain on shops(shop_domain);
