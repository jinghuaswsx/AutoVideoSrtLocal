from __future__ import annotations

import math
import re
from typing import Iterable

from pipeline.subtitle import format_subtitle_chunk_text


_TERMINAL_PUNCT_RE = re.compile(r"[,.!?;:]+$")
_STRONG_BOUNDARY = {".", "?", "!", ";", ":"}
_MEDIUM_BOUNDARY = {","}


def _clean_text(text: str) -> str:
    return str(text or "").strip()


def _display_text(text: str) -> str:
    return _TERMINAL_PUNCT_RE.sub("", _clean_text(text)).strip()


def _display_length(text: str) -> int:
    return len(_display_text(text))


def _hard_limit(max_chars_per_line: int, max_lines: int) -> int:
    return max(1, int(max_chars_per_line or 42)) * max(1, int(max_lines or 2))


def _soft_limit(max_chars_per_line: int, max_lines: int) -> int:
    hard = _hard_limit(max_chars_per_line, max_lines)
    return max(int(max_chars_per_line or 42), int(math.floor(hard * 0.9)))


def _duration(chunk: dict) -> float:
    return max(float(chunk.get("end_time", 0.0) or 0.0) - float(chunk.get("start_time", 0.0) or 0.0), 0.0)


def _target_limit_for_chunk(
    chunk: dict,
    *,
    max_chars_per_line: int,
    max_lines: int,
    max_chars_per_second: float,
) -> int:
    target = _soft_limit(max_chars_per_line, max_lines)
    duration = _duration(chunk)
    if duration > 0 and max_chars_per_second and max_chars_per_second > 0:
        cps_target = int(math.floor(duration * float(max_chars_per_second)))
        if cps_target > 0:
            target = min(target, max(int(max_chars_per_line or 42), cps_target))
    return max(1, target)


def _formatted_lines_fit(
    text: str,
    *,
    weak_boundary_words: set[str] | None,
    max_chars_per_line: int,
    max_lines: int,
) -> bool:
    formatted = format_subtitle_chunk_text(
        text,
        weak_boundary_words=weak_boundary_words,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
    )
    lines = [line for line in formatted.splitlines() if line]
    return (
        len(lines) <= max(1, int(max_lines or 2))
        and all(len(line) <= max(1, int(max_chars_per_line or 42)) for line in lines)
    )


