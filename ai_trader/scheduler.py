"""UTC schedule. Weekdays include an Asia run. Weekends prep for Monday."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ai_trader.data.market import gold_market_open

# (hour, minute) analysis slots.
WEEKDAY = (
    (3, 0),
    (7, 0),
    (8, 30),
    (10, 0),
    (12, 0),
    (13, 0),
    (13, 45),
    (14, 30),
    (15, 15),
    (16, 0),
)
SATURDAY = ((10, 0), (16, 0), (21, 0))
SUNDAY = ((10, 0), (16, 0), (22, 15))
DAILY_SUMMARY = (21, 10)
WEEKLY_REVIEW = (9, 0)


def analysis_slots() -> list[tuple[str, int, int, str]]:
    """(id, hour, minute, day_of_week) for APScheduler cron."""
    slots: list[tuple[str, int, int, str]] = []
    for hour, minute in WEEKDAY:
        slots.append((f"wd-{hour:02d}{minute:02d}", hour, minute, "mon-fri"))
    for hour, minute in SATURDAY:
        slots.append((f"sat-{hour:02d}{minute:02d}", hour, minute, "sat"))
    for hour, minute in SUNDAY:
        slots.append((f"sun-{hour:02d}{minute:02d}", hour, minute, "sun"))
    return slots


def next_analysis(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    candidates: list[datetime] = []
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        weekday = day.weekday()
        if weekday <= 4:
            times = WEEKDAY
        elif weekday == 5:
            times = SATURDAY
        else:
            times = SUNDAY
        for hour, minute in times:
            stamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
            if stamp > now:
                candidates.append(stamp)
        if candidates:
            return min(candidates)
    raise RuntimeError("no upcoming analysis slot")


def _crossed(previous: float, current: float, level: float) -> bool:
    return (previous - level) * (current - level) <= 0 and previous != current


def poll_price_alerts(settings: Any, repo: Any, factor: Any, state: dict[str, Any], **cycle_kwargs: Any) -> bool:
    """Start one alert cycle when the latest 15-minute close crosses an active level."""
    from ai_trader.cycle import run_cycle

    now = datetime.now(timezone.utc)
    if not gold_market_open(now):
        return False
    last = state.get("last_alert_at")
    pending = bool(state.get("pending"))
    if (
        last is not None
        and (now - last).total_seconds() < settings.alert_debounce_sec
        and not pending
    ):
        return False
    bars = repo.load_candles(now - timedelta(days=2))
    if len(bars) < 2:
        return False
    previous, current = bars[-2].close, bars[-1].close
    hits = [
        alert
        for alert in repo.active_alerts()
        if _crossed(previous, current, float(alert["price"]))
    ]
    if not hits and not pending:
        return False
    result = run_cycle(
        settings=settings,
        repo=repo,
        factor=factor,
        now=now,
        trigger="alert",
        **cycle_kwargs,
    )
    if result.busy:
        state["pending"] = True
        return False
    state["pending"] = False
    state["last_alert_at"] = now
    for alert in hits:
        if alert.get("id"):
            repo.mark_alert_hit(str(alert["id"]), now)
    return True
