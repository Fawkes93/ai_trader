"""Parse an X-search summary. This module does not read the Reaktor post mailbox."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

BIASES = {"up", "down", "flat", "mixed", "unknown"}


def should_refresh(latest: dict[str, Any] | None, now: datetime, max_age_min: int) -> bool:
    if not latest or not latest.get("created_at"):
        return True
    created = latest["created_at"]
    if isinstance(created, str):
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return True
    elif isinstance(created, datetime):
        created_dt = created
    else:
        return True
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - created_dt.astimezone(timezone.utc)
    return age >= timedelta(minutes=max_age_min)


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("json was not an object")
    return parsed


def parse_sentiment(text: str) -> dict[str, Any]:
    data = extract_json_object(text)
    bias = str(data.get("bias") or "unknown").lower()
    if bias not in BIASES:
        bias = "unknown"
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = min(100.0, max(0.0, confidence))
    posts = data.get("posts") if isinstance(data.get("posts"), list) else []
    clean_posts = []
    for post in posts[:12]:
        if not isinstance(post, dict):
            continue
        clean_posts.append(
            {
                "handle": str(post.get("handle") or ""),
                "text": str(post.get("text") or "")[:500],
                "url": str(post.get("url") or ""),
            }
        )
    return {
        "bias": bias,
        "summary": str(data.get("summary") or "")[:800],
        "confidence": confidence,
        "posts": clean_posts,
    }


def unknown_sentiment(reason: str) -> dict[str, Any]:
    return {
        "bias": "unknown",
        "summary": reason,
        "confidence": 0,
        "posts": [],
    }
