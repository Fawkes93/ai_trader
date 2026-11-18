"""Run one cycle or the UTC scheduler and the Telegram command bot.

    python -m ai_trader --once
    python -m ai_trader
    python -m ai_trader.backfill
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from ai_trader.config import Settings
from ai_trader.cycle import run_cycle, run_daily_summary, run_weekly_review, status_text
from ai_trader.db.repo import SupabaseRepo
from ai_trader.factor.client import build_factor
from ai_trader.halt import apply_saved_halt, resume_trading, stop_trading
from ai_trader.ai.client import chat_text
from ai_trader.notify.chat import ChatLog, answer_chat, chat_log_path
from ai_trader.notify.telegram import send_message, start_bot
from ai_trader.scheduler import DAILY_SUMMARY, WEEKLY_REVIEW, analysis_slots, poll_price_alerts


def _notify(settings: Settings):
    def send(text: str, chat_id: str | None = None) -> None:
        print(text, flush=True)
        try:
            send_message(settings, text, chat_id=chat_id)
        except Exception as exc:
            print(f"telegram send failed: {exc}", flush=True)

    return send


def _once(settings: Settings) -> int:
    repo = SupabaseRepo(settings)
    factor = build_factor(settings)
    result = run_cycle(
        settings=settings,
        repo=repo,
        factor=factor,
        trigger="manual",
        notify=_notify(settings),
    )
    if not settings.telegram_bot_token:
        print(result.message, flush=True)
    return 0 if result.rejection_reason is None else 0


async def _serve(settings: Settings) -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    repo = SupabaseRepo(settings)
    factor = build_factor(settings)
    notify = _notify(settings)
    alert_state: dict = {}

    async def analysis(trigger: str = "schedule") -> None:
        await asyncio.to_thread(
            run_cycle,
            settings=settings,
            repo=repo,
            factor=factor,
            trigger=trigger,
            notify=notify,
        )

    async def daily() -> None:
        await asyncio.to_thread(run_daily_summary, settings=settings, repo=repo, notify=notify)

    async def weekly() -> None:
        await asyncio.to_thread(run_weekly_review, settings=settings, repo=repo, notify=notify)

    async def alerts() -> None:
        while True:
            try:
                await asyncio.to_thread(
                    poll_price_alerts,
                    settings,
                    repo,
                    factor,
                    alert_state,
                    notify=notify,
                )
            except Exception as exc:
                print(f"alert poll failed: {exc}", flush=True)
            await asyncio.sleep(settings.alert_poll_sec)

    scheduler = AsyncIOScheduler(timezone="UTC")
    for slot_id, hour, minute, days in analysis_slots():
        scheduler.add_job(
            analysis,
            "cron",
            args=["schedule"],
            id=slot_id,
            hour=hour,
            minute=minute,
            day_of_week=days,
        )
    scheduler.add_job(
        daily,
        "cron",
        id="daily-summary",
        hour=DAILY_SUMMARY[0],
        minute=DAILY_SUMMARY[1],
        day_of_week="mon-fri",
    )
    scheduler.add_job(
        weekly,
        "cron",
        id="weekly-review",
        hour=WEEKLY_REVIEW[0],
        minute=WEEKLY_REVIEW[1],
        day_of_week="sat",
    )
    scheduler.start()
    print(
        f"AI-GOLD scheduler up mode={settings.factor_mode} "
        f"trading={settings.trading_enabled} label={settings.label}",
        flush=True,
    )
    alert_task = asyncio.create_task(alerts())

    if settings.telegram_bot_token:
        def on_status(chat_id: str) -> str:
            return status_text(settings, repo, factor)

        def on_order(chat_id: str) -> str:
            result = run_cycle(
                settings=settings,
                repo=repo,
                factor=factor,
                trigger="telegram_order",
                only_chat_id=chat_id,
            )
            print(result.message, flush=True)
            return result.message

        def on_start(code: str, chat_id: str) -> str:
            return "Send /subscribe to link this chat to your Factor account."

        def on_subscribe(chat_id: str, account_id: str | None) -> str:
            from ai_trader.db.supabase import SupabaseRest
            from ai_trader.notify.subscribe import subscribe_chat

            rest = SupabaseRest(settings.supabase_url, settings.supabase_key)
            return subscribe_chat(rest, chat_id, account_id)

        def on_stop(chat_id: str) -> str:
            text = stop_trading(settings, factor)
            print(text, flush=True)
            return text

        def on_resume(chat_id: str) -> str:
            text = resume_trading(settings)
            print(text, flush=True)
            return text

        chat_log = ChatLog(chat_log_path())

        def on_ai(chat_id: str, question: str) -> str:
            return answer_chat(
                settings=settings,
                repo=repo,
                factor=factor,
                chat_id=chat_id,
                question=question,
                store=chat_log,
                ask=chat_text,
            )

        app = start_bot(
            settings,
            on_status=on_status,
            on_order=on_order,
            on_start=on_start,
            on_subscribe=on_subscribe,
            on_stop=on_stop,
            on_resume=on_resume,
            on_ai=on_ai,
        )
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        print("telegram commands: /subscribe /status /order /ai /stop /resume", flush=True)
    else:
        print("telegram token or chat id missing — pushes and commands skipped", flush=True)

    try:
        await asyncio.Event().wait()
    finally:
        alert_task.cancel()
        scheduler.shutdown(wait=False)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="AI Gold intraday orchestrator")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    apply_saved_halt(settings)
    settings.require_runtime()
    if settings.factor_mode == "http" and not settings.factor_api_token:
        print("FACTOR_API_TOKEN is empty — order calls will be rejected", flush=True)
    if args.once:
        sys.exit(_once(settings))
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
