"""Weekly CFTC disaggregated gold futures (COMEX code 088691)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from typing import Any

CFTC_FUT_DISAGG_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
GOLD_MARKET_CODE = "088691"

# Column positions in the header-less futures-only disaggregated file.
COL_NAME = 0
COL_REPORT_DATE = 2
COL_MARKET_CODE = 3
COL_OPEN_INTEREST = 7
COL_PROD_LONG = 8
COL_PROD_SHORT = 9
COL_MONEY_LONG = 13
COL_MONEY_SHORT = 14


def _num(value: str) -> int | None:
    text = (value or "").strip().replace(",", "")
    if not text or text == ".":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_gold_report(text: str) -> dict[str, Any] | None:
    """Return the GOLD row, ignoring MICRO GOLD."""
    reader = csv.reader(io.StringIO(text))
    chosen: dict[str, Any] | None = None
    for row in reader:
        if len(row) <= COL_MONEY_SHORT:
            continue
        code = row[COL_MARKET_CODE].strip()
        name = row[COL_NAME].strip().upper()
        if code != GOLD_MARKET_CODE or not name.startswith("GOLD"):
            continue
        report_date = row[COL_REPORT_DATE].strip()
        prod_long = _num(row[COL_PROD_LONG])
        prod_short = _num(row[COL_PROD_SHORT])
        money_long = _num(row[COL_MONEY_LONG])
        money_short = _num(row[COL_MONEY_SHORT])
        chosen = {
            "report_date": report_date,
            "commercial_net": None if prod_long is None or prod_short is None else prod_long - prod_short,
            "managed_money_net": None
            if money_long is None or money_short is None
            else money_long - money_short,
            "open_interest": _num(row[COL_OPEN_INTEREST]),
            "raw": {
                "name": row[COL_NAME].strip(),
                "market_code": code,
                "prod_long": prod_long,
                "prod_short": prod_short,
                "money_long": money_long,
                "money_short": money_short,
            },
        }
    return chosen


def report_age_days(row: dict[str, Any] | None, now: datetime) -> int | None:
    if not row or not row.get("report_date"):
        return None
    try:
        reported = date.fromisoformat(str(row["report_date"])[:10])
    except ValueError:
        return None
    return (now.astimezone(timezone.utc).date() - reported).days
