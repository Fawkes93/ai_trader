"""Analysis log, the local AI position book, and reads of Factor tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ai_trader.config import Settings
from ai_trader.data.market import Bar, bar_from_row
from ai_trader.db.supabase import SupabaseRest


class MemoryRepo:
    """In-memory book for tests and for a cycle that has not been given a database."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.cot_rows: list[dict[str, Any]] = []
        self.sentiments: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.econ_rows: list[dict[str, Any]] = []
        self.candles: list[Bar] = []
        self.outcomes: dict[str, dict[str, Any]] = {}
        self.gate = {
            "pref_enabled": True,
            "kill_switch": False,
            "trading_enabled": True,
            "missing": False,
        }

    def account_gate(self) -> dict[str, Any]:
        return dict(self.gate)

    def insert_run(self, row: dict[str, Any]) -> dict[str, Any]:
        stored = dict(row)
        stored.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self.runs.append(stored)
        return stored

    def insert_position(self, row: dict[str, Any]) -> dict[str, Any]:
        stored = dict(row)
        stored.setdefault("id", str(uuid4()))
        stored.setdefault("status", "open")
        stored.setdefault("opened_at", datetime.now(timezone.utc).isoformat())
        self.positions.append(stored)
        return stored

    def update_position(self, position_id: str, values: dict[str, Any]) -> None:
        for row in self.positions:
            if str(row.get("broker_position_id")) == position_id or str(row.get("id")) == position_id:
                row.update(values)

    def open_positions(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.positions if row.get("status") == "open"]

    def closed_today(self, now: datetime) -> list[dict[str, Any]]:
        day = now.astimezone(timezone.utc).date()
        out = []
        for row in self.positions:
            closed = row.get("closed_at")
            if row.get("status") != "closed" or not closed:
                continue
            if isinstance(closed, str):
                try:
                    closed_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                except ValueError:
                    continue
            else:
                closed_dt = closed
            if closed_dt.astimezone(timezone.utc).date() == day:
                out.append(dict(row))
        return out

    def econ_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [dict(row) for row in self.econ_rows]

    def latest_cot(self) -> dict[str, Any] | None:
        return dict(self.cot_rows[-1]) if self.cot_rows else None

    def insert_cot(self, row: dict[str, Any]) -> None:
        self.cot_rows.append(dict(row))

    def latest_sentiment(self) -> dict[str, Any] | None:
        return dict(self.sentiments[-1]) if self.sentiments else None

    def insert_sentiment(self, row: dict[str, Any]) -> dict[str, Any]:
        stored = dict(row)
        stored.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self.sentiments.append(stored)
        return stored

    def replace_alerts(self, levels: list[dict[str, Any]], run_id: str) -> None:
        for alert in self.alerts:
            if alert.get("status") == "active":
                alert["status"] = "cancelled"
        for level in levels:
            self.alerts.append(
                {
                    "id": str(uuid4()),
                    "price": level["price"],
                    "type": level["type"],
                    "status": "active",
                    "created_by_run_id": run_id,
                }
            )

    def active_alerts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.alerts if row.get("status") == "active"]

    def mark_alert_hit(self, alert_id: str, now: datetime) -> None:
        for alert in self.alerts:
            if str(alert.get("id")) == alert_id:
                alert["status"] = "hit"
                alert["hit_at"] = now.isoformat()

    def latest_run(self) -> dict[str, Any] | None:
        return dict(self.runs[-1]) if self.runs else None

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        return [dict(row) for row in self.runs[-limit:]]

    def recent_closed(self, limit: int = 5) -> list[dict[str, Any]]:
        closed = [row for row in self.positions if row.get("status") == "closed"]
        closed.sort(key=lambda row: str(row.get("closed_at") or ""))
        return [dict(row) for row in closed[-limit:]]

    def closed_missing_pnl(self, since: datetime) -> list[dict[str, Any]]:
        start = since.astimezone(timezone.utc)
        found = []
        for row in self.positions:
            if row.get("status") != "closed" or row.get("realized_pnl") not in (None, ""):
                continue
            closed = row.get("closed_at")
            if isinstance(closed, str):
                try:
                    closed_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(closed, datetime):
                closed_dt = closed
            else:
                continue
            if closed_dt.astimezone(timezone.utc) >= start:
                found.append(dict(row))
        return found

    def order_outcome(self, position_id: str) -> dict[str, Any] | None:
        found = self.outcomes.get(str(position_id))
        return dict(found) if found else None

    def runs_since(self, since: datetime) -> list[dict[str, Any]]:
        out = []
        for row in self.runs:
            created = row.get("created_at")
            if isinstance(created, str):
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(created, datetime):
                created_dt = created
            else:
                continue
            if created_dt.astimezone(timezone.utc) >= since.astimezone(timezone.utc):
                out.append(dict(row))
        return out

    def insert_review(self, row: dict[str, Any]) -> None:
        self.reviews.append(dict(row))

    def load_candles(self, since: datetime) -> list[Bar]:
        return [bar for bar in self.candles if bar.open_time >= since]

    def oldest_candle_time(self) -> datetime | None:
        if not self.candles:
            return None
        return min(bar.open_time for bar in self.candles)

    def latest_close(self) -> float | None:
        if not self.candles:
            return None
        return max(self.candles, key=lambda bar: bar.open_time).close

    def upsert_candles(self, rows: list[dict[str, Any]]) -> int:
        existing = {bar.open_time.isoformat() for bar in self.candles}
        added = 0
        for row in rows:
            bar = bar_from_row(row)
            if bar is None or bar.open_time.isoformat() in existing:
                continue
            self.candles.append(bar)
            existing.add(bar.open_time.isoformat())
            added += 1
        self.candles.sort(key=lambda bar: bar.open_time)
        return added


