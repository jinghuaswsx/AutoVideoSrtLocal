from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PRODUCTION_BASE_URL = "http://172.30.254.14"
DEFAULT_API_KEY = os.getenv("SHOPIFY_IMAGE_LOCALIZER_API_KEY", "").strip()
DEFAULT_BROWSER_USER_DATA_DIR = r"C:\chrome-shopify-image"
CONFIG_FILENAME = "shopify_image_localizer_config.json"


def default_base_url(*, packaged: bool | None = None) -> str:
    # This desktop tool is an operations helper for the production Shopify
    # store. It must always use the production OpenAPI endpoint, even when run
    # from source during development.
    _ = packaged
    return PRODUCTION_BASE_URL


DEFAULT_BASE_URL = default_base_url()


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
    }


def save_runtime_config(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    root: str | Path | None = None,
) -> Path:
    path = config_path(root)
    payload = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": api_key.strip(),
        "browser_user_data_dir": browser_user_data_dir.strip(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
