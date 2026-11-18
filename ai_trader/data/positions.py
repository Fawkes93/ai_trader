"""Position math for the AI book. One standard lot of XAU is 100 ounces."""

from __future__ import annotations

from typing import Any


def risk_usd(entry: float | None, stop: float | None, volume: float, oz_per_lot: float) -> float:
    if entry is None or stop is None or volume <= 0:
        return 0.0
    return abs(entry - stop) * volume * oz_per_lot


def pnl_usd(
    side: str,
    entry: float | None,
    exit_price: float | None,
    volume: float,
    oz_per_lot: float,
) -> float | None:
    if entry is None or exit_price is None:
        return None
    sign = 1.0 if side == "buy" else -1.0
    return (exit_price - entry) * sign * volume * oz_per_lot


def widens(side: str, current_sl: float | None, new_sl: float) -> bool:
    if current_sl is None:
        return False
    if side == "buy":
        return new_sl < current_sl - 1e-9
    return new_sl > current_sl + 1e-9


def normalize_position(row: dict[str, Any]) -> dict[str, Any]:
    side = str(row.get("side") or "").lower()
    if side in {"buy", "long"}:
        side = "buy"
    elif side in {"sell", "short"}:
        side = "sell"
    position_id = row.get("broker_position_id") or row.get("position_id") or row.get("id")
    return {
        "id": str(row.get("id") or position_id or ""),
        "position_id": str(position_id or ""),
        "side": side,
        "volume": float(row.get("volume_lots") or row.get("volume") or 0),
        "entry_price": _float(row.get("entry_price")),
        "sl_price": _float(row.get("sl_price") if "sl_price" in row else row.get("stop_loss")),
        "tp_price": _float(row.get("tp_price") if "tp_price" in row else row.get("take_profit")),
        "unrealized_pnl": _float(row.get("unrealized_pnl")),
        "opened_at": row.get("opened_at"),
        "status": row.get("status") or "open",
        "realized_pnl": _float(row.get("realized_pnl")),
        "closed_at": row.get("closed_at"),
        "account_id": row.get("account_id"),
        "detail": row.get("detail") if isinstance(row.get("detail"), dict) else {},
    }


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
