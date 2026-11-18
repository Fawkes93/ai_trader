from datetime import datetime, timedelta, timezone

from ai_trader.config import Settings
from ai_trader.data.market import Bar
from ai_trader.data.sr import entry_setup
import httpx

from ai_trader.cycle import (
    _fill_missing_exits,
    _load_positions,
    _recent_trades,
    _refresh_sentiment,
    default_cot_fetch,
    run_cycle,
    run_weekly_review,
)
from ai_trader.db.repo import MemoryRepo
from ai_trader.factor.client import StubFactor
from ai_trader.models import Decision


def bars(count: int = 96) -> list[Bar]:
    start = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    rows = []
    price = 2300.0
    for index in range(count):
        opened = start + timedelta(minutes=15 * index)
        close = price + 0.25
        rows.append(Bar(opened, price, close + 0.3, price - 0.3, close))
        price = close
    return rows


def good_decision(price: float, **overrides) -> Decision:
    values = dict(
        action="market",
        side="buy",
        entry_price=None,
        stop_loss=round(price - 4, 2),
        take_profit=round(price + 8, 2),
        volume=0.01,
        confidence=80,
        reasoning="higher lows",
        alert_levels=[],
        manage_existing=[],
    )
    values.update(overrides)
    return Decision(**values)


def test_stub_cycle_logs_a_run_and_a_position():
    series = bars()
    price = series[-1].close
    repo = MemoryRepo()
    factor = StubFactor()
    calls = {"n": 0}

    def decide(settings, context, *, force, attempt):
        calls["n"] += 1
        return good_decision(price)

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=factor,
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    assert result.final_decision == "market"
    assert result.rejection_reason is None
    assert len(repo.runs) == 1
    assert repo.runs[0]["final_decision"] == "market"
    assert len(repo.positions) == 1
    assert repo.positions[0]["broker_position_id"] == "stub-1"
    assert factor.orders[0]["payload"]["label"] == "AI-GOLD-v1"
    assert factor.orders[0]["payload"]["entry_price"] == price
    assert calls["n"] == 1


def test_invalid_model_does_not_call_factor():
    repo = MemoryRepo()
    factor = StubFactor()

    def decide(settings, context, *, force, attempt):
        raise RuntimeError("not json")

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=factor,
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=bars(),
    )
    assert result.rejection_reason == "ai_invalid"
    assert factor.orders == []
    assert repo.positions == []
    assert repo.runs[0]["final_decision"] == "none"


def test_calendar_is_in_the_decision_context():
    series = bars()
    price = series[-1].close
    repo = MemoryRepo()
    repo.econ_rows = [
        {
            "event_time": "2026-10-21T18:00:00+00:00",
            "country": "USD",
            "impact": "High",
            "title": "FOMC Statement",
            "forecast": "",
            "previous": "",
        },
        {
            "event_time": "2026-10-21T12:30:00+00:00",
            "country": "USD",
            "impact": "Medium",
            "title": "Unemployment Claims",
            "forecast": "198K",
            "previous": "194K",
        },
    ]
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["calendar"] = context["calendar"]
        seen["note"] = context["calendar_note"]
        return good_decision(price)

    def browse(settings, events, now):
        assert [row["title"] for row in events] == ["FOMC Statement"]
        return "Statement expected to hold the funds rate."

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=StubFactor(),
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        calendar_fetch=browse,
        candles=series,
    )
    assert result.rejection_reason is None
    assert [row["title"] for row in seen["calendar"]] == ["Unemployment Claims", "FOMC Statement"]
    assert seen["note"] == "Statement expected to hold the funds rate."


def test_live_quote_is_the_price_and_the_market_entry():
    series = bars()
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["price"] = context["price"]
        seen["kind"] = context["price_kind"]
        return good_decision(context["price"])

    factor = StubFactor()
    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        quote_fetch=lambda settings: 4010.5,
        candles=series,
    )
    assert result.rejection_reason is None
    assert seen == {"price": 4010.5, "kind": "live"}
    assert result.price == 4010.5
    assert factor.orders[0]["payload"]["entry_price"] == 4010.5


