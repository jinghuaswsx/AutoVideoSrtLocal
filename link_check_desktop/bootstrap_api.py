from __future__ import annotations

from typing import Any

import requests


class BootstrapError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("error") or f"bootstrap failed: {status_code}")
        self.status_code = status_code
        self.payload = payload


def fetch_bootstrap(
    base_url: str,
    api_key: str,
    target_url: str,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/link-check/bootstrap",
        headers={"X-API-Key": api_key},
        json={"target_url": target_url},
        timeout=timeout,
    )
    payload = response.json()
    if response.status_code >= 400:
        raise BootstrapError(response.status_code, payload)
    return payload
