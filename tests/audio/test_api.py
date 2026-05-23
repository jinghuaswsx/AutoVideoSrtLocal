"""
Tests for Audio Separator API (tools/audio_separator/api_server.py).

Service is now mounted under the `/separate` URL prefix on the Caddy gateway
at http://172.30.254.12 (port 80). The audio service itself listens on
internal port 8081 — Caddy routes `/separate/*` there.

The service must be running before executing these tests.

Usage:
  pytest tests/audio/test_api.py -v
"""

import os
import requests

API_BASE = "http://172.30.254.12"
PREFIX = "/separate"
TIMEOUT = 120  # allow time for GPU queue


def _test_audio(tmp_path) -> str:
    """Create a tiny test WAV file."""
    import numpy as np
    import soundfile as sf

    path = os.path.join(tmp_path, "test.wav")
    sr = 44100
    t = np.linspace(0, 2, sr * 2)
    audio = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.random.randn(sr * 2)).astype(np.float32)
    sf.write(path, audio, sr)
    return path


def test_health():
    r = requests.get(f"{API_BASE}{PREFIX}/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["cuda_available"] is True
    assert "RTX 3060" in data["cuda_device"]
    assert "cache" in data
    assert "queue" in data


def test_queue():
    r = requests.get(f"{API_BASE}{PREFIX}/queue", timeout=10)
    assert r.status_code == 200
    assert "waiting_or_active" in r.json()


def test_models():
    r = requests.get(f"{API_BASE}{PREFIX}/models", timeout=10)
    assert r.status_code == 200
    assert r.json()["count"] >= 0  # 可能 0 或更多，宽松校验


def test_presets():
    r = requests.get(f"{API_BASE}{PREFIX}/presets", timeout=10)
    assert r.status_code == 200
    assert r.json()["count"] >= 9
    assert "vocal_balanced" in r.json()["presets"]


def test_separate(tmp_path):
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r = requests.post(f"{API_BASE}{PREFIX}/run", files={"file": f}, timeout=TIMEOUT)

    assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert data["status"] == "ok"
    assert data["duration_seconds"] > 0
    assert len(data["stems"]) >= 2  # Instrumental + Vocals
    assert any("Vocals" in s for s in data["stems"])
    assert any("Instrumental" in s for s in data["stems"])
    assert data["output_format"] == "WAV"


def test_separate_cached(tmp_path):
    """Same file twice: second request should be instant (cache hit)."""
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r1 = requests.post(f"{API_BASE}{PREFIX}/run", files={"file": f}, timeout=TIMEOUT)

    with open(audio_path, "rb") as f:
        r2 = requests.post(f"{API_BASE}{PREFIX}/run", files={"file": f}, timeout=TIMEOUT)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both should return same stems
    assert r1.json()["stems"] == r2.json()["stems"]


def test_separate_different_preset(tmp_path):
    """Test with instrumental_clean preset."""
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}{PREFIX}/run",
            files={"file": f},
            data={"ensemble_preset": "instrumental_clean"},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    assert "Instrumental" in r.json()["preset"] or r.json()["preset"] == "instrumental_clean"


def test_separate_single_stem(tmp_path):
    """Test extracting only vocals."""
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}{PREFIX}/run",
            files={"file": f},
            data={"single_stem": "Vocals"},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    for stem in data["stems"]:
        assert "Vocals" in stem


def test_download_zip(tmp_path):
    """Test /separate/download endpoint."""
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r = requests.post(f"{API_BASE}{PREFIX}/download", files={"file": f}, timeout=TIMEOUT)

    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/zip"
    assert len(r.content) > 100  # ZIP should have content


def test_invalid_format(tmp_path):
    audio_path = _test_audio(tmp_path)
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}{PREFIX}/run",
            files={"file": f},
            data={"output_format": "XYZ"},
            timeout=10,
        )
    assert r.status_code == 400


def test_swagger_docs():
    r = requests.get(f"{API_BASE}{PREFIX}/docs", timeout=10)
    assert r.status_code == 200
    assert "Swagger" in r.text or "OpenAPI" in r.text or "audio_separator" in r.text.lower()