class SupabaseRepo:
    def __init__(
        self,
        settings: Settings,
        rest: SupabaseRest | None = None,
        candles_rest: SupabaseRest | None = None,
    ) -> None:
        self.settings = settings
        self.rest = rest or SupabaseRest(settings.supabase_url, settings.supabase_key)
        if candles_rest is not None:
            self.candle_rest = candles_rest
        elif settings.reaktor_supabase_url and settings.reaktor_supabase_key:
            self.candle_rest = SupabaseRest(
                settings.reaktor_supabase_url,
                settings.reaktor_supabase_key,
            )
        else:
            self.candle_rest = self.rest
        self._candle_cache: list[Bar] = []
        self._candle_cache_at: datetime | None = None

    def account_gate(self) -> dict[str, Any]:
        user_id = self.settings.factor_user_id
        account = self.settings.account_id_int()
        if not user_id or account is None:
            return {
                "pref_enabled": False,
                "kill_switch": True,
                "trading_enabled": False,
                "missing": True,
            }
        prefs = self.rest.get(
            "factor_user_alert_prefs",
            {
                "select": "enabled",
                "user_id": f"eq.{user_id}",
                "account_id": f"eq.{account}",
                "kind": f"eq.{self.settings.pref_kind}",
                "symbol": f"eq.{self.settings.pref_symbol}",
                "limit": "1",
            },
        )
        connections = self.rest.get(
            "factor_ctrader_connections",
            {
                "select": "kill_switch,trading_enabled",
                "user_id": f"eq.{user_id}",
                "account_id": f"eq.{account}",
                "limit": "1",
            },
        )
        pref_enabled = bool(prefs and prefs[0].get("enabled"))
        if not connections:
            return {
                "pref_enabled": pref_enabled,
                "kill_switch": True,
                "trading_enabled": False,
                "missing": True,
            }
        row = connections[0]
        return {
            "pref_enabled": pref_enabled,
            "kill_switch": bool(row.get("kill_switch")),
            "trading_enabled": bool(row.get("trading_enabled")),
            "missing": False,
        }

    def insert_run(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.rest.insert("ai_analysis_runs", row)

    def insert_position(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.rest.insert("ai_positions", row)

    def update_position(self, position_id: str, values: dict[str, Any]) -> None:
        self.rest.update(
            "ai_positions",
            {"broker_position_id": f"eq.{position_id}"},
            values,
        )

    def open_positions(self) -> list[dict[str, Any]]:
        return self.rest.get(
            "ai_positions",
            {
                "select": "*",
                "status": "eq.open",
                "label": f"eq.{self.settings.label}",
                "order": "opened_at.desc",
            },
        )

    def closed_today(self, now: datetime) -> list[dict[str, Any]]:
        start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return self.rest.get(
            "ai_positions",
            {
                "select": "*",
                "status": "eq.closed",
                "and": f"(closed_at.gte.{start.isoformat()},closed_at.lt.{end.isoformat()})",
            },
        )

    def latest_cot(self) -> dict[str, Any] | None:
        rows = self.rest.get(
            "ai_cot_data",
            {"select": "*", "order": "report_date.desc", "limit": "1"},
        )
        return rows[0] if rows else None

    def insert_cot(self, row: dict[str, Any]) -> None:
        self.rest.upsert_ignore("ai_cot_data", [row], "report_date")

    def latest_sentiment(self) -> dict[str, Any] | None:
        rows = self.rest.get(
            "ai_x_sentiment",
            {"select": "*", "order": "created_at.desc", "limit": "1"},
        )
        return rows[0] if rows else None

    def insert_sentiment(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.rest.insert("ai_x_sentiment", row)

    def replace_alerts(self, levels: list[dict[str, Any]], run_id: str) -> None:
        self.rest.update("ai_price_alerts", {"status": "eq.active"}, {"status": "cancelled"})
        for level in levels:
            self.rest.insert(
                "ai_price_alerts",
                {
                    "price": level["price"],
                    "type": level["type"],
                    "status": "active",
                    "created_by_run_id": run_id,
                },
            )

    def active_alerts(self) -> list[dict[str, Any]]:
        return self.rest.get(
            "ai_price_alerts",
            {"select": "*", "status": "eq.active"},
        )

    def mark_alert_hit(self, alert_id: str, now: datetime) -> None:
        self.rest.update(
            "ai_price_alerts",
            {"id": f"eq.{alert_id}"},
            {"status": "hit", "hit_at": now.isoformat()},
        )

    def latest_run(self) -> dict[str, Any] | None:
        rows = self.rest.get(
            "ai_analysis_runs",
            {"select": "*", "order": "created_at.desc", "limit": "1"},
        )
        return rows[0] if rows else None

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.rest.get(
            "ai_analysis_runs",
            {
                "select": "created_at,trigger,session,price,final_decision,rejection_reason,ai_raw_output,subscriber_results",
                "order": "created_at.desc",
                "limit": str(max(1, limit)),
            },
        )
        return list(reversed(rows))

    def recent_closed(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.rest.get(
            "ai_positions",
            {
                "select": "closed_at,opened_at,side,entry_price,sl_price,tp_price,volume_lots,realized_pnl,detail,account_id,broker_position_id",
                "status": "eq.closed",
                "label": f"eq.{self.settings.label}",
                "order": "closed_at.desc",
                "limit": str(max(1, limit)),
            },
        )
        return list(reversed(rows))

    def closed_missing_pnl(self, since: datetime) -> list[dict[str, Any]]:
        return self.rest.get(
            "ai_positions",
            {
                "select": "closed_at,side,entry_price,sl_price,tp_price,volume_lots,realized_pnl,detail,broker_position_id",
                "status": "eq.closed",
                "label": f"eq.{self.settings.label}",
                "realized_pnl": "is.null",
                "closed_at": f"gte.{since.astimezone(timezone.utc).isoformat()}",
                "order": "closed_at.asc",
                "limit": "50",
            },
        )

    def order_outcome(self, position_id: str) -> dict[str, Any] | None:
        rows = self.rest.get(
            "factor_orders",
            {
                "select": "closed_at,detail",
                "broker_position_id": f"eq.{position_id}",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        detail = rows[0].get("detail") if isinstance(rows[0].get("detail"), dict) else {}
        outcome = detail.get("outcome") if isinstance(detail.get("outcome"), dict) else {}
        net = outcome.get("net_pnl")
        close = outcome.get("close_price")
        return {
            "closed_at": rows[0].get("closed_at"),
            "net": None if net in (None, "") else float(net),
            "close_price": None if close in (None, "") else float(close),
        }

    def runs_since(self, since: datetime) -> list[dict[str, Any]]:
        return self.rest.get(
            "ai_analysis_runs",
            {
                "select": "*",
                "created_at": f"gte.{since.isoformat()}",
                "order": "created_at.asc",
                "limit": "500",
            },
        )

    def insert_review(self, row: dict[str, Any]) -> None:
        self.rest.insert("ai_weekly_reviews", row)

    def econ_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.candle_rest.get(
            "econ_events",
            {
                "select": "event_time,country,impact,title,forecast,previous",
                "and": f"(event_time.gte.{start.isoformat()},event_time.lt.{end.isoformat()})",
                "order": "event_time.asc",
                "limit": "200",
            },
        )

    def load_candles(self, since: datetime) -> list[Bar]:
        now = datetime.now(timezone.utc)
        if (
            self._candle_cache_at
            and (now - self._candle_cache_at) < timedelta(minutes=10)
            and self._candle_cache
            and self._candle_cache[0].open_time <= since
        ):
            self._refresh_tail()
            return [bar for bar in self._candle_cache if bar.open_time >= since]
        bars = self._page_candles(since)
        self._candle_cache = bars
        self._candle_cache_at = now
        return bars

    def _refresh_tail(self) -> None:
        """Replace the latest cached bar and append any newer one.

        The year of history can stay cached. The last 15-minute close is the
        live print, and it goes stale within a minute.
        """
        if not self._candle_cache:
            return
        start = self._candle_cache[-1].open_time
        rows = self.candle_rest.get(
            "candles",
            {
                "select": "open_time,open,high,low,close",
                "symbol": f"eq.{self.settings.candle_symbol}",
                "timeframe_min": f"eq.{self.settings.candle_timeframe_min}",
                "open_time": f"gte.{start.isoformat()}",
                "order": "open_time.asc",
                "limit": "10",
            },
        )
        fresh = [bar for row in rows if (bar := bar_from_row(row)) is not None]
        if not fresh:
            return
        by_time = {bar.open_time: bar for bar in self._candle_cache}
        for bar in fresh:
            by_time[bar.open_time] = bar
        self._candle_cache = sorted(by_time.values(), key=lambda bar: bar.open_time)

    def _page_candles(self, since: datetime) -> list[Bar]:
        bars: list[Bar] = []
        offset = 0
        page = 1000
        while True:
            rows = self.candle_rest.get(
                "candles",
                {
                    "select": "open_time,open,high,low,close",
                    "symbol": f"eq.{self.settings.candle_symbol}",
                    "timeframe_min": f"eq.{self.settings.candle_timeframe_min}",
                    "open_time": f"gte.{since.isoformat()}",
                    "order": "open_time.asc",
                    "limit": str(page),
                    "offset": str(offset),
                },
            )
            for row in rows:
                bar = bar_from_row(row)
                if bar is not None:
                    bars.append(bar)
            if len(rows) < page:
                break
            offset += page
        return bars

    def oldest_candle_time(self) -> datetime | None:
        rows = self.candle_rest.get(
            "candles",
            {
                "select": "open_time",
                "symbol": f"eq.{self.settings.candle_symbol}",
                "timeframe_min": f"eq.{self.settings.candle_timeframe_min}",
                "order": "open_time.asc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return bar_from_row(
            {
                "open_time": rows[0]["open_time"],
                "open": 0,
                "high": 0,
                "low": 0,
                "close": 0,
            }
        ).open_time  # type: ignore[union-attr]

    def latest_close(self) -> float | None:
        rows = self.candle_rest.get(
            "candles",
            {
                "select": "close",
                "symbol": f"eq.{self.settings.candle_symbol}",
                "timeframe_min": f"eq.{self.settings.candle_timeframe_min}",
                "order": "open_time.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        return float(rows[0]["close"])

    def upsert_candles(self, rows: list[dict[str, Any]]) -> int:
        self.candle_rest.upsert_ignore("candles", rows, "symbol,timeframe_min,open_time")
        return len(rows)
