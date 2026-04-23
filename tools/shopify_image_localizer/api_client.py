from __future__ import annotations

import json
from typing import Any

import requests


class ApiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        super().__init__(
            payload.get("message")
            or payload.get("error")
            or f"api failed: {status_code}"
        )
        self.status_code = status_code
        self.payload = payload


def _json_payload(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {
            "error": "non-json response",
            "raw_response": (getattr(response, "text", "") or "").strip()[:1000],
        }
    return payload if isinstance(payload, dict) else {"data": payload}


def fetch_languages(base_url: str, api_key: str, *, timeout: int = 20) -> dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/languages",
        headers={"X-API-Key": api_key},
        timeout=timeout,
    )
    payload = _json_payload(response)
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload


def fetch_bootstrap(
    base_url: str,
    api_key: str,
    product_code: str,
    lang: str,
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/bootstrap",
        headers={"X-API-Key": api_key},
        json={"product_code": product_code, "lang": lang},
        timeout=timeout,
    )
    payload = _json_payload(response)
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload
