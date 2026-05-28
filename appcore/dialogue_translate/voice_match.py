from __future__ import annotations

import base64
import math
import subprocess
from pathlib import Path
from typing import Any, Iterable

INSUFFICIENT_SAMPLE_REASON = "insufficient_speaker_sample"
_SPEAKERS = ("A", "B")


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def resolve_default_voice(lang: str, user_id: int | None = None):
    from appcore.video_translate_defaults import resolve_default_voice as _resolve_default_voice

    return _resolve_default_voice(lang, user_id=user_id)


def build_speaker_sample_windows(
    dialogue_segments: Iterable[dict],
    min_duration: float = 3.0,
    target_duration: float = 10.0,
) -> dict[str, dict]:
    usable_by_speaker: dict[str, list[dict[str, float]]] = {speaker: [] for speaker in _SPEAKERS}
    for segment in dialogue_segments or []:
        speaker = segment.get("speaker_id")
        if speaker not in usable_by_speaker:
            continue
        if segment.get("review_required") or segment.get("overlap"):
            continue
        start = _safe_float(segment.get("start_time"))
        end = _safe_float(segment.get("end_time"))
        if start is None or end is None:
            continue
        duration = max(0.0, end - start)
        if duration <= 0:
            continue
        usable_by_speaker[speaker].append({"start": start, "end": end, "duration": duration})

    result: dict[str, dict] = {}
    for speaker in _SPEAKERS:
        windows: list[list[float]] = []
        total = 0.0
        segments = sorted(usable_by_speaker[speaker], key=lambda item: item["duration"], reverse=True)
        for segment in segments:
            if total >= target_duration:
                break
            windows.append([segment["start"], segment["end"]])
            total += segment["duration"]
        result[speaker] = {
            "sample_windows": windows,
            "sample_duration": round(total, 3),
            "match_warnings": [] if total >= min_duration else [INSUFFICIENT_SAMPLE_REASON],
        }
    return result


def _run_ffmpeg(cmd: list[str], description: str) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for dialogue voice sample extraction") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise RuntimeError(f"{description} failed: {detail}") from exc
        raise RuntimeError(f"{description} failed with exit code {exc.returncode}") from exc


def _concat_file_line(path: Path) -> str:
    return "file '" + path.as_posix().replace("'", "'\\''") + "'"


def extract_sample_for_windows(video_path: str, windows: list[list[float]], out_path: Path) -> str:
    if not windows:
        raise ValueError("sample windows are empty")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_paths: list[Path] = []
    list_path = out_path.with_suffix(out_path.suffix + ".concat.txt")
    try:
        for index, window in enumerate(windows):
            if len(window) < 2:
                continue
            start = _safe_float(window[0])
            end = _safe_float(window[1])
            if start is None or end is None or end <= start:
                continue
            temp_path = out_path.with_name(f"{out_path.stem}.part{index}.wav")
            temp_paths.append(temp_path)
            _run_ffmpeg(
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
                    str(temp_path),
                ],
                "ffmpeg sample window extraction",
            )
        if not temp_paths:
            raise ValueError("sample windows contain no positive durations")

        list_path.write_text("\n".join(_concat_file_line(path) for path in temp_paths), encoding="utf-8")
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(out_path),
            ],
            "ffmpeg sample concat",
        )
        return str(out_path)
    finally:
        for path in [*temp_paths, list_path]:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def _speaker_utterances(dialogue_segments: Iterable[dict], speaker: str) -> list[dict]:
    return [segment for segment in dialogue_segments or [] if segment.get("speaker_id") == speaker]


def _candidate_with_float_similarity(candidate: dict) -> dict:
    item = dict(candidate)
    if "similarity" in item:
        try:
            item["similarity"] = float(item["similarity"])
        except (TypeError, ValueError):
            item["similarity"] = 0.0
    return item


def match_voices_for_speakers(
    *,
    video_path: str,
    task_dir: str,
    target_lang: str,
    dialogue_segments: list[dict],
    sample_specs: dict[str, dict] | None = None,
    user_id: int | None = None,
) -> dict[str, dict]:
    from pipeline.voice_embedding import embed_audio_file, serialize_embedding
    from pipeline.voice_match_speed import match_candidates_speed_aware

    specs = sample_specs or build_speaker_sample_windows(dialogue_segments)
    default_voice = resolve_default_voice(target_lang, user_id=user_id)
    exclude_voice_ids = [default_voice] if default_voice else None
    profiles: dict[str, dict] = {}
    out_dir = Path(task_dir)

    for speaker in _SPEAKERS:
        spec = specs.get(speaker) or {}
        windows = spec.get("sample_windows") or []
        warnings = list(spec.get("match_warnings") or [])
        sample_duration = spec.get("sample_duration")
        if sample_duration is None:
            sample_duration = round(sum(_window_duration(window) for window in windows), 3)

        profile = {
            "sample_path": None,
            "sample_windows": windows,
            "sample_duration": sample_duration,
            "query_embedding": None,
            "candidates": [],
            "selected_voice": None,
            "match_warnings": warnings,
        }
        if not windows:
            profiles[speaker] = profile
            continue

        sample_path = extract_sample_for_windows(
            video_path,
            windows,
            out_dir / f"speaker_{speaker}_voice_sample.wav",
        )
        query_embedding = embed_audio_file(sample_path)
        serialized = serialize_embedding(query_embedding)
        candidates = match_candidates_speed_aware(
            query_embedding,
            language=target_lang,
            source_utterances=_speaker_utterances(dialogue_segments, speaker),
            candidate_pool_size=20,
            top_k=20,
            exclude_voice_ids=exclude_voice_ids,
        )
        profile.update(
            {
                "sample_path": sample_path,
                "query_embedding": base64.b64encode(serialized).decode("ascii"),
                "candidates": [_candidate_with_float_similarity(candidate) for candidate in candidates],
            }
        )
        profiles[speaker] = profile

    return profiles


def _window_duration(window: Any) -> float:
    if not isinstance(window, (list, tuple)) or len(window) < 2:
        return 0.0
    start = _safe_float(window[0])
    end = _safe_float(window[1])
    if start is None or end is None:
        return 0.0
    return max(0.0, end - start)
