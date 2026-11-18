# AI Gold

Python orchestrator for XAUUSD. It runs on this Mac, asks Grok for a decision, applies risk rules in code, and sends anything that passes to [Factor](https://factor.igrik.xyz). Factor is the only process that talks to cTrader.

Trades are labeled `AI-GOLD-v1`. Reaktor orders stay on the Factor label and are ignored here.

`FACTOR_MODE` defaults to `http` and `TRADING_ENABLED` defaults to `true`. A cycle will send a real order request as soon as the account gate is on. Connect that cTrader account as **demo** before you enable the pref.

## What a cycle does

1. Read the account kill switch and the `ai_gold` pref. If either blocks, the cycle logs the reason and does not call the model.
2. Load up to a year of 15-minute `XAU/USD` candles, aggregate them, and calculate support and resistance.
3. Refresh weekly COT gold futures when the stored report is older than 10 days.
4. Search X through Grok, limited to `X_HANDLES`, and store the summary in `ai_x_sentiment`. This does not read Reaktor's `x_posts` mailbox.
5. Load the next week of gold-relevant rows from Reaktor `econ_events` (high-impact USD, EUR, GBP, JPY, CNY, CAD, AUD, CHF, plus medium-impact USD). When a high-impact forecast is blank, or the release is already due, one web search fills that gap. A news day does not block a trade.
6. Load open positions for this label, plus that account's cTrader balance and equity.
7. Ask Grok for one JSON decision.
8. Reject the trade in code when risk rules fail. A bad new trade does not cancel a valid close.
9. Send the order or the management request to Factor.
10. Log the whole cycle and send a Telegram note.

New entries are allowed whenever gold is open, including the 03:00 UTC Asia run. The daily loss cap is the budget. When the market is closed, market orders are rejected. From Friday 21:00 UTC until Sunday 22:00 UTC, pending orders are rejected too. The weekday 21:00–22:00 break still allows a pending order.

`/order` must be built to reach its target within 1–2 hours. Code rejects a target further than `max(6, 2 × recent H1 range)` dollars from entry. It still has to pass reward-to-risk, the position cap, and the daily loss cap.

Management in this version is leave, close, and move stop to breakeven. Trail and partial close are parsed and then rejected.

## Setup

```bash
cd /Users/irusu/ai_trader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env`. Then, in the Factor Supabase SQL editor:

1. Run `sql/001_ai_tables.sql`.
2. Edit the user id and the cTrader account id in `sql/002_ai_gold_pref.sql` and run it. That row is the account gate. Leave it disabled until the account is a demo account, then set `enabled` to true.

`FACTOR_USER_ID` is the `factor_users.id` uuid. `FACTOR_ACCOUNT_ID` is the cTrader account id on `factor_ctrader_connections`.

Factor still needs these routes. This repo only calls them:

- `POST /api/ai/orders`
- `POST /api/positions/manage`
- `GET /api/positions?label=AI-GOLD-v1&account_id=`

The order body matches the preparation notes, plus `account_id`. Factor should refuse the order unless that account has `ai_gold` enabled for `XAU/USD`, and it should refuse to modify a position whose label is not `AI-GOLD-v1`.

## One year of candles

Reaktor's live 15-minute history starts around 13 July 2026. Backfill once:

```bash
python -m ai_trader.backfill
```

That pulls `BACKFILL_DAYS` (default 370) of 15-minute `XAU/USD` from Twelve Data in 21-day windows and inserts them into Reaktor's `public.candles` with `source=twelvedata_backfill`. A bar Reaktor already stored is left as it is. The script is not on the scheduler. It needs `TWELVEDATA_API_KEY` and `REAKTOR_SUPABASE_URL` / `REAKTOR_SUPABASE_KEY`. Analysis reads those same candles. Logs and the account gate stay on Factor's Supabase.

The model never sees the raw bars. It sees the trend of each timeframe, the higher-timeframe path, where price sits between the nearest support and resistance, and up to 12 zones.

## Run

```bash
python -m ai_trader --once
python -m ai_trader
```

`--once` runs a single cycle and exits. The long-running process is the scheduler and the Telegram bot.

Set `FACTOR_MODE=stub` to accept orders only in the log. Set `TRADING_ENABLED=false` to keep the analysis and skip every order and modification.

## Schedule (UTC)

Weekdays: 03:00 (Asia), 07:00, 08:30, 10:00, 12:00, 13:00, 13:45, 14:30, 15:15, 16:00.

Saturday prep: 10:00, 16:00, 21:00. Weekly review at 09:00, separate from those three.

Sunday prep: 10:00, 16:00, 22:15 (just after the gold open).

Daily summary: 21:10 Monday–Friday.

Price alerts are checked every 60 seconds while gold is open. A cross starts one cycle, then waits 10 minutes before another alert cycle.

## Telegram

Pushes: each cycle, orders and modifications, the daily summary, the weekly review.

Commands, only from `TELEGRAM_CHAT_ID`:

- `/status` — trading flag, Factor mode, pref, kill switch, oldest candle, last run, open positions, next slot. Places nothing.
- `/order` — runs a cycle that must open a trade from the current analysis, sized for 1–2 hours. Risk rules still apply. A second command while a cycle is running gets `busy`.
- `/ai your question` — answers from the current book and the last 10 questions and answers in this chat. Places nothing. Same chat as `/order`.

## Environment

| Variable | Role |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Factor project. Logs, prefs, kill switch. Required. |
| `REAKTOR_SUPABASE_URL`, `REAKTOR_SUPABASE_KEY` | Reaktor project. `public.candles` only. Copy Reaktor's `SUPABASE_URL` and `SUPABASE_KEY`. |
| `XAI_API_KEY`, `XAI_MODEL` | Decision and X search. Model defaults to `grok-4.6`. |
| `X_HANDLES` | Up to 20 handles. Empty skips the search. |
| `TWELVEDATA_API_KEY` | Backfill only. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Pushes and commands. |
| `FACTOR_BASE_URL`, `FACTOR_API_TOKEN` | Execution API. |
| `FACTOR_USER_ID`, `FACTOR_ACCOUNT_ID`, `TELEGRAM_CHAT_ID` | Unused. Turn on the AI trader alert in Factor instead. |
| `FACTOR_MODE` | `http` (default) or `stub`. |
| `TRADING_ENABLED` | Default `true`. |
| `MAX_VOLUME_LOTS`, `MAX_OPEN_POSITIONS`, `MIN_RR`, `MAX_DAILY_LOSS_USD` | Unused. Set min/max lots, max concurrent, min/max RR, and the daily loss cap on the AI trader alert in Factor. |
| `MIN_CONFIDENCE` | Default `60`, still in code. Not applied to `/order`. |
| `AI_LABEL` | Default `AI-GOLD-v1`. |

## Tests

```bash
pytest -q
```

Tests do not call Supabase, Factor, Grok, or Twelve Data.