def _text_fits(
    text: str,
    *,
    target_char_limit: int,
    weak_boundary_words: set[str] | None,
    max_chars_per_line: int,
    max_lines: int,
) -> bool:
    return (
        _display_length(text) <= target_char_limit
        and _formatted_lines_fit(
            text,
            weak_boundary_words=weak_boundary_words,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
    )


def _boundary_score(words: list[str], index: int, target: int, weak_boundary_words: set[str]) -> float:
    left = " ".join(words[:index])
    right = " ".join(words[index:])
    left_len = _display_length(left)
    right_len = _display_length(right)
    previous = words[index - 1].strip()
    next_word = words[index].strip() if index < len(words) else ""
    previous_clean = previous.strip(",.;:!?").lower()
    next_clean = next_word.strip(",.;:!?").lower()

    score = abs(left_len - target) + (abs(left_len - right_len) * 0.8)
    if previous[-1:] in _STRONG_BOUNDARY:
        score -= 40
    elif previous[-1:] in _MEDIUM_BOUNDARY:
        score -= 18
    if next_clean in weak_boundary_words:
        score += 10
    if 0 < len(next_clean) <= 3:
        score += 4
    if previous_clean in weak_boundary_words:
        score += 5
    return score


def _choose_split_index(
    words: list[str],
    *,
    target_char_limit: int,
    max_chars_per_line: int,
    max_lines: int,
    weak_boundary_words: set[str],
) -> int:
    if len(words) <= 1:
        return 1

    hard = _hard_limit(max_chars_per_line, max_lines)
    candidates = []
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        left_len = _display_length(left)
        if left_len > hard:
            continue
        candidates.append((index, _boundary_score(words, index, target_char_limit, weak_boundary_words)))

    if not candidates:
        return max(1, len(words) // 2)

    return min(candidates, key=lambda item: item[1])[0]


def _split_text(
    text: str,
    *,
    target_char_limit: int,
    weak_boundary_words: set[str] | None,
    max_chars_per_line: int,
    max_lines: int,
) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    weak = {str(word).lower() for word in (weak_boundary_words or set()) if str(word).strip()}
    if _text_fits(
        text,
        target_char_limit=target_char_limit,
        weak_boundary_words=weak,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
    ):
        return [text]

    words = text.split()
    if len(words) <= 1:
        return [text]

    split_index = _choose_split_index(
        words,
        target_char_limit=target_char_limit,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        weak_boundary_words=weak,
    )
    left = " ".join(words[:split_index]).strip()
    right = " ".join(words[split_index:]).strip()
    if not left or not right:
        return [text]

    return (
        _split_text(
            left,
            target_char_limit=target_char_limit,
            weak_boundary_words=weak,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
        + _split_text(
            right,
            target_char_limit=target_char_limit,
            weak_boundary_words=weak,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
    )


def _slice_words_for_pieces(words: list[dict], pieces: list[str]) -> list[list[dict]]:
    if not words:
        return []
    total_piece_words = sum(len(piece.split()) for piece in pieces)
    if len(words) < total_piece_words:
        return []

    slices: list[list[dict]] = []
    cursor = 0
    for piece in pieces:
        count = len(piece.split())
        piece_words = words[cursor:cursor + count]
        if len(piece_words) != count:
            return []
        slices.append(piece_words)
        cursor += count
    return slices


def _times_from_words(chunk: dict, pieces: list[str]) -> list[tuple[float, float, list[dict]]] | None:
    words = [word for word in (chunk.get("words") or []) if isinstance(word, dict)]
    word_slices = _slice_words_for_pieces(words, pieces)
    if not word_slices:
        return None

    times: list[tuple[float, float, list[dict]]] = []
    previous_end = float(chunk.get("start_time", 0.0) or 0.0)
    for piece_words in word_slices:
        start = float(piece_words[0].get("start_time", previous_end) or previous_end)
        end = float(piece_words[-1].get("end_time", start) or start)
        if start < previous_end:
            start = previous_end
        if end <= start:
            end = start + 0.001
        times.append((round(start, 3), round(end, 3), piece_words))
        previous_end = end
    return times


def _times_by_proportion(chunk: dict, pieces: list[str]) -> list[tuple[float, float, list[dict]]]:
    start = float(chunk.get("start_time", 0.0) or 0.0)
    end = float(chunk.get("end_time", start) or start)
    if end <= start:
        end = start + max(0.001, 0.001 * max(len(pieces), 1))

    total_duration = end - start
    weights = [max(_display_length(piece), 1) for piece in pieces]
    total_weight = max(sum(weights), 1)
    times: list[tuple[float, float, list[dict]]] = []
    cursor = start
    for index, weight in enumerate(weights):
        piece_start = cursor
        if index == len(weights) - 1:
            piece_end = end
        else:
            piece_end = start + total_duration * (sum(weights[:index + 1]) / total_weight)
            piece_end = max(piece_end, piece_start + 0.001)
        times.append((round(piece_start, 3), round(piece_end, 3), []))
        cursor = piece_end
    return times


def _build_split_chunks(chunk: dict, pieces: list[str]) -> list[dict]:
    timed = _times_from_words(chunk, pieces)
    if timed is None:
        timed = _times_by_proportion(chunk, pieces)

    split_chunks = []
    for split_index, (piece, timing) in enumerate(zip(pieces, timed)):
        start, end, piece_words = timing
        new_chunk = dict(chunk)
        new_chunk["text"] = piece
        new_chunk["start_time"] = start
        new_chunk["end_time"] = end
        new_chunk["split_index"] = split_index
        if piece_words:
            new_chunk["words"] = piece_words
            new_chunk["source_asr_text"] = " ".join(_clean_text(word.get("text")) for word in piece_words).strip()
        split_chunks.append(new_chunk)
    return split_chunks


def _renumber(chunks: Iterable[dict]) -> list[dict]:
    result = []
    for index, chunk in enumerate(chunks):
        item = dict(chunk)
        item["index"] = index
        result.append(item)
    return result


def split_oversized_subtitle_chunks(
    chunks: list[dict],
    *,
    weak_boundary_words: set[str] | None = None,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    max_chars_per_second: float = 17,
) -> list[dict]:
    """Split subtitle chunks that cannot safely fit the configured display box.

    The function preserves text order and tries to keep timing tied to subtitle
    ASR word timestamps. When timestamps are unavailable, it divides the
    original chunk duration proportionally by piece text length.
    """
    split_chunks: list[dict] = []
    for chunk in chunks or []:
        text = _clean_text(chunk.get("text"))
        if not text:
            split_chunks.append(dict(chunk))
            continue

        target_limit = _target_limit_for_chunk(
            chunk,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
            max_chars_per_second=max_chars_per_second,
        )
        pieces = _split_text(
            text,
            target_char_limit=target_limit,
            weak_boundary_words=weak_boundary_words,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
        if len(pieces) <= 1:
            split_chunks.append(dict(chunk))
        else:
            split_chunks.extend(_build_split_chunks(chunk, pieces))

    return _renumber(split_chunks)
