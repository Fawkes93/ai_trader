"""Entry and management checks.

New entries are allowed in any open gold session, including Asia and the
Sunday open. The daily loss cap is the budget. A market order is rejected
while gold is closed. From Friday 21:00 UTC until Sunday 22:00 UTC a pending
order is rejected too. The weekday 21:00–22:00 break still allows pending.
/order skips the confidence floor and must keep take-profit inside a 1–2 hour distance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_trader.config import Settings
from ai_trader.data.market import gold_market_open, weekend_closed
from ai_trader.data.positions import risk_usd, widens
from ai_trader.models import Decision, ManageItem


@dataclass
class Limits:
    """Per-account caps from the Factor AI trader alert."""

    max_volume_lots: float = 0.01
    min_volume_lots: float = 0.01
    max_open_positions: int = 1
    min_rr: float = 1.2
    max_rr: float | None = None
    max_daily_loss_usd: float = 50
    min_confidence: float = 60


LOT_STEP = 0.01
EQUITY_STEP = 100.0
STEP_CURRENCIES = {"USD", "EUR"}


def equity_step_lots(equity: float | None, currency: str | None = None) -> float | None:
    """0.01 lot for each full 100 of equity, and 0.01 below 100.

    Applies to USD and EUR deposit accounts. Equity of 150 is 0.01, 200 is
    0.02, 300 is 0.03. Missing equity, or another deposit currency, leaves
    the step unset. A missing currency is treated as USD.
    """
    if equity is None:
        return None
    code = (currency or "USD").strip().upper()
    if code not in STEP_CURRENCIES:
        return None
    try:
        amount = float(equity)
    except (TypeError, ValueError):
        return None
    if amount != amount:
        return None
    steps = int(amount // EQUITY_STEP)
    if steps < 1:
        steps = 1
    return round(steps * LOT_STEP, 2)


def resolve_volume(
    requested: float | None,
    limits: Limits,
    equity: float | None = None,
    currency: str | None = None,
) -> float | None:
    """Step size from equity, unless the model asked for a smaller lot.

    A null or larger model volume becomes the step. A smaller positive volume
    is kept. The result is then clamped to the account min and max. Returns
    None when the size is not a positive lot.
    """
    step = equity_step_lots(equity, currency)
    if step is None:
        volume = limits.max_volume_lots if requested is None else float(requested)
    elif requested is not None and float(requested) < step:
        volume = float(requested)
    else:
        volume = step
    if volume <= 0:
        return None
    return min(max(volume, limits.min_volume_lots), limits.max_volume_lots)


@dataclass
class RiskContext:
    settings: Settings
    now: object
    price: float
    positions: list[dict[str, Any]]
    closed_today: list[dict[str, Any]]
    trigger: str
    h1_median_range: float | None = None
    market_open: bool | None = None
    limits: Limits | None = None
    equity: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.market_open is None:
            self.market_open = gold_market_open(self.now)  # type: ignore[arg-type]


@dataclass
class Check:
    ok: bool
    reason: str | None = None
    volume: float | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    order_type: str | None = None


@dataclass
class ManageCheck:
    ok: bool
    reason: str | None = None
    item: ManageItem | None = None
    send: bool = False


def limits_of(ctx: RiskContext) -> Limits:
    if ctx.limits is not None:
        return ctx.limits
    cap = ctx.settings.max_volume_lots
    return Limits(
        max_volume_lots=cap,
        min_volume_lots=min(cap, getattr(ctx.settings, "min_volume_lots", cap)),
        max_open_positions=ctx.settings.max_open_positions,
        min_rr=ctx.settings.min_rr,
        max_rr=getattr(ctx.settings, "max_rr", None),
        max_daily_loss_usd=ctx.settings.max_daily_loss_usd,
        min_confidence=ctx.settings.min_confidence,
    )


def daily_loss_used(ctx: RiskContext) -> float:
    used = 0.0
    oz = ctx.settings.oz_per_lot
    loss_cap = limits_of(ctx).max_daily_loss_usd
    for row in ctx.closed_today:
        pnl = row.get("realized_pnl")
        if pnl is not None and float(pnl) < 0:
            used += -float(pnl)
    for row in ctx.positions:
        if row.get("status") not in (None, "open"):
            continue
        entry = row.get("entry_price")
        stop = row.get("sl_price")
        volume = float(row.get("volume") or 0)
        if entry is None or stop is None:
            used += loss_cap
        else:
            used += risk_usd(float(entry), float(stop), volume, oz)
    return used


def horizon_cap(ctx: RiskContext) -> float:
    median = ctx.h1_median_range
    if median is None or median <= 0:
        return 15.0
    return max(6.0, 2.0 * median)


def pending_order_type(side: str, entry: float, price: float) -> str:
    if side == "buy":
        return "limit" if entry < price else "stop"
    return "limit" if entry > price else "stop"


def check_entry(decision: Decision, ctx: RiskContext) -> Check:
    settings = ctx.settings
    limits = limits_of(ctx)
    if decision.action == "none":
        return Check(False, "no_trade")
    if not settings.trading_enabled:
        return Check(False, "trading_disabled")
    if decision.side not in {"buy", "sell"}:
        return Check(False, "bad_side")
    if weekend_closed(ctx.now):
        return Check(False, "market_closed")
    if decision.action == "market" and not ctx.market_open:
        return Check(False, "market_closed")
    if decision.action == "pending" and decision.entry_price is None:
        return Check(False, "missing_entry")
    if decision.stop_loss is None or decision.take_profit is None:
        return Check(False, "missing_stop_or_target")

    force = ctx.trigger == "telegram_order"
    if not force and decision.confidence < limits.min_confidence:
        return Check(False, "low_confidence")

    entry = ctx.price if decision.action == "market" else float(decision.entry_price)  # type: ignore[arg-type]
    stop = float(decision.stop_loss)
    target = float(decision.take_profit)
    if decision.side == "buy":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    if risk <= 0 or reward <= 0:
        return Check(False, "stop_side")
    median = ctx.h1_median_range
    if median is not None and median > 0 and risk < median:
        return Check(False, "stop_too_tight")
    rr = reward / risk
    if rr < limits.min_rr:
        return Check(False, "rr")
    if limits.max_rr is not None and rr > limits.max_rr:
        return Check(False, "rr_high")

    if force and abs(target - entry) > horizon_cap(ctx):
        return Check(False, "horizon_too_far")

    open_count = sum(1 for row in ctx.positions if row.get("status") in (None, "open"))
    if open_count >= limits.max_open_positions:
        return Check(False, "max_positions")
    if daily_loss_used(ctx) >= limits.max_daily_loss_usd:
        return Check(False, "daily_loss")

    volume = resolve_volume(decision.volume, limits, ctx.equity, ctx.currency)
    if volume is None:
        return Check(False, "bad_volume")
    order_type = "market" if decision.action == "market" else pending_order_type(decision.side, entry, ctx.price)
    return Check(True, volume=volume, entry=entry, stop=stop, take_profit=target, order_type=order_type)


def check_manage(item: ManageItem, position: dict[str, Any] | None, ctx: RiskContext) -> ManageCheck:
    if not ctx.settings.trading_enabled:
        return ManageCheck(False, "trading_disabled", item)
    if position is None:
        return ManageCheck(False, "unknown_position", item)
    if item.action in {"trail", "partial_close"}:
        return ManageCheck(False, "not_enabled", item)
    if item.action == "leave":
        return ManageCheck(True, item=item, send=False)
    if item.action == "close":
        return ManageCheck(True, item=item, send=True)
    if item.action == "move_sl_to_breakeven":
        entry = position.get("entry_price")
        if entry is None:
            return ManageCheck(False, "missing_entry", item)
        new_sl = float(entry)
        if widens(str(position.get("side")), position.get("sl_price"), new_sl):
            return ManageCheck(False, "widen_stop", item)
        updated = item.model_copy(update={"new_sl": new_sl})
        return ManageCheck(True, item=updated, send=True)
    return ManageCheck(False, "bad_action", item)


def select_management(
    decision: Decision, positions: list[dict[str, Any]], ctx: RiskContext
) -> list[ManageCheck]:
    by_id = {str(row.get("position_id")): row for row in positions}
    seen: set[str] = set()
    chosen: list[ManageCheck] = []
    for item in decision.manage_existing:
        if item.position_id in seen and item.action != "leave":
            chosen.append(ManageCheck(False, "duplicate_action", item))
            continue
        if item.action != "leave":
            seen.add(item.position_id)
        chosen.append(check_manage(item, by_id.get(item.position_id), ctx))
    return chosen


def positions_after_closes(
    positions: list[dict[str, Any]], checks: list[ManageCheck]
) -> list[dict[str, Any]]:
    closed = {
        item.item.position_id
        for item in checks
        if item.ok and item.send and item.item is not None and item.item.action == "close"
    }
    return [row for row in positions if str(row.get("position_id")) not in closed]
