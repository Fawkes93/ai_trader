"""Live XAU price. Closed 15-minute candles lag the print by up to 15 minutes."""

from __future__ import annotations

import httpx

from ai_trader.config import Settings

PRICE_URL = "https://api.twelvedata.com/price"


def latest_price(settings: Settings) -> float | None:
    """Twelve Data last price for the candle symbol. None when it cannot be read."""
    key = settings.twelvedata_api_key
    if not key:
        return None
    response = httpx.get(
        PRICE_URL,
        params={"symbol": settings.candle_symbol, "apikey": key},
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("status") == "error":
        raise RuntimeError(str(body.get("message") or "twelvedata price failed") if isinstance(body, dict) else "twelvedata price failed")
    price = float(body["price"])
    if price <= 0:
        raise RuntimeError("twelvedata price was not positive")
    return price
