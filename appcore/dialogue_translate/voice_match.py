from __future__ import annotations

import base64
import logging
import math
import subprocess
from pathlib import Path
from typing import Any, Iterable

INSUFFICIENT_SAMPLE_REASON = "insufficient_speaker_sample"
MALFORMED_SEGMENT_REASON = "malformed_dialogue_segment"
NO_VOICE_CANDIDATES_REASON = "no_voice_candidates"
VOICE_AI_SELECTION_FAILED_REASON = "voice_ai_selection_failed"
_SPEAKERS = ("A", "B")

log = logging.getLogger(__name__)


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def resolve_default_voice(lang: str, user_id: int | None = None):
    from appcore.video_translate_defaults import resolve_default_voice as _resolve_default_voice

    return _resolve_default_voice(lang, user_id=user_id)


def _append_warning(warnings: list[str], reason: str) -> None:
    if reason not in warnings:
        warnings.append(reason)


def _valid_dialogue_segments(dialogue_segments: Iterable[dict]) -> tuple[list[dict], int]:
    valid_segments: list[dict] = []
    malformed_count = 0
    for segment in dialogue_segments or []:
        if isinstance(segment, dict):
            valid_segments.append(segment)
        else:
            malformed_count += 1
    return valid_segments, malformed_count


def build_speaker_sample_windows(
    dialogue_segments: Iterable[dict],
    min_duration: float = 3.0,
    target_duration: float = 10.0,
) -> dict[str, dict]:
    usable_by_speaker: dict[str, list[dict[str, float]]] = {speaker: [] for speaker in _SPEAKERS}
    valid_segments, malformed_count = _valid_dialogue_segments(dialogue_segments)
    for segment in valid_segments:
        speaker = segment.get("speaker_id")
        if speaker not in usable_by_speaker:
            continue
        if segment.get("review_required") or segment.get("overlap"):
            continue
        start = _safe_float(segment.get("start_time"))
        end = _safe_float(segment.get("end_time"))
        if start is None or end is None:
            malformed_count += 1
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
        warnings: list[str] = []
        if malformed_count:
            _append_warning(warnings, MALFORMED_SEGMENT_REASON)
        if total < min_duration:
            _append_warning(warnings, INSUFFICIENT_SAMPLE_REASON)
        result[speaker] = {
            "sample_windows": windows,
            "sample_duration": round(total, 3),
            "match_warnings": warnings,
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


def _validate_sample_window(window: Any) -> tuple[float, float]:
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise ValueError("invalid sample window")
    start = _safe_float(window[0])
    end = _safe_float(window[1])
    if start is None or end is None or end <= start:
        raise ValueError("invalid sample window")
    return start, end


def extract_sample_for_windows(video_path: str, windows: list[list[float]], out_path: Path) -> str:
    if not windows:
        raise ValueError("sample windows are empty")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_paths: list[Path] = []
    list_path = out_path.with_suffix(out_path.suffix + ".concat.txt")
    try:
        for index, window in enumerate(windows):
            start, end = _validate_sample_window(window)
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
    valid_segments, _malformed_count = _valid_dialogue_segments(dialogue_segments)
    return [segment for segment in valid_segments if segment.get("speaker_id") == speaker]


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

    _valid_segments, malformed_count = _valid_dialogue_segments(dialogue_segments)
    specs = sample_specs or build_speaker_sample_windows(dialogue_segments)
    default_voice = resolve_default_voice(target_lang, user_id=user_id)
    exclude_voice_ids = [default_voice] if default_voice else None
    profiles: dict[str, dict] = {}
    out_dir = Path(task_dir)

    for speaker in _SPEAKERS:
        spec = specs.get(speaker) or {}
        windows = spec.get("sample_windows") or []
        warnings = list(spec.get("match_warnings") or [])
        if malformed_count:
            _append_warning(warnings, MALFORMED_SEGMENT_REASON)
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
        normalized_candidates = [_candidate_with_float_similarity(candidate) for candidate in candidates]
        if not normalized_candidates:
            _append_warning(warnings, NO_VOICE_CANDIDATES_REASON)
        profile.update(
            {
                "sample_path": sample_path,
                "query_embedding": base64.b64encode(serialized).decode("ascii"),
                "candidates": normalized_candidates,
                "match_warnings": warnings,
            }
        )
        profiles[speaker] = profile

    return profiles


def _voice_id_from(value: object) -> str:
    if isinstance(value, dict):
        for key in ("voice_id", "elevenlabs_voice_id", "id"):
            voice_id = str(value.get(key) or "").strip()
            if voice_id:
                return voice_id
    elif value:
        return str(value).strip()
    return ""


def _voice_name_from(value: object, voice_id: str) -> str:
    if isinstance(value, dict):
        for key in ("name", "voice_name", "label"):
            name = str(value.get(key) or "").strip()
            if name:
                return name
    return voice_id


def _rank_from(value: object) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _selected_voice_from_candidate(candidate: object) -> dict | None:
    voice_id = _voice_id_from(candidate)
    if not voice_id:
        return None

    selected = {
        "voice_id": voice_id,
        "name": _voice_name_from(candidate, voice_id),
    }
    if not isinstance(candidate, dict):
        return selected

    for key in (
        "voice_name",
        "elevenlabs_voice_id",
        "id",
        "language",
        "gender",
        "similarity",
        "preview_url",
        "provider",
        "llm_rank",
        "llm_reason_summary",
    ):
        value = candidate.get(key)
        if value not in (None, ""):
            selected[key] = value
    reason = candidate.get("reason_summary")
    if reason and not selected.get("llm_reason_summary"):
        selected["llm_reason_summary"] = reason
    return selected


def _merge_ranking_row(candidate: object, row: dict) -> dict:
    merged = dict(candidate) if isinstance(candidate, dict) else {}
    voice_id = str(row.get("voice_id") or "").strip()
    if voice_id:
        merged.setdefault("voice_id", voice_id)
    rank = _rank_from(row.get("llm_rank", row.get("rank")))
    if rank is not None:
        merged["llm_rank"] = rank
    reason = row.get("reason_summary") or row.get("reason")
    if reason and not merged.get("llm_reason_summary"):
        merged["llm_reason_summary"] = reason
    return merged


def _llm_rank_one_candidate(candidates: Iterable[dict], rankings: Iterable[dict]) -> dict | None:
    candidate_rows = [dict(candidate) for candidate in candidates or [] if isinstance(candidate, dict)]
    for candidate in candidate_rows:
        if _rank_from(candidate.get("llm_rank")) == 1 and _voice_id_from(candidate):
            return candidate

    by_voice_id = {
        _voice_id_from(candidate): candidate
        for candidate in candidate_rows
        if _voice_id_from(candidate)
    }
    for row in rankings or []:
        if not isinstance(row, dict):
            continue
        if _rank_from(row.get("llm_rank", row.get("rank"))) != 1:
            continue
        voice_id = str(row.get("voice_id") or "").strip()
        if not voice_id:
            continue
        return _merge_ranking_row(by_voice_id.get(voice_id) or {}, row)
    return None


def _speaker_task_for_voice_ai(task: dict, speaker: str, profile: dict, dialogue_segments: list[dict]) -> dict:
    speaker_segments = _speaker_utterances(dialogue_segments, speaker)
    speaker_task = dict(task or {})
    speaker_task["dialogue_speaker_id"] = speaker
    speaker_task["dialogue_speaker_sample_path"] = profile.get("sample_path")
    speaker_task["dialogue_speaker_sample_windows"] = profile.get("sample_windows") or []
    speaker_task["dialogue_segments"] = speaker_segments
    speaker_task["utterances"] = speaker_segments
    speaker_task["utterances_en"] = speaker_segments
    return speaker_task


def auto_select_speaker_voices_with_ai(
    *,
    task_id: str,
    task: dict,
    profiles: dict[str, dict],
    task_dir: str | Path,
    dialogue_segments: list[dict],
    user_id: int | None = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Run the normal voice AI ranking once per dialogue speaker and select rank 1."""
    from appcore.voice_ai_ranking import rank_voice_candidates

    normalized_profiles: dict[str, dict] = {
        speaker: dict(profile) if isinstance(profile, dict) else {}
        for speaker, profile in (profiles or {}).items()
    }
    selected_by_speaker: dict[str, dict] = {}
    base_task_dir = Path(task_dir or ".")

    for speaker in _SPEAKERS:
        profile = normalized_profiles.setdefault(speaker, {})
        candidates = profile.get("candidates") or []
        sample_path = str(profile.get("sample_path") or "").strip()
        warnings = list(profile.get("match_warnings") or [])

        if not candidates or not sample_path:
            _append_warning(warnings, VOICE_AI_SELECTION_FAILED_REASON)
            profile.update(
                {
                    "match_warnings": warnings,
                    "voice_ai_rank_status": "skipped",
                    "voice_ai_rankings": [],
                    "selected_voice": None,
                }
            )
            continue

        speaker_task = _speaker_task_for_voice_ai(task, speaker, profile, dialogue_segments)
        ai_result: dict
        try:
            ai_result = rank_voice_candidates(
                task_id=task_id,
                task=speaker_task,
                candidates=candidates,
                source_audio_path=sample_path,
                task_dir=base_task_dir / "dialogue_voice_ai" / f"speaker_{speaker}",
                user_id=user_id,
            )
        except Exception as exc:
            log.exception("dialogue voice AI ranking failed task=%s speaker=%s: %s", task_id, speaker, exc)
            _append_warning(warnings, VOICE_AI_SELECTION_FAILED_REASON)
            profile.update(
                {
                    "match_warnings": warnings,
                    "voice_ai_rank_status": "failed",
                    "voice_ai_rankings": [],
                    "voice_ai_rank_debug": {
                        "status": "failed",
                        "use_case": "voice_selection.assess",
                        "request": {"visual": {"media": [], "candidates": candidates[:10]}, "raw": {}},
                        "result": {"visual": {"rankings": []}, "raw": {"error": str(exc)[:500]}},
                    },
                    "selected_voice": None,
                }
            )
            continue

        ranked_candidates = ai_result.get("candidates") or candidates
        rankings = ai_result.get("rankings") or []
        selected_candidate = _llm_rank_one_candidate(ranked_candidates, rankings)
        selected_voice = _selected_voice_from_candidate(selected_candidate)
        if selected_voice is None:
            _append_warning(warnings, VOICE_AI_SELECTION_FAILED_REASON)

        profile.update(
            {
                "candidates": ranked_candidates,
                "match_warnings": warnings,
                "voice_ai_rank_status": ai_result.get("status") or "",
                "voice_ai_rankings": rankings,
                "voice_ai_rank_model": ai_result.get("model"),
                "voice_ai_rank_provider": ai_result.get("provider"),
                "voice_ai_rank_candidate_limit": ai_result.get("candidate_limit"),
                "voice_ai_rank_usage_log_id": ai_result.get("usage_log_id"),
                "voice_ai_rank_debug": ai_result.get("debug"),
                "selected_voice": selected_voice,
            }
        )
        if selected_voice is not None:
            selected_by_speaker[speaker] = selected_voice

    return normalized_profiles, selected_by_speaker


def _window_duration(window: Any) -> float:
    try:
        start, end = _validate_sample_window(window)
    except ValueError:
        return 0.0
    return max(0.0, end - start)
