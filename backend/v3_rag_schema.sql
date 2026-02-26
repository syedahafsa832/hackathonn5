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

-- RPC Function for similarity search
create or replace function match_rag_chunks (
  query_embedding vector(1024),
  match_threshold float,
  match_count int,
  filter_metadata jsonb default '{}'::jsonb
) returns table (
  id uuid,
  content text,
  metadata jsonb,
  similarity float
) language plpgsql as $$
begin
  return query
  select
    rag_chunks.id,
    rag_chunks.content,
    rag_chunks.metadata,
    1 - (rag_chunks.embedding <=> query_embedding) as similarity
  from rag_chunks
  where 1 - (rag_chunks.embedding <=> query_embedding) > match_threshold
    and rag_chunks.metadata @> filter_metadata
  order by rag_chunks.embedding <=> query_embedding
  limit match_count;
end;
$$;
