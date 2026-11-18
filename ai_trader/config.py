"""Environment configuration. Trading defaults on and Factor mode defaults to HTTP."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _handles(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in raw.split(","):
        handle = part.strip().lstrip("@").lower()
        if handle:
            out.append(handle)
    return tuple(out[:20])


@dataclass
class Settings:
    supabase_url: str = ""
    supabase_key: str = ""
    reaktor_supabase_url: str = ""
    reaktor_supabase_key: str = ""
    xai_api_key: str = ""
    xai_model: str = "grok-4.6"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-4-hydrogen"
    gemini_attempts: int = 3
    x_handles: tuple[str, ...] = ()
    twelvedata_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    factor_base_url: str = "https://factor.igrik.xyz"
    factor_api_token: str = ""
    factor_user_id: str = ""
    factor_account_id: str = ""
    factor_mode: str = "http"
    trading_enabled: bool = True
    label: str = "AI-GOLD-v1"
    candle_symbol: str = "XAU/USD"
    broker_symbol: str = "XAUUSD"
    pref_kind: str = "ai_gold"
    pref_symbol: str = "XAU/USD"
    max_volume_lots: float = 0.01
    max_open_positions: int = 1
    min_rr: float = 1.2
    min_confidence: float = 60
    max_daily_loss_usd: float = 50
    oz_per_lot: float = 100
    sr_cluster_usd: float = 3.0
    backfill_days: int = 370
    backfill_sleep_sec: float = 8
    backfill_window_days: int = 21
    sentiment_max_age_min: int = 90
    ai_timeout_sec: float = 180
    alert_poll_sec: int = 60
    alert_debounce_sec: int = 600
    history_short_days: int = 300
    max_alerts: int = 8
    cot_stale_days: int = 10
    candle_timeframe_min: int = 15

    def require_runtime(self) -> None:
        missing = []
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        if not self.reaktor_supabase_url:
            missing.append("REAKTOR_SUPABASE_URL")
        if not self.reaktor_supabase_key:
            missing.append("REAKTOR_SUPABASE_KEY")
        if not self.xai_api_key:
            missing.append("XAI_API_KEY")
        if missing:
            raise SystemExit("missing required env: " + ", ".join(missing))

    def account_id_int(self) -> int | None:
        raw = self.factor_account_id.strip()
        if not raw:
            return None
        return int(raw)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            reaktor_supabase_url=os.getenv("REAKTOR_SUPABASE_URL", "").strip().rstrip("/"),
            reaktor_supabase_key=os.getenv("REAKTOR_SUPABASE_KEY", "").strip(),
            xai_api_key=os.getenv("XAI_API_KEY", "").strip(),
            xai_model=os.getenv("XAI_MODEL", "grok-4.6").strip() or "grok-4.6",
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-4-hydrogen").strip() or "gemini-4-hydrogen",
            gemini_attempts=max(2, _int("GEMINI_ATTEMPTS", 3)),
            x_handles=_handles(os.getenv("X_HANDLES", "")),
            twelvedata_api_key=os.getenv("TWELVEDATA_API_KEY", "").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            factor_base_url=os.getenv("FACTOR_BASE_URL", "https://factor.igrik.xyz").strip().rstrip("/"),
            factor_api_token=os.getenv("FACTOR_API_TOKEN", "").strip(),
            factor_user_id=os.getenv("FACTOR_USER_ID", "").strip(),
            factor_account_id=os.getenv("FACTOR_ACCOUNT_ID", "").strip(),
            factor_mode=(os.getenv("FACTOR_MODE", "http").strip().lower() or "http"),
            trading_enabled=_bool("TRADING_ENABLED", True),
            label=os.getenv("AI_LABEL", "AI-GOLD-v1").strip() or "AI-GOLD-v1",
            max_volume_lots=_float("MAX_VOLUME_LOTS", 0.01),
            max_open_positions=_int("MAX_OPEN_POSITIONS", 1),
            min_rr=_float("MIN_RR", 1.2),
            min_confidence=_float("MIN_CONFIDENCE", 60),
            max_daily_loss_usd=_float("MAX_DAILY_LOSS_USD", 50),
            sr_cluster_usd=_float("SR_CLUSTER_USD", 3.0),
            backfill_days=_int("BACKFILL_DAYS", 370),
            backfill_sleep_sec=_float("BACKFILL_SLEEP_SEC", 8),
            backfill_window_days=_int("BACKFILL_WINDOW_DAYS", 21),
            sentiment_max_age_min=_int("SENTIMENT_MAX_AGE_MIN", 90),
            ai_timeout_sec=_float("AI_TIMEOUT_SEC", 180),
            alert_poll_sec=_int("ALERT_POLL_SEC", 60),
            history_short_days=_int("HISTORY_SHORT_DAYS", 300),
            max_alerts=_int("MAX_ALERTS", 8),
        )
