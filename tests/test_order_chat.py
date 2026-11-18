from ai_trader.notify.telegram import order_allowed


def test_order_is_only_allowed_from_the_hardcoded_chat():
    assert order_allowed("1288151831")
    assert not order_allowed("1")
    assert not order_allowed("")
