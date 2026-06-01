from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
import wave

import numpy as np
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


def validate_diarization_segments(segments: list) -> list[dict]:
    if not isinstance(segments, list):
        raise DiarizationUnavailable("diarization segments must be a list")
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


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _extract_audio_to_wav(source_path: Path, out_path: Path) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(out_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise DiarizationUnavailable("ffmpeg is required for local acoustic diarization") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise DiarizationUnavailable(f"ffmpeg audio extraction failed: {detail}") from exc
        raise DiarizationUnavailable(
            f"ffmpeg audio extraction failed with exit code {exc.returncode}"
        ) from exc


def _load_wav_samples(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except wave.Error as exc:
        raise DiarizationUnavailable(f"invalid wav audio for local diarization: {exc}") from exc

    if sample_width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise DiarizationUnavailable(f"unsupported wav sample width: {sample_width}")

    if channels > 1:
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    if samples.size == 0 or sample_rate <= 0:
        raise DiarizationUnavailable("local diarization audio contains no samples")
    return samples.astype(np.float32, copy=False), sample_rate


def _read_audio_samples(audio_path: str) -> tuple[np.ndarray, int]:
    source_path = Path(audio_path)
    if not source_path.exists():
        raise DiarizationUnavailable(f"audio file does not exist: {audio_path}")
    if source_path.suffix.lower() == ".wav":
        return _load_wav_samples(source_path)

    with tempfile.TemporaryDirectory(prefix="dialogue_diar_") as temp_dir:
        wav_path = Path(temp_dir) / "audio.wav"
        _extract_audio_to_wav(source_path, wav_path)
        return _load_wav_samples(wav_path)


def _utterance_window(utterance: dict, sample_rate: int, sample_count: int) -> tuple[int, int] | None:
    start = _safe_float(utterance.get("start_time"))
    end = _safe_float(utterance.get("end_time"))
    if start is None or end is None or end <= start:
        return None
    start_index = max(0, min(sample_count, int(start * sample_rate)))
    end_index = max(0, min(sample_count, int(end * sample_rate)))
    if end_index <= start_index:
        return None
    return start_index, end_index


def _band_energy(spectrum: np.ndarray, freqs: np.ndarray, low: float, high: float) -> float:
    mask = (freqs >= low) & (freqs < high)
    if not np.any(mask):
        return 0.0
    return float(np.sum(spectrum[mask]))


def _audio_features(samples: np.ndarray, sample_rate: int) -> np.ndarray | None:
    if samples.size < max(128, int(sample_rate * 0.05)):
        return None
    window = samples.astype(np.float32, copy=True)
    window -= float(np.mean(window))
    peak = float(np.max(np.abs(window))) if window.size else 0.0
    if peak <= 1e-6:
        return None
    window /= peak
    rms = float(np.sqrt(np.mean(np.square(window))))
    signs = np.signbit(window)
    zcr = float(np.mean(signs[1:] != signs[:-1])) if window.size > 1 else 0.0
    tapered = window * np.hanning(window.size)
    spectrum = np.abs(np.fft.rfft(tapered)) + 1e-9
    freqs = np.fft.rfftfreq(tapered.size, d=1.0 / sample_rate)
    total = float(np.sum(spectrum))
    centroid = float(np.sum(freqs * spectrum) / total)
    bandwidth = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * spectrum) / total))
    cumulative = np.cumsum(spectrum)
    rolloff_idx = int(np.searchsorted(cumulative, cumulative[-1] * 0.85))
    rolloff = float(freqs[min(rolloff_idx, freqs.size - 1)])
    nyquist = max(1.0, sample_rate / 2.0)
    bands = [
        _band_energy(spectrum, freqs, low, high)
        for low, high in (
            (0.0, 250.0),
            (250.0, 500.0),
            (500.0, 1000.0),
            (1000.0, 2000.0),
            (2000.0, 4000.0),
            (4000.0, nyquist + 1.0),
        )
    ]
    band_total = sum(bands) or 1.0
    band_features = [math.log1p(value / band_total) for value in bands]
    return np.array(
        [
            math.log1p(rms * 1000.0),
            zcr,
            centroid / nyquist,
            bandwidth / nyquist,
            rolloff / nyquist,
            *band_features,
        ],
        dtype=np.float32,
    )


