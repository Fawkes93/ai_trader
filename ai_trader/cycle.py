"""One analysis cycle. Scheduler, alerts, and /order all enter here."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

import httpx

from ai_trader.ai.client import decide as decide_with_fallback
from ai_trader.ai.client import fetch_calendar_note, fetch_sentiment
from ai_trader.data.calendar import lookup_targets, select_events
from ai_trader.config import Settings
from ai_trader.data.cot import CFTC_FUT_DISAGG_URL, parse_gold_report, report_age_days
from ai_trader.data.market import (
    TF_D1,
    TF_H1,
    TF_H4,
    TF_MO,
    TF_W1,
    aggregate,
    bucket_start,
    completed_before,
    history_days,
    median_range,
    session_name,
    timeframe_path,
    weekend_closed,
)
from ai_trader.data.positions import normalize_position, pnl_usd
from ai_trader.data.quote import latest_price
from ai_trader.data.sentiment import should_refresh, unknown_sentiment
from ai_trader.data.sr import build_zones, closest, entry_setup, nearest, price_location
from ai_trader.models import Decision
from ai_trader.notify.messages import format_chat_notice, format_cycle
from ai_trader.risk.checks import (
    RiskContext,
    check_entry,
    equity_step_lots,
    positions_after_closes,
    select_management,
)
from ai_trader.subscribers import Subscriber, parse_subscriber

_LOCK = threading.Lock()


@dataclass
class CycleResult:
    busy: bool = False
    run_id: str = ""
    trigger: str = ""
    session: str = ""
    price: float | None = None
    final_decision: str = "none"
    rejection_reason: str | None = None
    reasoning: str = ""
    confidence: float | None = None
    factor_result: dict[str, Any] | None = None
    manage_result: list[dict[str, Any]] = field(default_factory=list)
    subscriber_results: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    order_payload: dict[str, Any] | None = None


def gate_block_reason(gate: dict[str, Any]) -> str | None:
    if gate.get("missing"):
        return "missing_account"
    if gate.get("kill_switch") or not gate.get("trading_enabled"):
        return "kill_switch"
    if not gate.get("pref_enabled"):
        return "ai_gold_disabled"
    return None


def _public_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_id": row.get("position_id"),
        "side": row.get("side"),
        "volume": row.get("volume"),
        "entry": row.get("entry_price"),
        "stop_loss": row.get("sl_price"),
        "take_profit": row.get("tp_price"),
        "unrealized_pnl": row.get("unrealized_pnl"),
        "opened_at": row.get("opened_at"),
    }


def _sentiment_view(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"bias": "unknown", "summary": "", "confidence": 0, "posts": 0}
    posts = row.get("posts") or []
    return {
        "bias": row.get("bias") or "unknown",
        "summary": row.get("summary") or "",
        "confidence": row.get("confidence") or 0,
        "posts": len(posts) if isinstance(posts, list) else 0,
    }


def _cot_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "report_date": row.get("report_date"),
        "commercial_net": row.get("commercial_net"),
        "managed_money_net": row.get("managed_money_net"),
        "open_interest": row.get("open_interest"),
    }


def default_cot_fetch() -> dict[str, Any] | None:
    response = httpx.get(
        CFTC_FUT_DISAGG_URL,
        timeout=45,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ai-trader/1.0; +https://factor.igrik.xyz)"},
    )
    response.raise_for_status()
    return parse_gold_report(response.text)


def _note_model(context: dict[str, Any], notes: list[str], *, failed: bool) -> None:
    model = context.get("_model")
    if model != "gemini":
        return
    note = "model:gemini_failed" if failed else "model:gemini"
    if note not in notes:
        notes.append(note)
    error = context.get("_grok_error")
    if error and "grok_failed" not in " ".join(notes):
        notes.append(f"grok_failed:{error}")


def _as_decision(value: Any) -> Decision:
    if isinstance(value, Decision):
        return value
    return Decision.model_validate(value)


def _account_funds(factor: Any, account_id: int) -> dict[str, Any]:
    empty = {"balance": None, "equity": None, "currency": None}
    store = getattr(factor, "account_funds", None)
    if not isinstance(store, dict):
        return empty
    row = store.get(account_id)
    if row is None:
        row = store.get(str(account_id))
    if not isinstance(row, dict):
        return empty
    return {
        "balance": row.get("balance"),
        "equity": row.get("equity"),
        "currency": row.get("currency"),
    }


def _with_step_lots(funds: dict[str, Any]) -> dict[str, Any]:
    return {
        **funds,
        "step_lots": equity_step_lots(funds.get("equity"), funds.get("currency")),
    }


def _load_positions(
    factor: Any,
    repo: Any,
    price: float | None,
    oz: float,
    *,
    now: datetime | None = None,
    notes: list[str] | None = None,
    account_id: int | None = None,
) -> list[dict[str, Any]]:
    if getattr(factor, "uses_broker_positions", False):
        try:
            raw = factor.list_positions(account_id)
        except Exception as exc:
            if notes is not None:
                notes.append(f"positions_unavailable:{exc}")
            rows = _rows_for_account(repo.open_positions(), account_id)
        else:
            rows = [normalize_position(item) for item in raw]
            if now is not None:
                live_ids = {str(row.get("position_id")) for row in rows}
                for local in _rows_for_account(repo.open_positions(), account_id):
                    position_id = str(local.get("position_id") or "")
                    if position_id and position_id not in live_ids:
                        _close_missing_position(repo, local, position_id, now, notes)
    else:
        rows = _rows_for_account(repo.open_positions(), account_id)
    for row in rows:
        if row.get("unrealized_pnl") is None and price is not None:
            row["unrealized_pnl"] = pnl_usd(
                str(row.get("side")),
                row.get("entry_price"),
                price,
                float(row.get("volume") or 0),
                oz,
            )
    return rows


def run_cycle(
    *,
    settings: Settings,
    repo: Any,
    factor: Any,
    now: datetime | None = None,
    trigger: str = "manual",
    decide: Callable[..., Any] | None = None,
    sentiment_fetch: Callable[..., Any] | None = None,
    cot_fetch: Callable[[], Any] | None = None,
    calendar_fetch: Callable[..., Any] | None = None,
    quote_fetch: Callable[..., Any] | None = None,
    notify: Callable[[str], None] | None = None,
    candles: list | None = None,
    only_chat_id: str | None = None,
) -> CycleResult:
    if not _LOCK.acquire(blocking=False):
        result = CycleResult(busy=True, trigger=trigger, message="busy")
        return result
    try:
        result = _run_cycle_locked(
            settings=settings,
            repo=repo,
            factor=factor,
            now=now or datetime.now(timezone.utc),
            trigger=trigger,
            decide=decide or decide_with_fallback,
            sentiment_fetch=sentiment_fetch if sentiment_fetch is not None else fetch_sentiment,
            cot_fetch=cot_fetch if cot_fetch is not None else default_cot_fetch,
            calendar_fetch=calendar_fetch if calendar_fetch is not None else fetch_calendar_note,
            quote_fetch=quote_fetch if quote_fetch is not None else latest_price,
            candles=candles,
            only_chat_id=only_chat_id,
        )
    except Exception as exc:
        result = CycleResult(
            trigger=trigger,
            final_decision="none",
            rejection_reason="cycle_error",
            reasoning=str(exc)[:400],
        )
        result.message = format_cycle(result)
    finally:
        _LOCK.release()
    if notify is not None and result.message:
        _notify_result(result, notify)
    return result


def _run_cycle_locked(
    *,
    settings: Settings,
    repo: Any,
    factor: Any,
    now: datetime,
    trigger: str,
    decide: Callable[..., Any],
    sentiment_fetch: Callable[..., Any],
    cot_fetch: Callable[[], Any],
    calendar_fetch: Callable[..., Any],
    quote_fetch: Callable[..., Any],
    candles: list | None,
    only_chat_id: str | None = None,
) -> CycleResult:
    now = now.astimezone(timezone.utc)
    run_id = str(uuid4())
    session = session_name(now)
    notes: list[str] = []
    result = CycleResult(run_id=run_id, trigger=trigger, session=session)

    subscribers = _load_subscribers(factor, only_chat_id, notes)
    if not subscribers:
        result.rejection_reason = "no_subscribers"
        _finish(result, repo, settings, now, None, None, None, None, notes)
        return result

    since = now - timedelta(days=settings.backfill_days)
    bars = list(candles) if candles is not None else repo.load_candles(since)
    if not bars:
        result.rejection_reason = "no_price"
        _finish(result, repo, settings, now, None, None, None, None, notes)
        return result

    price = float(bars[-1].close)
    price_kind = "15m_close"
    current_bucket = bucket_start(now, settings.candle_timeframe_min)
    if bars[-1].open_time >= current_bucket:
        price_kind = "latest_close"
    try:
        live = _live_price(quote_fetch(settings))
    except Exception as exc:
        notes.append(f"quote_unavailable:{exc}")
        live = None
    if live is not None:
        price = live
        price_kind = "live"
    result.price = price
    span = history_days(bars)
    short = span < settings.history_short_days
    if short:
        notes.append(f"history_short:{span}d")

    h1 = aggregate(bars, TF_H1)
    h4 = aggregate(bars, TF_H4)
    daily = aggregate(bars, TF_D1)
    weekly = aggregate(bars, TF_W1)
    monthly = aggregate(bars, TF_MO)
    daily_done = completed_before(daily, now, TF_D1) or daily
    weekly_done = completed_before(weekly, now, TF_W1) or weekly
    trends = {
        "monthly": "insufficient" if short or len(monthly) < 3 else _trend(monthly, 3),
        "weekly": _trend(weekly, 4),
        "daily": _trend(daily, 5),
        "h4": _trend(h4, 5),
        "h1": _trend(h1, 5),
        "history_days": span,
        "history_short": short,
    }
    zones = build_zones(
        h1=h1,
        h4=h4,
        daily=daily_done,
        weekly=weekly_done,
        price=price,
        width=settings.sr_cluster_usd,
    )
    near_support, near_resistance = nearest(zones, price)
    zone_payload = [
        {
            "price": zone.price,
            "kind": zone.kind,
            "confluence": zone.confluence,
            "distance": zone.distance,
        }
        for zone in closest(zones, price, 12)
    ]
    sr_snapshot = {
        "zones": zone_payload,
        "nearest_support": near_support.price if near_support else None,
        "nearest_resistance": near_resistance.price if near_resistance else None,
    }

    cot_row = _refresh_cot(repo, cot_fetch, now, settings.cot_stale_days, notes)
    sentiment_row = _refresh_sentiment(repo, settings, sentiment_fetch, now, notes)
    calendar, calendar_note = _load_calendar(repo, settings, calendar_fetch, now, notes)
    _fill_missing_exits(repo, now, notes)
    books = {
        sub.account_id: _load_positions(
            factor, repo, price, settings.oz_per_lot, now=now, notes=notes, account_id=sub.account_id
        )
        for sub in subscribers
    }
    positions = [row for rows in books.values() for row in rows]
    closed = [_normalize_closed(row) for row in repo.closed_today(now)]
    h1_median = median_range(h1)
    path = timeframe_path(trends["monthly"], trends["weekly"])
    location = price_location(
        price,
        sr_snapshot["nearest_support"],
        sr_snapshot["nearest_resistance"],
    )

    context = {
        "symbol": settings.broker_symbol,
        "price": price,
        "price_kind": price_kind,
        "session": session,
        "market_open": session != "closed",
        "weekend": weekend_closed(now),
        "trends": trends,
        "path": path,
        "location": location,
        "setup": entry_setup(path, location),
        "zones": zone_payload,
        "nearest_support": sr_snapshot["nearest_support"],
        "nearest_resistance": sr_snapshot["nearest_resistance"],
        "cot": _cot_view(cot_row),
        "sentiment": _sentiment_view(sentiment_row),
        "calendar": calendar,
        "calendar_note": calendar_note,
        "open_positions": [_public_position(row) for row in positions],
        "min_rr": min(sub.min_rr for sub in subscribers),
        "max_lots": min(sub.max_volume_lots for sub in subscribers),
        "daily_loss_cap": min(sub.daily_loss_cap_usd for sub in subscribers),
        "accounts": [
            {
                "account_id": sub.account_id,
                "max_lots": sub.max_volume_lots,
                "min_lots": sub.min_volume_lots,
                "min_rr": sub.min_rr,
                "max_concurrent": sub.max_concurrent,
                **_with_step_lots(_account_funds(factor, sub.account_id)),
            }
            for sub in subscribers
        ],
        "force_order": trigger == "telegram_order",
        "min_stop_distance": round(h1_median, 2) if h1_median else None,
        "recent_cycles": _recent_cycles(repo, notes),
        "recent_trades": _recent_trades(repo, settings.oz_per_lot, notes),
    }
    if trigger == "telegram_order":
        cap = max(6.0, 2.0 * h1_median) if h1_median else 15.0
        context["max_tp_distance"] = round(cap, 2)

    decision, ai_reason = _call_model(decide, settings, context, trigger, notes)
    raw = decision.model_dump() if decision else None
    order_payload = None
    factor_result = None
    manage_payload: list[dict[str, Any]] = []
    manage_result: list[dict[str, Any]] = []

    if decision is None:
        result.rejection_reason = ai_reason or "ai_invalid"
    else:
        result.reasoning = decision.reasoning
        result.confidence = decision.confidence
        subscriber_results: list[dict[str, Any]] = []
        for sub in subscribers:
            account_positions = books.get(sub.account_id, [])
            account_closed = [
                row for row in closed if row.get("account_id") in (None, sub.account_id, str(sub.account_id))
            ]
            applied = _apply_subscriber(
                sub=sub,
                decision=decision,
                settings=settings,
                repo=repo,
                factor=factor,
                now=now,
                trigger=trigger,
                price=price,
                positions=account_positions,
                closed=account_closed,
                h1_median=h1_median,
                market_open=session != "closed",
                run_id=run_id,
                quote_fetch=quote_fetch,
            )
            subscriber_results.append(applied)
            manage_payload.extend(applied["manage_payload"])
            manage_result.extend(applied["manage_result"])
            if applied.get("order_payload"):
                order_payload = applied["order_payload"]
            if applied.get("factor_result"):
                factor_result = applied["factor_result"]
        result.subscriber_results = subscriber_results
        placed = [row for row in subscriber_results if row.get("placed")]
        managed = [
            row for row in subscriber_results if row.get("final_decision") == "manage_only"
        ]
        if placed:
            result.final_decision = decision.action
            result.factor_result = placed[0].get("factor_result")
        elif managed:
            result.final_decision = "manage_only"
        else:
            result.final_decision = "none"
            reasons = [row.get("rejection_reason") for row in subscriber_results if row.get("rejection_reason")]
            if reasons:
                result.rejection_reason = reasons[0]
            elif trigger == "telegram_order" and decision.action == "none":
                result.rejection_reason = "ai_refused"
        alerts = sorted(decision.alert_levels, key=lambda level: abs(level.price - price))
        repo.replace_alerts(
            [{"price": level.price, "type": level.type} for level in alerts[: settings.max_alerts]],
            run_id,
        )

    result.order_payload = order_payload
    result.manage_result = manage_result
    _finish(
        result,
        repo,
        settings,
        now,
        sr_snapshot,
        _cot_view(cot_row),
        _sentiment_view(sentiment_row),
        raw,
        notes,
        positions,
        order_payload,
        manage_payload,
        manage_result,
    )
    return result


def _rows_for_account(rows: list[dict[str, Any]], account_id: int | None) -> list[dict[str, Any]]:
    chosen = []
    for row in rows:
        owner = row.get("account_id")
        if account_id is None or owner in (None, "", account_id, str(account_id)):
            item = normalize_position(row)
            item["account_id"] = account_id if owner in (None, "") else owner
            chosen.append(item)
    return chosen


_SUBSCRIBER_ATTEMPTS = 3
_SUBSCRIBER_PAUSE_SEC = 1.0
_RECENT_CYCLES = 10
_RECENT_TRADES = 5
_RECENT_REASON_CHARS = 180
_SENTIMENT_UNPAID_MIN = 12 * 60
_EXIT_LOOKBACK_DAYS = 14


def _recent_cycles(repo: Any, notes: list[str]) -> list[dict[str, Any]]:
    """Last decisions, oldest first. A lookup failure leaves the cycle with an empty list."""
    try:
        rows = repo.recent_runs(_RECENT_CYCLES)
    except Exception as exc:
        notes.append(f"recent_runs_unavailable:{exc}")
        return []
    return [_compact_cycle(row) for row in rows or []]


def _compact_cycle(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("ai_raw_output") if isinstance(row.get("ai_raw_output"), dict) else {}
    subscribers = row.get("subscriber_results") if isinstance(row.get("subscriber_results"), list) else []
    placed = any(isinstance(item, dict) and item.get("placed") for item in subscribers)
    reason = " ".join(str(raw.get("reasoning") or "").split())
    if len(reason) > _RECENT_REASON_CHARS:
        reason = reason[:_RECENT_REASON_CHARS].rstrip() + "…"
    compact = {
        "at": row.get("created_at"),
        "trigger": row.get("trigger"),
        "session": row.get("session"),
        "price": row.get("price"),
        "decision": row.get("final_decision"),
        "rejection": row.get("rejection_reason"),
        "action": raw.get("action"),
        "side": raw.get("side"),
        "confidence": raw.get("confidence"),
        "stop": raw.get("stop_loss"),
        "target": raw.get("take_profit"),
        "placed": placed,
        "reasoning": reason or None,
    }
    return {key: value for key, value in compact.items() if value not in (None, "")}


def _recent_trades(repo: Any, oz: float, notes: list[str]) -> list[dict[str, Any]]:
    try:
        rows = repo.recent_closed(_RECENT_TRADES)
    except Exception as exc:
        notes.append(f"recent_trades_unavailable:{exc}")
        return []
    return [_compact_trade(row, oz) for row in rows or []]


def _compact_trade(row: dict[str, Any], oz: float) -> dict[str, Any]:
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    stop = detail.get("initial_stop")
    if stop in (None, ""):
        stop = row.get("sl_price")
    entry = _num(row.get("entry_price"))
    volume = _num(row.get("volume_lots") if row.get("volume_lots") not in (None, "") else row.get("volume"))
    net = _num(row.get("realized_pnl"))
    stop_n = _num(stop)
    risk = None
    if entry is not None and stop_n is not None and volume:
        risk = abs(entry - stop_n) * volume * oz
    result = "unknown"
    if net is not None:
        result = "win" if net > 0.01 else "loss" if net < -0.01 else "flat"
    compact = {
        "at": row.get("closed_at"),
        "side": row.get("side"),
        "entry": entry,
        "stop": stop_n,
        "target": _num(row.get("tp_price")),
        "close": _num(detail.get("close_price")),
        "net": net,
        "r": round(net / risk, 2) if net is not None and risk else None,
        "result": result,
    }
    return {key: value for key, value in compact.items() if value is not None}


def _failed_attempt(decision: Decision, price: float, h1_median: float | None, reason: str) -> dict[str, Any]:
    entry = price if decision.action == "market" else _num(decision.entry_price)
    stop = _num(decision.stop_loss)
    target = _num(decision.take_profit)
    risk = reward = None
    if entry is not None and stop is not None and decision.side in {"buy", "sell"}:
        risk = (entry - stop) if decision.side == "buy" else (stop - entry)
    if entry is not None and target is not None and decision.side in {"buy", "sell"}:
        reward = (target - entry) if decision.side == "buy" else (entry - target)
    compact = {
        "action": decision.action,
        "side": decision.side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "volume": decision.volume,
        "confidence": decision.confidence,
        "stop_distance": round(risk, 2) if risk is not None else None,
        "min_stop_distance": round(h1_median, 2) if h1_median else None,
        "rr": round(reward / risk, 2) if risk and reward and risk > 0 else None,
        "rejection": reason,
        "reasoning": " ".join((decision.reasoning or "").split())[:300] or None,
    }
    return {key: value for key, value in compact.items() if value is not None}


def _failed_attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for item in row.get("subscriber_results") or []:
        if isinstance(item, dict) and isinstance(item.get("attempt"), dict):
            found.append(item["attempt"])
    return found


def _close_missing_position(
    repo: Any,
    local: dict[str, Any],
    position_id: str,
    now: datetime,
    notes: list[str] | None,
) -> None:
    outcome = _lookup_exit(repo, position_id, notes)
    detail = dict(local.get("detail") or {})
    if outcome.get("close_price") is not None:
        detail["close_price"] = outcome["close_price"]
    values: dict[str, Any] = {
        "status": "closed",
        "closed_at": outcome.get("closed_at") or now.isoformat(),
        "detail": detail,
    }
    if outcome.get("net") is not None:
        values["realized_pnl"] = outcome["net"]
    repo.update_position(position_id, values)


def _fill_missing_exits(repo: Any, now: datetime, notes: list[str]) -> None:
    try:
        rows = repo.closed_missing_pnl(now - timedelta(days=_EXIT_LOOKBACK_DAYS))
    except Exception as exc:
        notes.append(f"exit_backfill_unavailable:{exc}")
        return
    for row in rows or []:
        position_id = str(row.get("broker_position_id") or row.get("position_id") or "")
        if not position_id:
            continue
        outcome = _lookup_exit(repo, position_id, notes)
        if outcome.get("net") is None:
            continue
        detail = dict(row.get("detail") or {})
        if outcome.get("close_price") is not None:
            detail["close_price"] = outcome["close_price"]
        repo.update_position(
            position_id,
            {
                "realized_pnl": outcome["net"],
                "closed_at": outcome.get("closed_at") or row.get("closed_at"),
                "detail": detail,
            },
        )


def _lookup_exit(repo: Any, position_id: str, notes: list[str] | None) -> dict[str, Any]:
    try:
        found = repo.order_outcome(position_id)
    except Exception as exc:
        if notes is not None:
            notes.append(f"exit_lookup_failed:{exc}")
        return {}
    return found or {}


def _sentiment_unpaid(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    raw = row.get("raw")
    return isinstance(raw, dict) and bool(raw.get("unpaid"))


def _hold_sentiment(repo: Any, latest: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    held = dict(latest or {})
    held.pop("id", None)
    held["bias"] = held.get("bias") or "unknown"
    held["summary"] = held.get("summary") or "sentiment_unpaid"
    held["confidence"] = held.get("confidence") or 0
    held["created_at"] = now.isoformat()
    held["raw"] = {"unpaid": True}
    return repo.insert_sentiment(held)


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _load_subscribers(
    factor: Any,
    only_chat_id: str | None,
    notes: list[str],
    *,
    pause: Callable[[float], None] | None = None,
) -> list[Subscriber]:
    wait = pause or time.sleep
    raw = None
    error: Exception | None = None
    attempt = 0
    for attempt in range(1, _SUBSCRIBER_ATTEMPTS + 1):
        try:
            raw = factor.list_subscribers()
            error = None
            break
        except Exception as exc:
            error = exc
            if attempt < _SUBSCRIBER_ATTEMPTS:
                wait(_SUBSCRIBER_PAUSE_SEC)
    if error is not None:
        notes.append(f"subscribers_unavailable:{error}")
        return []
    if attempt > 1:
        notes.append(f"subscribers_retry:{attempt}")
    found: list[Subscriber] = []
    for row in raw or []:
        try:
            found.append(parse_subscriber(row))
        except (KeyError, TypeError, ValueError):
            continue
    if only_chat_id:
        wanted = str(only_chat_id)
        matched = [sub for sub in found if sub.telegram_chat_id == wanted]
        if matched:
            return matched
        # /order is allowed from one chat. That chat is often not the chat
        # stored on the Factor account, which is only where notes are sent.
        if found:
            notes.append("order_chat_unlinked")
    return found


def _apply_subscriber(
    *,
    sub: Subscriber,
    decision: Decision,
    settings: Settings,
    repo: Any,
    factor: Any,
    now: datetime,
    trigger: str,
    price: float,
    positions: list[dict[str, Any]],
    closed: list[dict[str, Any]],
    h1_median: float | None,
    market_open: bool,
    run_id: str,
    quote_fetch: Callable[..., Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "user_id": sub.user_id,
        "account_id": sub.account_id,
        "telegram_chat_id": sub.telegram_chat_id,
        "final_decision": "none",
        "rejection_reason": None,
        "placed": False,
        "manage_payload": [],
        "manage_result": [],
    }
    risk_ctx = RiskContext(
        settings=settings,
        now=now,
        price=price,
        positions=positions,
        closed_today=closed,
        trigger=trigger,
        h1_median_range=h1_median,
        market_open=market_open,
        limits=sub.limits(),
    )
    manage_checks = select_management(decision, positions, risk_ctx)
    manage_result: list[dict[str, Any]] = []
    for item in manage_checks:
        row = {
            "account_id": sub.account_id,
            "position_id": item.item.position_id if item.item else None,
            "action": item.item.action if item.item else None,
            "ok": item.ok,
            "reason": item.reason,
        }
        if item.ok and item.send and item.item is not None:
            payload = {
                "account_id": sub.account_id,
                "position_id": item.item.position_id,
                "action": item.item.action,
                "new_sl": item.item.new_sl,
                "new_tp": item.item.new_tp,
                "close_percent": item.item.close_percent,
                "label": settings.label,
            }
            record["manage_payload"].append(payload)
            sent = factor.manage(payload, run_id)
            row["factor"] = sent
            row["ok"] = bool(sent.get("success"))
            if row["ok"]:
                _apply_local_manage(repo, item.item, positions, price, settings, now)
            else:
                row["reason"] = sent.get("message") or "factor_rejected"
        manage_result.append(row)
    record["manage_result"] = manage_result
    remaining = positions_after_closes(positions, manage_checks)
    failed_closes = {
        row["position_id"] for row in manage_result if row.get("action") == "close" and not row.get("ok")
    }
    if failed_closes:
        remaining = positions
    funds = _account_funds(factor, sub.account_id)
    entry = check_entry(
        decision,
        RiskContext(
            settings=settings,
            now=now,
            price=price,
            positions=remaining,
            closed_today=closed,
            trigger=trigger,
            h1_median_range=h1_median,
            market_open=market_open,
            limits=sub.limits(),
            equity=funds.get("equity"),
            currency=funds.get("currency"),
        ),
    )
    managed = any(row.get("ok") and row.get("action") not in {None, "leave"} for row in manage_result)
    if decision.action == "none":
        record["final_decision"] = "manage_only" if managed else "none"
        if trigger == "telegram_order":
            record["rejection_reason"] = "ai_refused"
    elif not entry.ok:
        record["rejection_reason"] = entry.reason
        record["final_decision"] = "manage_only" if managed else "none"
        record["attempt"] = _failed_attempt(decision, price, h1_median, entry.reason)
    else:
        payload = {
            "account_id": sub.account_id,
            "symbol": settings.broker_symbol,
            "side": decision.side,
            "volume": entry.volume,
            "order_type": entry.order_type,
            "entry_price": _order_entry(entry, quote_fetch, settings),
            "stop_loss": entry.stop,
            "take_profit": entry.take_profit,
            "label": settings.label,
            "comment": f"ai-run {run_id}",
        }
        sent = factor.place(payload, run_id)
        record["order_payload"] = payload
        record["factor_result"] = sent
        if sent.get("success"):
            record["placed"] = True
            record["final_decision"] = decision.action
            repo.insert_position(
                {
                    "label": settings.label,
                    "account_id": sub.account_id,
                    "factor_order_id": str(sent.get("order_id") or ""),
                    "broker_position_id": str(sent.get("position_id") or ""),
                    "side": decision.side,
                    "volume_lots": entry.volume,
                    "entry_price": entry.entry,
                    "sl_price": entry.stop,
                    "tp_price": entry.take_profit,
                    "status": "open",
                    "opened_by_run_id": run_id,
                    "opened_at": now.isoformat(),
                    "detail": {
                        "order_type": entry.order_type,
                        "trigger": trigger,
                        "initial_stop": entry.stop,
                    },
                }
            )
        else:
            reason = str(sent.get("message") or "factor_rejected")
            record["rejection_reason"] = reason
            record["attempt"] = _failed_attempt(decision, price, h1_median, reason)
    record["message"] = (
        f"AI-GOLD account {sub.account_id}\n"
        f"decision {record['final_decision']}\n"
        + (f"rejected {record['rejection_reason']}\n" if record.get("rejection_reason") else "")
        + (decision.reasoning or "")[:300]
    )
    return record


def _notify_result(result: CycleResult, notify: Callable[..., None]) -> None:
    delivered = False
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in result.subscriber_results:
        chat = item.get("telegram_chat_id")
        if not chat:
            continue
        groups.setdefault(str(chat), []).append(item)
    for chat, rows in groups.items():
        text = format_chat_notice(result, rows)
        try:
            notify(text, chat)
            delivered = True
        except TypeError:
            notify(text)
            delivered = True
        except Exception:
            pass
    if delivered:
        return
    try:
        notify(result.message)
    except TypeError:
        notify(result.message, None)
    except Exception:
        pass


def _trend(bars: list, minimum: int) -> str:
    from ai_trader.data.market import trend

    return trend(bars, minimum=minimum)


def _normalize_closed(row: dict[str, Any]) -> dict[str, Any]:
    if "realized_pnl" in row and "volume" not in row and "volume_lots" in row:
        normalized = normalize_position(row)
        normalized["realized_pnl"] = row.get("realized_pnl")
        normalized["status"] = "closed"
        return normalized
    return row


def _live_price(value: object) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _order_entry(entry: Any, quote_fetch: Callable[..., Any], settings: Settings) -> float:
    """Market orders need an entry so Factor can place the stop and target.

    Use a fresh print when the stop and target are still on the correct side
    of it. Otherwise keep the price the decision was checked against.
    """
    if entry.order_type != "market":
        return float(entry.entry)
    try:
        live = _live_price(quote_fetch(settings))
    except Exception:
        live = None
    if live is None or not _protection_holds(entry, live):
        return float(entry.entry)
    return live


def _protection_holds(entry: Any, price: float) -> bool:
    stop = float(entry.stop)
    target = float(entry.take_profit)
    if entry.order_type != "market":
        return True
    # Check stores side only on the decision. The entry prices are enough:
    # a buy stop is below the market and a sell stop is above it.
    if stop < price and target > price:
        return True
    if stop > price and target < price:
        return True
    return False


def _load_calendar(
    repo: Any,
    settings: Settings,
    calendar_fetch: Callable[..., Any],
    now: datetime,
    notes: list[str],
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        rows = repo.econ_events(now - timedelta(hours=12), now + timedelta(days=7))
    except Exception as exc:
        notes.append(f"calendar_unavailable:{exc}")
        return [], None
    events = select_events(rows or [], now)
    targets = lookup_targets(events, now)
    if not targets:
        return events, None
    try:
        note = str(calendar_fetch(settings, targets, now) or "").strip()
    except Exception as exc:
        notes.append(f"calendar_browse_failed:{exc}")
        return events, None
    return events, note[:800] or None


def _refresh_cot(repo: Any, cot_fetch: Callable[[], Any], now: datetime, stale_days: int, notes: list[str]) -> dict | None:
    latest = repo.latest_cot()
    age = report_age_days(latest, now) if latest else None
    if latest and age is not None and age <= stale_days:
        return latest
    try:
        fresh = cot_fetch()
    except Exception as exc:
        notes.append(f"cot_refresh_failed:{exc}")
        return latest
    if not fresh:
        notes.append("cot_missing")
        return latest
    try:
        repo.insert_cot(fresh)
    except Exception as exc:
        notes.append(f"cot_store_failed:{exc}")
    return fresh


def _refresh_sentiment(
    repo: Any,
    settings: Settings,
    sentiment_fetch: Callable[..., Any],
    now: datetime,
    notes: list[str],
) -> dict | None:
    latest = repo.latest_sentiment()
    wait_min = _SENTIMENT_UNPAID_MIN if _sentiment_unpaid(latest) else settings.sentiment_max_age_min
    if not should_refresh(latest, now, wait_min):
        return latest
    if not settings.x_handles:
        if latest:
            return latest
        row = unknown_sentiment("no handles configured")
        row["handles"] = []
        row["window_start"] = (now - timedelta(hours=48)).isoformat()
        row["window_end"] = now.isoformat()
        row["raw"] = {}
        return repo.insert_sentiment(row)
    try:
        fresh = sentiment_fetch(settings, now)
    except Exception as exc:
        if _http_status(exc) == 402:
            notes.append("sentiment_unpaid")
            return _hold_sentiment(repo, latest, now)
        notes.append(f"sentiment_refresh_failed:{exc}")
        return latest
    if not fresh:
        return latest
    return repo.insert_sentiment(fresh)


def _call_model(
    decide: Callable[..., Any],
    settings: Settings,
    context: dict[str, Any],
    trigger: str,
    notes: list[str],
) -> tuple[Decision | None, str | None]:
    force = trigger == "telegram_order"
    attempts = 2 if force else 1
    decision: Decision | None = None
    for attempt in range(attempts):
        try:
            decision = _as_decision(decide(settings, context, force=force, attempt=attempt))
        except Exception:
            _note_model(context, notes, failed=True)
            return None, "ai_invalid"
        _note_model(context, notes, failed=False)
        if force and decision.action == "none" and attempt == 0:
            continue
        break
    if force and decision is not None and decision.action == "none":
        return decision, "ai_refused"
    return decision, None


def _apply_local_manage(repo: Any, item: Any, positions: list[dict], price: float, settings: Settings, now: datetime) -> None:
    if item.action == "close":
        match = next((row for row in positions if str(row.get("position_id")) == item.position_id), None)
        realized = None
        if match is not None:
            realized = pnl_usd(
                str(match.get("side")),
                match.get("entry_price"),
                price,
                float(match.get("volume") or 0),
                settings.oz_per_lot,
            )
            match["status"] = "closed"
        repo.update_position(
            item.position_id,
            {
                "status": "closed",
                "closed_at": now.isoformat(),
                "realized_pnl": realized,
            },
        )
    elif item.action == "move_sl_to_breakeven" and item.new_sl is not None:
        repo.update_position(item.position_id, {"sl_price": item.new_sl})
        for row in positions:
            if str(row.get("position_id")) == item.position_id:
                row["sl_price"] = item.new_sl


def _finish(
    result: CycleResult,
    repo: Any,
    settings: Settings,
    now: datetime,
    sr: dict | None,
    cot: dict | None,
    sentiment: dict | None,
    raw: dict | None,
    notes: list[str],
    positions: list | None = None,
    order_payload: dict | None = None,
    manage_payload: list | None = None,
    manage_result: list | None = None,
) -> None:
    result.message = format_cycle(result)
    try:
        repo.insert_run(
            {
                "id": result.run_id or str(uuid4()),
                "created_at": now.isoformat(),
                "trigger": result.trigger,
                "session": result.session,
                "price": result.price,
                "sr_levels": sr,
                "cot_snapshot": cot,
                "x_sentiment": sentiment,
                "open_positions": [_public_position(row) for row in (positions or [])],
                "ai_raw_output": raw,
                "final_decision": result.final_decision,
                "rejection_reason": result.rejection_reason,
                "order_payload": order_payload if order_payload is not None else result.order_payload,
                "factor_result": result.factor_result,
                "manage_payload": manage_payload or [],
                "manage_result": manage_result if manage_result is not None else result.manage_result,
                "subscriber_results": result.subscriber_results,
                "notes": "; ".join(notes) if notes else None,
            }
        )
    except Exception as exc:
        result.reasoning = (result.reasoning + f" log_failed:{exc}")[:400]


def question_context(settings: Settings, repo: Any, factor: Any) -> dict[str, Any]:
    """Current book for a /ai question. One subscriber read, no trade."""
    notes: list[str] = []
    subscribers: list[Subscriber] = []
    try:
        for row in factor.list_subscribers() or []:
            try:
                subscribers.append(parse_subscriber(row))
            except (KeyError, TypeError, ValueError):
                continue
    except Exception as exc:
        notes.append(f"subscribers_unavailable:{exc}")
    try:
        price = repo.latest_close()
    except Exception as exc:
        notes.append(f"price_unavailable:{exc}")
        price = None
    positions: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    targets = subscribers or [None]
    for sub in targets:
        account_id = None if sub is None else sub.account_id
        rows = _load_positions(
            factor,
            repo,
            price,
            settings.oz_per_lot,
            notes=notes,
            account_id=account_id,
        )
        for row in rows:
            item = _public_position(row)
            if account_id is not None:
                item["account_id"] = account_id
            positions.append(item)
        if sub is not None:
            accounts.append(
                {
                    "account_id": sub.account_id,
                    "min_lots": sub.min_volume_lots,
                    "max_lots": sub.max_volume_lots,
                    **_with_step_lots(_account_funds(factor, sub.account_id)),
                }
            )
    return {
        "price": price,
        "accounts": accounts,
        "open_positions": positions,
        "recent_cycles": _recent_cycles(repo, notes),
        "recent_trades": _recent_trades(repo, settings.oz_per_lot, notes),
    }


def status_text(settings: Settings, repo: Any, factor: Any, now: datetime | None = None) -> str:
    from ai_trader.notify.messages import format_status
    from ai_trader.scheduler import next_analysis

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        subscribers = _load_subscribers(factor, None, [])
    except Exception:
        subscribers = []
    gate = {
        "pref_enabled": bool(subscribers),
        "kill_switch": False,
        "trading_enabled": True,
    }
    oldest = repo.oldest_candle_time()
    price = repo.latest_close()
    positions = _load_positions(factor, repo, price, settings.oz_per_lot, now=now)
    return format_status(
        trading_enabled=settings.trading_enabled,
        factor_mode=settings.factor_mode,
        gate=gate,
        oldest=oldest.date().isoformat() if oldest else "none",
        last_run=repo.latest_run(),
        positions=positions,
        next_run=next_analysis(now).strftime("%Y-%m-%d %H:%M UTC"),
    )


def run_daily_summary(
    *,
    settings: Settings,
    repo: Any,
    now: datetime | None = None,
    notify: Callable[[str], None] | None = None,
) -> str:
    from ai_trader.notify.messages import format_daily

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    text = format_daily(repo.runs_since(start), repo.closed_today(now))
    if notify:
        notify(text)
    return text


def run_weekly_review(
    *,
    settings: Settings,
    repo: Any,
    now: datetime | None = None,
    ask: Callable[[Settings, dict[str, Any]], dict[str, Any]] | None = None,
    notify: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = now - timedelta(days=7)
    runs = repo.runs_since(since)
    compact = []
    for row in runs[-80:]:
        item = {
            "created_at": row.get("created_at"),
            "trigger": row.get("trigger"),
            "final_decision": row.get("final_decision"),
            "rejection_reason": row.get("rejection_reason"),
            "reasoning": (row.get("ai_raw_output") or {}).get("reasoning")
            if isinstance(row.get("ai_raw_output"), dict)
            else None,
            "price": row.get("price"),
        }
        attempts = _failed_attempts(row)
        if attempts:
            item["attempts"] = attempts
        compact.append(item)
    payload = {"period_start": since.isoformat(), "period_end": now.isoformat(), "runs": compact}
    try:
        review = (ask or _ask_review)(settings, payload)
    except Exception as exc:
        review = {"summary": f"review failed: {exc}", "suggestions": []}
    if not isinstance(review, dict):
        review = {"summary": str(review), "suggestions": []}
    row = {
        "period_start": since.isoformat(),
        "period_end": now.isoformat(),
        "summary": str(review.get("summary") or "")[:2000],
        "suggestions": review.get("suggestions") if isinstance(review.get("suggestions"), list) else [],
        "sources": {"runs": len(runs)},
    }
    repo.insert_review(row)
    text = "AI-GOLD week\n" + row["summary"]
    if notify:
        notify(text)
    return row


def _ask_review(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    from ai_trader.ai.client import complete_text
    from ai_trader.data.sentiment import extract_json_object

    prompt = (
        "Review this gold book's last 7 days. Reply with one JSON object "
        '{"summary":"short text","suggestions":["..."]}. '
        "Suggestions are changes to the prompt or the risk defaults, not orders. "
        "Base them only on these logs.\n"
        + json_dumps(payload)
    )
    return extract_json_object(complete_text(settings, prompt))


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str)
