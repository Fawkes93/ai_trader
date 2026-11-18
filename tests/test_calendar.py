from datetime import datetime, timezone

from ai_trader.config import Settings
from ai_trader.cycle import _load_calendar
from ai_trader.data.calendar import lookup_targets, select_events

NOW = datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)


def _row(**overrides):
    values = dict(
        event_time="2026-10-21T18:00:00+00:00",
        country="USD",
        impact="High",
        title="Federal Funds Rate",
        forecast="4.00%",
        previous="4.00%",
    )
    values.update(overrides)
    return values


def test_calendar_keeps_high_impact_and_medium_usd_only():
    rows = [
        _row(),
        _row(event_time="2026-10-21T12:30:00+00:00", impact="Medium", title="Unemployment Claims", forecast="198K", previous="194K"),
        _row(country="NZD", impact="Low", title="Noise"),
        _row(country="EUR", impact="Medium", title="German CPI"),
        _row(event_time="2026-10-30T18:00:00+00:00", title="Too far"),
        _row(event_time="2026-10-20T00:00:00+00:00", title="Too old"),
    ]
    events = select_events(rows, NOW)
    assert [row["title"] for row in events] == ["Unemployment Claims", "Federal Funds Rate"]
    assert events[1]["forecast"] == "4.00%"


def test_blank_forecast_and_a_release_already_due_need_a_lookup():
    future_blank = select_events([_row(title="FOMC Statement", forecast="")], NOW)
    filled = select_events([_row()], NOW)
    released = select_events([_row(event_time="2026-10-21T12:00:00+00:00")], NOW)
    assert [row["title"] for row in lookup_targets(future_blank, NOW)] == ["FOMC Statement"]
    assert lookup_targets(filled, NOW) == []
    assert [row["title"] for row in lookup_targets(released, NOW)] == ["Federal Funds Rate"]


def test_calendar_failure_does_not_block_the_cycle():
    class Boom:
        def econ_events(self, start, end):
            raise RuntimeError("down")

    notes: list[str] = []
    events, note = _load_calendar(Boom(), Settings(), lambda *_a, **_k: "unused", NOW, notes)
    assert events == []
    assert note is None
    assert notes[0].startswith("calendar_unavailable:")


def test_browse_failure_keeps_the_table_rows():
    class Table:
        def econ_events(self, start, end):
            return [_row(title="FOMC Statement", forecast="")]

    def boom(*_a, **_k):
        raise RuntimeError("search down")

    notes: list[str] = []
    events, note = _load_calendar(Table(), Settings(), boom, NOW, notes)
    assert [row["title"] for row in events] == ["FOMC Statement"]
    assert note is None
    assert notes[0].startswith("calendar_browse_failed:")
