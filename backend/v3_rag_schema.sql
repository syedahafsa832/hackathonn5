-- Migration to create RAG Chunks table with pgvector
-- Target dimension: 1024 (Mistral Embed)

create table if not exists rag_chunks (
    id uuid primary key default uuid_generate_v4(),
    content text not null,
    embedding vector(1024),
    metadata jsonb default '{}'::jsonb,
    created_at timestamp with time zone default now()
);

-- Index for fast vector similarity search (COSINE)
create index if not exists idx_rag_chunks_embedding_hnsw 
on rag_chunks using hnsw (embedding vector_cosine_ops);

-- Index for metadata filtering (GIN)
create index if not exists idx_rag_chunks_metadata on rag_chunks using gin (metadata);
