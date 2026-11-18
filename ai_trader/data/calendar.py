"""Gold-relevant rows from Reaktor public.econ_events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ai_trader.data.market import as_utc

# High-impact prints in these currencies move gold. Medium is kept for USD only.
CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CNY", "CAD", "AUD", "CHF"}
LOOKBACK = timedelta(hours=12)
LOOKAHEAD = timedelta(days=7)
LIMIT = 20


def select_events(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """High-impact events for the week, plus medium-impact USD events."""
    now = as_utc(now)
    start = now - LOOKBACK
    end = now + LOOKAHEAD
    chosen: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        event = _compact(row)
        if event is None:
            continue
        when, impact, country = event[0], event[1], event[2]
        if when < start or when >= end:
            continue
        if impact == "High" and country in CURRENCIES:
            chosen.append((when, _public(event)))
        elif impact == "Medium" and country == "USD":
            chosen.append((when, _public(event)))
    chosen.sort(key=lambda item: item[0])
    return [item[1] for item in chosen[:LIMIT]]


def lookup_targets(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """High-impact rows a web search can fill: a blank forecast, or a release already due."""
    now = as_utc(now)
    targets: list[dict[str, Any]] = []
    for event in events:
        if event.get("impact") != "High":
            continue
        when = _parse_time(event.get("time"))
        if when is None:
            continue
        if when <= now or not event.get("forecast"):
            targets.append(event)
    return targets[:8]


def _compact(row: dict[str, Any]) -> tuple[datetime, str, str, str, str | None, str | None] | None:
    when = _parse_time(row.get("event_time") or row.get("time"))
    if when is None:
        return None
    impact = str(row.get("impact") or "").strip().lower()
    if impact == "high":
        label = "High"
    elif impact == "medium":
        label = "Medium"
    else:
        return None
    country = str(row.get("country") or "").strip().upper()
    title = str(row.get("title") or "").strip()
    if not country or not title:
        return None
    return when, label, country, title, _text(row.get("forecast")), _text(row.get("previous"))


def _public(event: tuple[datetime, str, str, str, str | None, str | None]) -> dict[str, Any]:
    when, impact, country, title, forecast, previous = event
    return {
        "time": when.isoformat(),
        "country": country,
        "impact": impact,
        "title": title,
        "forecast": forecast,
        "previous": previous,
    }


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return as_utc(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
