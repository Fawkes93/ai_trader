"""Halt AI-GOLD from Telegram. Other cTrader labels are left open."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ai_trader.config import Settings

ROOT = Path(__file__).resolve().parents[1]
_FILE = ".trading_halt"


def halt_path(root: Path | None = None) -> Path:
    return (root or ROOT) / _FILE


def engage_halt(settings: Settings, root: Path | None = None) -> None:
    settings.trading_enabled = False
    halt_path(root).write_text("stopped\n")


def clear_halt(settings: Settings, root: Path | None = None) -> None:
    path = halt_path(root)
    path.unlink(missing_ok=True)
    settings.trading_enabled = _env_enabled()


def apply_saved_halt(settings: Settings, root: Path | None = None) -> None:
    if halt_path(root).exists():
        settings.trading_enabled = False


def stop_trading(
    settings: Settings,
    factor: object,
    *,
    root: Path | None = None,
) -> str:
    """Turn AI-GOLD off and close its open positions."""
    engage_halt(settings, root)
    lines = ["stopped", "trading off"]
    accounts, problem = _accounts(factor)
    if problem:
        lines.append(problem)
    if not accounts:
        lines.append("no accounts")
        return "\n".join(lines)
    for account in accounts:
        lines.extend(_close_label(factor, account, settings.label))
    return "\n".join(lines)


def resume_trading(settings: Settings, root: Path | None = None) -> str:
    clear_halt(settings, root)
    state = "on" if settings.trading_enabled else "off"
    return f"trading {state}"


def _env_enabled() -> bool:
    import os

    raw = os.getenv("TRADING_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _accounts(factor: object) -> tuple[list[int], str | None]:
    found: list[int] = []
    problem = None
    try:
        rows = factor.list_subscribers()  # type: ignore[attr-defined]
    except Exception as exc:
        problem = f"subscribers unavailable:{exc}"
        rows = []
    for row in rows or []:
        raw = row.get("account_id") if isinstance(row, dict) else None
        if raw in (None, ""):
            continue
        try:
            account = int(raw)
        except (TypeError, ValueError):
            continue
        if account > 0 and account not in found:
            found.append(account)
    return found, problem


def _close_label(factor: object, account: int, label: str) -> list[str]:
    try:
        rows = factor.list_positions(account, label=label)  # type: ignore[attr-defined]
    except Exception as exc:
        return [f"{label} {account} unavailable:{exc}"]
    if not rows:
        return [f"{label} {account} none"]
    lines: list[str] = []
    for row in rows:
        position_id = row.get("position_id")
        sent = factor.manage(  # type: ignore[attr-defined]
            {
                "account_id": account,
                "position_id": position_id,
                "action": "close",
                "label": label,
            },
            str(uuid4()),
        )
        if isinstance(sent, dict) and sent.get("success"):
            state = "closed"
        else:
            message = ""
            if isinstance(sent, dict):
                message = str(sent.get("message") or "")
            state = f"failed {message}".rstrip()
        lines.append(f"{label} {account} {position_id} {state}")
    return lines
