from datetime import datetime, timedelta, timezone

from ai_trader.config import Settings
from ai_trader.db.repo import SupabaseRepo


class FakeRest:
    def __init__(self) -> None:
        self.tables: list[str] = []

    def get(self, table, params):
        self.tables.append(table)
        if table == "candles":
            opened = datetime(2026, 10, 1, tzinfo=timezone.utc)
            return [
                {
                    "open_time": opened.isoformat(),
                    "open": 2300,
                    "high": 2302,
                    "low": 2298,
                    "close": 2301,
                }
            ]
        return [{"enabled": True, "kill_switch": False, "trading_enabled": True}]

    def upsert_ignore(self, table, rows, on_conflict):
        self.tables.append(table)


def _bar(open_time: str, close: float) -> dict:
    return {
        "open_time": open_time,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
    }


def test_cached_tail_includes_the_bar_that_just_opened():
    class TailRest:
        def __init__(self) -> None:
            self.n = 0

        def get(self, table, params):
            assert table == "candles"
            self.n += 1
            if self.n == 1:
                return [_bar("2026-10-26T15:45:00+00:00", 3991.38)]
            return [
                _bar("2026-10-26T15:45:00+00:00", 3991.38),
                _bar("2026-10-26T16:00:00+00:00", 4004.32),
            ]

    rest = TailRest()
    repo = SupabaseRepo(
        Settings(factor_user_id="user", factor_account_id="1"),
        rest=rest,
        candles_rest=rest,
    )
    since = datetime(2026, 10, 26, 15, 45, tzinfo=timezone.utc)
    first = repo.load_candles(since)
    second = repo.load_candles(since)
    assert first[-1].close == 3991.38
    assert [bar.close for bar in second[-2:]] == [3991.38, 4004.32]
    assert rest.n == 2


def test_candles_use_reaktor_and_gate_uses_factor():
    factor = FakeRest()
    reaktor = FakeRest()
    repo = SupabaseRepo(
        Settings(factor_user_id="user", factor_account_id="1"),
        rest=factor,
        candles_rest=reaktor,
    )
    bars = repo.load_candles(datetime.now(timezone.utc) - timedelta(days=1))
    repo.account_gate()
    repo.upsert_candles([{"open_time": "2026-10-01T00:00:00+00:00"}])
    repo.econ_events(
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 8, tzinfo=timezone.utc),
    )
    assert bars[0].close == 2301
    assert reaktor.tables == ["candles", "candles", "econ_events"]
    assert factor.tables == ["factor_user_alert_prefs", "factor_ctrader_connections"]
