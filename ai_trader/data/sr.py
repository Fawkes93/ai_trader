"""Swings, floor pivots, and clustered zones."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ai_trader.data.market import Bar


@dataclass
class Level:
    price: float
    source: str


@dataclass
class Zone:
    price: float
    sources: list[str] = field(default_factory=list)
    count: int = 1
    confluence: int = 1
    kind: str = "mid"
    distance: float = 0.0


def swings(bars: Sequence[Bar], source: str, wing: int = 2, limit: int = 24) -> list[Level]:
    found: list[Level] = []
    last = len(bars) - wing
    for index in range(wing, last):
        high = bars[index].high
        low = bars[index].low
        left_right = list(range(index - wing, index)) + list(range(index + 1, index + wing + 1))
        if all(high > bars[other].high for other in left_right):
            found.append(Level(high, source))
        if all(low < bars[other].low for other in left_right):
            found.append(Level(low, source))
    return found[-limit:]


def floor_pivots(bar: Bar, source: str) -> list[Level]:
    pivot = (bar.high + bar.low + bar.close) / 3
    span = bar.high - bar.low
    return [
        Level(pivot, source),
        Level(2 * pivot - bar.low, source),
        Level(2 * pivot - bar.high, source),
        Level(pivot + span, source),
        Level(pivot - span, source),
    ]


def cluster_levels(levels: Sequence[Level], width: float) -> list[Zone]:
    zones: list[Zone] = []
    for level in sorted(levels, key=lambda item: item.price):
        if zones and abs(level.price - zones[-1].price) <= width:
            zone = zones[-1]
            count = zone.count + 1
            zone.price = (zone.price * zone.count + level.price) / count
            zone.count = count
            zone.sources.append(level.source)
        else:
            zones.append(Zone(price=level.price, sources=[level.source], count=1))
    for zone in zones:
        zone.confluence = len(set(zone.sources))
    return zones


def annotate(zones: Sequence[Zone], price: float) -> list[Zone]:
    out: list[Zone] = []
    for zone in zones:
        kind = "support" if zone.price < price else "resistance" if zone.price > price else "mid"
        out.append(
            Zone(
                price=round(zone.price, 2),
                sources=list(zone.sources),
                count=zone.count,
                confluence=zone.confluence,
                kind=kind,
                distance=round(abs(zone.price - price), 2),
            )
        )
    return out


def nearest(zones: Sequence[Zone], price: float) -> tuple[Zone | None, Zone | None]:
    support = [zone for zone in zones if zone.price < price]
    resistance = [zone for zone in zones if zone.price > price]
    near_support = max(support, key=lambda zone: zone.price) if support else None
    near_resistance = min(resistance, key=lambda zone: zone.price) if resistance else None
    return near_support, near_resistance


def price_location(
    price: float,
    support: float | None,
    resistance: float | None,
) -> str:
    """Where price sits in the span from the nearest support to the nearest resistance.

    The outer thirds are at that level. The inner third is the middle.
    A price with both levels is always between them. That fact is not the middle.
    """
    if support is None or resistance is None:
        return "unclear"
    span = resistance - support
    if span <= 0:
        return "unclear"
    pos = (price - support) / span
    if pos >= 2 / 3:
        return "at_resistance"
    if pos <= 1 / 3:
        return "at_support"
    return "middle"


def entry_setup(path: str, location: str) -> str:
    """Sell a down path into resistance. Buy an up path off support."""
    if path == "down" and location == "at_resistance":
        return "sell"
    if path == "up" and location == "at_support":
        return "buy"
    return "none"


def closest(zones: Sequence[Zone], price: float, limit: int) -> list[Zone]:
    return sorted(zones, key=lambda zone: abs(zone.price - price))[:limit]


def build_zones(
    *,
    h1: Sequence[Bar],
    h4: Sequence[Bar],
    daily: Sequence[Bar],
    weekly: Sequence[Bar],
    price: float,
    width: float,
) -> list[Zone]:
    levels: list[Level] = []
    levels.extend(swings(h1, "h1"))
    levels.extend(swings(h4, "h4"))
    levels.extend(swings(daily, "d1"))
    if len(daily) >= 2:
        levels.extend(floor_pivots(daily[-2], "daily_pivot"))
    elif len(daily) == 1:
        levels.extend(floor_pivots(daily[-1], "daily_pivot"))
    if len(weekly) >= 2:
        levels.extend(floor_pivots(weekly[-2], "weekly_pivot"))
    elif len(weekly) == 1:
        levels.extend(floor_pivots(weekly[-1], "weekly_pivot"))
    return annotate(cluster_levels(levels, width), price)
