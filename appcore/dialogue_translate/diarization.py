from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import requests


class DiarizationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpDiarizationClient:
    endpoint: str
    timeout_seconds: int = 120

    def run(self, audio_path: str, task_id: str) -> list[dict]:
        path = Path(audio_path)
        if not path.exists():
            raise DiarizationUnavailable(f"audio file does not exist: {audio_path}")

        try:
            with path.open("rb") as audio_file:
                response = requests.post(
                    self.endpoint,
                    files={"audio": audio_file},
                    data={"task_id": task_id},
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload: Any = response.json()
        except DiarizationUnavailable:
            raise
        except Exception as exc:
            raise DiarizationUnavailable(f"diarization request failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise DiarizationUnavailable("diarization response must be a JSON object")
        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise DiarizationUnavailable("diarization response missing segments list")
        return segments


def resolve_diarization_client() -> HttpDiarizationClient:
    endpoint = (os.environ.get("DIALOGUE_DIARIZATION_URL") or "").strip()
    if not endpoint:
        raise DiarizationUnavailable("DIALOGUE_DIARIZATION_URL is not configured")

    raw_timeout = (os.environ.get("DIALOGUE_DIARIZATION_TIMEOUT") or "").strip()
    timeout_seconds = 120
    if raw_timeout:
        try:
            timeout_seconds = int(raw_timeout)
        except ValueError as exc:
            raise DiarizationUnavailable("DIALOGUE_DIARIZATION_TIMEOUT must be an integer") from exc
        if timeout_seconds <= 0:
            raise DiarizationUnavailable("DIALOGUE_DIARIZATION_TIMEOUT must be positive")

    return HttpDiarizationClient(endpoint=endpoint, timeout_seconds=timeout_seconds)
