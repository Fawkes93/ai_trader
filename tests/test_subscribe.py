from ai_trader.notify.subscribe import subscribe_chat


class FakeStore:
    def __init__(self, users, connections, prefs=None):
        self.users = [dict(row) for row in users]
        self.connections = [dict(row) for row in connections]
        self.prefs = [dict(row) for row in (prefs or [])]
        self.updates = []

    def get(self, table, params):
        if table == "factor_users":
            rows = self.users
        elif table == "factor_user_alert_prefs":
            rows = self.prefs
        else:
            rows = self.connections
        if "account_id" in params:
            wanted = params["account_id"].removeprefix("eq.")
            rows = [row for row in rows if str(row.get("account_id")) == wanted]
        if "telegram_chat_id" in params:
            wanted = params["telegram_chat_id"].removeprefix("eq.")
            rows = [row for row in rows if str(row.get("telegram_chat_id")) == wanted]
        return rows[: int(params.get("limit", "20"))]

    def update(self, table, params, values):
        self.updates.append((table, params, values))
        user_id = params["id"].removeprefix("eq.")
        for row in self.users:
            if str(row["id"]) == user_id:
                row.update(values)


def test_one_enabled_alert_subscribes_without_listing_accounts():
    store = FakeStore(
        [{"id": "user-1"}, {"id": "other"}],
        [
            {"user_id": "user-1", "account_id": 10},
            {"user_id": "other", "account_id": 20},
        ],
        prefs=[{"user_id": "user-1", "kind": "ai_gold", "enabled": True}],
    )
    text = subscribe_chat(store, "555")
    assert text.startswith("Subscribed")
    assert "10" not in text and "20" not in text
    assert store.users[0]["telegram_chat_id"] == 555


def test_several_enabled_alerts_do_not_list_account_ids():
    store = FakeStore(
        [{"id": "a"}, {"id": "b"}],
        [
            {"user_id": "a", "account_id": 10, "is_live": False},
            {"user_id": "b", "account_id": 20, "is_live": True},
        ],
        prefs=[
            {"user_id": "a", "kind": "ai_gold", "enabled": True},
            {"user_id": "b", "kind": "ai_gold", "enabled": True},
        ],
    )
    text = subscribe_chat(store, "555")
    assert "10" not in text and "20" not in text
    assert "account id" in text
    assert store.updates == []
    linked = subscribe_chat(store, "555", "20")
    assert "20" not in linked
    assert linked.startswith("Subscribed")
    assert next(row for row in store.users if row["id"] == "b")["telegram_chat_id"] == 555
