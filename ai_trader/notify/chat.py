"""Telegram /ai. Remembers this chat and answers from the current book."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

from ai_trader.ai.prompt import CHAT
from ai_trader.config import Settings
from ai_trader.halt import ROOT

_FILE = ".ai_chat.json"
# Ten questions and ten answers.
_HISTORY_LIMIT = 20
_FAIL = "The model did not answer."


def chat_log_path(root: Path | None = None) -> Path:
    return (root or ROOT) / _FILE


def question_text(text: str | None) -> str:
    """The words after /ai. Empty when the command has no question."""
    raw = (text or "").strip()
    if not raw.lower().startswith("/ai"):
        return raw
    head, _, rest = raw.partition(" ")
    command = head.split("@", 1)[0].lower()
    if command != "/ai":
        return raw
    return rest.strip()


class ChatLog:
    """Last questions and answers for one Telegram chat, kept on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def turns(self, chat_id: str) -> list[dict[str, str]]:
        with self._lock:
            rows = self._read().get(str(chat_id)) or []
        return [_turn(row) for row in rows if _turn(row)]

    def add(self, chat_id: str, question: str, answer: str) -> None:
        with self._lock:
            data = self._read()
            rows = list(data.get(str(chat_id)) or [])
            rows.append({"role": "user", "text": question})
            rows.append({"role": "assistant", "text": answer})
            data[str(chat_id)] = rows[-_HISTORY_LIMIT:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data))
            temporary.replace(self.path)

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            body = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(body, dict):
            return {}
        return body


def build_messages(question: str, book: dict[str, Any], prior: list[dict[str, str]]) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": CHAT.strip()}]
    for turn in prior:
        role = turn.get("role")
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": turn.get("text") or ""})
    messages.append(
        {
            "role": "user",
            "content": question.strip() + "\n\nBook:\n" + json.dumps(book, default=str),
        }
    )
    return messages


def answer_chat(
    *,
    settings: Settings,
    repo: Any,
    factor: Any,
    chat_id: str,
    question: str,
    store: ChatLog,
    ask: Callable[[Settings, list[dict[str, str]]], str],
) -> str:
    """Answer one question. A failed model call leaves the chat history unchanged."""
    from ai_trader.cycle import question_context

    cleaned = question.strip()
    if not cleaned:
        return "Send /ai and then your question."
    prior = store.turns(chat_id)
    messages = build_messages(cleaned, question_context(settings, repo, factor), prior)
    try:
        text = ask(settings, messages).strip()
    except Exception as exc:
        print(f"ai chat failed: {exc}", flush=True)
        return _FAIL
    if not text:
        return _FAIL
    store.add(chat_id, cleaned, text)
    return text[:4000]


def _turn(row: Any) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    role = row.get("role")
    text = row.get("text")
    if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
        return None
    return {"role": role, "text": text}