def test_context_names_the_path_location_and_setup():
    series = bars()
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["path"] = context["path"]
        seen["location"] = context["location"]
        seen["setup"] = context["setup"]
        return good_decision(context["price"])

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=StubFactor(),
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    assert result.rejection_reason is None
    assert seen["path"] in {"up", "down", "mixed"}
    assert seen["location"] in {"at_support", "at_resistance", "middle", "unclear"}
    assert seen["setup"] == entry_setup(seen["path"], seen["location"])


def test_context_includes_balance_and_equity():
    series = bars()
    factor = StubFactor()
    factor.account_funds[0] = {"balance": 1000.0, "equity": 1004.5, "currency": "USD"}
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["accounts"] = context["accounts"]
        return good_decision(context["price"])

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    assert result.rejection_reason is None
    assert seen["accounts"] == [
        {
            "account_id": 0,
            "max_lots": 0.01,
            "min_lots": 0.01,
            "min_rr": 1.2,
            "max_concurrent": 1,
            "balance": 1000.0,
            "equity": 1004.5,
            "currency": "USD",
            "step_lots": 0.10,
        }
    ]


def test_context_includes_the_last_ten_cycles_oldest_first():
    series = bars()
    repo = MemoryRepo()
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for index in range(12):
        repo.insert_run(
            {
                "created_at": (start + timedelta(hours=index)).isoformat(),
                "trigger": "schedule" if index % 2 == 0 else "alert",
                "session": "london",
                "price": 4000 + index,
                "final_decision": "market" if index == 3 else "none",
                "rejection_reason": None,
                "ai_raw_output": {
                    "action": "market" if index == 3 else "none",
                    "side": "sell" if index == 3 else None,
                    "confidence": 70,
                    "stop_loss": 4010 if index == 3 else None,
                    "take_profit": 3980 if index == 3 else None,
                    "reasoning": ("bounce " * 80) if index == 11 else f"cycle {index}",
                },
                "subscriber_results": [{"placed": index == 3}],
            }
        )
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["recent"] = context["recent_cycles"]
        return good_decision(context["price"])

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=StubFactor(),
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    recent = seen["recent"]
    assert result.rejection_reason is None
    assert len(recent) == 10
    assert [row["price"] for row in recent] == list(range(4002, 4012))
    assert recent[0]["at"] < recent[-1]["at"]
    placed = next(row for row in recent if row["decision"] == "market")
    assert placed["placed"] is True
    assert placed["side"] == "sell"
    assert placed["stop"] == 4010
    assert len(recent[-1]["reasoning"]) <= 181
    assert recent[-1]["reasoning"].endswith("…")


def test_context_shows_stop_floor_and_closed_trade_r():
    series = bars()
    repo = MemoryRepo()
    repo.insert_position(
        {
            "status": "closed",
            "closed_at": "2026-11-03T09:23:00+00:00",
            "side": "sell",
            "entry_price": 3958,
            "sl_price": 3956.87,
            "tp_price": 3896,
            "volume_lots": 0.01,
            "realized_pnl": 47.35,
            "detail": {"initial_stop": 3984, "close_price": 3904},
        }
    )
    seen: dict = {}

    def decide(settings, context, *, force, attempt):
        seen["distance"] = context["min_stop_distance"]
        seen["trades"] = context["recent_trades"]
        return good_decision(context["price"])

    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=StubFactor(),
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    assert result.rejection_reason is None
    assert seen["distance"] > 0
    trade = seen["trades"][0]
    assert trade["result"] == "win"
    assert trade["stop"] == 3984
    assert trade["r"] == round(47.35 / (26 * 0.01 * 100), 2)


def test_stale_sentiment_does_not_block_a_trade():
    def explode(settings, now):
        raise RuntimeError("no x news")

    factor = StubFactor()
    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=("gold",)),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=lambda settings, context, force, attempt: good_decision(context["price"]),
        sentiment_fetch=explode,
        cot_fetch=lambda: None,
        calendar_fetch=lambda settings, events, now: "",
        candles=bars(),
    )
    assert result.rejection_reason is None
    assert factor.orders


