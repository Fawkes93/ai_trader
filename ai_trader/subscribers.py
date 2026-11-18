"""Accounts that turned the AI trader alert on in Factor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_trader.risk.checks import Limits


@dataclass
class Subscriber:
    user_id: str
    account_id: int
    telegram_chat_id: str | None = None
    max_concurrent: int = 1
    min_volume_lots: float = 0.01
    max_volume_lots: float = 0.01
    min_rr: float = 1.2
    max_rr: float | None = None
    daily_loss_cap_usd: float = 50

    def limits(self) -> Limits:
        return Limits(
            max_volume_lots=self.max_volume_lots,
            min_volume_lots=self.min_volume_lots,
            max_open_positions=self.max_concurrent,
            min_rr=self.min_rr,
            max_rr=self.max_rr,
            max_daily_loss_usd=self.daily_loss_cap_usd,
        )


def parse_subscriber(row: dict[str, Any]) -> Subscriber:
    max_rr = row.get("max_rr")
    chat = row.get("telegram_chat_id")
    return Subscriber(
        user_id=str(row.get("user_id") or ""),
        account_id=int(row["account_id"]),
        telegram_chat_id=None if chat in (None, "") else str(chat),
        max_concurrent=int(row.get("max_concurrent") or 1),
        min_volume_lots=float(row.get("min_volume_lots") or 0.01),
        max_volume_lots=float(row.get("max_volume_lots") or 0.01),
        min_rr=float(row.get("min_rr") or 1.2),
        max_rr=None if max_rr in (None, "") else float(max_rr),
        daily_loss_cap_usd=float(row.get("daily_loss_cap_usd") or 50),
    )


def default_subscriber() -> Subscriber:
    """Used by the stub client so a local cycle still has one book."""
    return Subscriber(user_id="stub", account_id=0)
