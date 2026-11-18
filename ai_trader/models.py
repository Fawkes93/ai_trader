"""Decision and order shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertLevel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    price: float
    type: Literal["support", "resistance", "custom"]


class ManageItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    position_id: str
    action: Literal["leave", "trail", "move_sl_to_breakeven", "partial_close", "close"]
    new_sl: float | None = None
    new_tp: float | None = None
    close_percent: float | None = None


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["none", "market", "pending"]
    side: Literal["buy", "sell"] | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    volume: float | None = None
    confidence: float = Field(ge=0, le=100)
    reasoning: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_scale(cls, value: object) -> object:
        """A 0–1 score is a fraction. 0.62 means 62. The floor stays 60."""
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
        if 0 < number <= 1:
            return round(number * 100, 2)
        return number
    alert_levels: list[AlertLevel] = Field(default_factory=list)
    manage_existing: list[ManageItem] = Field(default_factory=list)