def test_rejected_attempt_is_stored_for_the_weekly_review():
    repo = MemoryRepo()
    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=StubFactor(),
        now=datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=lambda settings, context, force, attempt: good_decision(
            context["price"], stop_loss=round(context["price"] - 0.2, 2), take_profit=round(context["price"] + 2, 2)
        ),
        cot_fetch=lambda: None,
        candles=bars(),
    )
    attempt = result.subscriber_results[0]["attempt"]
    assert attempt["rejection"] == "stop_too_tight"
    assert "stop_distance" in attempt
    assert "min_stop_distance" in attempt
    seen: dict = {}

    def ask(settings, payload):
        seen["runs"] = payload["runs"]
        return {"summary": "tight stops", "suggestions": []}

    run_weekly_review(
        settings=Settings(),
        repo=repo,
        now=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
        ask=ask,
    )
    assert seen["runs"][-1]["attempts"][0]["rejection"] == "stop_too_tight"


def test_broker_close_copies_pnl_and_a_later_cycle_fills_a_blank():
    repo = MemoryRepo()
    repo.insert_position(
        {
            "broker_position_id": "p1",
            "account_id": 0,
            "side": "sell",
            "volume_lots": 0.01,
            "entry_price": 3958,
            "sl_price": 3984,
            "status": "open",
            "detail": {"initial_stop": 3984},
        }
    )
    repo.outcomes["p1"] = {"net": 47.35, "close_price": 3904.0, "closed_at": "2026-11-03T09:23:00+00:00"}
    factor = StubFactor()
    factor.uses_broker_positions = True
    _load_positions(factor, repo, 3900, 100, now=datetime(2026, 11, 3, 10, tzinfo=timezone.utc), account_id=0)
    closed = repo.positions[0]
    assert closed["realized_pnl"] == 47.35
    assert closed["detail"]["close_price"] == 3904.0
    assert closed["detail"]["initial_stop"] == 3984

    repo.insert_position(
        {
            "broker_position_id": "p2",
            "status": "closed",
            "closed_at": "2026-11-03T21:37:00+00:00",
            "side": "sell",
            "entry_price": 3932,
            "volume_lots": 0.01,
            "realized_pnl": None,
            "detail": {"initial_stop": 3947.5},
        }
    )
    repo.outcomes["p2"] = {"net": 4.26, "close_price": 3928.1, "closed_at": "2026-11-03T21:37:00+00:00"}
    _fill_missing_exits(repo, datetime(2026, 11, 10, tzinfo=timezone.utc), [])
    assert repo.positions[1]["realized_pnl"] == 4.26
    trades = _recent_trades(repo, 100, [])
    assert trades[-1]["result"] == "win"
    assert trades[-1]["r"] == round(4.26 / (15.5 * 0.01 * 100), 2)


def test_unpaid_sentiment_keeps_the_last_bias_for_twelve_hours():
    repo = MemoryRepo()
    repo.insert_sentiment(
        {
            "bias": "down",
            "summary": "sellers",
            "confidence": 40,
            "created_at": "2026-11-10T00:00:00+00:00",
            "raw": {},
        }
    )
    calls = {"n": 0}

    def fetch(settings, now):
        calls["n"] += 1
        request = httpx.Request("POST", "https://api.x.ai/v1/responses")
        response = httpx.Response(402, request=request)
        raise httpx.HTTPStatusError("unpaid", request=request, response=response)

    now = datetime(2026, 11, 10, 2, tzinfo=timezone.utc)
    settings = Settings(x_handles=("gold",), sentiment_max_age_min=90)
    row = _refresh_sentiment(repo, settings, fetch, now, [])
    assert calls["n"] == 1
    assert row["bias"] == "down"
    _refresh_sentiment(repo, settings, fetch, now + timedelta(hours=1), [])
    assert calls["n"] == 1


def test_cot_fetch_sends_a_browser_user_agent(monkeypatch):
    seen = {}

    class Response:
        text = ""

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=None, headers=None):
        seen["url"] = url
        seen["headers"] = headers
        return Response()

    monkeypatch.setattr("ai_trader.cycle.httpx.get", fake_get)
    assert default_cot_fetch() is None
    assert "Mozilla" in seen["headers"]["User-Agent"]


def test_disabled_pref_skips_the_model():
    repo = MemoryRepo()
    factor = StubFactor()
    factor.subscribers = []

    def decide(settings, context, *, force, attempt):
        raise AssertionError("model should not run")

    result = run_cycle(
        settings=Settings(trading_enabled=True),
        repo=repo,
        factor=factor,
        now=datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=bars(),
    )
    assert result.rejection_reason == "no_subscribers"
    assert factor.orders == []
