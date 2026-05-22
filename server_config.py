"""Shared server address defaults for the main app and helper tools."""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


DEFAULT_SERVER_HOST = "172.16.254.106"
DEFAULT_SERVER_SCHEME = "http"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _with_path(base_url: str, path: str = "") -> str:
    base = base_url.rstrip("/")
    suffix = (path or "").strip()
    if not suffix:
        return base
    return f"{base}/{suffix.lstrip('/')}"


def build_server_base_url(
    *,
    host: str | None = None,
    scheme: str | None = None,
    port: int | str | None = None,
    path: str = "",
) -> str:
    """Build a server URL from the global host defaults."""
    raw_host = (host or SERVER_HOST).strip().rstrip("/")
    parsed = urlsplit(raw_host if "://" in raw_host else f"//{raw_host}")
    hostname = parsed.hostname or parsed.netloc or raw_host
    current_port = port if port is not None else parsed.port
    if current_port in (None, ""):
        netloc = hostname
    else:
        netloc = f"{hostname}:{current_port}"
    current_scheme = (scheme or parsed.scheme or SERVER_SCHEME).strip().rstrip(":") or DEFAULT_SERVER_SCHEME
    base = urlunsplit((current_scheme, netloc, "", "", ""))
    return _with_path(base, path)


SERVER_HOST = _env("AUTOVIDEOSRT_SERVER_HOST", DEFAULT_SERVER_HOST) or DEFAULT_SERVER_HOST
SERVER_SCHEME = _env("AUTOVIDEOSRT_SERVER_SCHEME", DEFAULT_SERVER_SCHEME) or DEFAULT_SERVER_SCHEME
SERVER_BASE_URL = _env("AUTOVIDEOSRT_SERVER_BASE_URL", build_server_base_url())
TEST_SERVER_BASE_URL = _env("AUTOVIDEOSRT_TEST_SERVER_BASE_URL", build_server_base_url(port=8080))
LOCAL_IMAGE_BASE_URL_DEFAULT = _env(
    "AUTOVIDEOSRT_LOCAL_IMAGE_BASE_URL",
    build_server_base_url(port=82, path="/v1"),
)
PROXY_BYPASS_LIST = f"127.0.0.1;localhost;{SERVER_HOST};<local>"
