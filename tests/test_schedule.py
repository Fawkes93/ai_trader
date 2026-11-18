from ai_trader.scheduler import SATURDAY, SUNDAY, WEEKDAY


def test_asia_run_and_weekend_prep():
    assert (3, 0) in WEEKDAY
    assert len(SATURDAY) == 3
    assert len(SUNDAY) == 3
    assert (22, 15) in SUNDAY
