"""Sidecar LLM ranking for TTS voice match candidates."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

import requests

from appcore import voice_preview_archive
from appcore.llm_client import invoke_generate

log = logging.getLogger(__name__)

VOICE_AI_USE_CASE = "voice_selection.assess"
VOICE_AI_MODEL = "google/gemini-3.5-flash"
VOICE_AI_PROVIDER = "openrouter"
MAX_VOICE_AI_CANDIDATES = 10
VOICE_AI_REASON_LIMIT = 30
SOURCE_SAMPLE_MIN_SECONDS = 3.0
SOURCE_SAMPLE_MAX_SECONDS = 10.0
PREVIEW_SAMPLE_MIN_SECONDS = 3.0
PREVIEW_SAMPLE_MAX_SECONDS = 10.0
MAX_PREVIEW_AUDIO_BYTES = 12 * 1024 * 1024

VOICE_AI_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "voice_id": {"type": "string"},
                    "llm_rank": {"type": "integer", "minimum": 1},
                    "reason_summary": {"type": "string"},
                },
                "required": ["voice_id", "llm_rank", "reason_summary"],
            },
        },
    },
    "required": ["rankings"],
}


def normalize_voice_ai_rankings(
    raw: object,
    candidates: Iterable[dict],
    *,
    max_candidates: int = MAX_VOICE_AI_CANDIDATES,
    reason_limit: int = VOICE_AI_REASON_LIMIT,
) -> list[dict]:
    allowed = [
        str(candidate.get("voice_id") or "").strip()
        for candidate in list(candidates)[:max_candidates]
    ]
    allowed_set = {voice_id for voice_id in allowed if voice_id}
    if not allowed_set:
        return []

    rows = _extract_ranking_rows(raw)
    seen: set[str] = set()
    normalized: list[tuple[int, int, dict]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        voice_id = str(row.get("voice_id") or "").strip()
        if not voice_id or voice_id in seen or voice_id not in allowed_set:
            continue
        rank = _coerce_rank(row.get("llm_rank", row.get("rank")))
        if rank is None:
            continue
        reason = _trim_reason(
            row.get("reason_summary") or row.get("reason") or row.get("summary") or "",
            limit=reason_limit,
        )
        seen.add(voice_id)
        normalized.append((
            rank,
            index,
            {"voice_id": voice_id, "llm_rank": rank, "reason_summary": reason},
        ))

    normalized.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in normalized]


def apply_voice_ai_rankings(candidates: Iterable[dict], rankings: Iterable[dict]) -> list[dict]:
    rank_by_voice_id = {
        str(row.get("voice_id") or "").strip(): row
        for row in rankings
        if str(row.get("voice_id") or "").strip()
    }
    enriched: list[dict] = []
    for candidate in candidates:
        item = dict(candidate)
        row = rank_by_voice_id.get(str(item.get("voice_id") or "").strip())
        if row:
            item["llm_rank"] = row.get("llm_rank")
            item["llm_reason_summary"] = row.get("reason_summary") or ""
        enriched.append(item)
    return enriched


def rank_voice_candidates(
    *,
    task_id: str,
    task: dict,
    candidates: list[dict],
    source_audio_path: str | Path | None,
    task_dir: str | Path,
    user_id: int | None,
    preview_downloader: Callable[[str, Path], str | Path] | None = None,
    audio_trimmer: Callable[[Path, Path], str | Path] | None = None,
) -> dict:
    top_candidates = list(candidates or [])[:MAX_VOICE_AI_CANDIDATES]
    if not top_candidates:
        return _empty_result("skipped", candidates)
    if not source_audio_path:
        return _empty_result("skipped", candidates)

    work_dir = Path(task_dir) / "voice_ai_ranking"
    work_dir.mkdir(parents=True, exist_ok=True)

    media_items: list[dict] = []
    preview_assets_by_voice_id: dict[str, dict] = {}
    source_sample = _prepare_audio_sample(
        Path(source_audio_path),
        work_dir / "source_sample.mp3",
        min_seconds=SOURCE_SAMPLE_MIN_SECONDS,
        max_seconds=SOURCE_SAMPLE_MAX_SECONDS,
        audio_trimmer=audio_trimmer,
    )
    media: list[str] = [str(source_sample)]
    media_items.append(_media_debug_item(
        role="source_sample",
        path=source_sample,
        task_dir=Path(task_dir),
    ))
    prompt_candidates = []
    downloader = preview_downloader or _download_preview_audio

    for index, candidate in enumerate(top_candidates, start=1):
        prompt_candidate = _candidate_prompt_payload(candidate, index)
        preview_url = str(candidate.get("preview_url") or "").strip()
        archive = _resolve_voice_preview_archive(task=task, candidate=candidate)
        local_preview_path = (
            Path(archive["local_path"]) if archive
            else _resolve_local_preview_audio_path(candidate, Path(task_dir))
        )
        if archive:
            _attach_archive_metadata(prompt_candidate, archive)
        if local_preview_path or preview_url:
            try:
                source_kind = "archive" if archive else "local"
                if local_preview_path:
                    downloaded_path = local_preview_path
                else:
                    source_kind = "downloaded"
                    downloaded_path = Path(downloader(
                        preview_url,
                        work_dir / f"candidate_{index:02d}_{_safe_stem(candidate.get('voice_id'), index)}{_suffix_from_url(preview_url)}",
                    ))
                preview_sample = _prepare_audio_sample(
                    Path(downloaded_path),
                    work_dir / f"candidate_{index:02d}_{_safe_stem(candidate.get('voice_id'), index)}_sample.mp3",
                    min_seconds=PREVIEW_SAMPLE_MIN_SECONDS,
                    max_seconds=PREVIEW_SAMPLE_MAX_SECONDS,
                    audio_trimmer=audio_trimmer,
                )
                media.append(str(preview_sample))
                prompt_candidate["audio_ref"] = f"candidate_audio_{index}"
                asset = _media_debug_item(
                    role="candidate_preview",
                    path=preview_sample,
                    task_dir=Path(task_dir),
                    voice_id=candidate.get("voice_id"),
                    match_order=index,
                    source_url=preview_url,
                )
                asset["source"] = source_kind
                media_items.append(asset)
                voice_id = str(candidate.get("voice_id") or "").strip()
                if voice_id:
                    preview_assets_by_voice_id[voice_id] = asset
                    prompt_candidate["local_preview_audio"] = {
                        "relative_path": asset.get("relative_path"),
                        "bytes": asset.get("bytes"),
                    }
            except Exception as exc:
                log.warning(
                    "voice AI ranking preview audio skipped task=%s voice=%s: %s",
                    task_id,
                    candidate.get("voice_id"),
                    exc,
                )
                prompt_candidate["audio_ref"] = "preview_unavailable"
        else:
            prompt_candidate["audio_ref"] = "preview_unavailable"
        prompt_candidates.append(prompt_candidate)

    prompt = _build_prompt(task=task, candidates=prompt_candidates)
    request_debug = _build_request_debug(
        prompt=prompt,
        media_items=media_items,
        prompt_candidates=prompt_candidates,
        task=task,
    )
    result = invoke_generate(
        VOICE_AI_USE_CASE,
        prompt=prompt,
        user_id=user_id,
        project_id=task_id,
        media=media,
        response_schema=VOICE_AI_RESPONSE_SCHEMA,
        temperature=0.2,
        max_output_tokens=1200,
        provider_override=VOICE_AI_PROVIDER,
        model_override=VOICE_AI_MODEL,
        billing_extra={
            "task_id": task_id,
            "candidate_count": len(top_candidates),
            "media_count": len(media),
        },
    )
    raw = result.get("json") if isinstance(result, dict) else None
    rankings = normalize_voice_ai_rankings(raw, top_candidates)
    enriched_top = apply_voice_ai_rankings(top_candidates, rankings)
    for item in enriched_top:
        asset = preview_assets_by_voice_id.get(str(item.get("voice_id") or "").strip())
        if asset:
            item["voice_ai_preview_audio_relpath"] = asset.get("relative_path")
    enriched_candidates = enriched_top + [dict(candidate) for candidate in list(candidates or [])[MAX_VOICE_AI_CANDIDATES:]]
    debug = {
        "status": "done",
        "provider": VOICE_AI_PROVIDER,
        "model": VOICE_AI_MODEL,
        "use_case": VOICE_AI_USE_CASE,
        "request": request_debug,
        "result": _build_result_debug(raw=raw, rankings=rankings, result=result),
    }
    return {
        "status": "done",
        "rankings": rankings,
        "candidates": enriched_candidates,
        "model": VOICE_AI_MODEL,
        "provider": VOICE_AI_PROVIDER,
        "debug": debug,
    }


def _empty_result(status: str, candidates: Iterable[dict]) -> dict:
    return {
        "status": status,
        "rankings": [],
        "candidates": [dict(candidate) for candidate in candidates or []],
        "model": VOICE_AI_MODEL,
        "provider": VOICE_AI_PROVIDER,
        "debug": {
            "status": status,
            "provider": VOICE_AI_PROVIDER,
            "model": VOICE_AI_MODEL,
            "use_case": VOICE_AI_USE_CASE,
            "request": {"visual": {"media": [], "candidates": []}, "raw": {}},
            "result": {"visual": {"rankings": []}, "raw": {}},
        },
    }


def _extract_ranking_rows(raw: object) -> list:
    if isinstance(raw, dict):
        rows = raw.get("rankings")
        return rows if isinstance(rows, list) else []
    if isinstance(raw, list):
        return raw
    return []


def _coerce_rank(value: object) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None


def _trim_reason(value: object, *, limit: int) -> str:
    reason = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(reason) <= limit:
        return reason
    return reason[:limit]


def _candidate_prompt_payload(candidate: dict, index: int) -> dict:
    keys = (
        "voice_id",
        "name",
        "provider",
        "gender",
        "locale",
        "accent",
        "age",
        "description",
        "descriptive",
        "similarity",
        "similarity_rank",
        "source_words_per_second",
        "preview_words_per_second",
        "speed_match_score",
        "voice_speed_status",
        "preview_url_hash",
        "preview_duration_seconds",
        "preview_transcript_text",
    )
    payload = {"match_order": index}
    for key in keys:
        value = candidate.get(key)
        if value is not None and value != "":
            payload[key] = value
    return payload


def _build_prompt(*, task: dict, candidates: list[dict]) -> str:
    source_text = _task_text_sample(task)
    context = {
        "target_lang": task.get("target_lang"),
        "source_language": task.get("source_language"),
        "source_text_sample": source_text,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    return (
        "You are evaluating TTS voices for video translation. "
        "Use the source speaker audio, the candidate preview audios, speed metadata, "
        "voice similarity, content, emotional tone, and expressiveness. "
        "Rank only the provided Top10 candidates. Penalize voices that sound strange, "
        "too sharp, harsh, unusable, emotionally mismatched, or clearly unsuitable. "
        "Do not change voice_id values.\n\n"
        "Media order: item 1 is the source speaker sample. Subsequent audio files are "
        "candidate previews in the same order as candidates whose audio_ref is "
        "candidate_audio_N.\n\n"
        "Return JSON only with rankings[]. Each row must contain voice_id, llm_rank, "
        "and reason_summary. reason_summary must be Chinese and no more than 30 characters.\n\n"
        f"Top10 context:\n{json.dumps(context, ensure_ascii=False, default=str)}"
    )


def _build_request_debug(
    *,
    prompt: str,
    media_items: list[dict],
    prompt_candidates: list[dict],
    task: dict,
) -> dict:
    raw_messages = [{
        "role": "user",
        "content": [{"type": "text", "text": prompt}] + [
            {
                "type": "input_audio",
                "input_audio": {
                    "data": f"[base64-audio from {item.get('filename')}, {item.get('bytes') or 0} bytes]",
                    "format": item.get("format") or _audio_format_from_path(item.get("filename") or ""),
                },
            }
            for item in media_items
        ],
    }]
    raw = {
        "use_case": VOICE_AI_USE_CASE,
        "provider": VOICE_AI_PROVIDER,
        "model": VOICE_AI_MODEL,
        "temperature": 0.2,
        "max_output_tokens": 1200,
        "response_schema": VOICE_AI_RESPONSE_SCHEMA,
        "messages": raw_messages,
    }
    return {
        "visual": {
            "target_lang": task.get("target_lang"),
            "source_language": task.get("source_language"),
            "media": media_items,
            "candidates": prompt_candidates,
        },
        "raw": raw,
    }


def _build_result_debug(*, raw: object, rankings: list[dict], result: dict) -> dict:
    usage = result.get("usage") if isinstance(result, dict) else None
    return {
        "visual": {
            "rankings": rankings,
            "usage": usage or {},
        },
        "raw": {
            "json": raw,
            "text": result.get("text") if isinstance(result, dict) else None,
            "usage": usage or {},
            "json_parse_error": result.get("json_parse_error") if isinstance(result, dict) else None,
        },
    }


def _media_debug_item(
    *,
    role: str,
    path: Path,
    task_dir: Path,
    voice_id: object | None = None,
    match_order: int | None = None,
    source_url: str | None = None,
) -> dict:
    item = {
        "role": role,
        "filename": path.name,
        "format": _audio_format_from_path(str(path)),
        "bytes": _file_size(path),
    }
    rel = _relative_path(path, task_dir)
    if rel:
        item["relative_path"] = rel
    if voice_id:
        item["voice_id"] = str(voice_id)
    if match_order is not None:
        item["match_order"] = match_order
    if source_url:
        item["source_url"] = source_url
    return item


def _resolve_voice_preview_archive(*, task: dict, candidate: dict) -> dict | None:
    voice_id = str(candidate.get("voice_id") or "").strip()
    language = str(
        candidate.get("language")
        or task.get("target_lang")
        or task.get("language")
        or ""
    ).strip().lower()
    preview_url = str(candidate.get("preview_url") or "").strip()
    preview_hash = str(candidate.get("preview_url_hash") or "").strip()
    if not preview_hash and preview_url:
        preview_hash = voice_preview_archive.hash_preview_url(preview_url)
    if not voice_id or not language or not preview_hash:
        return None
    try:
        return voice_preview_archive.resolve_local_preview_archive(
            language=language,
            voice_id=voice_id,
            preview_url_hash=preview_hash,
        )
    except Exception as exc:
        log.warning(
            "voice preview archive lookup skipped voice=%s language=%s: %s",
            voice_id,
            language,
            exc,
        )
        return None


def _attach_archive_metadata(prompt_candidate: dict, archive: dict) -> None:
    if archive.get("preview_url_hash"):
        prompt_candidate["preview_url_hash"] = archive.get("preview_url_hash")
    if archive.get("duration_seconds") is not None:
        prompt_candidate["preview_duration_seconds"] = archive.get("duration_seconds")
    transcript = str(archive.get("transcript_text") or "").strip()
    if transcript:
        prompt_candidate["preview_transcript_text"] = transcript
    utterances = archive.get("utterances_json")
    if isinstance(utterances, list) and utterances:
        prompt_candidate["preview_utterances_sample"] = [
            _utterance_prompt_sample(item)
            for item in utterances[:5]
            if isinstance(item, dict)
        ]


def _utterance_prompt_sample(item: dict) -> dict:
    return {
        "text": item.get("text") or item.get("transcript") or "",
        "start_time": item.get("start_time", item.get("start")),
        "end_time": item.get("end_time", item.get("end")),
    }


def _relative_path(path: Path, task_dir: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(task_dir.resolve())).replace("\\", "/")
    except (OSError, ValueError):
        return None


def _file_size(path: Path) -> int | None:
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def _audio_format_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix if suffix in {"mp3", "wav", "flac", "m4a", "ogg", "webm", "aac"} else "mp3"


def _task_text_sample(task: dict, *, limit: int = 1200) -> str:
    chunks = []
    for key in ("utterances_en", "utterances", "script_segments"):
        rows = task.get(key) or []
        if isinstance(rows, list):
            for row in rows[:30]:
                if isinstance(row, dict):
                    text = row.get("text") or row.get("translated") or row.get("source") or ""
                    if text:
                        chunks.append(str(text))
    if not chunks:
        localized = task.get("localized_translation") or {}
        if isinstance(localized, dict):
            chunks.append(str(localized.get("full_text") or ""))
    return " ".join(chunks).strip()[:limit]


def _download_preview_audio(url: str, dest: Path) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("preview_url must be http(s)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with requests.get(url, timeout=20, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_PREVIEW_AUDIO_BYTES:
                    raise RuntimeError("preview audio too large")
                fh.write(chunk)
    return dest


def _resolve_local_preview_audio_path(candidate: dict, task_dir: Path) -> Path | None:
    for key in (
        "local_preview_audio_path",
        "preview_audio_path",
        "preview_local_path",
        "preview_audio_local_path",
        "voice_audio_path",
        "audio_path",
    ):
        raw = str(candidate.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw)
        candidates = [path]
        if not path.is_absolute():
            candidates.append(task_dir / path)
        for item in candidates:
            try:
                if item.is_file():
                    return item
            except OSError:
                continue
    return None


def _prepare_audio_sample(
    src: Path,
    dest: Path,
    *,
    min_seconds: float,
    max_seconds: float,
    audio_trimmer: Callable[..., str | Path] | None,
) -> Path:
    if not src.is_file():
        raise RuntimeError(f"audio file does not exist: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if audio_trimmer is not None:
        result = audio_trimmer(
            src,
            dest,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            duration_seconds=max_seconds,
        )
        return Path(result)
    return _trim_audio_with_ffmpeg(
        src,
        dest,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
    )


def _trim_audio_with_ffmpeg(src: Path, dest: Path, *, min_seconds: float, max_seconds: float) -> Path:
    cut_seconds = _detect_breath_cut_seconds(src, min_seconds=min_seconds, max_seconds=max_seconds)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-t",
        f"{cut_seconds:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        if dest.is_file():
            return dest
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.warning("ffmpeg trim failed for voice AI ranking: %s", src, exc_info=True)
    if src.resolve() != dest.resolve():
        try:
            shutil.copy2(src, dest)
            return dest
        except OSError:
            pass
    return src


def _detect_breath_cut_seconds(src: Path, *, min_seconds: float, max_seconds: float) -> float:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(src),
        "-af",
        "silencedetect=noise=-35dB:d=0.15",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        starts = _parse_silence_starts((completed.stderr or "") + "\n" + (completed.stdout or ""))
        return _pick_breath_cut_seconds(starts, min_seconds=min_seconds, max_seconds=max_seconds)
    except (FileNotFoundError, OSError):
        return float(max_seconds)


def _parse_silence_starts(text: str) -> list[float]:
    starts = []
    for match in re.finditer(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", text or ""):
        try:
            starts.append(float(match.group(1)))
        except ValueError:
            continue
    return starts


def _pick_breath_cut_seconds(
    silence_starts: Iterable[float],
    *,
    min_seconds: float,
    max_seconds: float,
) -> float:
    valid = [
        float(value)
        for value in silence_starts
        if min_seconds <= float(value) <= max_seconds
    ]
    if valid:
        return round(min(valid), 3)
    return float(max_seconds)


def _safe_stem(value: object, fallback_index: int) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text[:48] or f"voice_{fallback_index}"


def _suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac", ".aac"}:
        return suffix
    return ".mp3"
