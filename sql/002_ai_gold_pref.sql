-- Account gate for AI Gold orders.
-- Edit the user id and the cTrader account id, then run once in the Supabase SQL editor.
-- Volume and SL/TP point columns are required by Factor and are not used.
-- The orchestrator sends absolute volume, stop, and target.
-- Leave this row disabled until the cTrader account behind it is a demo account.

insert into factor_user_alert_prefs (
  user_id,
  account_id,
  kind,
  symbol,
  enabled,
  max_concurrent,
  max_per_day,
  volume_lots,
  sl_points,
  tp_points
) values (
  '6f626a0c-b4d7-4a99-8d3b-40f3de4a291a',
  48137878,
  'ai_gold',
  'XAU/USD',
  true,
  1,
  10,
  0.01,
  1000,
  1000
);
