"""HTTP client for the Q-Select backend API."""

from __future__ import annotations

import httpx


class FrontendError(RuntimeError):
    """Raised when the backend API cannot be reached or returns an error."""


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text or f"HTTP {response.status_code}"

    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg", item)))
            else:
                parts.append(str(item))
        return "; ".join(parts) or f"HTTP {response.status_code}"
    if detail:
        return str(detail)
    return response.text or f"HTTP {response.status_code}"


def _request(
    method: str,
    base_url: str,
    path: str,
    payload: dict | None,
    timeout: float,
    client: httpx.Client | None,
) -> dict:
    url = base_url.rstrip("/") + path
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        response = client.request(method, url, json=payload, timeout=timeout)
        if response.status_code != 200:
            raise FrontendError(_extract_detail(response))
        return response.json()
    except httpx.HTTPError as exc:
        raise FrontendError(f"Could not reach the Q-Select API at {base_url}: {exc}") from exc
    finally:
        if owns_client:
            client.close()


def health(base_url: str, timeout: float = 10.0, client: httpx.Client | None = None) -> dict:
    """Fetch the API health/policy summary."""
    return _request("GET", base_url, "/health", None, timeout, client)


def rewrite(
    base_url: str,
    sql: str,
    estimated_rows: float | None = None,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> dict:
    """Ask the API which action it would apply and for the rewritten SQL (no execution)."""
    payload: dict = {"sql": sql}
    if estimated_rows is not None:
        payload["estimated_rows"] = estimated_rows
    return _request("POST", base_url, "/rewrite", payload, timeout, client)


def analyze(
    base_url: str,
    sql: str,
    estimated_rows: float | None = None,
    timeout: float = 120.0,
    client: httpx.Client | None = None,
) -> dict:
    """Run original + rewritten query via the API and return action, SQL, and timings."""
    payload: dict = {"sql": sql}
    if estimated_rows is not None:
        payload["estimated_rows"] = estimated_rows
    return _request("POST", base_url, "/execute", payload, timeout, client)
