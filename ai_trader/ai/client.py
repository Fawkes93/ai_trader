"""Model calls. Grok decides first. Gemini 4 Hydrogen is the fallback."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from ai_trader.ai.prompt import system_prompt
from ai_trader.config import Settings
from ai_trader.data.sentiment import extract_json_object, parse_sentiment
from ai_trader.models import Decision

CHAT_URL = "https://api.x.ai/v1/chat/completions"
RESPONSES_URL = "https://api.x.ai/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MIN_MODEL_ATTEMPTS = 2

DECISION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "gold_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": ["none", "market", "pending"]},
                "side": {"type": ["string", "null"], "enum": ["buy", "sell", None]},
                "entry_price": {"type": ["number", "null"]},
                "stop_loss": {"type": ["number", "null"]},
                "take_profit": {"type": ["number", "null"]},
                "volume": {
                    "type": ["number", "null"],
                    "description": "Lots. Null accepts accounts.step_lots. A smaller lot is kept. A larger lot is replaced by the step.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Integer from 0 to 100. Write 70, not 0.70.",
                },
                "reasoning": {"type": "string"},
                "alert_levels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "price": {"type": "number"},
                            "type": {"type": "string", "enum": ["support", "resistance", "custom"]},
                        },
                        "required": ["price", "type"],
                    },
                },
                "manage_existing": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "position_id": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": [
                                    "leave",
                                    "trail",
                                    "move_sl_to_breakeven",
                                    "partial_close",
                                    "close",
                                ],
                            },
                            "new_sl": {"type": ["number", "null"]},
                            "new_tp": {"type": ["number", "null"]},
                            "close_percent": {"type": ["number", "null"]},
                        },
                        "required": ["position_id", "action", "new_sl", "new_tp", "close_percent"],
                    },
                },
            },
            "required": [
                "action",
                "side",
                "entry_price",
                "stop_loss",
                "take_profit",
                "volume",
                "confidence",
                "reasoning",
                "alert_levels",
                "manage_existing",
            ],
        },
    },
}

GEMINI_DECISION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "enum": ["none", "market", "pending"]},
        "side": {"type": "STRING", "enum": ["buy", "sell"], "nullable": True},
        "entry_price": {"type": "NUMBER", "nullable": True},
        "stop_loss": {"type": "NUMBER", "nullable": True},
        "take_profit": {"type": "NUMBER", "nullable": True},
        "volume": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Lots. Null accepts accounts.step_lots. A smaller lot is kept. A larger lot is replaced by the step.",
        },
        "confidence": {"type": "NUMBER"},
        "reasoning": {"type": "STRING"},
        "alert_levels": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "price": {"type": "NUMBER"},
                    "type": {"type": "STRING", "enum": ["support", "resistance", "custom"]},
                },
                "required": ["price", "type"],
            },
        },
        "manage_existing": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "position_id": {"type": "STRING"},
                    "action": {
                        "type": "STRING",
                        "enum": ["leave", "trail", "move_sl_to_breakeven", "partial_close", "close"],
                    },
                    "new_sl": {"type": "NUMBER", "nullable": True},
                    "new_tp": {"type": "NUMBER", "nullable": True},
                    "close_percent": {"type": "NUMBER", "nullable": True},
                },
                "required": ["position_id", "action", "new_sl", "new_tp", "close_percent"],
            },
        },
    },
    "required": [
        "action",
        "side",
        "entry_price",
        "stop_loss",
        "take_profit",
        "volume",
        "confidence",
        "reasoning",
        "alert_levels",
        "manage_existing",
    ],
}


def response_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str) and body["output_text"].strip():
        return body["output_text"]
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
    chunks: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            chunks.append(item["text"])
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
    return "\n".join(chunks)


def _public_context(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if not str(key).startswith("_")}


def decide_with_grok(settings: Settings, context: dict[str, Any], *, force: bool, attempt: int) -> Decision:
    payload = {
        "model": settings.xai_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt(force=force, attempt=attempt)},
            {"role": "user", "content": json.dumps(_public_context(context), default=str)},
        ],
        "response_format": DECISION_SCHEMA,
    }
    response = httpx.post(
        CHAT_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        timeout=settings.ai_timeout_sec,
    )
    response.raise_for_status()
    text = response_text(response.json())
    return Decision.model_validate(extract_json_object(text))


def gemini_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("gemini returned no candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    chunks = [
        part.get("text")
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
        and part.get("thought") is not True
    ]
    text = "\n".join(chunks).strip()
    if not text:
        raise ValueError("gemini returned no text")
    return text


def _gemini_post(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise RuntimeError("missing GEMINI_API_KEY")
    response = httpx.post(
        GEMINI_URL.format(model=settings.gemini_model),
        json=payload,
        headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"},
        timeout=settings.ai_timeout_sec,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("gemini returned a non-object")
    return body


def _model_tries(settings: Settings) -> int:
    return max(MIN_MODEL_ATTEMPTS, settings.gemini_attempts)


def _retry(call: Callable[[], Any], tries: int, pause: Callable[[float], None] | None) -> Any:
    """Call until it returns. Wait one second between failures."""
    wait = pause or time.sleep
    last: Exception | None = None
    for index in range(max(MIN_MODEL_ATTEMPTS, tries)):
        try:
            return call()
        except Exception as exc:
            last = exc
            if index + 1 < max(MIN_MODEL_ATTEMPTS, tries):
                wait(1)
    assert last is not None
    raise last


def decide_with_gemini(
    settings: Settings,
    context: dict[str, Any],
    *,
    force: bool,
    attempt: int,
    pause: Callable[[float], None] | None = None,
) -> Decision:
    """At least two tries, or gemini_attempts when that is higher. The last error is raised."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt(force=force, attempt=attempt)}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(_public_context(context), default=str)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_DECISION_SCHEMA,
        },
    }

    def once() -> Decision:
        text = gemini_text(_gemini_post(settings, payload))
        return Decision.model_validate(extract_json_object(text))

    return _retry(once, _model_tries(settings), pause)


