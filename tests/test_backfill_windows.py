from datetime import date, timedelta

from ai_trader.backfill import windows


def test_year_is_split_into_windows_of_at_most_21_days():
    end = date(2026, 10, 24)
    chunks = windows(end, 370, 21)
    assert chunks
    assert chunks[0][0] == end - timedelta(days=370)
    assert chunks[-1][1] == end
    for start, stop in chunks:
        assert 0 < (stop - start).days <= 21
    for (_, stop), (start, _) in zip(chunks, chunks[1:]):
        assert stop == start
