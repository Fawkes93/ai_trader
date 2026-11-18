"""Dedicated bot. Pushes go out over HTTP. Commands include /status, /order, /ai, and /stop."""

from __future__ import annotations

from typing import Any, Callable

import httpx

from ai_trader.config import Settings
from ai_trader.notify.chat import question_text

# Only this Telegram chat may force a trade with /order.
ORDER_CHAT_ID = "1288151831"


def order_allowed(chat_id: str) -> bool:
    return str(chat_id) == ORDER_CHAT_ID


def send_message(
    settings: Settings,
    text: str,
    chat_id: str | None = None,
    http: httpx.Client | None = None,
) -> None:
    chat = chat_id or settings.telegram_chat_id
    if not settings.telegram_bot_token or not chat:
        return
    client = http or httpx.Client(timeout=20)
    client.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": chat, "text": text[:4000]},
    )


def start_bot(
    settings: Settings,
    *,
    on_status: Callable[[str], str],
    on_order: Callable[[str], str],
    on_start: Callable[[str, str], str],
    on_subscribe: Callable[[str, str | None], str],
    on_stop: Callable[[str], str],
    on_resume: Callable[[str], str],
    on_ai: Callable[[str, str], str],
) -> Any:
    """Build an Application that polls. The caller starts it on the running loop."""
    from telegram.ext import Application, CommandHandler

    def _chat(update: Any) -> str | None:
        chat = getattr(update, "effective_chat", None)
        message = getattr(update, "message", None)
        if chat is None or message is None:
            return None
        return str(chat.id)

    async def status(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        text = await _in_thread(lambda: on_status(chat_id))
        await update.message.reply_text(text[:4000])

    async def order(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        if not order_allowed(chat_id):
            await update.message.reply_text("Not allowed.")
            return
        await update.message.reply_text("running")
        text = await _in_thread(lambda: on_order(chat_id))
        await update.message.reply_text(text[:4000])

    async def stop(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        if not order_allowed(chat_id):
            await update.message.reply_text("Not allowed.")
            return
        text = await _in_thread(lambda: on_stop(chat_id))
        await update.message.reply_text(text[:4000])

    async def resume(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        if not order_allowed(chat_id):
            await update.message.reply_text("Not allowed.")
            return
        text = await _in_thread(lambda: on_resume(chat_id))
        await update.message.reply_text(text[:4000])

    async def ai(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        if not order_allowed(chat_id):
            await update.message.reply_text("Not allowed.")
            return
        question = question_text(getattr(update.message, "text", None))
        if not question:
            await update.message.reply_text("Send /ai and then your question.")
            return
        await update.message.reply_text("asking")
        text = await _in_thread(lambda: on_ai(chat_id, question))
        await update.message.reply_text(text[:4000])

    async def subscribe(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        args = getattr(context, "args", None) or []
        account_id = str(args[0]) if args else None
        text = await _in_thread(lambda: on_subscribe(chat_id, account_id))
        await update.message.reply_text(text[:4000])

    async def start(update, context) -> None:
        chat_id = _chat(update)
        if not chat_id:
            return
        code = ""
        args = getattr(context, "args", None) or []
        if args:
            code = str(args[0])
        text = await _in_thread(lambda: on_start(code, chat_id))
        await update.message.reply_text(text[:4000])

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("order", order))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("ai", ai))
    return app


async def _in_thread(fn: Callable[[], str]) -> str:
    import asyncio

    return await asyncio.to_thread(fn)
