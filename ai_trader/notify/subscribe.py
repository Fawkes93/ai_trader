"""Bind a Telegram chat to a Factor user. /subscribe does this from the bot."""

from __future__ import annotations

from typing import Any


class SubscribeStore:
    def get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def update(self, table: str, params: dict[str, str], values: dict[str, Any]) -> None:
        raise NotImplementedError


def subscribe_chat(store: SubscribeStore, chat_id: str, account_id: str | None = None) -> str:
    account_id = (account_id or "").strip()
    if account_id and not account_id.isdigit():
        return "Send /subscribe or /subscribe and the cTrader account id."
    if account_id:
        rows = store.get(
            "factor_ctrader_connections",
            {"select": "user_id,account_id", "account_id": f"eq.{account_id}", "limit": "1"},
        )
        if not rows:
            return "That account is not connected in Factor."
        user_id = str(rows[0]["user_id"])
    else:
        prefs = store.get(
            "factor_user_alert_prefs",
            {
                "select": "user_id",
                "kind": "eq.ai_gold",
                "enabled": "eq.true",
                "limit": "20",
            },
        )
        user_ids = {str(row["user_id"]) for row in prefs if row.get("user_id")}
        if len(user_ids) == 1:
            user_id = next(iter(user_ids))
        elif not user_ids:
            return "Turn on the AI trader alert in Factor, then send /subscribe again."
        else:
            return "Send /subscribe followed by your cTrader account id."
    _claim(store, user_id, chat_id)
    return "Subscribed. This chat gets AI trader messages for your Factor account."


def _claim(store: SubscribeStore, user_id: str, chat_id: str) -> None:
    holders = store.get(
        "factor_users",
        {"select": "id", "telegram_chat_id": f"eq.{chat_id}", "limit": "5"},
    )
    for row in holders:
        if str(row.get("id")) != user_id:
            store.update(
                "factor_users",
                {"id": f"eq.{row['id']}"},
                {"telegram_chat_id": None},
            )
    store.update(
        "factor_users",
        {"id": f"eq.{user_id}"},
        {"telegram_chat_id": int(chat_id), "telegram_link_code": None},
    )
