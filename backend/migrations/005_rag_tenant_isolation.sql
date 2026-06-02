-- ============================================
-- RAG TENANT ISOLATION - Multi-tenant Knowledge Base
-- Run this in Supabase SQL Editor after 004_saas_clean_setup.sql
-- ============================================

-- Enable pgvector extension if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;

-- Drop existing table to recreate with tenant_id
DROP TABLE IF EXISTS rag_chunks CASCADE;

-- ============================================
-- 1. RAG_CHUNKS TABLE (with tenant isolation)
-- ============================================
CREATE TABLE rag_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding vector(1024),
    metadata JSONB DEFAULT '{}'::jsonb,
    source_name VARCHAR(255),
    chunk_index INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast vector similarity search (COSINE)
CREATE INDEX idx_rag_chunks_embedding_hnsw
ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- Index for tenant filtering
CREATE INDEX idx_rag_chunks_tenant ON rag_chunks(tenant_id);

-- Index for metadata filtering (GIN)
CREATE INDEX idx_rag_chunks_metadata ON rag_chunks USING gin (metadata);

-- ============================================
-- 2. KNOWLEDGE_BASE_SOURCES TABLE (track uploads)
-- ============================================
CREATE TABLE knowledge_base_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'text', -- text, url, file
    status VARCHAR(50) NOT NULL DEFAULT 'processing', -- processing, completed, failed
    chunk_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_kb_sources_tenant ON knowledge_base_sources(tenant_id);

-- Trigger for updated_at
CREATE TRIGGER update_kb_sources_updated_at
    BEFORE UPDATE ON knowledge_base_sources
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 3. RPC FUNCTION FOR TENANT-ISOLATED SEARCH
-- ============================================
CREATE OR REPLACE FUNCTION match_rag_chunks (
    query_embedding vector(1024),
    match_threshold float,
    match_count int,
    filter_metadata jsonb default '{}'::jsonb
) RETURNS TABLE (
    id uuid,
    content text,
    metadata jsonb,
    similarity float
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        rag_chunks.id,
        rag_chunks.content,
        rag_chunks.metadata,
        1 - (rag_chunks.embedding <=> query_embedding) AS similarity
    FROM rag_chunks
    WHERE 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
        AND rag_chunks.metadata @> filter_metadata
    ORDER BY rag_chunks.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ============================================
-- 4. TENANT-SPECIFIC SEARCH FUNCTION
-- ============================================
CREATE OR REPLACE FUNCTION match_tenant_rag_chunks (
    p_tenant_id uuid,
    query_embedding vector(1024),
    match_threshold float,
    match_count int
) RETURNS TABLE (
    id uuid,
    content text,
    metadata jsonb,
    source_name varchar,
    similarity float
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        rag_chunks.id,
        rag_chunks.content,
        rag_chunks.metadata,
        rag_chunks.source_name,
        1 - (rag_chunks.embedding <=> query_embedding) AS similarity
    FROM rag_chunks
    WHERE rag_chunks.tenant_id = p_tenant_id
        AND 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
    ORDER BY rag_chunks.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ============================================
-- DONE! RAG tables with tenant isolation created.
-- ============================================
