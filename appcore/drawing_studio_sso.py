from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode, urljoin


DEFAULT_DRAWING_STUDIO_BASE_URL = "http://127.0.0.1:81"
DRAWING_STUDIO_SSO_PATH = "/api/auth/autovideosrt-sso"
DRAWING_STUDIO_SSO_SECRET_ENV = "DRAWING_STUDIO_SSO_SECRET"
DRAWING_STUDIO_BASE_URL_ENV = "DRAWING_STUDIO_BASE_URL"
DEFAULT_SSO_TTL_SECONDS = 120


class DrawingStudioSsoConfigError(RuntimeError):
    pass


def _secret_from_env() -> str:
    secret = (os.getenv(DRAWING_STUDIO_SSO_SECRET_ENV) or "").strip()
    if not secret:
        raise DrawingStudioSsoConfigError("DRAWING_STUDIO_SSO_SECRET is not configured")
    return secret


def _base_url_from_env() -> str:
    return (os.getenv(DRAWING_STUDIO_BASE_URL_ENV) or DEFAULT_DRAWING_STUDIO_BASE_URL).strip().rstrip("/")


def _canonical_query(params: dict[str, str]) -> str:
    return urlencode([(key, params[key]) for key in sorted(params)])


def build_drawing_studio_sso_url(
    *,
    user_id: int | str,
    username: str,
    role: str,
    now: int | None = None,
    nonce: str | None = None,
    ttl_seconds: int = DEFAULT_SSO_TTL_SECONDS,
    base_url: str | None = None,
    secret: str | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    params = {
        "avs_user_id": str(user_id),
        "avs_username": str(username),
        "avs_role": str(role),
        "exp": str(issued_at + int(ttl_seconds)),
        "nonce": nonce or secrets.token_urlsafe(18),
    }
    signing_secret = secret if secret is not None else _secret_from_env()
    canonical = _canonical_query(params)
    params["sig"] = hmac.new(
        signing_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    root = (base_url or _base_url_from_env()).rstrip("/")
    return f"{urljoin(root + '/', DRAWING_STUDIO_SSO_PATH.lstrip('/'))}?{_canonical_query(params)}"
