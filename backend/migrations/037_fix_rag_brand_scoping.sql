-- Migration 006 defined knowledge_base_sources.brand_id / rag_chunks.brand_id
-- and a match_brand_rag_chunks RPC, but the live tables were never actually
-- migrated off the older (005) tenant_id-only shape -- confirmed by direct
-- schema inspection, not by trusting the migration file. This left the
-- existing manual-paste Knowledge Base upload feature (v2_knowledge.py ->
-- brand_knowledge_service.upload_text) silently broken in production
-- (inserts referencing a brand_id column that didn't exist), and blocked
-- any brand-scoped RAG retrieval.
ALTER TABLE knowledge_base_sources ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id) ON DELETE CASCADE;
ALTER TABLE knowledge_base_sources ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;
ALTER TABLE knowledge_base_sources ADD COLUMN IF NOT EXISTS created_by UUID;
ALTER TABLE knowledge_base_sources ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0;

ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brands(id) ON DELETE CASCADE;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS source_id UUID REFERENCES knowledge_base_sources(id) ON DELETE CASCADE;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS token_count INTEGER DEFAULT 0;

-- Backfill brand_id from tenant_id -> brands. This app is effectively
-- single-brand-per-tenant in current usage, so the first brand per tenant
-- is a safe, non-destructive best-effort backfill for any pre-existing rows.
UPDATE knowledge_base_sources k
SET brand_id = b.id
FROM (SELECT DISTINCT ON (tenant_id) tenant_id, id FROM brands WHERE tenant_id IS NOT NULL ORDER BY tenant_id, created_at ASC) b
WHERE k.brand_id IS NULL AND k.tenant_id = b.tenant_id;

UPDATE rag_chunks r
SET brand_id = b.id
FROM (SELECT DISTINCT ON (tenant_id) tenant_id, id FROM brands WHERE tenant_id IS NOT NULL ORDER BY tenant_id, created_at ASC) b
WHERE r.brand_id IS NULL AND r.tenant_id = b.tenant_id;

CREATE INDEX IF NOT EXISTS idx_knowledge_base_sources_brand_id ON knowledge_base_sources(brand_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_brand_id ON rag_chunks(brand_id);

CREATE OR REPLACE FUNCTION match_brand_rag_chunks(
    p_brand_id uuid,
    query_embedding vector,
    match_threshold double precision,
    match_count integer
)
RETURNS TABLE(id uuid, content text, metadata jsonb, source_name character varying, similarity double precision)
LANGUAGE plpgsql
SET search_path TO 'public'
AS $$
BEGIN
    RETURN QUERY
    SELECT
        rag_chunks.id,
        rag_chunks.content,
        rag_chunks.metadata,
        rag_chunks.source_name,
        1 - (rag_chunks.embedding <=> query_embedding) AS similarity
    FROM rag_chunks
    WHERE rag_chunks.brand_id = p_brand_id
        AND 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
    ORDER BY rag_chunks.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
