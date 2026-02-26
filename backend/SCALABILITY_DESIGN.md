# Scalability Design: V3 AI Customer Support Agent

This document outlines the architecture required to scale the system for 10x traffic spikes and manage up to 100K SKUs.

## 1. Connection Pooling & Database

- **Issue**: Supabase REST API handles pooling well, but direct Postgres connections (if used) might hit limits during spikes.
- **Solution**: Use **Supabase Vector Search (HNSW)** with a dedicated instance to handle 100K embedding lookups under 20ms. Implement **PgBouncer** for direct DB access if moving away from REST.

## 2. Async Workers & Queuing

- **Issue**: Webhook bursts from Shopify (e.g., during Flash Sales) can overwhelm FastAPI background tasks.
- **Solution**:
  - Migrate from `BackgroundTasks` to **Celery + Redis** on Railway.
  - Separate `ingestion-worker` for sync and `message-worker` for AI processing.
  - Implement **Dead-Letter Queues (DLQ)** in Redis for failed webhook events.

## 3. Rate Limiting & Flow Control

- **Issue**: External APIs (Shopify, AfterShip, Mistral) have strict rate limits.
- **Solution**:
  - **Shopify**: Use a distributed leaky-bucket algorithm to stay under 2 req/s.
  - **Mistral/OpenAI**: Implement **batch embedding** for product sync to reduce API calls by 50x.
  - **Internal**: Use `FastAPI-Limiter` to protect public webhook endpoints.

## 4. Embedding & Search Optimization

- **Issue**: Vector search over 100K items becomes slow without proper indexing.
- **Solution**:
  - Use **HNSW (Hierarchical Navigable Small World)** indexes instead of IVFFlat for faster approximate nearest neighbor search.
  - Implement **Semantic Caching**: Store common sizing inquiries (e.g., "Medium fit for 5'10") in Redis to prevent redundant LLM/Vector calls.

## 5. Railway Pro-Hardening

- **Scaling**: Enable Railway **Autoscaling** based on CPU/Memory usage.
- **Observability**: Integrate **Sentry** for error tracking and **Prometheus/Grafana** for metrics on webhook latency and AI confidence drift.
