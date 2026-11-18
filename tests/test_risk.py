from datetime import datetime, timezone

from ai_trader.config import Settings
from ai_trader.models import Decision, ManageItem
from ai_trader.risk.checks import Limits, RiskContext, check_entry, check_manage, equity_step_lots

WEDNESDAY_OVERLAP = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)
WEDNESDAY_ASIA = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc)
SUNDAY_PREP = datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
SUNDAY_OPEN = datetime(2026, 10, 25, 22, 15, tzinfo=timezone.utc)
FRIDAY_CLOSE = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
DAILY_BREAK = datetime(2026, 10, 21, 21, 30, tzinfo=timezone.utc)


def settings(**overrides) -> Settings:
    base = dict(
        trading_enabled=True,
        max_volume_lots=0.01,
        max_open_positions=1,
        min_rr=1.5,
        min_confidence=60,
        max_daily_loss_usd=50,
        oz_per_lot=100,
    )
    base.update(overrides)
    return Settings(**base)


def ctx(now, **overrides) -> RiskContext:
    values = dict(
        settings=settings(),
        now=now,
        price=2300.0,
        positions=[],
        closed_today=[],
        trigger="schedule",
        h1_median_range=4.0,
        market_open=None,
    )
    values.update(overrides)
    return RiskContext(**values)


def trade(**overrides) -> Decision:
    values = dict(
        action="market",
        side="buy",
        entry_price=None,
        stop_loss=2296.0,
        take_profit=2308.0,
        volume=0.01,
        confidence=80,
        reasoning="up",
    )
    values.update(overrides)
    return Decision(**values)


def test_defaults_are_live_http():
    cfg = Settings()
    assert cfg.factor_mode == "http"
    assert cfg.trading_enabled is True
    assert cfg.min_rr == 1.2


def test_stop_inside_the_hour_is_rejected():
    wide = ctx(WEDNESDAY_OVERLAP, h1_median_range=10)
    tight = check_entry(trade(), wide)
    assert tight.reason == "stop_too_tight"
    far = check_entry(trade(stop_loss=2288, take_profit=2320), wide)
    assert far.ok


def test_reward_floor_accepts_1_2():
    context = ctx(WEDNESDAY_OVERLAP, settings=settings(min_rr=1.2))
    assert check_entry(trade(take_profit=2304.8), context).ok
    short = check_entry(trade(take_profit=2304.4), context)
    assert short.reason == "rr"


def test_rr_session_and_volume():
    bad_rr = check_entry(trade(take_profit=2302), ctx(WEDNESDAY_OVERLAP))
    assert bad_rr.reason == "rr"

    asia = check_entry(trade(), ctx(WEDNESDAY_ASIA))
    assert asia.ok
    assert asia.order_type == "market"

    clamped = check_entry(trade(volume=1.0), ctx(WEDNESDAY_OVERLAP))
    assert clamped.ok
    assert clamped.volume == 0.01


def test_equity_step_unless_the_model_is_smaller():
    assert equity_step_lots(150) == 0.01
    assert equity_step_lots(199.99) == 0.01
    assert equity_step_lots(200) == 0.02
    assert equity_step_lots(300) == 0.03
    assert equity_step_lots(50) == 0.01
    assert equity_step_lots(None) is None
    assert equity_step_lots(250, "EUR") == 0.02
    assert equity_step_lots(500, "GBP") is None

    wide = Limits(min_volume_lots=0.01, max_volume_lots=1.0, min_rr=1.5)
    at_300 = ctx(WEDNESDAY_OVERLAP, limits=wide, equity=300, currency="USD")
    assert check_entry(trade(volume=0.05), at_300).volume == 0.03
    assert check_entry(trade(volume=0.01), at_300).volume == 0.01
    assert check_entry(trade(volume=None), at_300).volume == 0.03
    at_150 = ctx(WEDNESDAY_OVERLAP, limits=wide, equity=150, currency="USD")
    assert check_entry(trade(volume=None), at_150).volume == 0.01
    at_eur = ctx(WEDNESDAY_OVERLAP, limits=wide, equity=300, currency="EUR")
    assert check_entry(trade(volume=None), at_eur).volume == 0.03
    assert check_entry(trade(volume=0.01), at_eur).volume == 0.01
    capped = ctx(
        WEDNESDAY_OVERLAP,
        limits=Limits(min_volume_lots=0.01, max_volume_lots=0.02, min_rr=1.5),
        equity=500,
        currency="USD",
    )
    assert check_entry(trade(volume=None), capped).volume == 0.02

    low = check_entry(trade(confidence=10), ctx(WEDNESDAY_OVERLAP))
    assert low.reason == "low_confidence"
    scaled = check_entry(trade(confidence=0.62), ctx(WEDNESDAY_OVERLAP))
    assert scaled.ok
    still_low = check_entry(trade(confidence=0.5), ctx(WEDNESDAY_OVERLAP))
    assert still_low.reason == "low_confidence"


