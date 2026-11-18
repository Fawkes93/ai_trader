"""Order and management requests. Factor is the only cTrader speaker."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from ai_trader.config import Settings


class FactorClient(Protocol):
    uses_broker_positions: bool

    def place(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        ...

    def manage(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        ...

    def list_positions(
        self, account_id: int | None = None, *, label: str | None = None
    ) -> list[dict[str, Any]]:
        ...

    def list_subscribers(self) -> list[dict[str, Any]]:
        ...


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _currency(value: Any) -> str | None:
    if value is None or value == "":
        return None
    code = str(value).strip().upper()
    return code or None


def funds_from_body(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "balance": _optional_float(body.get("balance")),
        "equity": _optional_float(body.get("equity")),
        "currency": _currency(body.get("currency")),
    }


class StubFactor:
    """Accepts orders in memory. Used by tests and FACTOR_MODE=stub."""

    uses_broker_positions = False

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.managed: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.account_funds: dict[int, dict[str, Any]] = {}
        self._n = 0
        self.subscribers: list[dict[str, Any]] | None = None

    def place(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        self._n += 1
        result = {
            "success": True,
            "order_id": f"stub-order-{self._n}",
            "position_id": f"stub-{self._n}",
            "message": "stub",
        }
        self.orders.append({"run_id": run_id, "payload": payload, "result": result})
        return result

    def manage(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        result = {"success": True, "message": "stub", "position_id": payload.get("position_id")}
        self.managed.append({"run_id": run_id, "payload": payload, "result": result})
        return result

    def list_positions(
        self, account_id: int | None = None, *, label: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.positions:
            owner = row.get("account_id")
            if account_id is not None and owner not in (None, account_id, str(account_id)):
                continue
            if label is not None and str(row.get("label") or "") != label:
                continue
            rows.append(row)
        return rows

    def list_subscribers(self) -> list[dict[str, Any]]:
        if self.subscribers is not None:
            return self.subscribers
        from ai_trader.subscribers import default_subscriber

        one = default_subscriber()
        return [
            {
                "user_id": one.user_id,
                "account_id": one.account_id,
                "telegram_chat_id": None,
                "max_concurrent": one.max_concurrent,
                "min_volume_lots": one.min_volume_lots,
                "max_volume_lots": one.max_volume_lots,
                "min_rr": one.min_rr,
                "max_rr": one.max_rr,
                "daily_loss_cap_usd": one.daily_loss_cap_usd,
            }
        ]


class HttpFactor:
    """POST /api/ai/orders and /api/positions/manage on Factor."""

    uses_broker_positions = True

    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.Client(timeout=30)
        self.account_funds: dict[int, dict[str, Any]] = {}

    def _headers(self, run_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.factor_api_token}",
            "Idempotency-Key": run_id,
            "Content-Type": "application/json",
        }

    def place(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        return self._post("/api/ai/orders", payload, run_id)

    def manage(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        return self._post("/api/positions/manage", payload, run_id)

    def list_positions(
        self, account_id: int | None = None, *, label: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.settings.factor_api_token:
            raise RuntimeError("missing_factor_token")
        account = account_id if account_id is not None else self.settings.account_id_int()
        params = {"label": label or self.settings.label}
        if account is not None:
            params["account_id"] = str(account)
        response = self.http.get(
            f"{self.settings.factor_base_url}/api/positions",
            params=params,
            headers={"Authorization": f"Bearer {self.settings.factor_api_token}"},
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict) and isinstance(body.get("positions"), list):
            if account is not None:
                self.account_funds[int(account)] = funds_from_body(body)
            return body["positions"]
        raise RuntimeError("unexpected positions payload")

    def list_subscribers(self) -> list[dict[str, Any]]:
        if not self.settings.factor_api_token:
            raise RuntimeError("missing_factor_token")
        response = self.http.get(
            f"{self.settings.factor_base_url}/api/ai/subscribers",
            headers={"Authorization": f"Bearer {self.settings.factor_api_token}"},
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("subscribers"), list):
            return body["subscribers"]
        raise RuntimeError("unexpected subscribers payload")

    def _post(self, path: str, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        if not self.settings.factor_api_token:
            return {"success": False, "message": "missing_factor_token"}
        try:
            response = self.http.post(
                f"{self.settings.factor_base_url}{path}",
                json=payload,
                headers=self._headers(run_id),
            )
        except httpx.HTTPError as exc:
            return {"success": False, "message": str(exc)}
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text[:500]}
        if not isinstance(body, dict):
            body = {"message": str(body)}
        if response.status_code >= 400:
            body.setdefault("success", False)
            body.setdefault("message", f"http_{response.status_code}")
        body.setdefault("success", response.status_code < 400)
        return body


def build_factor(settings: Settings) -> FactorClient:
    if settings.factor_mode == "stub":
        return StubFactor()
    return HttpFactor(settings)
