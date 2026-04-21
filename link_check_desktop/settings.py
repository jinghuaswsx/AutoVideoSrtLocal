from __future__ import annotations

import json
from pathlib import Path

from link_check_desktop.storage import executable_root


DEFAULT_BASE_URL = "http://172.30.254.14:8888"
DEFAULT_API_KEY = "autovideosrt-materials-openapi"
CONFIG_FILENAME = "link_check_desktop_config.json"

# Desktop child project runs independently on Windows and talks to the
# existing server only over HTTP APIs. The multimodal Gemini key stays local.
GEMINI_API_KEY = "AIzaSyAGjumMmYv4p2uPds4SAnkarGhFUmvc660"
GEMINI_ANALYZE_MODEL = "gemini-2.5-flash"
GEMINI_SAME_IMAGE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_CHANNEL = "aistudio"
GEMINI_CHANNEL_LABEL = "Google AI Studio"


def config_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else executable_root()
    return base / CONFIG_FILENAME


def load_runtime_config(root: str | Path | None = None) -> dict[str, str]:
    defaults = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": DEFAULT_API_KEY,
    }
    path = config_path(root)
    if not path.is_file():
        return dict(defaults)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(defaults)

    base_url = str(payload.get("base_url") or "").strip() or defaults["base_url"]
    api_key = str(payload.get("api_key") or "").strip() or defaults["api_key"]
    return {
        "base_url": base_url,
        "api_key": api_key,
    }


def save_runtime_config(
    *,
    base_url: str,
    api_key: str,
    root: str | Path | None = None,
) -> Path:
    path = config_path(root)
    payload = {
        "base_url": base_url.strip(),
        "api_key": api_key.strip(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
