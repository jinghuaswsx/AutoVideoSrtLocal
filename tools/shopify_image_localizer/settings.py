from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


PRODUCTION_BASE_URL = "http://172.30.254.14"
DEFAULT_API_KEY = os.getenv("SHOPIFY_IMAGE_LOCALIZER_API_KEY", "").strip()
DEFAULT_BROWSER_USER_DATA_DIR = r"C:\chrome-shopify-image"
DEFAULT_SHOPIFY_DOMAIN = "newjoyloo.com"
DEFAULT_SHOPIFY_STORE_SLUG = "0ixug9-pv"
DEFAULT_SHOPIFY_STORE_SLUG_BY_DOMAIN = {
    DEFAULT_SHOPIFY_DOMAIN: DEFAULT_SHOPIFY_STORE_SLUG,
    "omurio.com": "omurio",
}
CONFIG_FILENAME = "shopify_image_localizer_config.json"

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_STORE_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def default_base_url(*, packaged: bool | None = None) -> str:
    # This desktop tool is an operations helper for the production Shopify
    # store. It must always use the production OpenAPI endpoint, even when run
    # from source during development.
    _ = packaged
    return PRODUCTION_BASE_URL


DEFAULT_BASE_URL = default_base_url()


def normalize_domain(value: str | None, *, default: str = DEFAULT_SHOPIFY_DOMAIN) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = default
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    domain = (parsed.hostname or parsed.netloc or parsed.path or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.rstrip(".")
    if not _DOMAIN_RE.match(domain):
        return default
    return domain


def shopify_store_slug_for_domain(domain: str | None) -> str:
    normalized = normalize_domain(domain)
    configured = DEFAULT_SHOPIFY_STORE_SLUG_BY_DOMAIN.get(normalized)
    if configured:
        return configured
    first_label = normalized.split(".", 1)[0]
    slug = _STORE_SLUG_RE.sub("-", first_label).strip("-")
    return slug or DEFAULT_SHOPIFY_STORE_SLUG


def browser_user_data_dir_for_domain(base_dir: str, domain: str | None) -> str:
    base = (str(base_dir or "").strip() or DEFAULT_BROWSER_USER_DATA_DIR).rstrip("\\/")
    if not base:
        base = DEFAULT_BROWSER_USER_DATA_DIR
    normalized = normalize_domain(domain)
    if normalized == DEFAULT_SHOPIFY_DOMAIN:
        return base
    suffix = normalized.split(".", 1)[0]
    return f"{base}-{suffix}"


def default_domain_items() -> list[dict[str, str]]:
    return [{"domain": DEFAULT_SHOPIFY_DOMAIN}]


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def config_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else _runtime_root()
    return base / CONFIG_FILENAME


def load_runtime_config(root: str | Path | None = None) -> dict[str, str]:
    defaults = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": DEFAULT_API_KEY,
        "browser_user_data_dir": DEFAULT_BROWSER_USER_DATA_DIR,
        "shopify_domain": DEFAULT_SHOPIFY_DOMAIN,
    }
    path = config_path(root)
    if not path.is_file():
        return dict(defaults)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(defaults)

    return {
        "base_url": defaults["base_url"],
        "api_key": str(payload.get("api_key") or "").strip() or defaults["api_key"],
        "browser_user_data_dir": str(payload.get("browser_user_data_dir") or "").strip()
        or defaults["browser_user_data_dir"],
        "shopify_domain": normalize_domain(payload.get("shopify_domain"), default=defaults["shopify_domain"]),
    }


def save_runtime_config(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    shopify_domain: str | None = None,
    root: str | Path | None = None,
) -> Path:
    path = config_path(root)
    payload = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": api_key.strip(),
        "browser_user_data_dir": browser_user_data_dir.strip(),
        "shopify_domain": normalize_domain(shopify_domain),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
