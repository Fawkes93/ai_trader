from ai_trader.cycle import CycleResult, _notify_result
from ai_trader.notify.messages import format_chat_notice, format_cycle


def _row(account: int, **overrides) -> dict:
    values = {
        "telegram_chat_id": "100",
        "account_id": account,
        "final_decision": "none",
        "rejection_reason": "low_confidence",
        "message": f"account {account}",
        "placed": False,
    }
    values.update(overrides)
    return values


def _result(rows: list[dict]) -> CycleResult:
    return CycleResult(
        trigger="alert",
        session="asia",
        price=3992.1,
        final_decision="none",
        reasoning="timeframes agree, confidence short",
        subscriber_results=rows,
    )


def test_order_reply_includes_confidence():
    text = format_cycle(
        CycleResult(
            trigger="telegram_order",
            session="london",
            price=4100,
            final_decision="market",
            confidence=72,
            reasoning="sell the resistance",
        )
    )
    assert "confidence 72" in text
    assert "decision market" in text


def test_sent_order_includes_entry_stop_target_and_confidence():
    payload = {
        "side": "sell",
        "entry_price": 4102.5,
        "stop_loss": 4114,
        "take_profit": 4088,
        "volume": 0.02,
    }
    result = CycleResult(
        trigger="schedule",
        session="london",
        price=4102.5,
        final_decision="market",
        confidence=74,
        order_payload=payload,
    )
    text = format_cycle(result)
    assert "sell entry 4102.5 sl 4114 tp 4088" in text
    assert "confidence 74" in text

    note = format_chat_notice(
        result,
        [_row(22, final_decision="market", rejection_reason=None, placed=True, order_payload=payload)],
    )
    assert "sell entry 4102.5 sl 4114 tp 4088" in note
    assert "confidence 74" in note
    assert "volume 0.02" in note


def test_scheduled_reply_leaves_confidence_out():
    text = format_cycle(
        CycleResult(
            trigger="schedule",
            session="london",
            price=4100,
            final_decision="none",
            confidence=42,
        )
    )
    assert "confidence" not in text


def test_same_chat_and_outcome_sends_one_note():
    sent: list[tuple] = []

    def notify(text: str, chat: str | None = None) -> None:
        sent.append((chat, text))

    _notify_result(_result([_row(11), _row(22)]), notify)
    assert len(sent) == 1
    assert sent[0][0] == "100"
    text = sent[0][1]
    assert text.count("timeframes agree") == 1
    assert "account" not in text
    assert "rejected low_confidence" in text


def test_different_outcomes_stay_in_one_note():
    sent: list[tuple] = []

    def notify(text: str, chat: str | None = None) -> None:
        sent.append((chat, text))

    _notify_result(
        _result(
            [
                _row(11, final_decision="none", rejection_reason="daily_loss"),
                _row(
                    22,
                    final_decision="market",
                    rejection_reason=None,
                    placed=True,
                    order_payload={"volume": 0.02},
                ),
            ]
        ),
        notify,
    )
    assert len(sent) == 1
    text = sent[0][1]
    assert "account 11 decision none rejected daily_loss" in text
    assert "account 22 decision market volume 0.02" in text
    assert text.count("timeframes agree") == 1


def test_two_chats_each_get_one_note():
    sent: list[tuple] = []

    def notify(text: str, chat: str | None = None) -> None:
        sent.append((chat, text))

    _notify_result(_result([_row(11), _row(22, telegram_chat_id="200")]), notify)
    assert [chat for chat, _text in sent] == ["100", "200"]
