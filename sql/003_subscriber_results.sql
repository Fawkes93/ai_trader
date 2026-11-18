-- Per-account results on a cycle, and which cTrader account owns a position.
-- Run in the Factor Supabase SQL editor if 001 was applied earlier.

alter table ai_analysis_runs
  add column if not exists subscriber_results jsonb;

alter table ai_positions
  add column if not exists account_id bigint;
