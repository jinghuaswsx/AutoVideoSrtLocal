import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOLC_API_KEY", "test-volc-key")
    monkeypatch.setenv("VOLC_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("TOS_ACCESS_KEY", "test-tos-ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "test-tos-sk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("VOICES_FILE", str(ROOT / "voices" / "voices.json"))
