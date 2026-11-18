"""Thin PostgREST client. The service role key bypasses RLS."""

from __future__ import annotations

from typing import Any

import httpx


class SupabaseError(RuntimeError):
    pass


class SupabaseRest:
    def __init__(self, url: str, key: str, http: httpx.Client | None = None) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        self.http = http or httpx.Client(timeout=60)

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self.http.get(
            f"{self.base}/{table}",
            params=params,
            headers=self._headers(),
        )
        self._raise(response)
        body = response.json()
        return body if isinstance(body, list) else []

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        response = self.http.post(
            f"{self.base}/{table}",
            json=row,
            headers=self._headers("return=representation"),
        )
        self._raise(response)
        body = response.json()
        if isinstance(body, list) and body:
            return body[0]
        if isinstance(body, dict):
            return body
        return row

    def update(self, table: str, params: dict[str, str], values: dict[str, Any]) -> None:
        response = self.http.patch(
            f"{self.base}/{table}",
            params=params,
            json=values,
            headers=self._headers("return=minimal"),
        )
        self._raise(response)

    def upsert_ignore(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        if not rows:
            return
        response = self.http.post(
            f"{self.base}/{table}",
            params={"on_conflict": on_conflict},
            json=rows,
            headers=self._headers("resolution=ignore-duplicates,return=minimal"),
        )
        self._raise(response)

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise SupabaseError(f"{response.status_code} {response.text[:400]}")