def _normalize_features(features: np.ndarray) -> np.ndarray:
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std < 1e-6] = 1.0
    return (features - mean) / std


def _initial_centroids(features: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    if features.shape[0] < 2:
        return None
    distances = np.linalg.norm(features[:, None, :] - features[None, :, :], axis=2)
    row, col = np.unravel_index(int(np.argmax(distances)), distances.shape)
    if float(distances[row, col]) < 1e-6:
        return None
    return features[row].copy(), features[col].copy()


def _cluster_two_speakers(features: np.ndarray) -> tuple[list[int], list[float]]:
    if features.shape[0] == 1:
        return [0], [0.72]
    normalized = _normalize_features(features)
    initial = _initial_centroids(normalized)
    if initial is None:
        return [0 for _ in range(features.shape[0])], [0.72 for _ in range(features.shape[0])]
    centroids = [initial[0], initial[1]]
    labels = np.zeros(features.shape[0], dtype=np.int32)
    for _ in range(25):
        distances = np.stack(
            [
                np.linalg.norm(normalized - centroids[0], axis=1),
                np.linalg.norm(normalized - centroids[1], axis=1),
            ],
            axis=1,
        )
        next_labels = np.argmin(distances, axis=1)
        if np.array_equal(next_labels, labels):
            break
        labels = next_labels
        for cluster in (0, 1):
            members = normalized[labels == cluster]
            if members.size:
                centroids[cluster] = members.mean(axis=0)

    distances = np.stack(
        [
            np.linalg.norm(normalized - centroids[0], axis=1),
            np.linalg.norm(normalized - centroids[1], axis=1),
        ],
        axis=1,
    )
    confidences: list[float] = []
    for row in distances:
        best = float(np.min(row))
        other = float(np.max(row))
        margin = (other - best) / max(1e-6, other + best)
        confidences.append(round(max(0.72, min(0.98, 0.78 + 0.20 * margin)), 4))
    return [int(label) for label in labels], confidences


def local_acoustic_diarization_segments(
    *,
    audio_path: str,
    utterances: list[dict],
    task_id: str,
) -> list[dict]:
    """Best-effort two-speaker diarization using per-utterance acoustic features."""
    samples, sample_rate = _read_audio_samples(audio_path)
    feature_rows: list[np.ndarray] = []
    feature_indices: list[int] = []
    for index, utterance in enumerate(utterances or []):
        if not isinstance(utterance, dict):
            continue
        window = _utterance_window(utterance, sample_rate, samples.size)
        if window is None:
            continue
        start_index, end_index = window
        features = _audio_features(samples[start_index:end_index], sample_rate)
        if features is None:
            continue
        feature_indices.append(index)
        feature_rows.append(features)

    if not feature_rows:
        raise DiarizationUnavailable(
            f"local acoustic diarization found no usable utterance audio for task {task_id}"
        )

    cluster_labels, confidences = _cluster_two_speakers(np.vstack(feature_rows))
    cluster_order: dict[int, int] = {}
    for cluster in cluster_labels:
        cluster_order.setdefault(cluster, len(cluster_order))

    assigned_by_index = {
        utterance_index: (
            f"local_{cluster_order[cluster_labels[row_index]]}",
            confidences[row_index],
        )
        for row_index, utterance_index in enumerate(feature_indices)
    }

    segments: list[dict] = []
    last_label = "local_0"
    for index, utterance in enumerate(utterances or []):
        if not isinstance(utterance, dict):
            continue
        start = _safe_float(utterance.get("start_time"))
        end = _safe_float(utterance.get("end_time"))
        if start is None or end is None or end <= start:
            continue
        label, confidence = assigned_by_index.get(index, (last_label, 0.55))
        last_label = label
        segments.append(
            {
                "speaker": label,
                "start_time": start,
                "end_time": end,
                "confidence": confidence,
                "source": "local_acoustic_diarization",
            }
        )

    return validate_diarization_segments(segments)


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
        return validate_diarization_segments(segments)


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
