from datetime import datetime, timezone

from ai_trader.config import Settings
from ai_trader.factor.client import StubFactor
from ai_trader.halt import apply_saved_halt, resume_trading, stop_trading
from ai_trader.models import Decision
from ai_trader.risk.checks import RiskContext, check_entry

OPEN = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _factor() -> StubFactor:
    factor = StubFactor()
    factor.subscribers = [
        {"account_id": 48137878},
        {"account_id": 48769858},
    ]
    factor.positions = [
        {"position_id": "ai-demo", "label": "AI-GOLD-v1", "account_id": 48137878},
        {"position_id": "manan-live", "label": "MANAN", "account_id": 48769858},
        {"position_id": "reaktor", "label": "factor", "account_id": 48137878},
    ]
    return factor


def test_stop_closes_ai_gold_and_leaves_other_labels(tmp_path):
    settings = Settings(trading_enabled=True, label="AI-GOLD-v1")
    factor = _factor()
    text = stop_trading(settings, factor, root=tmp_path)
    closed = {call["payload"]["position_id"] for call in factor.managed}
    assert closed == {"ai-demo"}
    assert {call["payload"]["label"] for call in factor.managed} == {"AI-GOLD-v1"}
    assert settings.trading_enabled is False
    assert (tmp_path / ".trading_halt").exists()
    assert "trading off" in text
    assert "ai-demo closed" in text
    assert "manan-live" not in text

    fresh = Settings(trading_enabled=True)
    apply_saved_halt(fresh, tmp_path)
    assert fresh.trading_enabled is False

    resumed = resume_trading(settings, tmp_path)
    assert resumed == "trading on"
    assert settings.trading_enabled is True
    assert not (tmp_path / ".trading_halt").exists()


def test_halt_blocks_a_new_ai_entry():
    settings = Settings(trading_enabled=False, label="AI-GOLD-v1")
    blocked = check_entry(
        Decision(
            action="market",
            side="buy",
            stop_loss=2290,
            take_profit=2320,
            volume=0.01,
            confidence=80,
            reasoning="up",
        ),
        RiskContext(
            settings=settings,
            now=OPEN,
            price=2300,
            positions=[],
            closed_today=[],
            trigger="schedule",
        ),
    )
    assert blocked.reason == "trading_disabled"
