from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenApiCredential:
    key: str
    caller: str = "legacy"
    scopes: tuple[str, ...] = ("*",)

    def allows(self, required_scope: str | None) -> bool:
        if not required_scope:
            return True
        return "*" in self.scopes or required_scope in self.scopes


def _as_scope_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("*",)
    if isinstance(value, str):
        scopes = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        scopes = [str(part).strip() for part in value]
    else:
        scopes = []
    return tuple(scope for scope in scopes if scope) or ("*",)


def parse_openapi_credentials(raw_value: str | None) -> list[OpenApiCredential]:
    raw = (raw_value or "").strip()
    if not raw:
        return []
    if not raw.startswith(("{", "[")):
        return [OpenApiCredential(key=raw)]

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = payload.get("keys") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []

    credentials: list[OpenApiCredential] = []
    for item in items:
        if isinstance(item, str):
            key = item.strip()
            if key:
                credentials.append(OpenApiCredential(key=key))
            continue
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        credentials.append(
            OpenApiCredential(
                key=key,
                caller=str(item.get("caller") or "unnamed").strip() or "unnamed",
                scopes=_as_scope_tuple(item.get("scopes")),
            )
        )
    return credentials


def validate_openapi_key(
    provided_key: str | None,
    configured_value: str | None,
    *,
    required_scope: str | None = None,
) -> OpenApiCredential | None:
    provided = (provided_key or "").strip()
    if not provided:
        return None
    for credential in parse_openapi_credentials(configured_value):
        if credential.allows(required_scope) and hmac.compare_digest(provided, credential.key):
            return credential
    return None
