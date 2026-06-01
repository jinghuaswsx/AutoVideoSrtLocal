from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Iterable

from appcore.dialogue_translate.voice_match import extract_sample_for_windows


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_index(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _speaker_id(value: object) -> str:
    speaker = str(value or "").strip().upper()
    return speaker if speaker in {"A", "B"} else "unknown"


def _relative_path(path: Path, task_dir: Path) -> str:
    return path.relative_to(task_dir).as_posix()


def _run_ffmpeg_clip(video_path: str, start: float, end: float, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                video_path,
                "-t",
                f"{end - start:.3f}",
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
        raise RuntimeError("ffmpeg is required for dialogue sentence audio extraction") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise RuntimeError(f"ffmpeg dialogue sentence audio extraction failed: {detail}") from exc
        raise RuntimeError(
            f"ffmpeg dialogue sentence audio extraction failed with exit code {exc.returncode}"
        ) from exc


def _valid_segments(dialogue_segments: Iterable[dict]) -> list[dict]:
    return [
        dict(segment)
        for segment in (dialogue_segments or [])
        if isinstance(segment, dict)
    ]


def build_dialogue_segment_audio_assets(
    *,
    video_path: str,
    task_dir: str,
    dialogue_segments: list[dict],
) -> dict:
    """Extract one protected-playback source clip per dialogue sentence."""
    base_dir = Path(task_dir)
    out_dir = base_dir / "dialogue_segments"
    out_dir.mkdir(parents=True, exist_ok=True)

    enriched: list[dict] = []
    manifest_segments: list[dict] = []
    windows_by_speaker: dict[str, list[list[float]]] = {"A": [], "B": []}

    for position, segment in enumerate(_valid_segments(dialogue_segments)):
        index = _safe_index(segment.get("index"), position)
        speaker = _speaker_id(segment.get("speaker_id"))
        start = _safe_float(segment.get("start_time"))
        end = _safe_float(segment.get("end_time"))
        item = dict(segment)
        if start is None or end is None or end <= start:
            item["source_audio_error"] = "invalid_time_window"
            enriched.append(item)
            continue

        filename = f"segment_{index:03d}_speaker_{speaker}.wav"
        out_path = out_dir / filename
        _run_ffmpeg_clip(video_path, start, end, out_path)
        relpath = _relative_path(out_path, base_dir)
        item["source_audio_relpath"] = relpath
        enriched.append(item)
        manifest_segments.append(
            {
                "index": index,
                "speaker_id": speaker,
                "start_time": round(start, 3),
                "end_time": round(end, 3),
                "duration": round(end - start, 3),
                "source_audio_relpath": relpath,
            }
        )
        if speaker in windows_by_speaker:
            windows_by_speaker[speaker].append([start, end])

    speaker_audio_tracks: dict[str, dict] = {}
    for speaker, windows in windows_by_speaker.items():
        if not windows:
            continue
        out_path = out_dir / f"speaker_{speaker}_source.wav"
        extract_sample_for_windows(video_path, windows, out_path)
        speaker_audio_tracks[speaker] = {
            "relative_path": _relative_path(out_path, base_dir),
            "segment_count": len(windows),
            "duration": round(sum(max(0.0, end - start) for start, end in windows), 3),
        }

    return {
        "dialogue_segments": enriched,
        "dialogue_segment_audio_manifest": {
            "segments": manifest_segments,
            "count": len(manifest_segments),
        },
        "speaker_audio_tracks": speaker_audio_tracks,
    }
