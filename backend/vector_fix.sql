-- Migration to fix vector dimension mismatch
-- Mistral mistral-embed uses 1024 dimensions, but the schema was set to 1536.

-- 1. Drop the index first (if it exists)
drop index if exists idx_products_embedding_hnsw;

-- 2. Alter the column type
alter table products 
alter column embedding type vector(1024);

-- 3. Re-create the index
create index if not exists idx_products_embedding_hnsw on products using hnsw (embedding vector_cosine_ops);

-- 4. Update the sync status of all products to force a re-sync (optional)
-- update products set embedding = null;
