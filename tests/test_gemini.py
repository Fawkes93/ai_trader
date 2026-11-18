import json

import httpx

from ai_trader.ai.client import decide, gemini_text
from ai_trader.config import Settings
from ai_trader.models import Decision


def _settings() -> Settings:
    return Settings(
        xai_api_key="xai",
        gemini_api_key="gemini",
        gemini_model="gemini-4-hydrogen",
        gemini_attempts=3,
        ai_timeout_sec=5,
    )


def _decision_body() -> dict:
    decision = {
        "action": "none",
        "side": None,
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "volume": None,
        "confidence": 40,
        "reasoning": "no setup",
        "alert_levels": [],
        "manage_existing": [],
    }
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(decision)}]}}]}


def _post(handler):
    def post(url, json=None, headers=None, timeout=None):
        return handler(url)

    return post


def test_gemini_text_skips_thought_parts():
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "checking the stop"},
                        {"text": '{"action":"none"}'},
                    ]
                }
            }
        ]
    }
    assert gemini_text(body) == '{"action":"none"}'


def test_grok_success_does_not_call_gemini(monkeypatch):
    seen = []

    def handler(url):
        seen.append(url)
        request = httpx.Request("POST", url)
        body = {"choices": [{"message": {"content": json.dumps({
            "action": "none", "side": None, "entry_price": None, "stop_loss": None,
            "take_profit": None, "volume": None, "confidence": 40, "reasoning": "flat",
            "alert_levels": [], "manage_existing": [],
        })}}]}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post(handler))
    context = {}
    decision = decide(_settings(), context, force=False, attempt=0, pause=lambda _sec: None)
    assert isinstance(decision, Decision)
    assert context["_model"] == "grok"
    assert seen == ["https://api.x.ai/v1/chat/completions"]


def test_grok_retries_once_then_returns_the_decision(monkeypatch):
    grok_calls = {"n": 0}

    def handler(url):
        request = httpx.Request("POST", url)
        grok_calls["n"] += 1
        if grok_calls["n"] == 1:
            return httpx.Response(503, json={"error": "down"}, request=request)
        body = {"choices": [{"message": {"content": json.dumps({
            "action": "none", "side": None, "entry_price": None, "stop_loss": None,
            "take_profit": None, "volume": None, "confidence": 40, "reasoning": "flat",
            "alert_levels": [], "manage_existing": [],
        })}}]}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post(handler))
    context = {}
    decision = decide(_settings(), context, force=False, attempt=0, pause=lambda _sec: None)
    assert decision.action == "none"
    assert context["_model"] == "grok"
    assert grok_calls["n"] == 2


def test_gemini_retries_then_returns_the_decision(monkeypatch):
    grok_calls = {"n": 0}
    gemini_calls = {"n": 0}
    waits = []

    def handler(url):
        request = httpx.Request("POST", url)
        if "x.ai" in url:
            grok_calls["n"] += 1
            return httpx.Response(503, json={"error": "down"}, request=request)
        gemini_calls["n"] += 1
        if gemini_calls["n"] < 3:
            return httpx.Response(503, json={"error": "busy"}, request=request)
        return httpx.Response(200, json=_decision_body(), request=request)

    monkeypatch.setattr(httpx, "post", _post(handler))
    context = {"price": 4100}
    decision = decide(_settings(), context, force=False, attempt=0, pause=waits.append)
    assert decision.action == "none"
    assert decision.reasoning == "no setup"
    assert grok_calls["n"] == 2
    assert gemini_calls["n"] == 3
    assert waits == [1, 1, 1]
    assert context["_model"] == "gemini"
    assert "price" in context
    assert "_grok_error" not in json.dumps({key: value for key, value in context.items() if key == "price"})


def test_gemini_gives_up_after_three_failures(monkeypatch):
    grok_calls = {"n": 0}
    gemini_calls = {"n": 0}

    def handler(url):
        request = httpx.Request("POST", url)
        if "generativelanguage" in url:
            gemini_calls["n"] += 1
        else:
            grok_calls["n"] += 1
        return httpx.Response(503, json={"error": "down"}, request=request)

    monkeypatch.setattr(httpx, "post", _post(handler))
    try:
        decide(_settings(), {}, force=False, attempt=0, pause=lambda _sec: None)
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected the third Gemini failure to raise")
    assert grok_calls["n"] == 2
    assert gemini_calls["n"] == 3


def test_one_configured_gemini_attempt_still_tries_twice(monkeypatch):
    gemini_calls = {"n": 0}

    def handler(url):
        request = httpx.Request("POST", url)
        if "generativelanguage" in url:
            gemini_calls["n"] += 1
        return httpx.Response(503, json={"error": "down"}, request=request)

    monkeypatch.setattr(httpx, "post", _post(handler))
    settings = _settings()
    settings.gemini_attempts = 1
    try:
        decide(settings, {}, force=False, attempt=0, pause=lambda _sec: None)
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected Gemini to raise")
    assert gemini_calls["n"] == 2
