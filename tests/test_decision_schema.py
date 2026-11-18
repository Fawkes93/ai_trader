import pytest
from pydantic import ValidationError

from ai_trader.models import Decision


def _payload(**overrides):
    base = {
        "action": "market",
        "side": "buy",
        "entry_price": None,
        "stop_loss": 2300,
        "take_profit": 2320,
        "volume": 0.01,
        "confidence": 70,
        "reasoning": "trend up",
        "alert_levels": [{"price": 2298, "type": "support"}],
        "manage_existing": [],
    }
    base.update(overrides)
    return base


def test_accepts_a_trade_decision():
    decision = Decision.model_validate(_payload())
    assert decision.action == "market"
    assert decision.alert_levels[0].price == 2298


def test_fraction_confidence_becomes_a_percent():
    assert Decision.model_validate(_payload(confidence=0.62)).confidence == 62
    assert Decision.model_validate(_payload(confidence=1)).confidence == 100
    assert Decision.model_validate(_payload(confidence=0)).confidence == 0
    assert Decision.model_validate(_payload(confidence=32)).confidence == 32


def test_rejects_bad_action_and_confidence():
    with pytest.raises(ValidationError):
        Decision.model_validate(_payload(action="buy"))
    with pytest.raises(ValidationError):
        Decision.model_validate(_payload(confidence=101))
    with pytest.raises(ValidationError):
        Decision.model_validate(_payload(confidence=-1))