def decide(
    settings: Settings,
    context: dict[str, Any],
    *,
    force: bool,
    attempt: int,
    pause: Callable[[float], None] | None = None,
) -> Decision:
    """Grok first, twice. Gemini only after both Grok calls fail."""
    try:
        decision = _retry(
            lambda: decide_with_grok(settings, context, force=force, attempt=attempt),
            MIN_MODEL_ATTEMPTS,
            pause,
        )
    except Exception as exc:
        context["_model"] = "gemini"
        context["_grok_error"] = str(exc)[:200]
        return decide_with_gemini(settings, context, force=force, attempt=attempt, pause=pause)
    context["_model"] = "grok"
    return decision


def _grok_chat(settings: Settings, messages: list[dict[str, str]]) -> str:
    response = httpx.post(
        CHAT_URL,
        json={"model": settings.xai_model, "temperature": 0, "messages": messages},
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        timeout=settings.ai_timeout_sec,
    )
    response.raise_for_status()
    text = response_text(response.json()).strip()
    if not text:
        raise ValueError("empty grok answer")
    return text


def chat_text(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    pause: Callable[[float], None] | None = None,
) -> str:
    """Plain answer. Grok twice, then Gemini. messages include the system turn."""
    try:
        return _retry(lambda: _grok_chat(settings, messages), MIN_MODEL_ATTEMPTS, pause)
    except Exception:
        system = "\n".join(item["content"] for item in messages if item.get("role") == "system")
        contents = []
        for item in messages:
            role = item.get("role")
            if role == "system":
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": item.get("content") or ""}],
                }
            )
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0},
        }

        def once() -> str:
            text = gemini_text(_gemini_post(settings, payload)).strip()
            if not text:
                raise ValueError("empty gemini answer")
            return text

        return _retry(once, _model_tries(settings), pause)


def complete_text(settings: Settings, prompt: str, *, pause: Callable[[float], None] | None = None) -> str:
    """Plain JSON text. Grok twice, then Gemini for at least two tries."""
    try:
        return _retry(
            lambda: _grok_chat(settings, [{"role": "user", "content": prompt}]),
            MIN_MODEL_ATTEMPTS,
            pause,
        )
    except Exception:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        return _retry(lambda: gemini_text(_gemini_post(settings, payload)), _model_tries(settings), pause)


def fetch_calendar_note(settings: Settings, events: list[dict[str, Any]], now: datetime) -> str:
    """One web search for missing forecasts and releases that are already due."""
    prompt = (
        "These high-impact events matter for gold. Search the web. "
        "Where forecast is null, give the published consensus if there is one. "
        "Where the event time is already past, give the released actual if it is out. "
        "Reply in one short paragraph. No trade instruction.\n"
        f"Now: {now.astimezone(timezone.utc).isoformat()}\n"
        + json.dumps(events, default=str)
    )
    response = httpx.post(
        RESPONSES_URL,
        json={
            "model": settings.xai_model,
            "input": [{"role": "user", "content": prompt}],
            "tools": [{"type": "web_search"}],
        },
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        timeout=settings.ai_timeout_sec,
    )
    response.raise_for_status()
    return response_text(response.json()).strip()[:800]


def fetch_sentiment(settings: Settings, now: datetime) -> dict[str, Any]:
    end = now.astimezone(timezone.utc)
    start = end - timedelta(hours=48)
    prompt = (
        "Search recent X posts from the allowed handles about gold or XAUUSD. "
        "Reply with one JSON object: "
        '{"bias":"up|down|flat|mixed|unknown","summary":"short text",'
        '"confidence":0,"posts":[{"handle":"","text":"","url":""}]}. '
        "Bias is the lean of those posts, not a trade instruction. "
        "If the posts disagree, bias is mixed. If there is nothing useful, bias is unknown."
    )
    payload = {
        "model": settings.xai_model,
        "input": [{"role": "user", "content": prompt}],
        "tools": [
            {
                "type": "x_search",
                "allowed_x_handles": list(settings.x_handles),
                "from_date": start.date().isoformat(),
                "to_date": end.date().isoformat(),
            }
        ],
    }
    response = httpx.post(
        RESPONSES_URL,
        json=payload,
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        timeout=settings.ai_timeout_sec,
    )
    response.raise_for_status()
    body = response.json()
    parsed = parse_sentiment(response_text(body))
    parsed["window_start"] = start.isoformat()
    parsed["window_end"] = end.isoformat()
    parsed["handles"] = list(settings.x_handles)
    parsed["raw"] = {"output_text": response_text(body)[:4000]}
    return parsed
