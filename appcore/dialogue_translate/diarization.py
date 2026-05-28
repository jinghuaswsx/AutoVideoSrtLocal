from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any

import requests


class DiarizationUnavailable(RuntimeError):
    pass


_SPEAKER_KEYS = ("speaker_id", "speaker", "speaker_label", "channel_tag")


def _speaker_label(segment: dict) -> str:
    for key in _SPEAKER_KEYS:
        value = str(segment.get(key) or "").strip()
        if value:
            return value
    return ""


def _finite_float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise DiarizationUnavailable(f"invalid diarization time: {value!r}") from exc
    if not math.isfinite(parsed):
        raise DiarizationUnavailable(f"invalid diarization time: {value!r}")
    return parsed


def _validate_segments(segments: list) -> list[dict]:
    if not segments:
        raise DiarizationUnavailable("diarization response contains no segments")

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise DiarizationUnavailable(f"diarization segment {index} must be an object")
        if not _speaker_label(segment):
            raise DiarizationUnavailable(f"diarization segment {index} missing speaker label")
        start_time = _finite_float(segment.get("start_time"))
        end_time = _finite_float(segment.get("end_time"))
        if end_time <= start_time:
            raise DiarizationUnavailable(f"diarization segment {index} must have positive duration")
    return segments


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
        return _validate_segments(segments)


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
