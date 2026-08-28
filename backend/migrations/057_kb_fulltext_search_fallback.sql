-- Postgres full-text search fallback for merchant Knowledge Base retrieval,
-- used only when the embedding provider (Mistral) is unavailable,
-- rate-limited, timed out, or has exhausted its quota (see
-- brand_knowledge_service.get_brand_context_with_status / _fts_fallback).
--
-- Semantic (pgvector) search via match_brand_rag_chunks stays the primary
-- retrieval path and is untouched by this migration - this only adds a
-- second way to search the SAME rag_chunks rows when a query embedding
-- can't be generated at all, so embeddings become optional rather than a
-- hard dependency for ordinary KB/policy questions. No new table, no new
-- knowledge base, no change to existing vector data.

CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_fts
ON rag_chunks USING gin (to_tsvector('english', content));

CREATE OR REPLACE FUNCTION match_brand_rag_chunks_fts(
    p_brand_id uuid,
    query_text text,
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
        ts_rank(to_tsvector('english', rag_chunks.content), websearch_to_tsquery('english', query_text)) AS similarity
    FROM rag_chunks
    JOIN knowledge_base_sources ON knowledge_base_sources.id = rag_chunks.source_id
    WHERE rag_chunks.brand_id = p_brand_id
        AND knowledge_base_sources.status = 'completed'
        AND to_tsvector('english', rag_chunks.content) @@ websearch_to_tsquery('english', query_text)
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$;
