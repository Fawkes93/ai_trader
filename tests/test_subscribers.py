from datetime import datetime, timezone

from ai_trader.config import Settings
from ai_trader.cycle import _load_subscribers, run_cycle
from ai_trader.data.market import Bar
from ai_trader.db.repo import MemoryRepo
from ai_trader.factor.client import StubFactor
from ai_trader.models import Decision
from datetime import timedelta


def _bars():
    start = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rows = []
    price = 2300.0
    for index in range(96):
        close = price + 0.25
        rows.append(
            Bar(start + timedelta(minutes=15 * index), price, close + 0.3, price - 0.3, close)
        )
        price = close
    return rows


def _decision(price, volume=1.0):
    return Decision(
        action="market",
        side="buy",
        stop_loss=round(price - 4, 2),
        take_profit=round(price + 8, 2),
        volume=volume,
        confidence=80,
        reasoning="up",
    )


def _sub(account_id, max_lots, loss=50):
    return {
        "user_id": f"user-{account_id}",
        "account_id": account_id,
        "telegram_chat_id": None,
        "max_concurrent": 1,
        "min_volume_lots": 0.01,
        "max_volume_lots": max_lots,
        "min_rr": 1.5,
        "max_rr": None,
        "daily_loss_cap_usd": loss,
    }


def test_each_account_clamps_its_own_size():
    series = _bars()
    price = series[-1].close
    factor = StubFactor()
    factor.subscribers = [_sub(11, 0.01), _sub(22, 0.10)]
    result = run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, tzinfo=timezone.utc),
        trigger="schedule",
        decide=lambda *a, **k: _decision(price),
        cot_fetch=lambda: None,
        candles=series,
    )
    sizes = sorted(call["payload"]["volume"] for call in factor.orders)
    assert sizes == [0.01, 0.10]
    assert {call["payload"]["account_id"] for call in factor.orders} == {11, 22}
    assert result.final_decision == "market"


def test_step_volume_follows_each_account_equity():
    series = _bars()
    price = series[-1].close
    factor = StubFactor()
    factor.subscribers = [_sub(11, 0.10), _sub(22, 0.10)]
    factor.account_funds[11] = {"balance": 250.0, "equity": 250.0, "currency": "USD"}
    factor.account_funds[22] = {"balance": 180.0, "equity": 180.0, "currency": "USD"}
    seen: dict = {}

    def decide(_settings, context, **_k):
        seen["steps"] = {row["account_id"]: row["step_lots"] for row in context["accounts"]}
        return _decision(price, volume=0.05)

    run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, tzinfo=timezone.utc),
        trigger="schedule",
        decide=decide,
        cot_fetch=lambda: None,
        candles=series,
    )
    sizes = {call["payload"]["account_id"]: call["payload"]["volume"] for call in factor.orders}
    assert seen["steps"] == {11: 0.02, 22: 0.01}
    assert sizes == {11: 0.02, 22: 0.01}


def test_smaller_model_lot_beats_the_step():
    series = _bars()
    price = series[-1].close
    factor = StubFactor()
    factor.subscribers = [_sub(11, 0.10)]
    factor.account_funds[11] = {"balance": 400.0, "equity": 400.0, "currency": "USD"}
    run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, tzinfo=timezone.utc),
        trigger="schedule",
        decide=lambda *a, **k: _decision(price, volume=0.01),
        cot_fetch=lambda: None,
        candles=series,
    )
    assert [call["payload"]["volume"] for call in factor.orders] == [0.01]


def test_daily_loss_skips_one_account_only():
    series = _bars()
    price = series[-1].close
    repo = MemoryRepo()
    repo.positions.append(
        {
            "account_id": 11,
            "status": "closed",
            "closed_at": "2026-10-21T12:00:00+00:00",
            "realized_pnl": -50,
            "side": "buy",
            "volume_lots": 0.01,
        }
    )
    factor = StubFactor()
    factor.subscribers = [_sub(11, 0.01, loss=50), _sub(22, 0.01, loss=50)]
    run_cycle(
        settings=Settings(trading_enabled=True, x_handles=()),
        repo=repo,
        factor=factor,
        now=datetime(2026, 10, 21, 13, tzinfo=timezone.utc),
        trigger="schedule",
        decide=lambda *a, **k: _decision(price, volume=0.01),
        cot_fetch=lambda: None,
        candles=series,
    )
    assert [call["payload"]["account_id"] for call in factor.orders] == [22]


def test_no_subscribers_skips_the_model():
    factor = StubFactor()
    factor.subscribers = []
    called = {"n": 0}

    def decide(*_a, **_k):
        called["n"] += 1
        raise AssertionError("model")

    result = run_cycle(
        settings=Settings(),
        repo=MemoryRepo(),
        factor=factor,
        now=datetime(2026, 10, 21, 13, tzinfo=timezone.utc),
        decide=decide,
        cot_fetch=lambda: None,
        candles=_bars(),
    )
    assert result.rejection_reason == "no_subscribers"
    assert called["n"] == 0
    assert factor.orders == []


class _FlakySubscribers:
    def __init__(self, fail_times: int, rows: list[dict] | None = None) -> None:
        self.fail_times = fail_times
        self.rows = rows if rows is not None else []
        self.calls = 0

    def list_subscribers(self) -> list[dict]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise OSError(8, "nodename nor servname provided, or not known")
        return self.rows


def test_subscriber_query_retries_until_the_list_returns():
    factor = _FlakySubscribers(2, [_sub(11, 0.01)])
    notes: list[str] = []
    found = _load_subscribers(factor, None, notes, pause=lambda _sec: None)
    assert factor.calls == 3
    assert [item.account_id for item in found] == [11]
    assert notes == ["subscribers_retry:3"]


def test_subscriber_query_stops_after_three_failures():
    factor = _FlakySubscribers(5, [_sub(11, 0.01)])
    notes: list[str] = []
    found = _load_subscribers(factor, None, notes, pause=lambda _sec: None)
    assert factor.calls == 3
    assert found == []
    assert len(notes) == 1
    assert notes[0].startswith("subscribers_unavailable:")


def test_order_chat_uses_the_account_when_the_link_is_a_different_chat():
    rows = [_sub(48769858, 0.01)]
    rows[0]["telegram_chat_id"] = 1828958308
    factor = _FlakySubscribers(0, rows)
    notes: list[str] = []
    found = _load_subscribers(factor, "1288151831", notes, pause=lambda _sec: None)
    assert [item.account_id for item in found] == [48769858]
    assert notes == ["order_chat_unlinked"]


def test_order_chat_stays_on_the_linked_account_when_several_match():
    first = _sub(11, 0.01)
    second = _sub(22, 0.01)
    first["telegram_chat_id"] = "100"
    second["telegram_chat_id"] = "200"
    factor = _FlakySubscribers(0, [first, second])
    notes: list[str] = []
    found = _load_subscribers(factor, "200", notes, pause=lambda _sec: None)
    assert [item.account_id for item in found] == [22]
    assert notes == []


def test_empty_subscriber_list_is_one_query():
    factor = _FlakySubscribers(0, [])
    notes: list[str] = []
    found = _load_subscribers(factor, None, notes, pause=lambda _sec: None)
    assert factor.calls == 1
    assert found == []
    assert notes == []