def _pending() -> Decision:
    return trade(action="pending", entry_price=2290, stop_loss=2284, take_profit=2302)


def test_weekend_rejects_market_and_pending():
    assert check_entry(trade(), ctx(SATURDAY)).reason == "market_closed"
    assert check_entry(_pending(), ctx(SATURDAY)).reason == "market_closed"
    assert check_entry(_pending(), ctx(SUNDAY_PREP)).reason == "market_closed"
    assert check_entry(_pending(), ctx(FRIDAY_CLOSE)).reason == "market_closed"
    opened = check_entry(_pending(), ctx(SUNDAY_OPEN))
    assert opened.ok
    assert opened.order_type == "limit"
    assert check_entry(trade(), ctx(DAILY_BREAK)).reason == "market_closed"
    daily_pending = check_entry(_pending(), ctx(DAILY_BREAK))
    assert daily_pending.ok
    assert daily_pending.order_type == "limit"


def test_position_cap_daily_loss_and_kill_is_not_here():
    second = check_entry(
        trade(),
        ctx(WEDNESDAY_OVERLAP, positions=[{"position_id": "1", "status": "open", "entry_price": 1, "sl_price": 1, "volume": 0.01}]),
    )
    assert second.reason == "max_positions"
    loss = check_entry(
        trade(),
        ctx(WEDNESDAY_OVERLAP, closed_today=[{"realized_pnl": -50}]),
    )
    assert loss.reason == "daily_loss"


def test_order_command_horizon_and_confidence():
    forced = ctx(WEDNESDAY_OVERLAP, trigger="telegram_order")
    low_conf = check_entry(
        trade(action="pending", entry_price=2290, stop_loss=2286, take_profit=2296, confidence=10),
        forced,
    )
    assert low_conf.ok
    far = check_entry(
        trade(action="pending", entry_price=2290, stop_loss=2280, take_profit=2330, confidence=10),
        forced,
    )
    assert far.reason == "horizon_too_far"
    # Same far target is allowed on a normal cycle. /order is the short-horizon path.
    normal = check_entry(
        trade(action="pending", entry_price=2290, stop_loss=2280, take_profit=2330, confidence=80),
        ctx(WEDNESDAY_OVERLAP),
    )
    assert normal.ok


def test_manage_rules():
    position = {"position_id": "9", "side": "buy", "entry_price": 2300.0, "sl_price": 2290.0, "status": "open"}
    context = ctx(WEDNESDAY_OVERLAP, positions=[position])
    trail = check_manage(ManageItem(position_id="9", action="trail"), position, context)
    assert trail.reason == "not_enabled"
    be = check_manage(ManageItem(position_id="9", action="move_sl_to_breakeven"), position, context)
    assert be.ok and be.item is not None and be.item.new_sl == 2300
    widened = dict(position)
    widened["sl_price"] = 2305
    blocked = check_manage(
        ManageItem(position_id="9", action="move_sl_to_breakeven"),
        widened,
        context,
    )
    assert blocked.reason == "widen_stop"
    missing = check_manage(ManageItem(position_id="nope", action="close"), None, context)
    assert missing.reason == "unknown_position"
