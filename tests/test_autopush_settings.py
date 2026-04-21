from __future__ import annotations

import importlib
import sys
from pathlib import Path

import dotenv


AUTOPUSH_DIR = Path(__file__).resolve().parents[1] / "AutoPush"
if str(AUTOPUSH_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOPUSH_DIR))


def test_autovideo_base_url_default_points_to_current_local_server(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_BASE_URL", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    settings = importlib.import_module("backend.settings")
    settings.get_settings.cache_clear()
    settings = importlib.reload(settings)
    settings.get_settings.cache_clear()

    assert settings.get_settings().autovideo_base_url == "http://172.30.254.14"
