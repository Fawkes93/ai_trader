-- AI Gold orchestrator tables. Run once in the Factor Supabase SQL editor.
-- Service role bypasses RLS. No anon policies: the anon key cannot read these rows.

create table if not exists ai_analysis_runs (
  id uuid primary key,
  created_at timestamptz not null default now(),
  trigger text not null,
  session text,
  price numeric,
  sr_levels jsonb,
  cot_snapshot jsonb,
  x_sentiment jsonb,
  open_positions jsonb,
  ai_raw_output jsonb,
  final_decision text,
  rejection_reason text,
  order_payload jsonb,
  factor_result jsonb,
  manage_payload jsonb,
  manage_result jsonb,
  subscriber_results jsonb,
  notes text
);

create index if not exists ai_analysis_runs_created_idx
  on ai_analysis_runs (created_at desc);

create table if not exists ai_positions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  label text not null,
  account_id bigint,
  factor_order_id text,
  broker_position_id text,
  side text not null,
  volume_lots numeric not null,
  entry_price numeric,
  sl_price numeric,
  tp_price numeric,
  status text not null,
  opened_by_run_id uuid,
  opened_at timestamptz not null default now(),
  closed_at timestamptz,
  realized_pnl numeric,
  detail jsonb not null default '{}'::jsonb
);

create index if not exists ai_positions_status_idx
  on ai_positions (status, opened_at desc);

create table if not exists ai_price_alerts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  price numeric not null,
  type text not null,
  status text not null default 'active',
  created_by_run_id uuid,
  hit_at timestamptz
);

create index if not exists ai_price_alerts_status_idx
  on ai_price_alerts (status, price);

create table if not exists ai_cot_data (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  report_date date not null unique,
  commercial_net integer,
  managed_money_net integer,
  open_interest integer,
  raw jsonb not null default '{}'::jsonb
);

create table if not exists ai_x_sentiment (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  bias text not null,
  summary text,
  confidence numeric,
  window_start timestamptz,
  window_end timestamptz,
  handles text[] not null default '{}',
  posts jsonb not null default '[]'::jsonb,
  raw jsonb not null default '{}'::jsonb
);

create index if not exists ai_x_sentiment_created_idx
  on ai_x_sentiment (created_at desc);

create table if not exists ai_weekly_reviews (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  period_start timestamptz not null,
  period_end timestamptz not null,
  summary text,
  suggestions jsonb not null default '[]'::jsonb,
  sources jsonb not null default '{}'::jsonb
);

alter table ai_analysis_runs enable row level security;
alter table ai_positions enable row level security;
alter table ai_price_alerts enable row level security;
alter table ai_cot_data enable row level security;
alter table ai_x_sentiment enable row level security;
alter table ai_weekly_reviews enable row level security;
