"""One-shot Twelve Data backfill of 15-minute XAU/USD into public.candles.

Existing bars are left alone. Reaktor's live rows win on the same open time.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from ai_trader.config import Settings

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"


def windows(end: date, days: int, window_days: int = 21) -> list[tuple[date, date]]:
    """Half-open [start, end) chunks of at most `window_days` covering `days` back from `end`."""
    if days <= 0:
        return []
    window_days = max(1, window_days)
    start = end - timedelta(days=days)
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=window_days), end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


def bars_from_payload(payload: dict[str, Any], *, source: str = "twelvedata_backfill") -> list[dict[str, Any]]:
    if payload.get("status") == "error":
        raise RuntimeError(str(payload.get("message") or "twelvedata error"))
    values = payload.get("values")
    if not isinstance(values, list):
        raise RuntimeError("twelvedata response missing values")
    rows: list[dict[str, Any]] = []
    for item in values:
        opened = datetime.fromisoformat(str(item["datetime"]).replace(" ", "T"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        closed = opened + timedelta(minutes=15)
        rows.append(
            {
                "symbol": "XAU/USD",
                "timeframe_min": 15,
                "open_time": opened.isoformat(),
                "close_time": closed.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "source": source,
            }
        )
    return rows


def fetch_window(api_key: str, start: date, end: date, http: httpx.Client | None = None) -> list[dict[str, Any]]:
    client = http or httpx.Client(timeout=60)
    response = client.get(
        TIME_SERIES_URL,
        params={
            "symbol": "XAU/USD",
            "interval": "15min",
            "timezone": "UTC",
            "outputsize": 5000,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apikey": api_key,
        },
    )
    response.raise_for_status()
    return bars_from_payload(response.json())


def run_backfill(settings: Settings, *, sleep: Callable[[float], None] | None = None, http: httpx.Client | None = None) -> int:
    from ai_trader.db.repo import SupabaseRepo

    if not settings.twelvedata_api_key:
        raise SystemExit("TWELVEDATA_API_KEY is required for backfill")
    settings.require_runtime()
    repo = SupabaseRepo(settings)
    end = datetime.now(timezone.utc).date()
    chunks = windows(end, settings.backfill_days, settings.backfill_window_days)
    pause = sleep or time.sleep
    written = 0
    for index, (start, stop) in enumerate(chunks):
        if index:
            pause(settings.backfill_sleep_sec)
        rows = fetch_window(settings.twelvedata_api_key, start, stop, http)
        for offset in range(0, len(rows), 500):
            written += repo.upsert_candles(rows[offset : offset + 500])
        print(f"backfill {start.isoformat()} .. {stop.isoformat()} bars {len(rows)}", flush=True)
    print(f"backfill done rows {written}", flush=True)
    return written


def main() -> None:
    from dotenv import load_dotenv

    from ai_trader.config import Settings

    load_dotenv()
    run_backfill(Settings.from_env())


if __name__ == "__main__":
    main()
