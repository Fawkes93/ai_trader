"""Plain-text Telegram messages."""

from __future__ import annotations

from typing import Any


def format_cycle(result: Any) -> str:
    if result.busy:
        return "busy"
    payload = getattr(result, "order_payload", None)
    lines = [
        f"AI-GOLD {result.trigger} {result.session or '-'}",
        f"price {result.price if result.price is not None else '-'}",
        f"decision {result.final_decision}",
    ]
    levels = _order_levels(payload)
    if levels:
        lines.append(levels)
    if _show_confidence(result, bool(payload)):
        lines.append(f"confidence {_confidence_text(result.confidence)}")
    if result.rejection_reason:
        lines.append(f"rejected {result.rejection_reason}")
    if result.reasoning:
        lines.append(result.reasoning[:400])
    if result.factor_result:
        ok = result.factor_result.get("success")
        lines.append(
            "order "
            + ("sent" if ok else "failed")
            + f" {result.factor_result.get('message') or ''}".rstrip()
        )
    for item in result.manage_result or []:
        if item.get("action") == "leave":
            continue
        state = "ok" if item.get("ok") else "rejected"
        lines.append(f"manage {item.get('position_id')} {item.get('action')} {state}")
    return "\n".join(lines)


def format_chat_notice(result: Any, rows: list[dict[str, Any]]) -> str:
    """One note for a chat. Account lines appear only when the outcomes differ."""
    sent = any(row.get("order_payload") for row in rows)
    lines = [
        f"AI-GOLD {result.trigger} {result.session or '-'}",
        f"price {result.price if result.price is not None else '-'}",
    ]
    if _show_confidence(result, sent):
        lines.append(f"confidence {_confidence_text(result.confidence)}")
    signatures = [_outcome(row) for row in rows]
    if len(set(signatures)) <= 1:
        decision, reason, volume, levels = (
            signatures[0] if signatures else (result.final_decision, result.rejection_reason, None, None)
        )
        lines.append(f"decision {decision}")
        if levels:
            lines.append(levels)
        if reason:
            lines.append(f"rejected {reason}")
        if volume is not None:
            lines.append(f"volume {volume}")
    else:
        for row, (decision, reason, volume, levels) in zip(rows, signatures):
            bits = [f"account {row.get('account_id')}", f"decision {decision}"]
            if levels:
                bits.append(levels)
            if reason:
                bits.append(f"rejected {reason}")
            if volume is not None:
                bits.append(f"volume {volume}")
            lines.append(" ".join(bits))
    if result.reasoning:
        lines.append(str(result.reasoning)[:400])
    return "\n".join(lines)


def _show_confidence(result: Any, sent: bool) -> bool:
    """Confidence is on /order replies, and on any cycle that sent an order."""
    if getattr(result, "confidence", None) is None:
        return False
    return sent or result.trigger == "telegram_order"


def _order_levels(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    parts: list[str] = []
    side = payload.get("side")
    if side:
        parts.append(str(side))
    if payload.get("entry_price") is not None:
        parts.append(f"entry {payload['entry_price']}")
    if payload.get("stop_loss") is not None:
        parts.append(f"sl {payload['stop_loss']}")
    if payload.get("take_profit") is not None:
        parts.append(f"tp {payload['take_profit']}")
    return " ".join(parts) or None


def _confidence_text(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _outcome(row: dict[str, Any]) -> tuple[Any, Any, Any, str | None]:
    payload = row.get("order_payload") if isinstance(row.get("order_payload"), dict) else None
    volume = payload.get("volume") if payload else None
    return (row.get("final_decision"), row.get("rejection_reason"), volume, _order_levels(payload))


def format_status(
    *,
    trading_enabled: bool,
    factor_mode: str,
    gate: dict[str, Any],
    oldest: str,
    last_run: dict[str, Any] | None,
    positions: list[dict[str, Any]],
    next_run: str,
) -> str:
    lines = [
        f"trading {'on' if trading_enabled else 'off'}",
        f"factor {factor_mode}",
        f"ai_gold {'on' if gate.get('pref_enabled') else 'off'}",
        f"kill {'on' if gate.get('kill_switch') or not gate.get('trading_enabled') else 'off'}",
        f"oldest candle {oldest}",
        f"next run {next_run}",
    ]
    if last_run:
        lines.append(
            "last "
            f"{last_run.get('created_at')} "
            f"{last_run.get('final_decision')} "
            f"price {last_run.get('price')}"
        )
    else:
        lines.append("last none")
    if not positions:
        lines.append("positions none")
    for row in positions:
        lines.append(
            "pos "
            f"{row.get('position_id')} {row.get('side')} "
            f"entry {row.get('entry_price')} sl {row.get('sl_price')} "
            f"tp {row.get('tp_price')} pnl {row.get('unrealized_pnl')}"
        )
    return "\n".join(lines)


def format_daily(runs: list[dict[str, Any]], closed: list[dict[str, Any]]) -> str:
    entries = sum(1 for row in runs if row.get("final_decision") in {"market", "pending"})
    rejects = sum(1 for row in runs if row.get("rejection_reason"))
    pnl = 0.0
    for row in closed:
        if row.get("realized_pnl") is not None:
            pnl += float(row["realized_pnl"])
    return (
        f"AI-GOLD day\n"
        f"runs {len(runs)}\n"
        f"entries {entries}\n"
        f"closes {len(closed)}\n"
        f"pnl {pnl:.2f}\n"
        f"rejections {rejects}"
    )
