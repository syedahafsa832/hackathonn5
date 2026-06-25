-- Per-brand configurable AI agent name (default "Luna")
ALTER TABLE brands ADD COLUMN IF NOT EXISTS agent_name TEXT DEFAULT 'Luna';
