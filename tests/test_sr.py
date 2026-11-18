from datetime import datetime, timedelta, timezone

from ai_trader.data.market import Bar, timeframe_path
from ai_trader.data.sr import build_zones, entry_setup, nearest, price_location


def _bar(index: int, low: float, high: float) -> Bar:
    opened = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened, (low + high) / 2, high, low, (low + high) / 2)


def test_clustered_zone_and_nearest_levels():
    # Swing low near 2300 on two timeframes, swing high near 2330 on H1.
    h1 = [
        _bar(0, 2310, 2314),
        _bar(1, 2306, 2310),
        _bar(2, 2302, 2306),
        _bar(3, 2299, 2303),
        _bar(4, 2304, 2308),
        _bar(5, 2308, 2312),
        _bar(6, 2316, 2322),
        _bar(7, 2324, 2332),
        _bar(8, 2320, 2326),
        _bar(9, 2314, 2318),
    ]
    h4 = [
        _bar(0, 2312, 2320),
        _bar(1, 2304, 2312),
        _bar(2, 2300, 2306),
        _bar(3, 2305, 2314),
        _bar(4, 2310, 2318),
    ]
    daily = h1[-5:]
    zones = build_zones(h1=h1, h4=h4, daily=daily, weekly=[], price=2310, width=3)
    support, resistance = nearest(zones, 2310)
    assert support is not None and support.price < 2310
    assert resistance is not None and resistance.price > 2310
    assert any(zone.confluence >= 2 for zone in zones)


def test_path_follows_monthly_and_weekly():
    assert timeframe_path("down", "down") == "down"
    assert timeframe_path("up", "up") == "up"
    assert timeframe_path("down", "up") == "mixed"
    assert timeframe_path("flat", "down") == "mixed"
    assert timeframe_path("insufficient", "down") == "mixed"


def test_price_just_under_resistance_is_a_short_not_the_middle():
    # 4017.35 under 4020.62, with support at 4005.45. H1 down does not cancel it.
    assert price_location(4017.35, 4005.45, 4020.62) == "at_resistance"
    assert entry_setup("down", "at_resistance") == "sell"
    assert price_location(4013.04, 4005.45, 4020.62) == "middle"
    assert entry_setup("down", "middle") == "none"
    assert entry_setup("down", "at_support") == "none"
    assert price_location(4008.0, 4005.45, 4020.62) == "at_support"
    assert entry_setup("up", "at_support") == "buy"
    assert price_location(4017.35, None, 4020.62) == "unclear"
