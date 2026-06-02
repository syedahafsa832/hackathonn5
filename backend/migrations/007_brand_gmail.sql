-- Migration 007: Per-brand Gmail OAuth
-- Run this in the Supabase SQL Editor

ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS gmail_email      VARCHAR(255),
  ADD COLUMN IF NOT EXISTS gmail_token      TEXT,
  ADD COLUMN IF NOT EXISTS gmail_connected  BOOLEAN DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_brands_gmail_connected ON brands(gmail_connected) WHERE gmail_connected = true;
